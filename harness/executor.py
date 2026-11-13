"""One task: bounded ReAct loop with budget admission, dedup, and validated submit."""
import json
import shutil
import tempfile
import time
from pathlib import Path

from . import tools
from .strategies import TEST_SNIPPET, action_key, execute, strategy_for

CONTEXT_TURNS = 4
MIN_OUTPUT = 64


class Outcome:
    SUBMITTED = 'submitted'
    SKIPPED = 'skipped'
    STOPPED = 'stopped'


def _chat_view(messages):
    return messages[:2] + messages[2:][-CONTEXT_TURNS:]


def _parse(content):
    content = content.strip()
    if content.startswith('```'):
        content = '\n'.join(content.splitlines()[1:-1])
    try:
        action = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(action, dict) or len(action) != 1:
        return None
    return action


def run_task(task, task_id, workspace, model, budget, store, log, *,
             steps=6, task_budget=1500, output_cap=700, deadline=None):
    strategy = strategy_for(task)
    if strategy is None:
        log('task_stop', task=task_id, reason='unsupported_type')
        return Outcome.STOPPED

    work = Path(tempfile.mkdtemp(prefix='task-'))
    source = Path(workspace)
    if source.is_dir():
        shutil.copytree(source, work, dirs_exist_ok=True)
    tests = {p.relative_to(work).as_posix(): p.read_bytes()
             for p in work.rglob('test*.py')}
    seen = set()
    messages = [
        {'role': 'system', 'content': strategy['system']},
        {'role': 'user', 'content': task['instruction'] + '\nFiles:\n' + tools.inventory(work)},
    ]
    task_spent = 0.0
    try:
        for step in range(steps):
            if deadline and time_left(deadline) <= 0:
                log('task_stop', task=task_id, reason='time_limit')
                return Outcome.STOPPED
            view = _chat_view(messages)
            reserve = len(json.dumps(view).encode('utf-8')) / 10
            room = min(budget.remaining(), task_budget - task_spent)
            output_limit = min(output_cap, int(max(0, room - reserve)))
            if output_limit < MIN_OUTPUT:
                reason = 'budget' if budget.remaining() <= room else 'attempt_limit'
                log('task_stop', task=task_id, reason=reason, detail='token_reserve')
                return Outcome.STOPPED
            try:
                reply = model.invoke(messages=view, max_tokens=output_limit)
            except Exception as error:  # noqa: BLE001 - proxy failure must stop the run
                log('call', task=task_id, step=step, usage=None, error=type(error).__name__)
                raise
            usage = reply.usage_metadata
            if usage is None:
                log('call', task=task_id, step=step, usage=None, error='missing_usage')
                raise RuntimeError('Missing token usage; stopping to preserve budget')
            before = budget.spent
            spent = budget.charge(usage.input_tokens, usage.output_tokens)
            task_spent += spent - before
            log('call', task=task_id, step=step, spent=spent,
                usage=usage.model_dump(include={'input_tokens', 'output_tokens',
                                                 'reasoning_tokens', 'total_tokens', 'model_name'}))
            content = reply.content.strip()
            action = _parse(content)
            if action is None or not set(action) <= strategy['allowed'] or not isinstance(
                    next(iter(action.values())), str):
                log('bad_action', task=task_id, step=step, content=content[:400])
                messages.append({'role': 'user', 'content': 'Return one supported JSON action with a string value.'})
                continue
            key = next(iter(action))
            log('action', task=task_id, step=step, action={key: action[key][:200]})

            if key == 'skip':
                store.skip(task_id, action['skip'])
                log('skip', task=task_id, reason=action['skip'])
                return Outcome.SKIPPED

            if key in ('read', 'search', 'python'):
                dk = action_key(action)
                if dk in seen:
                    log('task_stop', task=task_id, reason='repeated_action')
                    return Outcome.STOPPED
                seen.add(dk)
                try:
                    if key == 'python':
                        output = execute(action['python'], work)
                    else:
                        output = tools.file_action(action, work)
                except (OSError, ValueError) as error:
                    output = f'{type(error).__name__}: {error}'
                log('tool_result', task=task_id, step=step, output=output[:1500])
                _restore_tests(work, tests)
                messages.extend([{'role': 'assistant', 'content': content},
                                 {'role': 'user', 'content': output}])
                continue

            # answer
            answer = str(action['answer']).strip()
            if not answer:
                continue
            if strategy['verify_tests']:
                output = execute(TEST_SNIPPET, work)
                if not output.startswith('exit=0\n') or not tests:
                    messages.append({'role': 'user', 'content': output})
                    continue
                _write_solution(source, work, tests)
                answer = 'DONE'
            files = _solution_files(work, tests) if strategy['verify_tests'] else None
            store.save(task_id, answer, files)
            log('submit', task=task_id)
            return Outcome.SUBMITTED
        log('task_stop', task=task_id, reason='attempt_limit')
        return Outcome.STOPPED
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _restore_tests(work, tests):
    for name, data in tests.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _solution_files(work, tests):
    files = {}
    for path in work.rglob('*.py'):
        relative = path.relative_to(work).as_posix()
        if relative not in tests:
            files[relative] = path.read_text(encoding='utf-8')
    return files


def _write_solution(source, work, tests):
    if not source.is_dir():
        return
    for path in work.rglob('*.py'):
        relative = path.relative_to(work)
        if relative.as_posix() in tests:
            continue
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')


def time_left(deadline):
    return deadline - time.monotonic()

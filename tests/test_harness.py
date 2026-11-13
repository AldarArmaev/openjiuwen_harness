"""Unit tests for the deterministic harness parts. No network."""
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness import tools, triage
from harness.budget import Budget
from harness.executor import Outcome, run_task
from harness.planner import plan
from harness.store import Store


class FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.reasoning_tokens = 0
        self.total_tokens = input_tokens + output_tokens
        self.model_name = 'fake'

    def model_dump(self, include=None):
        return {'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens,
                'reasoning_tokens': 0, 'total_tokens': self.total_tokens, 'model_name': 'fake'}


class FakeReply:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = FakeUsage()


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, messages, max_tokens):
        self.calls.append({'messages': messages, 'max_tokens': max_tokens})
        if not self.replies:
            raise AssertionError('model called too many times')
        return FakeReply(self.replies.pop(0))


def record():
    events = []
    return events, (lambda event, **fields: events.append(dict(event=event, **fields)))


def workspace(tmp_path, files):
    work = tmp_path / 'tasks' / 't1' / 'workspace'
    work.mkdir(parents=True)
    for name, content in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    return work


def test_budget_units_and_admission():
    budget = Budget(100)
    budget.charge(100, 0)          # 10 units
    budget.charge(0, 20)           # 20 units
    assert budget.spent == 30
    assert budget.admitted(50, 20)
    assert not budget.admitted(50, 21)


def test_planner_orders_by_reward_per_estimate_and_skips_done(tmp_path):
    root = tmp_path / 'tasks'
    for task_id, task in {'a': {'task_type': 'math', 'reward': 1},
                          'b': {'task_type': 'math', 'reward': 2},
                          'c': {'task_type': 'coding', 'reward': 1}}.items():
        folder = root / task_id
        folder.mkdir(parents=True)
        (folder / 'task.json').write_text(json.dumps(task), encoding='utf-8')
    ordered = [path.parent.name for path, _ in plan(root, done={'c'})]
    assert ordered == ['b', 'a']


def test_probe_flags_search_without_terms(tmp_path):
    work = workspace(tmp_path, {'k.md': 'The gateway port is 8443.'})
    task = {'task_type': 'search', 'instruction': 'Find value "zzz_absent_value"?'}
    solvable, _ = triage.probe(task, tools.texts(work))
    assert solvable is False
    solvable, _ = triage.probe({'task_type': 'search', 'instruction': 'What is the "gateway port"?'},
                               tools.texts(work))
    assert solvable is True


def test_read_and_search_are_workspace_scoped(tmp_path):
    work = workspace(tmp_path, {'a/k.md': 'alpha value is 12'})
    assert '12' in tools.file_action({'search': 'alpha'}, work)
    assert 'alpha' in tools.file_action({'read': 'a/k.md'}, work)
    with pytest.raises(ValueError):
        tools.file_action({'read': '../../etc/passwd'}, work)
    with pytest.raises(ValueError):
        tools.file_action({'read': 'task.json'}, work)


def test_math_submits_and_persists(tmp_path):
    out = tmp_path / 'out'
    store = Store(out, tmp_path / 'tasks')
    events, log = record()
    work = workspace(tmp_path, {})
    model = FakeModel(['{"python": "print(2+3)"}', '{"answer": "5"}'])
    result = run_task({'task_type': 'math', 'instruction': '2+3?'}, 't1', work,
                      model, Budget(10_000), store, log)
    assert result is Outcome.SUBMITTED
    assert (out / 'tasks' / 't1' / 'workspace' / 'answer.txt').read_text().strip() == '5'
    assert (work / 'answer.txt').read_text().strip() == '5'
    assert [event['event'] for event in events] == ['call', 'action', 'tool_result',
                                                    'call', 'action', 'submit']


def test_repeated_python_stops_task(tmp_path):
    store = Store(tmp_path / 'out', None)
    events, log = record()
    work = workspace(tmp_path, {})
    model = FakeModel(['{"python": "print(1)"}', '{"python": "print(1)"}'])
    result = run_task({'task_type': 'math', 'instruction': 'q'}, 't1', work,
                      model, Budget(10_000), store, log)
    assert result is Outcome.STOPPED
    assert events[-1]['reason'] == 'repeated_action'


def test_budget_blocks_call_before_it_happens(tmp_path):
    store = Store(tmp_path / 'out', None)
    events, log = record()
    work = workspace(tmp_path, {})
    model = FakeModel(['{"answer": "5"}'])
    result = run_task({'task_type': 'math', 'instruction': 'q'}, 't1', work,
                      model, Budget(30), store, log)
    assert result is Outcome.STOPPED
    assert model.calls == []
    assert events[-1]['reason'] == 'budget'


def test_missing_usage_raises(tmp_path):
    store = Store(tmp_path / 'out', None)
    events, log = record()
    work = workspace(tmp_path, {})

    class Silent(FakeModel):
        def invoke(self, messages, max_tokens):
            reply = FakeReply('{"answer": "5"}')
            reply.usage_metadata = None
            return reply

    with pytest.raises(RuntimeError):
        run_task({'task_type': 'math', 'instruction': 'q'}, 't1', work,
                 Silent([]), Budget(10_000), store, log)


def test_coding_requires_green_tests_then_writes_source(tmp_path):
    out = tmp_path / 'out'
    store = Store(out, tmp_path / 'tasks')
    events, log = record()
    tests = ('import unittest\n'
             'from mylib import add\n'
             'class T(unittest.TestCase):\n'
             '    def test_add(self):\n'
             '        self.assertEqual(add(1, 2), 3)\n'
             '        self.assertEqual(add(4, 5), 9)\n'
             'if __name__ == "__main__":\n'
             '    unittest.main()\n')
    work = workspace(tmp_path, {'mylib.py': 'def add(a, b):\n    return a - b\n',
                                'test_mylib.py': tests})
    model = FakeModel([
        '{"answer": "DONE"}',
        '{"python": "import pathlib; p=pathlib.Path(\'mylib.py\'); '
        'p.write_text(p.read_text().replace(\'a - b\', \'a + b\'))"}',
        '{"answer": "DONE"}',
    ])
    result = run_task({'task_type': 'coding', 'instruction': 'fix mylib'}, 't1', work,
                      model, Budget(10_000), store, log)
    assert result is Outcome.SUBMITTED
    submitted = (out / 'tasks' / 't1' / 'workspace' / 'mylib.py').read_text()
    assert 'a + b' in submitted
    assert 'test_mylib.py' not in json.dumps(events)
    assert (work / 'answer.txt').read_text().strip() == 'DONE'
    assert 'a + b' in (work / 'mylib.py').read_text()


def test_skip_writes_marker(tmp_path):
    out = tmp_path / 'out'
    store = Store(out, None)
    events, log = record()
    work = workspace(tmp_path, {})
    model = FakeModel(['{"skip": "no local file mentions the value"}'])
    result = run_task({'task_type': 'search', 'instruction': 'q'}, 't1', work,
                      model, Budget(10_000), store, log)
    assert result is Outcome.SKIPPED
    assert (out / 'tasks' / 't1.skip').exists()


def test_store_resumes_after_restart(tmp_path):
    out = tmp_path / 'out'
    tasks = tmp_path / 'tasks'
    folder = tasks / 't1'
    folder.mkdir(parents=True)
    (folder / 'task.json').write_text(json.dumps({'task_type': 'math', 'reward': 1}), encoding='utf-8')
    store = Store(out, tasks)
    store.save('t1', '5')
    fresh = Store(out, tasks)
    assert fresh.done == {'t1'}
    assert plan(tasks, done=fresh.done) == []


def test_invalid_json_asks_again(tmp_path):
    store = Store(tmp_path / 'out', None)
    events, log = record()
    work = workspace(tmp_path, {})
    model = FakeModel(['I think the answer is five', '{"answer": "5"}'])
    result = run_task({'task_type': 'math', 'instruction': 'q'}, 't1', work,
                      model, Budget(10_000), store, log)
    assert result is Outcome.SUBMITTED
    assert model.calls[1]['messages'][-1]['content'].startswith('Return one supported')

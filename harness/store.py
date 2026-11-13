"""Crash-safe submission store: every completed task is persisted at once."""
import json
import os


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


class Store:
    """Submissions live in out/tasks/<task_id>/workspace, plus per-task answer.txt."""

    def __init__(self, out, tasks_dir):
        self.out = out
        self.tasks_dir = tasks_dir
        self.dir = out / 'tasks' if out else None
        self.journal_path = out / 'agent.log' if out else None
        self.state_path = out / 'state.json' if out else None
        self._done = set()
        if self.state_path and self.state_path.exists():
            try:
                self._done = set(json.loads(self.state_path.read_text(encoding='utf-8')).get('done', []))
            except (OSError, ValueError):
                self._done = set()

    @property
    def done(self):
        return set(self._done)

    def save(self, task_id, answer, files=None):
        """Persist answer (and optionally the solved workspace) atomically, then mark done."""
        if self.dir:
            workspace = self.dir / task_id / 'workspace'
            if files is not None:
                workspace.mkdir(parents=True, exist_ok=True)
                for name, data in files.items():
                    target = workspace / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(target, data)
            answer = str(answer).strip() + '\n'
            atomic_write(workspace / 'answer.txt', answer)
            if self.tasks_dir:
                source = self.tasks_dir / task_id / 'workspace'
                if source.is_dir():
                    atomic_write(source / 'answer.txt', answer)
        self._done.add(task_id)
        if self.state_path:
            atomic_write(self.state_path, json.dumps({'done': sorted(self._done)}, ensure_ascii=False))

    def skip(self, task_id, reason):
        if self.dir:
            atomic_write(self.dir / f'{task_id}.skip', reason[:500] + '\n')

    def event(self, record):
        if self.journal_path:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')

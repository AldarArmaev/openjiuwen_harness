"""Task ordering: expected points per token unit, excluding already-solved tasks."""
import json

ESTIMATES = {'math': 180, 'data_analysis': 320, 'coding': 900, 'search': 500}
DEFAULT_ESTIMATE = 700


def plan(tasks_dir, done=(), file_counts=None):
    """tasks_dir contains <task_id>/task.json (+ optional workspace). Returns an ordered list."""
    pending = []
    for path in sorted(tasks_dir.glob('*/task.json')):
        task_id = path.parent.name
        if task_id in done:
            continue
        try:
            task = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        count = (file_counts or {}).get(task_id)
        if count is None:
            workspace = path.parent / 'workspace'
            count = sum(1 for p in workspace.rglob('*') if p.is_file()) if workspace.is_dir() else 0
        estimate = ESTIMATES.get(task.get('task_type'), DEFAULT_ESTIMATE)
        if task.get('task_type') == 'search':
            estimate += min(count * 3, 600)
        pending.append((float(task.get('reward', 0)) / estimate, path, task))
    pending.sort(key=lambda item: item[0], reverse=True)
    return [(path, task) for _, path, task in pending]

#!/usr/bin/env python3
"""Hackathon agent entry point.

Contract: reads tasks from --tasks-dir (or $TASKS), writes submissions to $OUT
(default ./out) and persists each solved task immediately.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.budget import Budget
from harness.executor import Outcome, run_task
from harness.models import build
from harness.planner import plan
from harness.store import Store
from harness.tools import texts
from harness.triage import probe

TIME_LIMIT = 55 * 60
SAFETY_MARGIN = 90
TOKEN_LIMIT = float(os.environ.get('AGENT_TOKEN_LIMIT', '400000'))
MIN_TASK_RESERVE = 900


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks-dir', default=os.environ.get('TASKS', 'task-dir'))
    parser.add_argument('--out', default=os.environ.get('OUT', '/tmp/out'))
    parser.add_argument('--model', default=os.environ.get('AGENT_MODEL'))
    parser.add_argument('--token-limit', type=float, default=TOKEN_LIMIT)
    parser.add_argument('--steps', type=int, default=10)
    parser.add_argument('--task-budget', type=float, default=1500)
    parser.add_argument('--offline', action='store_true', help='Only deterministic pre-checks')
    args = parser.parse_args()

    tasks_dir = Path(args.tasks_dir).resolve()
    out = Path(args.out).resolve()
    store = Store(out, tasks_dir)
    budget = Budget(args.token_limit)
    started = time.monotonic()
    deadline = started + TIME_LIMIT - SAFETY_MARGIN

    def log(event, **fields):
        record = {'event': event, 'at': round(time.monotonic() - started, 2),
                  'spent': round(budget.spent, 1), **fields}
        store.event(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    if not tasks_dir.is_dir():
        log('fatal', reason='missing_tasks_dir', path=str(tasks_dir))
        return 2

    model = None
    if not args.offline:
        try:
            model = build(args.model)
        except Exception as error:  # noqa: BLE001
            log('fatal', reason='model_init', error=type(error).__name__, detail=str(error)[:300])
            return 2

    log('start', tasks=str(tasks_dir), out=str(out), token_limit=budget.total,
        model=args.model or 'default')
    results = {}
    for path, task in plan(tasks_dir, done=store.done):
        task_id = path.parent.name
        work = path.parent / 'workspace'
        evidence = probe(task, texts(work)) if task.get('task_type') == 'search' else (None, '')
        log('triage', task=task_id, type=task.get('task_type'), reward=task.get('reward', 0),
            solvable=evidence[0], detail=evidence[1])
        if evidence[0] is False:
            store.skip(task_id, 'triage: ' + evidence[1])
            results[task_id] = 'skipped'
            continue
        if args.offline:
            results[task_id] = 'planned'
            continue
        reserve = args.task_budget
        if not args.offline and budget.remaining() < reserve + MIN_TASK_RESERVE:
            log('run_stop', reason='budget', task=task_id)
            break
        if time.monotonic() >= deadline:
            log('run_stop', reason='time_limit', task=task_id)
            break
        try:
            results[task_id] = run_task(task, task_id, work, model, budget, store, log,
                                        steps=args.steps, task_budget=args.task_budget,
                                        deadline=deadline)
        except Exception as error:  # noqa: BLE001 - proxy failure means no more useful calls
            log('run_stop', reason='model_error', task=task_id, error=type(error).__name__,
                detail=str(error)[:300])
            break
    done = store.done
    log('finish', submitted=len(done), spent=round(budget.spent, 1),
        seconds=round(time.monotonic() - started, 1), results=results)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('interrupted; saved submissions stay in OUT', file=sys.stderr)
        sys.exit(130)

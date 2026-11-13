"""Deterministic file tools over a task workspace."""
import json
import os
import re
import stat
import tempfile
import threading

READ_LIMIT = 4000
SEARCH_LIMIT = 3500
TEXT_SUFFIXES = {'.md', '.txt', '.csv', '.py', '.json', '.yaml', '.yml', '.tsv'}


def regular_files(work):
    root = work.resolve()
    found = {}
    for path in sorted(work.rglob('*')):
        try:
            info = path.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            continue
        relative = path.relative_to(work).as_posix()
        if path.name == 'task.json' or path.name == 'answer.txt' or '__pycache__' in path.parts:
            continue
        found[relative] = path
    return found


def inventory(work):
    files = regular_files(work)
    lines = [f'{len(files)} files. Use search for literal content or Python to inspect directories.']
    for name, path in list(files.items())[:12]:
        lines.append(f'{name} ({path.stat().st_size} bytes)')
        if path.suffix == '.csv':
            with path.open(encoding='utf-8', errors='replace') as stream:
                lines.append('CSV header: ' + stream.readline()[:400])
    return '\n'.join(lines)


def texts(work, limit=200_000):
    blob = {}
    budget = limit
    for name, path in regular_files(work).items():
        if path.suffix not in TEXT_SUFFIXES or budget <= 0:
            continue
        try:
            raw = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        blob[name] = raw[:budget]
        budget -= len(raw)
    return blob


def safe_child(work, relative):
    path = (work / relative).resolve()
    if not path.is_relative_to(work.resolve()) or path.name == 'task.json':
        raise ValueError('Invalid file path')
    return path


def file_action(action, work):
    if 'read' in action:
        return safe_child(work, action['read']).open(encoding='utf-8', errors='replace').read(READ_LIMIT)
    needle = action['search'].casefold()
    matches = []
    size = 0
    for name, text in texts(work, limit=2_000_000).items():
        for number, line in enumerate(text.splitlines(), 1):
            if needle in line.casefold():
                hit = f'{name}:{number}: {line[:500].rstrip()}'
                matches.append(hit)
                size += len(hit)
                if size > SEARCH_LIMIT:
                    return '\n'.join(matches) + '\nMore matches exist; narrow search.'
    return '\n'.join(matches) or 'No matching text in local text files.'

"""Cheap deterministic pre-checks run before spending model tokens."""
import re

QUOTED = re.compile(r'"([^"\n]{3,80})"|«([^»\n]{3,80})»')
WORD = re.compile(r'[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_.-]{3,}')
STOP = {'that', 'this', 'with', 'from', 'have', 'what', 'when', 'which', 'where', 'who', 'does',
        'find', 'file', 'files', 'task', 'answer', 'current', 'directory', 'python', 'write',
        'only', 'final', 'text', 'value', 'data', 'using', 'about', 'their', 'there', 'then',
        'them', 'were', 'will', 'would', 'should', 'could', 'into', 'each', 'every', 'all',
        'instruction', 'workspace', 'knowledge', 'records', 'record', 'search', 'within'}


def terms_of(instruction):
    terms = [a or b for a, b in QUOTED.findall(instruction)]
    if not terms:
        terms = [w for w in WORD.findall(instruction.lower()) if w not in STOP]
    return terms


def probe(task, files):
    """Return (solvable_hint, evidence). Files is a dict name->text for text files."""
    if task.get('task_type') != 'search':
        return None, 'no probe'
    terms = terms_of(task.get('instruction', ''))
    if not terms:
        return None, 'no terms'
    haystacks = [(name, text.casefold()) for name, text in files.items()]
    hits = {name for name, blob in haystacks for term in terms if term.casefold() in blob}
    if hits:
        return True, f'{len(hits)} files mention the query'
    return False, 'no local file mentions any query term'

"""Reserve repository write globs before an executor starts."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import shlex
import sys
import socket

from desks import DeskError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from lib.github_reader import GitHubReader

ACTIVE = {'running', 'started', 'pending', 'delivered', 'delivered_live', 'timed_out', 'stuck'}


def process_owner() -> dict:
    return {'host': socket.gethostname(), 'pid': os.getpid()}


def process_terminated(identity: dict, *, group: bool = False) -> bool:
    """Only ESRCH proves termination. Reused IDs or denied probes retain ownership."""
    number = identity.get('pgid' if group else 'pid')
    if identity.get('host') != socket.gethostname() or type(number) is not int or number <= 0:
        return False
    try:
        (os.killpg if group else os.kill)(number, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False


def termination_evidence(row: dict) -> str | None:
    executor = row.get('executor') or {}
    kind = executor.get('kind')
    if kind == 'reservation' and process_terminated(row.get('owner_process') or {}):
        return 'reservation owner terminated before executor handoff'
    if kind == 'process_group' and process_terminated(executor, group=True):
        return 'executor process group terminated'
    if kind == 'codex_turn' and executor.get('socket') and executor.get('turn_id'):
        import codex_wire
        if codex_wire.turn_terminated(executor['socket'], executor['thread_id'], executor['turn_id']):
            return 'Codex turn terminated (thread/read)'
    return None


def declaration(task: str, explicit: list[str] | None) -> list[str]:
    patterns = list(explicit or [])
    for line in task.splitlines():
        match = re.match(r'^\s*Writes:\s*(.*)$', line, re.I)
        if match:
            lexer = shlex.shlex(match[1].replace('`', ''), posix=True)
            lexer.whitespace += ','
            lexer.whitespace_split = True
            lexer.commenters = ''
            try:
                patterns.extend(lexer)
            except ValueError as exc:
                raise DeskError('bad_write_set', 'Writes: has invalid quoting') from exc
    result = []
    for pattern in patterns:
        pattern = pattern.removeprefix('./')
        if (not pattern or pattern.startswith('/') or '..' in pattern.split('/')
                or '\n' in pattern or '\r' in pattern):
            raise DeskError('bad_write_set', 'write globs must be nonempty repository-relative paths')
        if pattern not in result:
            result.append(pattern)
    return result


def own_pr(task: str, repo: str) -> int | None:
    match = re.search(r'^\s*(?:Own PR|PR|Pull request):\s*(\S+)', task, re.I | re.M)
    if not match:
        match = re.search(r'\b(?:continue (?:your )?own|own|update) PR\s*#?(\d+)\b', task, re.I)
    if not match:
        return None
    value = match[1].strip('`.,')
    url = re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/pull/(\d+)', value)
    if url:
        if url[1].lower() != repo.lower():
            raise DeskError('bad_own_pr', 'the brief names a PR in a different repository')
        value = url[2]
    if not re.fullmatch(r'#?[1-9]\d*', value):
        raise DeskError('bad_own_pr', 'Own PR: must name a PR number or its GitHub URL')
    return int(value.lstrip('#'))


def open_prs(cwd: str) -> tuple[str, list[dict]]:
    reader = GitHubReader(cwd=cwd)
    try:
        repo = reader.json(['repo', 'view', '--json', 'nameWithOwner'])['nameWithOwner']
        if not isinstance(repo, str) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
            raise ValueError('invalid repository')
        prs = reader.api(f'repos/{repo}/pulls?state=open', paginate=True)
        owners = []
        for pr in prs:
            number = pr['number']
            if type(number) is not int or number <= 0:
                raise ValueError('invalid PR number')
            files = reader.api(f'repos/{repo}/pulls/{number}/files', paginate=True)
            paths = []
            for item in files:
                for key in ('filename', 'previous_filename'):
                    if key in item:
                        if not isinstance(item[key], str) or not item[key]:
                            raise ValueError('invalid PR file')
                        paths.append(item[key])
            owners.append({'number': number, 'title': pr['title'], 'files': paths})
        return repo, owners
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        raise DeskError('ownership_unreadable', f'cannot verify open PR ownership: {exc}') from exc


def _tokens(pattern: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(pattern):
        end = i + 1
        if pattern[i] == '[':
            j = end
            if j < len(pattern) and pattern[j] == '!':
                j += 1
            if j < len(pattern) and pattern[j] == ']':
                j += 1
            close = pattern.find(']', j)
            if close >= 0:
                end = close + 1
        token = pattern[i:end]
        if token != '*' or not tokens or tokens[-1] != '*':
            tokens.append(token)
        i = end
    return tokens


def overlaps(left: str, right: str) -> bool:
    """Intersect shell-style fnmatch languages, including future filenames.

    A star can consume a character or advance without one. Literal/class
    transitions are intersected over character boundaries from both patterns;
    range endpoints and their neighbors represent every membership interval.
    """
    a, b = _tokens(left), _tokens(right)
    points = {0, 0x10ffff}
    for char in left + right:
        value = ord(char)
        points.update(x for x in (value - 1, value, value + 1) if 0 <= x <= 0x10ffff)
    alphabet = [chr(x) for x in points]
    seen = set()
    todo = [(0, 0)]
    while todo:
        i, j = todo.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and a[i] == '*':
            todo.append((i + 1, j))
        if j < len(b) and b[j] == '*':
            todo.append((i, j + 1))
        if i < len(a) and j < len(b) and any(
                fnmatchcase(char, a[i]) and fnmatchcase(char, b[j]) for char in alphabet):
            todo.append((i if a[i] == '*' else i + 1, j if b[j] == '*' else j + 1))
    return False


@contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _append(path: Path, row: dict) -> None:
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(row, separators=(',', ':')) + '\n')


def record(path: Path, row: dict) -> None:
    with _locked(path):
        _append(path, row)


def reserve(path: Path, row: dict, cwd: str, writes: list[str]) -> dict:
    repo, prs = open_prs(cwd)
    mine = own_pr(row['task'], repo)
    metadata = {'repo': repo, 'writes': writes, 'own_pr': mine,
                'owner_process': process_owner(), 'executor': {'kind': 'reservation'},
                'ownership_state': 'held'}
    with _locked(path):
        for pr in prs:
            if pr['number'] != mine and any(fnmatchcase(file, pattern)
                    for file in pr['files'] for pattern in writes):
                raise DeskError('write_set_overlap', f"write set owned by PR {pr['number']} "
                    f"({pr['title']}); build on top of PR {pr['number']}")
        active: dict[str, dict] = {}
        try:
            if path.exists():
                for line in path.read_text(encoding='utf-8').splitlines():
                    previous = json.loads(line)
                    if not isinstance(previous, dict):
                        raise ValueError('result row is not an object')
                    key = previous.get('msg_id')
                    if key:
                        active.setdefault(key, {}).update(previous)
                    elif previous.get('writes') and previous.get('status') in ACTIVE:
                        raise ValueError('active claim has no msg_id')
        except (OSError, ValueError, TypeError) as exc:
            raise DeskError('ownership_unreadable', 'cannot verify in-flight results ledger') from exc
        for previous in active.values():
            if previous.get('repo', '').lower() != repo.lower():
                continue
            held = previous.get('ownership_state') == 'held'
            if previous.get('ownership_state') == 'released' or (not held and previous.get('status') not in ACTIVE):
                continue
            claimed = previous.get('writes', [])
            if not isinstance(claimed, list) or any(not isinstance(p, str) for p in claimed):
                raise DeskError('ownership_unreadable', 'invalid in-flight write set')
            if any(overlaps(a, b) for a in writes for b in claimed):
                evidence = termination_evidence(previous)
                if evidence:
                    _append(path, {'msg_id': previous['msg_id'], 'status': 'recovered',
                                   'ownership_state': 'released', 'ownership_detail': evidence})
                    continue
                owner_pr = previous.get('own_pr')
                advice = f'build on top of PR {owner_pr}' if owner_pr else "build on top of the owner's PR once it is opened"
                stuck = ('; stuck: termination unconfirmed' if previous.get('status') in ('timed_out', 'stuck')
                         or not previous.get('executor')
                         or previous.get('executor', {}).get('kind') == 'unconfirmed'
                         or 'stuck' in previous.get('ownership_detail', '')
                         or process_terminated(previous.get('owner_process') or {}) else '')
                raise DeskError('write_set_overlap', f"write set owned by in-flight job "
                    f"{previous['msg_id']} (desk {previous.get('desk', '?')}){stuck}; {advice}")
        _append(path, {**row, **metadata, 'status': 'running'})
    return metadata

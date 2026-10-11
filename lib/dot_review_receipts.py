"""Detect local receipt tampering and bind Dot decisions to relay runs and live authors.

Both ledgers are append-only hash chains, not an authentication boundary against
the same OS user rewriting both chains consistently.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

SCHEMA = 'carr-dot-review-receipt/v2'
RUN_SCHEMA = 'carr-dot-relay-run/v1'
ALARM = 'DOT_REVIEW_RECEIPT_ALARM'
GENESIS = '0' * 64
DOT_MARKER = 'Reviewer: ChatGPT Dot'
STAMP = re.compile(r'^APPROVE\r?\nReviewed-SHA: [0-9a-f]{40}\r?\n(?:\r?\n)?'
                   r'(?:Orchestrator merge queue:|Orchestrator: verified exact head)')


def receipt_directory():
    return Path(os.environ.get('CARR_DOT_REVIEW_RECEIPTS',
                               str(Path.home() / '.local/state/carr/merge-queue/relay')))


def _binding(meta, body):
    if (not isinstance(meta.get('repo'), str) or not meta['repo'].strip() or
            type(meta.get('pr')) is not int or meta['pr'] < 1 or
            not isinstance(meta.get('sha'), str) or not re.fullmatch(r'[0-9a-f]{40}', meta['sha']) or
            not isinstance(body, str)):
        raise ValueError('invalid Dot receipt binding')
    return {'repo': meta['repo'], 'pr': meta['pr'], 'reviewed_sha': meta['sha'],
            'body_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode('utf-8')).hexdigest()


class _ChainError(ValueError):
    pass


def _alarm(reason):
    print(f'{ALARM}: {reason}', file=sys.stderr)


@contextmanager
def _locked(directory):
    with (directory / '.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_chain(directory, schema):
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise _ChainError(f'{directory.name} ledger is not a directory')
    entries, previous, seen_runs = [], GENESIS, set()
    for sequence, path in enumerate(sorted(directory.iterdir()), 1):
        if path.name != f'{sequence:020d}.json' or not path.is_file():
            raise _ChainError(f'{directory.name} ledger has an unexpected entry or chain gap')
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise _ChainError(f'{directory.name} ledger entry is unreadable') from error
        if not isinstance(value, dict):
            raise _ChainError(f'{directory.name} ledger entry is not an object')
        unsigned = {k: v for k, v in value.items() if k != 'sha256'}
        if (value.get('schema') != schema or type(value.get('sequence')) is not int or
                value['sequence'] != sequence or value.get('prev_sha256') != previous or
                value.get('sha256') != _digest(unsigned)):
            raise _ChainError(f'{directory.name} ledger hash chain is broken')
        try:
            _binding({'repo': value.get('repo'), 'pr': value.get('pr'),
                      'sha': value.get('reviewed_sha')}, '')
        except ValueError as error:
            raise _ChainError(f'{directory.name} ledger binding is invalid') from error
        if (not isinstance(value.get('body_sha256'), str) or
                not re.fullmatch(r'[0-9a-f]{64}', value['body_sha256']) or
                not isinstance(value.get('reviewer'), str) or not value['reviewer'].strip() or
                not isinstance(value.get('relay_run_id'), str) or not value['relay_run_id'].strip()):
            raise _ChainError(f'{directory.name} ledger relay identity is invalid')
        if value['relay_run_id'] in seen_runs:
            raise _ChainError(f'{directory.name} ledger repeats a relay run')
        seen_runs.add(value['relay_run_id'])
        entries.append(value)
        previous = value['sha256']
    return entries


def _read_ledgers(directory):
    if any(path.name not in {'.lock', 'runs', 'receipts'} for path in directory.iterdir()):
        raise _ChainError('legacy or unrecognized receipt storage requires reconciliation')
    return {'runs': _read_chain(directory / 'runs', RUN_SCHEMA),
            'receipts': _read_chain(directory / 'receipts', SCHEMA)}


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append(ledger, value):
    directory = receipt_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with _locked(directory):
        try:
            chain = _read_ledgers(directory)[ledger]
        except (OSError, _ChainError) as error:
            _alarm(str(error))
            raise ValueError('append-only Dot ledger failed validation') from error
        for entry in chain:
            if entry['relay_run_id'] == value['relay_run_id']:
                stored = {k: v for k, v in entry.items() if k not in {'sequence', 'prev_sha256', 'sha256'}}
                if stored != value:
                    raise ValueError('append-only Dot relay run conflict')
                return entry
        value = {**value, 'sequence': len(chain) + 1,
                 'prev_sha256': chain[-1]['sha256'] if chain else GENESIS}
        value['sha256'] = _digest(value)
        destination = directory / ledger
        destination.mkdir(exist_ok=True)
        fd = os.open(destination / f'{value["sequence"]:020d}.json',
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        _sync(destination)
        _sync(directory)
        return value


def _value(schema, meta, body, reviewer, relay_run_id):
    if (not isinstance(reviewer, str) or not reviewer.strip() or
            not isinstance(relay_run_id, str) or not relay_run_id.strip()):
        raise ValueError('Dot ledger requires an authenticated reviewer and relay run')
    return {'schema': schema, **_binding(meta, body), 'reviewer': reviewer.strip(),
            'relay_run_id': relay_run_id.strip()}


def record_run(meta, body, *, reviewer, relay_run_id):
    """Append only after the relay consumes a completed authenticated Dot report."""
    return _append('runs', _value(RUN_SCHEMA, meta, body, reviewer, relay_run_id))


def record(meta, body, *, reviewer, relay_run_id, builder=None, branch_author=None):
    """Append publication provenance; builder strings are informational only."""
    value = _value(SCHEMA, meta, body, reviewer, relay_run_id)
    if builder is not None:
        value['builder'] = builder
    if branch_author is not None:
        value['branch_author'] = branch_author
    return _append('receipts', value)


def _live_authors(meta, api):
    if api is None:
        raise ValueError('fresh GitHub author reader is unavailable')
    endpoint = f'repos/{meta["repo"]}/pulls/{meta["pr"]}'
    pr = api(endpoint)
    author = (pr.get('user') or {}).get('login') if isinstance(pr, dict) else None
    if not isinstance(author, str) or not author.strip():
        raise ValueError('live GitHub PR author is unknown')
    commits = api(endpoint + '/commits?per_page=100')
    if not isinstance(commits, list):
        raise ValueError('live GitHub commit authors are unreadable')
    authors = {author.strip().casefold()}
    for commit in commits:
        login = (commit.get('author') or {}).get('login') if isinstance(commit, dict) else None
        if not isinstance(login, str) or not login.strip():
            raise ValueError('live GitHub commit author is unknown')
        authors.add(login.strip().casefold())
        raw_author = ((commit.get('commit') or {}).get('author') or {})
        for field in ('name', 'email'):
            alias = raw_author.get(field)
            if isinstance(alias, str) and alias.strip():
                authors.add(alias.strip().casefold())
    return authors


def _matching(meta, body, api):
    binding = _binding(meta, body)
    directory = receipt_directory()
    if not directory.exists():
        return None, False
    try:
        with _locked(directory):
            ledgers = _read_ledgers(directory)
    except (OSError, _ChainError) as error:
        _alarm(str(error))
        return None, _claims_relay(body)
    candidates = [entry for entry in ledgers['receipts']
                  if all(entry.get(k) == v for k, v in binding.items())]
    if not candidates:
        if any(all(run.get(k) == v for k, v in binding.items()) for run in ledgers['runs']):
            _alarm('relay run lacks its publication receipt')
            return None, True
        return None, False
    paired = []
    for entry in candidates:
        run = next((run for run in ledgers['runs']
                    if run['relay_run_id'] == entry['relay_run_id'] and
                    run['reviewer'] == entry['reviewer'] and
                    all(run.get(k) == v for k, v in binding.items())), None)
        if run is None:
            _alarm('receipt lacks a matching relay run')
        else:
            paired.append(entry)
    if not paired:
        return None, True
    try:
        authors = _live_authors(meta, api)
    except Exception as error:
        _alarm(f'live GitHub author verification failed ({type(error).__name__})')
        return None, True
    for entry in paired:
        if entry['reviewer'].casefold() in authors:
            _alarm('relay reviewer is a live PR or commit author')
        else:
            return entry, True
    return None, True


def matching(meta, body, *, api=None):
    """Return verified provenance; absent, corrupted, or self-authored evidence refuses."""
    return _matching(meta, body, api)[0]


def deciding(comments, repo, pr, *, policy, config, api=None):
    """Return (comment, receipt); comment metadata cannot authenticate a Dot verdict."""
    carrying = []
    for comment in comments:
        body = comment.get('body', '')
        if STAMP.match(body) or not policy['verdict'](body, config):
            continue
        sha = policy['reviewed_header_sha'](body.replace('REVIEW: BLOCKED', 'APPROVE', 1))
        receipt, attempted = _matching({'repo': repo, 'pr': pr, 'sha': sha}, body, api) if sha else (None, False)
        if receipt:
            carrying.append((comment, receipt))
        elif attempted or _claims_relay(body):
            # A claimed relay publication without provenance cannot become an ordinary owner approval.
            continue
        elif policy['trusted_commenter'](comment, config):
            carrying.append((comment, None))
    return max(carrying, key=lambda item: (str(item[0].get('created_at') or ''),
                                         int(item[0].get('id') or 0))) if carrying else (None, None)


def _claims_relay(body):
    fence = None
    for line in body.splitlines()[2:]:
        text = line.strip()
        marker = re.match(r'(`{3,}|~{3,})', text)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
        elif fence is None and (text == DOT_MARKER or re.fullmatch(r'<!-- dot-review:[^\n]* -->', text)):
            return True
    return False

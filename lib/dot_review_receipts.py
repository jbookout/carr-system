"""Local relay provenance shared by publication and independent-review consumers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

SCHEMA = 'carr-dot-review-receipt/v1'
DOT_MARKER = 'Reviewer: ChatGPT Dot'
STAMP = re.compile(r'^APPROVE\r?\nReviewed-SHA: [0-9a-f]{40}\r?\n(?:\r?\n)?'
                   r'(?:Orchestrator merge queue:|Orchestrator: verified exact head)')


def receipt_directory():
    return Path(os.environ.get('CARR_DOT_REVIEW_RECEIPTS',
                               str(Path.home() / '.local/state/carr/merge-queue/relay')))


def _binding(meta, body):
    return {'repo': meta['repo'], 'pr': meta['pr'], 'reviewed_sha': meta['sha'],
            'body_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}


def _key(binding):
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def _independent(receipt):
    builder, reviewer = receipt.get('builder'), receipt.get('reviewer')
    return (isinstance(builder, str) and bool(builder.strip()) and
            isinstance(reviewer, str) and bool(reviewer.strip()) and
            builder.strip().casefold() != reviewer.strip().casefold() and
            str(receipt.get('branch_author') or '').strip().casefold() != reviewer.strip().casefold() and
            isinstance(receipt.get('relay_run_id'), str) and bool(receipt['relay_run_id'].strip()))


def record(meta, body, *, builder, reviewer, relay_run_id, branch_author):
    """Called only after the relay consumed an authenticated Dot report, before POST."""
    binding = _binding(meta, body)
    value = {'schema': SCHEMA, **binding, 'builder': builder, 'reviewer': reviewer,
             'branch_author': branch_author, 'relay_run_id': relay_run_id}
    if not _independent(value):
        raise ValueError('Dot relay receipt requires a known builder and a distinct authenticated reviewer')
    directory = receipt_directory() / _key(binding)
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(relay_run_id.encode()).hexdigest() + '.json'
    target = directory / name
    fd, partial = tempfile.mkstemp(prefix='.', dir=directory)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(partial, target)
        except FileExistsError:
            if json.loads(target.read_text()) != value:
                raise ValueError('append-only Dot receipt conflict') from None
        for parent in (directory, directory.parent):
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        Path(partial).unlink(missing_ok=True)
    return value


def matching(meta, body):
    binding = _binding(meta, body)
    directory = receipt_directory() / _key(binding)
    for path in sorted(directory.glob('*.json')):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (isinstance(value, dict) and value.get('schema') == SCHEMA and
                all(value.get(k) == v for k, v in binding.items()) and _independent(value)):
            return value
    return None


def deciding(comments, repo, pr, *, policy, config):
    """Return (comment, receipt); comment metadata cannot authenticate a Dot verdict."""
    carrying = []
    for comment in comments:
        body = comment.get('body', '')
        if STAMP.match(body) or not policy['verdict'](body, config):
            continue
        sha = policy['reviewed_header_sha'](body.replace('REVIEW: BLOCKED', 'APPROVE', 1))
        receipt = matching({'repo': repo, 'pr': pr, 'sha': sha}, body) if sha else None
        if receipt:
            carrying.append((comment, receipt))
        elif _claims_relay(body):
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

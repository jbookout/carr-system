"""Evaluate caller-owned acceptance criteria against retained artifacts and checks.

Criteria are supplied before execution, never learned from output. Evidence is
data, not authority. This module reads artifacts; it never executes commands or
models. Unknown criteria abstain, missing or contradictory evidence fails.
"""
import hashlib
import json
from pathlib import Path
import re

MAX_BYTES = 96000
SHA = re.compile(r'[0-9a-f]{64}')


def contract(instructions):
    """Extract the explicit acceptance_contract JSON object, including a fence.

Natural language is deliberately not compiled into a permission to complete.
"""
    try:
        if isinstance(instructions, dict):
            value = instructions
        else:
            text = str(instructions or '').strip()
            if text.startswith('```json\n') and text.endswith('```'):
                text = text[8:-3]
            value = json.loads(text)
        result = value.get('acceptance_contract')
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError, AttributeError):
        return {}


def _path(value, root):
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    path.relative_to(root)
    return path


def _pointer(value, pointer):
    if pointer == '':
        return value
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('invalid JSON pointer')
    for part in pointer[1:].split('/'):
        key = part.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def evaluate(criteria, evidence, *, root='.'):
    """Return {status: passed|failed|needs_review, criteria: [{id,status,reason}]}.

Supported predicates: artifact (existence/optional expected digest), contains
(literal required output), json_equals (RFC6901 pointer and expected value),
check (exact command, source revision, exit code and required output). A check
receipt's zero exit alone cannot pass. No supplied evidence changes a criterion.
"""
    root = Path(root).resolve()
    evidence = evidence if isinstance(evidence, dict) else {}
    if not isinstance(criteria, list) or not criteria:
        return {'status':'needs_review','criteria':[], 'reason':'explicit acceptance criteria required'}
    rows, seen, cache = [], set(), {}
    for index, criterion in enumerate(criteria):
        cid = criterion.get('id', str(index)) if isinstance(criterion, dict) else str(index)
        status, reason = 'needs_review', 'semantic or unknown criterion; needs review'
        try:
            if not isinstance(cid, str) or cid in seen:
                raise ValueError('duplicate or invalid criterion id')
            seen.add(cid)
            if not isinstance(criterion, dict):
                rows.append({'id':cid,'status':status,'reason':reason})
                continue
            kind = criterion.get('kind')
            if kind in {'artifact','contains','json_equals'}:
                path = _path(criterion['path'], root)
                matches = [item for item in evidence.get('artifacts', [])
                           if isinstance(item, dict) and _path(item.get('path',''),root) == path]
                if len(matches) != 1 or not SHA.fullmatch(str(matches[0].get('sha256',''))):
                    raise ValueError('one digest-bound artifact receipt required')
                if path not in cache:
                    with path.open('rb') as handle:
                        cache[path] = handle.read(MAX_BYTES + 1)
                raw = cache[path]
                if not raw or len(raw) > MAX_BYTES:
                    raise ValueError('artifact empty or over byte budget')
                digest = hashlib.sha256(raw).hexdigest()
                if digest != matches[0]['sha256']:
                    raise ValueError('artifact digest mismatch')
                if criterion.get('sha256') and digest != criterion['sha256']:
                    raise ValueError('expected artifact digest mismatch')
                if kind == 'contains':
                    expected = criterion['text']
                    if not isinstance(expected,str) or not expected or expected not in raw.decode('utf-8'):
                        raise ValueError('required literal output missing')
                if kind == 'json_equals':
                    actual = _pointer(json.loads(raw), criterion['pointer'])
                    expected = criterion['value']
                    if type(actual) is not type(expected) or actual != expected:
                        raise ValueError('expected JSON value mismatch')
                if kind == 'artifact' and not criterion.get('sha256'):
                    status, reason = 'needs_review', 'artifact identity alone does not establish acceptance; expected digest required'
                else:
                    status, reason = 'passed', 'artifact read and predicate matched'
            elif kind == 'check':
                command, source = criterion['command'], criterion['source_sha']
                expected = criterion['output_contains']
                if not all(isinstance(v,str) and v for v in (command,source,expected)):
                    raise ValueError('exact command, source and required output needed')
                matches = [item for item in evidence.get('checks', []) if isinstance(item, dict)
                           and item.get('command') == command and item.get('source_sha') == source]
                # Last matching run resolves an earlier failure of the SAME check.
                if not matches:
                    raise ValueError('bound check receipt missing')
                latest = matches[-1]
                if type(latest.get('exit_code')) is not int or latest['exit_code'] != 0:
                    raise ValueError('check did not pass')
                if not isinstance(latest.get('output'),str) or expected not in latest['output']:
                    raise ValueError('required check output missing')
                status, reason = 'passed', 'bound check receipt and output matched'
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            status, reason = 'failed', str(exc)
        rows.append({'id':cid,'status':status,'reason':reason})
    status = ('failed' if any(r['status'] == 'failed' for r in rows) else
              'needs_review' if any(r['status'] == 'needs_review' for r in rows) else 'passed')
    return {'status':status,'criteria':rows}

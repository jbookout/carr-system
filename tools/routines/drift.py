import json
from pathlib import Path
import re

MANIFEST = Path(__file__).resolve().parents[2] / 'ops/routines/jobs.v1.json'


def registry_rows(value):
    if isinstance(value, dict):
        if 'tasks' in value:
            return registry_rows(value['tasks'])
        if 'scheduledTasks' in value:
            return registry_rows(value['scheduledTasks'])
        return [dict(v, id=v.get('id', k)) for k, v in value.items() if isinstance(v, dict)]
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError('scheduler registry must contain task objects')
    return value


def check(manifest=None, home=None):
    manifest = manifest if manifest is not None else json.loads(MANIFEST.read_text())
    home = Path(home) if home is not None else Path.home()
    replacements = {old: job['id'] for job in manifest['jobs']
                    if job['label'].startswith('com.carr.routine-') for old in job['replaces']}
    replacements.update({old: 'retired' for old in manifest.get('retired', [])})
    registry = home / '.claude/scheduled_tasks.json'
    statuses = {}
    if registry.exists():
        try:
            for row in registry_rows(json.loads(registry.read_text())):
                ident = row.get('id') or row.get('taskId') or row.get('name')
                if ident in replacements:
                    enabled = row.get('enabled')
                    if not isinstance(enabled, bool):
                        raise ValueError('enabled must be boolean')
                    statuses[ident] = statuses.get(ident, False) or enabled
        except (OSError, ValueError):
            return [{'task_id': 'scheduler-registry', 'replacement': 'all', 'state': 'unreadable'}]
    rows = []
    for ident, replacement in sorted(replacements.items()):
        enabled = statuses.get(ident)
        task = home / '.claude/scheduled-tasks' / ident
        if (task / 'SKILL.md').exists():
            try:
                for name in ('task.json', 'metadata.json'):
                    if (task / name).exists():
                        metadata = json.loads((task / name).read_text())
                        if not isinstance(metadata, dict) or not isinstance(metadata.get('enabled'), bool):
                            raise ValueError('enabled must be boolean')
                        enabled = enabled is True or metadata['enabled']
                text = (task / 'SKILL.md').read_text()
                frontmatter = text.split('---', 2)[1] if text.startswith('---') else ''
                match = re.search(r'^enabled:\s*(true|false)\s*$', frontmatter, re.M)
                if match:
                    enabled = enabled is True or match.group(1) == 'true'
            except (OSError, ValueError):
                rows.append({'task_id': ident, 'replacement': replacement, 'state': 'unreadable'})
                continue
            if enabled is None:
                rows.append({'task_id': ident, 'replacement': replacement, 'state': 'unverified'})
        if enabled is True:
            rows.append({'task_id': ident, 'replacement': replacement, 'state': 'enabled'})
    return rows


def render(rows):
    response = ('on breach: owner=claude; bin/routine-run.sh --record-drift creates one deduplicated add-loop; '
                'remediation=install declared replacements then disable replaced/retired Claude tasks and retain enabled=false readback; '
                'verify=run.sh health --section routines; auto-clear=all replaced/retired task states are disabled or absent')
    detail = ', '.join(f"{row['task_id']}={row['state']}" for row in rows) if rows else 'no enabled or unverified replaced tasks'
    return f"{'FAIL' if rows else 'OK'} routine drift: {detail} · {response}"

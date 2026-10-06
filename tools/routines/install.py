"""Preview, render and explicitly install the three code routine LaunchAgents."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import subprocess
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
TIMEZONE = 'America/Chicago'


def render(repo, home, job):
    job_id = job.get('id')
    label = job.get('label')
    if not isinstance(job_id, str) or label != f'com.carr.routine-{job_id}' or '/' in job_id or '..' in job_id:
        raise ValueError('routine manifest ID and label must match')
    template = repo / 'ops/launchd' / f'{label}.plist'
    raw = template.read_text().replace('{{REPO}}', escape(str(repo))).replace('{{HOME}}', escape(str(home)))
    plist = plistlib.loads(raw.encode())
    expected = {'Weekday': job['weekday'], 'Hour': job['hour'], 'Minute': job['minute']}
    if plist['Label'] != label or plist['StartCalendarInterval'] != expected:
        raise ValueError(f'routine template disagrees with manifest: {job_id}')
    if '{{' in raw:
        raise ValueError('unresolved routine plist placeholder')
    return plistlib.dumps(plist, sort_keys=False)


def validate_main(repo, home):
    if repo.resolve() != (home / 'carr-system').resolve():
        raise ValueError('--apply requires the canonical ~/carr-system checkout')
    branch = subprocess.run(['git', 'branch', '--show-current'], cwd=repo,
                            capture_output=True, text=True, check=True).stdout.strip()
    if branch != 'main':
        raise ValueError('--apply requires canonical main, never a feature worktree')
    paths = ['bin/routine-run.sh', 'bin/install-routines.sh', 'tools/routines',
             'ops/routines', 'ops/launchd']
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *paths], cwd=repo, check=True)
    if not (repo / 'bin/routine-run.sh').is_file() or not os.access(repo / '.venv/bin/python', os.X_OK):
        raise ValueError('canonical routine runner or repository Python is unavailable')


def validate_timezone(localtime=Path('/etc/localtime')):
    if not str(localtime.resolve()).endswith('/' + TIMEZONE):
        raise ValueError('launchd calendar schedules require machine timezone America/Chicago')


def install(repo, home, manifest, *, apply=False):
    if manifest.get('timezone') != TIMEZONE or not isinstance(manifest.get('jobs'), list):
        raise ValueError('routine manifest needs America/Chicago and a jobs array')
    jobs = manifest['jobs']
    expected = {'lead-signals-weekly', 'contact-enrichment-weekly', 'social-weekly'}
    if {job.get('id') for job in jobs} != expected or len(jobs) != len(expected):
        raise ValueError('routine manifest must declare the three replacement jobs once each')
    rendered = [(job, render(repo, home, job)) for job in jobs]
    destination = home / 'Library/LaunchAgents'
    report = {'mode': 'preview', 'timezone': TIMEZONE,
              'calendar_uses_machine_timezone': True, 'jobs': [
                  {'id': job['id'], 'label': job['label'], 'schedule': plistlib.loads(raw)['StartCalendarInterval'],
                   'destination': str(destination / f"{job['label']}.plist")}
                  for job, raw in rendered]}
    if not apply:
        return report
    validate_main(repo, home)
    validate_timezone()
    destination.mkdir(parents=True, exist_ok=True)
    (repo / 'out/routines').mkdir(parents=True, exist_ok=True)
    domain = f'gui/{os.getuid()}'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    for job, raw in rendered:
        target = destination / f"{job['label']}.plist"
        service = f"{domain}/{job['label']}"
        state = subprocess.run(['/bin/launchctl', 'print', service], capture_output=True, text=True)
        if state.returncode and 'Could not find service' not in state.stderr:
            raise RuntimeError(f"launchd readback failed for {job['label']}")
        if not state.returncode:
            subprocess.run(['/bin/launchctl', 'bootout', service], capture_output=True, check=True)
        if target.exists():
            backups = destination / '_to_delete'
            backups.mkdir(exist_ok=True)
            backup = backups / f'{target.name}.{stamp}'
            target.rename(backup)
        target.write_bytes(raw)
        target.chmod(0o644)
        subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(target)], capture_output=True, check=True)
        subprocess.run(['/bin/launchctl', 'print', service], capture_output=True, check=True)
    report['mode'] = 'installed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='orchestrator activation after merge, canonical main only')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'ops/routines/jobs.v1.json').read_text())
    try:
        print(json.dumps(install(ROOT, Path.home(), manifest, apply=args.apply), sort_keys=True))
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'routine install refused: {exc}\n')


if __name__ == '__main__':
    main()

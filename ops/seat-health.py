#!/usr/bin/env python3
"""Probe builder seats daily; write only allowlisted results for the orchestrator."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sys
import uuid
from seat_health import SEATS, atomic_json, dispatchable, health_rows, probe, probe_jev, reconcile, verb_call

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-repo', type=Path, default=ROOT)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--seat', choices=list(SEATS), action='append')
    parser.add_argument('--timeout-seconds', type=int, default=90)
    parser.add_argument('--e2e-cli', type=Path, default=Path.home() / 'doctorcre-app/node_modules/.bin/e2e')
    parser.add_argument('--no-record', action='store_true', help='Watched probes without record mutations')
    parser.add_argument('--force', action='store_true', help='Run again today for attended verification')
    parser.add_argument('--show', action='store_true', help='Read current health without a model call')
    parser.add_argument('--jev-task', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.jev_task:
        return probe_jev(args.jev_task)
    if args.timeout_seconds < 1 or args.timeout_seconds > 1800:
        parser.error('timeout must be between 1 and 1800 seconds')
    output = args.output_root or args.runtime_repo / 'out/orch/budget'
    report_path = output / 'seat-health.json'
    if args.show:
        try:
            report = json.loads(report_path.read_text())
        except (OSError, ValueError):
            report = {}
        rows = health_rows(report)
        print('\n'.join(rows))
        return int(any(line.startswith('FAIL') for line in rows))
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'seat-health.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('seat-health: another probe run owns the lock', file=sys.stderr)
            return 2
        now = datetime.now(timezone.utc)
        ledger = output / 'seat-health-runs.jsonl'
        previous = ledger.read_text().splitlines() if ledger.exists() else []
        if previous and not args.force and not args.seat and json.loads(previous[-1]).get('day') == now.date().isoformat():
            print('seat-health: today already probed; read seat-health.json')
            return int(not json.loads(previous[-1]).get('passed'))
        run_id = 'seat-health-' + str(uuid.uuid4())
        run_dir = args.runtime_repo / 'out/seat-health-runs' / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        rows = {}
        failed = False
        for seat in args.seat or SEATS:
            row = probe(seat, args.runtime_repo.resolve(), run_dir.resolve(), args.timeout_seconds, args.e2e_cli)
            row['observed_at'] = datetime.now(timezone.utc).isoformat()
            row['record_action'] = 'not_requested'
            if not args.no_record:
                try:
                    row['record_action'] = reconcile(row, output / ('seat-health-' + seat + '-loop.json'),
                        lambda name, payload: verb_call(args.runtime_repo, name, payload))
                except (ValueError, OSError):
                    row['record_action'] = 'error'
            failed |= not row['passed'] or row['record_action'] == 'error'
            rows[seat] = row
            atomic_json(report_path, {'schema': 'carr-seat-health/v1', 'run_id': run_id,
                'observed_at': now.isoformat(), 'max_age_seconds': 86400,
                'recipient': 'orchestrator', 'seats': rows})
            print(f"{'PASS' if row['passed'] else 'FAIL'} {seat} latency={row['latency_seconds']}s "
                  f"layer={row['failing_layer'] or 'none'} record={row['record_action']} · {row['action']}")
        with ledger.open('a') as log:
            log.write(json.dumps({'run_id': run_id, 'day': now.date().isoformat(),
                'ended_at': datetime.now(timezone.utc).isoformat(), 'seats': list(rows),
                'passed': not failed}) + '\n')
        return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())

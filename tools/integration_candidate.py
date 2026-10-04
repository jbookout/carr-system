"""Current-main union and integration-time migration/registry allocation.

Used by registry generator writes and disposable database proofs. This module
never changes applied artifacts or guesses how a domain generator renders SQL.
The integration owner renders the returned successor once, then proves its tree.
"""
from __future__ import annotations
import hashlib
import fcntl
import os
import tempfile
import json
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from git_env import scrubbed_env
from migration_number_contract import (
    MigrationNumberError, allocate_integration_successors,
    allocate_registry_successor, validate_integration_union,
)

REGISTRY = re.compile(r'^mcp-server/src/scac-mutation-registry\.v([1-9][0-9]*)\.generated\.js$')
SHA = re.compile(r'^[0-9a-f]{40}$')


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(['git', *args], cwd=repo, env=scrubbed_env(), capture_output=True, timeout=60)
    if result.returncode:
        raise MigrationNumberError('integration Git read failed; refresh the exact base')
    return result.stdout


def main_snapshot(repo: Path, base: str) -> dict[str, bytes]:
    if not SHA.fullmatch(base):
        raise MigrationNumberError('integration base must be an exact commit SHA')
    if git(repo, 'rev-parse', 'origin/main').decode().strip() != base:
        raise MigrationNumberError('integration base differs from current origin/main')
    paths = git(repo, 'ls-tree', '-r', '--name-only', base, '--', 'migrations', 'mcp-server/src').decode().splitlines()
    return {p: git(repo, 'show', f'{base}:{p}') for p in paths
            if (p.startswith('migrations/') and p.endswith('.sql')) or REGISTRY.fullmatch(p) or p=='mcp-server/src/scac-mutation-registry.generated.js'}


def require_current_base(repo: Path, base: str) -> None:
    if not SHA.fullmatch(base) or git(repo, "rev-parse", "origin/main").decode().strip() != base:
        raise MigrationNumberError("integration base differs from current origin/main")
    result = subprocess.run(['git', 'merge-base', '--is-ancestor', base, 'HEAD'], cwd=repo, env=scrubbed_env(), capture_output=True, timeout=60)
    if result.returncode:
        raise MigrationNumberError('integrate current main before generation or proof')


def allocation_plan(repo: Path, base: str, pending: list[str]) -> dict:
    snapshot = main_snapshot(repo, base)
    migrations = [Path(p).name for p in snapshot if p.startswith('migrations/')]
    versions = [int(match.group(1)) for p in snapshot if (match := REGISTRY.fullmatch(p))]
    predecessor, successor = allocate_registry_successor(versions)
    predecessor_path = f'mcp-server/src/scac-mutation-registry.v{predecessor}.generated.js'
    return {'schema': 'integration-allocation/v1', 'base': base,
            'migration_names': allocate_integration_successors(migrations, pending),
            'registry_predecessor': predecessor, 'registry_successor': successor,
            'predecessor_sha256': hashlib.sha256(snapshot[predecessor_path]).hexdigest()}


def validate_candidate(repo: Path, base: str, *, require_clean: bool = True) -> dict:
    require_current_base(repo, base)
    if require_clean and git(repo, "status", "--porcelain").strip():
        raise MigrationNumberError("commit the integrated candidate before exact-source proof")
    main = main_snapshot(repo, base)
    migrations = {Path(p).name: b for p,b in main.items() if p.startswith('migrations/')}
    candidate = {p.name: p.read_bytes() for p in (repo/'migrations').glob('*.sql')}
    validate_integration_union(migrations, candidate)
    main_versions = []
    for p, content in main.items():
        if p.startswith('mcp-server/src/'):
            match = REGISTRY.fullmatch(p)
            if match: main_versions.append(int(match.group(1)))
            if not (repo/p).is_file() or (repo/p).read_bytes() != content:
                raise MigrationNumberError(f'applied registry missing or edited: {p}')
    predecessor, successor = allocate_registry_successor(main_versions)
    new = []
    for candidate_path in (repo/'mcp-server/src').glob('scac-mutation-registry.v*.generated.js'):
        relative = candidate_path.relative_to(repo).as_posix()
        match = REGISTRY.fullmatch(relative)
        if match and relative not in main:
            version = int(match.group(1)); new.append(version)
            expected = f'export const SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry.v{version}";'
            if expected not in candidate_path.read_text():
                raise MigrationNumberError('generated registry filename/version mismatch')
    if sorted(new) != list(range(successor, successor + len(new))):
        raise MigrationNumberError('registry must be an ordered successor of current main')
    return {'schema': 'integration-source/v1', 'base': base,
            'head': git(repo, 'rev-parse', 'HEAD').decode().strip(),
            'tree': git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
            'registry_predecessor': predecessor,
            'pending_migrations': sorted(set(candidate)-set(migrations))}


def check_generated_write(repo: Path, target: Path, content: bytes, base: str) -> None:
    """Guard the actual generator sink, preserving byte-exact historical rebuilds."""
    require_current_base(repo, base)
    relative = target.resolve().relative_to(repo.resolve()).as_posix()
    main = git(repo,'ls-tree','-r','--name-only',base,'--','migrations','mcp-server/src').decode().splitlines()
    if relative in main:
        if content != git(repo,'show',f'{base}:{relative}') and (relative.startswith('migrations/') or REGISTRY.fullmatch(relative) or relative=='mcp-server/src/scac-mutation-registry.generated.js'):
            raise MigrationNumberError('sealed main artifact cannot be resealed; allocate a successor at integration')
        return
    if relative.startswith('migrations/'):
        if Path(relative).parent.as_posix() != 'migrations':
            raise MigrationNumberError('generated migrations require the canonical migrations directory')
        pending = sorted(p.name for p in (repo/'migrations').glob('*.sql')
                         if 'migrations/'+p.name not in main and p.resolve() != target.resolve())
        names = [Path(p).name for p in main if p.startswith('migrations/')]
        validate_integration_union({n: b'' for n in names}, {**{n: b'' for n in names},
                                  **{n: b'' for n in pending}, target.name: b''})
    elif (match := REGISTRY.fullmatch(relative)):
        versions = [int(version_match.group(1)) for p in main if (version_match := REGISTRY.fullmatch(p))]
        predecessor, successor = allocate_registry_successor(versions)
        if int(match.group(1)) != successor:
            raise MigrationNumberError(f'generator must allocate v{successor} after current-main v{predecessor}')
    else:
        # The inventory also emits non-seal fixtures; their existing checks apply.
        return


def source_input_digest(repo: Path) -> str:
    digest=hashlib.sha256(git(repo,'diff','HEAD','--binary'))
    for raw in sorted(git(repo,'ls-files','-z','--others','--exclude-standard').split(b'\0')):
        if raw:
            digest.update(raw); digest.update(b'\0')
            digest.update(hashlib.sha256((repo/os.fsdecode(raw)).read_bytes()).digest())
    return digest.hexdigest()


def regenerate_once(repo: Path, base: str, pending: list[str], argv: list[str], receipt: Path) -> dict:
    """Render an allocated successor exactly once under a shared Git-root lock.

    Renderers consume CARR_INTEGRATION_ALLOCATION. They own all reference and
    schema changes; this coordinator never substitutes numbers in applied SQL.
    Failed or interrupted execution stays recorded until its source input changes.
    """
    if not argv or any(not isinstance(a, str) or not a for a in argv):
        raise MigrationNumberError('generator argv must be a nonempty string array')
    if not receipt.is_absolute() or receipt.resolve().is_relative_to(repo.resolve()):
        raise MigrationNumberError('generation receipt must be a private absolute path outside the worktree')
    common = Path(git(repo, 'rev-parse', '--git-common-dir').decode().strip())
    if not common.is_absolute(): common = repo/common
    with (common/'integration-generation.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MigrationNumberError('integration generation already owned') from None
        require_current_base(repo, base)
        plan = allocation_plan(repo, base, pending)
        inputs = {'plan': plan, 'argv': argv, 'head': git(repo,'rev-parse','HEAD').decode().strip(),
                  'diff': source_input_digest(repo)}
        # Include untracked draft bytes; their edits are changed generator input.
        if receipt.exists():
            prior=json.loads(receipt.read_text())
            if prior.get('state') == 'running':
                raise MigrationNumberError('interrupted generation requires reconciliation before retry')
            if prior.get('state') == 'generated' and prior.get('base') == base and prior.get('head') == inputs['head'] and prior.get('argv_digest') == hashlib.sha256(json.dumps(argv).encode()).hexdigest() and prior.get('pending_requested') == pending and prior.get('source_after') == source_input_digest(repo) and prior.get('outputs') and all(
                (repo/p).is_file() and hashlib.sha256((repo/p).read_bytes()).hexdigest()==digest
                for p,digest in prior['outputs'].items()):
                validate_candidate(repo,base,require_clean=False)
                return prior
        inputs['pending'] = {n: hashlib.sha256((repo/'migrations'/n).read_bytes()).hexdigest() for n in pending}
        fingerprint = hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
        def publish(value: dict) -> None:
            receipt.parent.mkdir(parents=True,exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=receipt.parent)
            with os.fdopen(fd,'w') as out:
                json.dump(value,out,sort_keys=True); out.flush(); os.fsync(out.fileno())
            os.replace(temporary,receipt)
        if receipt.exists():
            prior=json.loads(receipt.read_text())
            if prior.get('fingerprint') == fingerprint:
                raise MigrationNumberError('generation already attempted for these inputs; reconcile its receipt before retry')
        result={'schema':'integration-generation/v1','fingerprint':fingerprint,'base':base,'head':inputs['head'],
                'argv_digest':hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
                'pending_requested':pending,'state':'running','allocation':plan}
        publish(result)
        # No service credential or provider diagnostic reaches this source renderer.
        env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR','USER'}}
        env['CARR_INTEGRATION_ALLOCATION']=json.dumps(plan,sort_keys=True)
        try:
            run=subprocess.run(argv,cwd=repo,env=env,capture_output=True,timeout=300)
            if run.returncode: raise MigrationNumberError(f'generator refused or failed (exit {run.returncode})')
            main=main_snapshot(repo,base)
            for name in plan['migration_names'].values():
                if not (repo/'migrations'/name).is_file():
                    raise MigrationNumberError('generator exited zero without its allocated migration output')
            for old,new in plan['migration_names'].items():
                if old != new and (repo/'migrations'/old).exists():
                    raise MigrationNumberError('generator left an obsolete pending migration')
            current={p.name:p.read_bytes() for p in (repo/'migrations').glob('*.sql')}
            validate_integration_union({Path(p).name:b for p,b in main.items() if p.startswith('migrations/')},current)
            target=repo/f"mcp-server/src/scac-mutation-registry.v{plan['registry_successor']}.generated.js"
            if not target.is_file():
                raise MigrationNumberError('generator exited zero without its allocated registry successor')
            check_generated_write(repo,target,target.read_bytes(),base)
            validate_candidate(repo,base,require_clean=False)
            result['state']='generated'
            result['source_after']=source_input_digest(repo)
            result['outputs']={str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [target,*[repo/'migrations'/n for n in plan['migration_names'].values()]]}
            publish(result)
            return result
        except Exception:
            result['state']='refused'; publish(result); raise


if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--pending', action='append', default=[])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-write', type=Path)
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--regenerate', help='JSON argv for the source renderer consuming the allocation')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.base is None:
            if not args.check_write: raise MigrationNumberError("an exact --base is required")
            args.base=git(repo,"rev-parse","origin/main").decode().strip()
        if args.regenerate:
            if args.receipt is None: raise MigrationNumberError('--regenerate requires --receipt')
            print(json.dumps(regenerate_once(repo,args.base,args.pending,json.loads(args.regenerate),args.receipt),sort_keys=True))
        elif args.check_write:
            check_generated_write(repo, args.check_write, sys.stdin.buffer.read(), args.base)
        else:
            print(json.dumps(validate_candidate(repo,args.base) if args.verify else allocation_plan(repo,args.base,args.pending), sort_keys=True))
    except (MigrationNumberError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'integration candidate refused: {exc}', file=sys.stderr)
        raise SystemExit(78)

"""Current-main union and integration-time migration/registry allocation.

Used by registry generator writes and disposable database proofs. This module
never changes applied artifacts or guesses how a domain generator renders SQL.
The integration owner renders the returned successor once, then proves its tree.
"""
from __future__ import annotations
import hashlib
import hmac
import fcntl
import os
import secrets
import signal
import tempfile
import time
import json
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ops'))
from git_env import scrubbed_env
from migration_number_contract import (
    MigrationNumberError, allocate_integration_successors,
    allocate_registry_successor, validate_integration_union,
)

REGISTRY = re.compile(r'^mcp-server/src/scac-mutation-registry\.v([1-9][0-9]*)\.generated\.js$')
SHA = re.compile(r'^[0-9a-f]{40}$')
GENERATOR_TIMEOUT_SECONDS = 300
OWNER_ENV = 'CARR_INTEGRATION_OWNER'


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


def source_binding(repo: Path) -> dict:
    return {'head': git(repo,'rev-parse','HEAD').decode().strip(), 'diff': source_input_digest(repo)}


def _ownership_paths(repo: Path) -> tuple[Path, Path]:
    common = Path(git(repo, 'rev-parse', '--git-common-dir').decode().strip())
    if not common.is_absolute(): common = repo/common
    return common/'integration-generation.lock', common/'integration-generation.owner'


@contextmanager
def _exclusive(lock_path: Path):
    """Yield whether this process now holds the shared Git-root generation lock."""
    with lock_path.open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: yield False
        else: yield True


def write_generated_artifact(repo: Path, target: Path, content: bytes) -> None:
    """Check and write one source artifact as a single owned operation.

    Without a coordinator the writer takes the generation lock itself. Under a
    coordinator only its renderer, carrying the published owner token, may
    write, and it writes against the coordinator's pinned base. The base and
    the target bytes are revalidated immediately before the atomic replace.
    """
    lock_path, owner_path = _ownership_paths(repo)
    with _exclusive(lock_path) as acquired:
        if acquired:
            base = git(repo, 'rev-parse', 'origin/main').decode().strip()
        else:
            try: owner = json.loads(owner_path.read_text())
            except (OSError, ValueError): owner = {}
            token = os.environ.get(OWNER_ENV, '')
            if not token or not hmac.compare_digest(token, str(owner.get('token', ''))):
                raise MigrationNumberError('integration generation already owned')
            base = owner['base']
        before = target.read_bytes() if target.exists() else None
        check_generated_write(repo, target, content, base)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f'.{target.name}.')
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(content); out.flush(); os.fsync(out.fileno())
            os.chmod(temporary, target.stat().st_mode & 0o777 if before is not None else 0o644)
            current = target.read_bytes() if target.exists() else None
            if current != before or git(repo, 'rev-parse', 'origin/main').decode().strip() != base:
                raise MigrationNumberError('integration base or target changed during the write; refresh main and regenerate')
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True); raise


def _run_renderer(argv: list[str], repo: Path, env: dict[str, str]) -> int | None:
    """Run the renderer in its own process group; None means it timed out.

    The whole group is stopped before returning, so no descendant can write
    after the receipt becomes terminal or ownership is released.
    """
    process = subprocess.Popen(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        return process.wait(timeout=GENERATOR_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None
    finally:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        deadline = time.monotonic() + 5
        while True:
            try: os.killpg(process.pid, 0)
            except ProcessLookupError: break
            if time.monotonic() > deadline:
                raise MigrationNumberError('renderer processes survived termination; reconcile before retry')
            time.sleep(0.01)


def regenerate_once(repo: Path, base: str, pending: list[str], argv: list[str], receipt: Path) -> dict:
    """Render an allocated successor exactly once under a shared Git-root lock.

    Renderers consume CARR_INTEGRATION_ALLOCATION. They own all reference and
    schema changes; this coordinator never substitutes numbers in applied SQL.
    A failed attempt records the source state it left behind; neither that
    state nor the attempt's own inputs may execute again until reconciled.
    """
    if not argv or any(not isinstance(a, str) or not a for a in argv):
        raise MigrationNumberError('generator argv must be a nonempty string array')
    if not receipt.is_absolute() or receipt.resolve().is_relative_to(repo.resolve()):
        raise MigrationNumberError('generation receipt must be a private absolute path outside the worktree')
    lock_path, owner_path = _ownership_paths(repo)
    with _exclusive(lock_path) as acquired:
        if not acquired:
            raise MigrationNumberError('integration generation already owned')
        require_current_base(repo, base)
        plan = allocation_plan(repo, base, pending)
        argv_digest = hashlib.sha256(json.dumps(argv).encode()).hexdigest()
        # Untracked draft bytes are part of the source; their edits are changed input.
        source = source_binding(repo)
        prior = json.loads(receipt.read_text()) if receipt.exists() else {}
        if prior.get('state') == 'generated' and prior.get('base') == base and prior.get('argv_digest') == argv_digest and prior.get('pending_requested') == pending and prior.get('source_after') == source and prior.get('outputs') and all(
            (repo/p).is_file() and hashlib.sha256((repo/p).read_bytes()).hexdigest()==digest
            for p,digest in prior['outputs'].items()):
            validate_candidate(repo,base,require_clean=False)
            return prior
        if prior.get('state') == 'running' or (prior.get('state') == 'refused' and prior.get('source_after') in (None, source)):
            raise MigrationNumberError('failed or interrupted generation requires reconciliation before retry')
        inputs = {'plan': plan, 'argv': argv, 'source': source,
                  'pending': {n: hashlib.sha256((repo/'migrations'/n).read_bytes()).hexdigest() for n in pending}}
        fingerprint = hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
        if prior.get('fingerprint') == fingerprint:
            raise MigrationNumberError('generation already attempted for these inputs; reconcile its receipt before retry')
        def publish(value: dict) -> None:
            receipt.parent.mkdir(parents=True,exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=receipt.parent)
            with os.fdopen(fd,'w') as out:
                json.dump(value,out,sort_keys=True); out.flush(); os.fsync(out.fileno())
            os.replace(temporary,receipt)
        result={'schema':'integration-generation/v1','fingerprint':fingerprint,'base':base,'head':source['head'],
                'argv_digest':argv_digest,'pending_requested':pending,'state':'running','allocation':plan}
        publish(result)
        # No service credential or provider diagnostic reaches this source renderer.
        env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR','USER'}}
        env['CARR_INTEGRATION_ALLOCATION']=json.dumps(plan,sort_keys=True)
        env[OWNER_ENV]=secrets.token_hex(32)
        fd = os.open(owner_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump({'token': env[OWNER_ENV], 'base': base}, out)
        try:
            # A surviving process group leaves the receipt running: only a human
            # can know what it wrote.
            returncode = _run_renderer(argv, repo, env)
        finally:
            owner_path.unlink(missing_ok=True)
        try:
            if returncode is None: raise MigrationNumberError('generator timed out')
            if returncode: raise MigrationNumberError(f'generator refused or failed (exit {returncode})')
            main=main_snapshot(repo,base)
            outputs = set(plan['migration_names'].values())
            for name in outputs:
                if not (repo/'migrations'/name).is_file():
                    raise MigrationNumberError('generator exited zero without its allocated migration output')
            for old,new in plan['migration_names'].items():
                stale_input = new in inputs['pending'] and inputs['pending'][new] != inputs['pending'][old] and \
                    hashlib.sha256((repo/'migrations'/new).read_bytes()).hexdigest() == inputs['pending'][new]
                if (old not in outputs and (repo/'migrations'/old).exists()) or stale_input:
                    raise MigrationNumberError('generator left an obsolete pending migration')
            current={p.name:p.read_bytes() for p in (repo/'migrations').glob('*.sql')}
            validate_integration_union({Path(p).name:b for p,b in main.items() if p.startswith('migrations/')},current)
            target=repo/f"mcp-server/src/scac-mutation-registry.v{plan['registry_successor']}.generated.js"
            if not target.is_file():
                raise MigrationNumberError('generator exited zero without its allocated registry successor')
            check_generated_write(repo,target,target.read_bytes(),base)
            # Success attests only the pinned source: HEAD must not move, whether
            # the renderer committed or a concurrent writer did.
            final = validate_candidate(repo,base,require_clean=False)
            result['source_after']=source_binding(repo)
            if source['head'] != final['head'] or source['head'] != result['source_after']['head']:
                raise MigrationNumberError('HEAD moved during generation; regenerate against the pinned source')
            result['state']='generated'
            result['outputs']={str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [target,*[repo/'migrations'/n for n in plan['migration_names'].values()]]}
            publish(result)
            return result
        except Exception:
            result['state']='refused'
            result.pop('outputs', None)
            try: result['source_after']=source_binding(repo)
            except (MigrationNumberError, OSError): result.pop('source_after', None)
            publish(result); raise


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--pending', action='append', default=[])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--write', type=Path, help='owned check-and-write of stdin bytes to one source artifact')
    mode.add_argument('--verify', action='store_true')
    mode.add_argument('--regenerate', help='JSON argv for the source renderer consuming the allocation')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.write:
            write_generated_artifact(repo, args.write, sys.stdin.buffer.read())
            return 0
        if args.base is None: raise MigrationNumberError('an exact --base is required')
        if args.regenerate:
            if args.receipt is None: raise MigrationNumberError('--regenerate requires --receipt')
            print(json.dumps(regenerate_once(repo,args.base,args.pending,json.loads(args.regenerate),args.receipt),sort_keys=True))
        else:
            print(json.dumps(validate_candidate(repo,args.base) if args.verify else allocation_plan(repo,args.base,args.pending), sort_keys=True))
        return 0
    except MigrationNumberError as exc:
        print(f'integration candidate refused: {exc}', file=sys.stderr)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        # Other exception text can carry renderer argv, paths or child output.
        print(f'integration candidate refused: {type(exc).__name__}', file=sys.stderr)
    return 78


if __name__ == '__main__':
    raise SystemExit(main())

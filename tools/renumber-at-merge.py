#!/usr/bin/env python3
"""Renumber a PR's pending migrations after rebasing onto fetched origin/main.

The serialized merge queue runs these commands on its owned PR branch:
  git fetch origin main
  git rebase origin/main
  python3 tools/renumber-at-merge.py
  git add -- <changed paths reported in the JSON summary>
  printf '%s\n' 'chore: renumber migrations at merge' > /tmp/renumber-commit.txt
  git commit -F /tmp/renumber-commit.txt
  git push --force-with-lease origin HEAD
  gh pr checks <PR> --watch
Skip add/commit when the summary's changed_paths is empty. CI remains the gate.

--dry-run prints the plan without writes or generators. --branch REF is a
read-only preview of that branch's delta over current main, even before rebase;
requires_rebase records that limitation. It never checks out or pushes the REF.

One stale/colliding slot reallocates the ordered pending batch via the existing
integration allocator. This preserves execution order between dependent files.
Only branch-added migrations move. Main files and its snapshot ledger are
immutable evidence; no live database or provider credential is consulted.

Snapshots regenerate through ops/migration-shadow.py --write (disposable PG18).
SCAC runtime projections use the owning registry renderer. For other generated
outputs, supply their owning command with --generator PATH JSON_ARGV, e.g.
--generator migrations/0010_feature.sql '["python3","tools/render-feature.py"]'.
The PATH identifies the original output; the generator must emit its allocated
name and updated references. Commands are argv arrays, never shell snippets.
Declare each output of a multi-output generator separately for rollback.
References are full filenames and explicit migration labels/keys. Unrelated
numeric SQL values and string codes retain their bytes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import difflib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import scrubbed_env
from successor_ownership import GENERATED_SQL, JSON_ARTIFACTS, is_owned_file, validate_outputs
from migration_number_contract import (
    MigrationNumberError, SLOT_RE, allocate_integration_successors,
    validate_integration_union,
)

SNAPSHOT = "db/schema.sql"
RUNTIME = re.compile(r"^mcp-server/src/scac-mutation-registry\.(?:current|v[1-9][0-9]*)\.generated\.js$")


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, env=scrubbed_env(),
                            capture_output=True, timeout=60)
    if result.returncode:
        raise MigrationNumberError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def blob(repo: Path, revision: str, path: str) -> bytes | None:
    if not git(repo, "ls-tree", revision, "--", path):
        return None
    return git(repo, "show", f"{revision}:{path}")


def paths(repo: Path, revision: str) -> list[str]:
    return [p.decode() for p in git(repo, "ls-tree", "-rz", "--name-only", revision).split(b"\0") if p]


def read_file(repo: Path, path: str) -> bytes:
    target = repo / path
    if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(repo):
        raise MigrationNumberError(f"expected a regular source file: {path}")
    return target.read_bytes()


def applied_names(snapshot: bytes | None) -> set[str]:
    if snapshot is None:
        return set()
    spec = importlib.util.spec_from_file_location("renumber_migration_safety", ROOT / "ops/migration-safety-gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    ledger = module.ledger_rows(snapshot.decode())
    if ledger is None:
        raise MigrationNumberError("main snapshot has no complete migration ledger")
    return set(ledger)


def rewrite(content: bytes, before: bytes | None, renames: dict[str, str], main_names: set[str]) -> bytes:
    try:
        text = content.decode("utf-8")
        old_text = (before or b"").decode("utf-8")
    except UnicodeDecodeError:
        return content
    slots: dict[str, set[str]] = {}
    for old, new in renames.items():
        slots.setdefault(old[:4], set()).add(new[:4])
    identities = set(renames) | main_names
    label_pattern = r"(?i:\bmigration(?:[ _-]?(?:number|slot|no|id))?[ \t:=\'\"#]+)(?:" + "|".join("0*" + str(int(n)) for n in slots) + r")(?![A-Za-z0-9_])"
    pattern = re.compile("|".join(re.escape(n) for n in sorted(identities, key=len, reverse=True)) + "|" + label_pattern)

    def replace(match, *, labels):
        value = match[0]
        if value in renames:
            return renames[value]
        if value in main_names or not labels:
            return value
        label = re.fullmatch(r"(.*?)(\d+)", value)
        slot = f"{int(label[2]):04d}" if label else value
        choices = slots[slot]
        if len(choices) != 1:
            raise MigrationNumberError(f"ambiguous migration label {value}; use the full migration filename")
        number = next(iter(choices))
        return label[1] + str(int(number)).zfill(len(label[2])) if label else number

    original, current = old_text.splitlines(keepends=True), text.splitlines(keepends=True)
    result: list[str] = []
    for tag, _, _, start, end in difflib.SequenceMatcher(None, original, current, autojunk=False).get_opcodes():
        result.extend(pattern.sub(lambda m: replace(m, labels=tag != "equal"), line)
                      for line in current[start:end])
    return "".join(result).encode()


@dataclass
class Plan:
    base: str
    head: str
    requires_rebase: bool
    renames: dict[str, str]
    writes: dict[str, bytes]
    commands: list[list[str]]
    generated: dict[str, bytes]
    immutable: dict[str, bytes]

    def summary(self, dry_run: bool) -> dict:
        changed = set(self.writes) | set(self.generated)
        for old, new in self.renames.items():
            changed.update((f"migrations/{old}", f"migrations/{new}"))
        return dict(schema="migration-renumber/v1", base=self.base, head=self.head,
                    dry_run=dry_run, requires_rebase=self.requires_rebase,
                    renames=self.renames, changed_paths=sorted(changed), generators=self.commands)


def plan(repo: Path, branch: str | None, generators: dict[str, list[str]]) -> Plan:
    base = git(repo, "rev-parse", "origin/main^{commit}").decode().strip()
    head = git(repo, "rev-parse", f"{branch or 'HEAD'}^{{commit}}").decode().strip()
    ancestor = git(repo, "merge-base", base, head).decode().strip()
    if not branch and ancestor != base:
        raise MigrationNumberError("rebase the PR branch on current origin/main before renumbering")
    base_paths = paths(repo, base)
    main = {Path(p).name: blob(repo, base, p) for p in base_paths
            if p.startswith("migrations/") and p.endswith(".sql")}
    comparison = ancestor if branch else base
    delta = git(repo, "diff", "--name-only", "--no-renames", "-z", comparison, *([head] if branch else [])).split(b"\0")
    changed = {p.decode() for p in delta if p}
    if not branch:
        changed.update(p.decode() for p in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p)
    contents = {}
    for path in sorted(changed):
        if branch:
            contents[path] = blob(repo, head, path)
        elif (repo / path).exists() or (repo / path).is_symlink():
            contents[path] = read_file(repo, path)
        else:
            contents[path] = None
    for name, content in main.items():
        path = f"migrations/{name}"
        candidate = contents.get(path, content) if branch else read_file(repo, path) if (repo / path).exists() else None
        if candidate != content:
            raise MigrationNumberError(f"applied main migration cannot be renamed, deleted or edited: {name}")
    pending = {Path(p).name: content for p, content in contents.items()
               if p.startswith("migrations/") and p.endswith(".sql") and content is not None and Path(p).name not in main}
    for name in pending:
        if not SLOT_RE.fullmatch(name) or not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", name):
            raise MigrationNumberError(f"pending migration needs an ordinary numeric filename: {name}")
    applied = applied_names(blob(repo, base, SNAPSHOT))
    if set(pending) & applied:
        raise MigrationNumberError("applied main migration cannot be reallocated: " + ", ".join(sorted(set(pending) & applied)))
    immutable = {f"migrations/{name}": content for name, content in main.items() if content is not None}
    result = Plan(base, head, ancestor != base, {}, {}, [], {}, immutable)
    try:
        validate_integration_union(main, main | pending)
        return result
    except MigrationNumberError:
        allocated = allocate_integration_successors(main, pending)
        result.renames = {old: new for old, new in allocated.items() if old != new}
    if not result.renames:
        return result
    chain_path = "ops/config/scac-registry-chain.json"
    scac = any(GENERATED_SQL.fullmatch(f"migrations/{name}") for name in result.renames)
    if scac:
        if blob(repo, base, chain_path) is None or contents.get(chain_path) is None:
            raise MigrationNumberError("generated SCAC successor needs its branch registry chain and main predecessor")
        for path in JSON_ARTIFACTS:
            before = blob(repo, base, path)
            candidate = contents.get(path) or (blob(repo, head, path) if branch else read_file(repo, path))
            if candidate != before and not is_owned_file(path, before, candidate):
                raise MigrationNumberError(f"applied registry history cannot be rewritten: {path}")
        result.commands.append(["node", str(ROOT / "tools/renumber-scac-artifacts.mjs"), str(repo), base, json.dumps(result.renames)])
        for path in [*JSON_ARTIFACTS, "mcp-server/src/scac-mutation-registry.current.generated.js"]:
            content = contents.get(path) or (blob(repo, head, path) if branch else read_file(repo, path))
            if content is None:
                raise MigrationNumberError(f"generated registry input is missing: {path}")
            result.generated[path] = rewrite(content, blob(repo, comparison, path), result.renames, set(main))
    for path, content in contents.items():
        if content is None:
            continue
        transformed = rewrite(content, blob(repo, comparison, path), result.renames, set(main))
        output = f"migrations/{result.renames[Path(path).name]}" if path.startswith("migrations/") and Path(path).name in result.renames else path
        if scac and (path in JSON_ARTIFACTS or GENERATED_SQL.fullmatch(path) or RUNTIME.fullmatch(path)):
            if Path(path).name in pending or path in JSON_ARTIFACTS or RUNTIME.fullmatch(path):
                result.generated[output] = transformed
        elif transformed != content or output != path or path in generators:
            if path == SNAPSHOT:
                result.commands.append([sys.executable, "ops/migration-shadow.py", "--base", base, "--write"])
                result.generated[output] = transformed
            elif path in generators:
                command = generators[path]
                if command not in result.commands:
                    result.commands.append(command)
                result.generated[output] = transformed
            elif RUNTIME.fullmatch(path):
                result.commands.append(runtime_generator(path))
                result.generated[output] = transformed
            elif ".generated." in path or re.search(rb"(?i)generated by|auto-generated|do not (?:hand[- ]?)?edit", content[:2048]):
                raise MigrationNumberError(f"generated output needs its owning generator: {path}; supply --generator PATH JSON_ARGV")
            else:
                result.writes[output] = transformed
        if output != path and output not in result.writes and output not in result.generated:
            result.writes[output] = content
    for path, command in generators.items():
        if path not in contents:
            content = blob(repo, head, path) if branch else read_file(repo, path)
            if content is None:
                raise MigrationNumberError(f"declared generator output is missing: {path}")
            result.generated[path] = content
            if command not in result.commands:
                result.commands.append(command)
    if blob(repo, base, SNAPSHOT) is not None and SNAPSHOT not in result.generated:
        result.generated[SNAPSHOT] = b""
        result.commands.append([sys.executable, "ops/migration-shadow.py", "--base", base, "--write"])
    result.commands.sort(key=lambda command: len(command) > 1 and command[1] == "ops/migration-shadow.py")
    validate_integration_union(main, main | {allocated[name]: result.writes.get(f"migrations/{allocated[name]}", pending[name]) for name in pending})
    return result


def runtime_generator(path: str) -> list[str]:
    code = """import {writeIntegratedArtifact} from './ops/integration-generation.mjs';
import {renderRuntimeProjection,registryChain} from './ops/registry-chain.mjs';
import {frozenInventory} from './ops/scac-mutation-inventory.mjs';
const path=process.argv[1];
const number=path.match(/\\.v([0-9]+)\\./)?.[1];
const row=number ? registryChain.versions.find(r=>r.number===Number(number)) : registryChain.versions.at(-1);
await writeIntegratedArtifact(path,renderRuntimeProjection(frozenInventory(row.version),{version:row.version,dbCatalogBaseline:row.catalog}));
"""
    return ["node", "--input-type=module", "-e", code, path]


def apply(repo: Path, result: Plan) -> None:
    targets = set(result.writes) | set(result.generated) | {f"migrations/{old}" for old in result.renames} | set(result.immutable)
    validate_outputs(repo, targets)
    backup = {p: read_file(repo, p) if (repo / p).exists() else None for p in targets}
    try:
        for path, content in result.writes.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for old in result.renames:
            (repo / "migrations" / old).unlink()
        for path in result.generated:
            if path.startswith("migrations/") and (repo / path).exists():
                (repo / path).unlink()
        for command in result.commands:
            generated = subprocess.run(command, cwd=repo, env=scrubbed_env(), capture_output=True, timeout=1200)
            if generated.returncode:
                raise MigrationNumberError(f"generator failed ({command[0:2]}): {generated.stderr.decode(errors='replace').strip()}")
        for path, content in result.immutable.items():
            if not (repo / path).is_file() or read_file(repo, path) != content:
                raise MigrationNumberError(f"generator changed an applied main migration: {path}")
        for path, expected in result.generated.items():
            observed = read_file(repo, path)
            if path == SNAPSHOT:
                names = applied_names(observed)
                if not set(result.renames.values()) <= names or set(result.renames) & names:
                    raise MigrationNumberError("snapshot generator did not emit the allocated ledger")
            else:
                references = lambda data: set(re.findall(rb"\d{4}[a-z]?_[a-z0-9_]+\.sql", data))
                wanted = references(expected) & {n.encode() for n in result.renames.values()}
                if not wanted <= references(observed) or references(observed) & {n.encode() for n in result.renames}:
                    raise MigrationNumberError(f"generator did not emit the allocated references: {path}")
        if git(repo, "rev-parse", "origin/main").decode().strip() != result.base:
            raise MigrationNumberError("origin/main advanced during renumbering; fetch and rebase again")
    except (OSError, subprocess.SubprocessError, MigrationNumberError):
        for path, previous in backup.items():
            target = repo / path
            if previous is None:
                if target.exists():
                    target.unlink()
            else:
                target.write_bytes(previous)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--branch", help="revision to preview without checkout; requires --dry-run")
    parser.add_argument("--generator", nargs=2, action="append", default=[], metavar=("PATH", "JSON_ARGV"))
    args = parser.parse_args()
    try:
        if args.branch and not args.dry_run:
            raise MigrationNumberError("--branch requires --dry-run")
        generators = {}
        for path, argv in args.generator:
            command = json.loads(argv)
            if not isinstance(command, list) or not command or any(not isinstance(a, str) or not a for a in command):
                raise MigrationNumberError("generator must be a nonempty JSON argv array")
            generators[path] = command
        result = plan(args.repo.resolve(), args.branch, generators)
        if not args.dry_run:
            apply(args.repo.resolve(), result)
        print(json.dumps(result.summary(args.dry_run), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(schema="migration-renumber/v1", error=str(exc), renames={}), sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())

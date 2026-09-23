#!/usr/bin/env python3
"""Seal the next SCAC registry successor: one config entry, measured on a disposable PostgreSQL.

THE DELTA SEAL (Joe's ruling 4 of 8, 2026-09-23). Before this script a seal
was a hand-copied render function, two generated files, a fixture patch, an
entry-set seal and a catalog baseline that could only be learned by applying to
a disposable database and reading two failures, plus edits to the snapshot
selector, two database gates and two tests — about two hours, and serial. Now
it is:

    ops/scac-seal-successor.py --slug <slug> --label "<Label>" --reason "<why>" \\
        [--pg-bin /opt/homebrew/opt/postgresql@17/bin] [--port N] [--keep]

which appends the next entry to ops/config/scac-registry-successors.v1.json,
writes the fixture patch, renders the migration and runtime projection, and
runs the same measure-then-bind rounds a human used to run by hand:

  1. apply on a fresh database → the catalog preflight reports the observed
     secdef_execute count and digest → bind them into the config entry;
  2. re-render, apply on a fresh clone → the seed check reports the actual
     entry-set seal → bind it into scac-registry-full-entry-set-seals.json;
  3. re-render, apply on a fresh clone → must succeed; then the source-inventory
     and generated-frontier checks must pass.

Nothing here is guessed: every number written is one the database reported.
The forward-only guarantee is unchanged — still one migration per seal, still
pinned to the exact predecessor, still sealed by the entry-set digest. What
changed is that the renderer is version-parameterised and the consumers read
the config, so this script is the whole procedure.

Run it LAST in a change set: the fixture patch digests every script entrypoint,
this one included, so any later edit to a sealed file reopens the seal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib import scac_successors  # noqa: E402

GENERATOR = REPO / "ops" / "scac-mutation-inventory.mjs"
SEALS_PATH = REPO / "ops" / "config" / "scac-registry-full-entry-set-seals.json"
PLACEHOLDER = "sha256:" + "0" * 64
CATALOG_DRIFT = re.compile(r"database catalog category (\w+) drifted: count (\d+), digest (sha256:[0-9a-f]{64})")
SEAL_DRIFT = re.compile(r"seed or entry-set seal drifted: actual (sha256:[0-9a-f]{64}) expected (sha256:[0-9a-f]{64})")


def sh(*argv: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), cwd=REPO, capture_output=True, text=True, check=check,
                          env={**os.environ, **(env or {})})


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DisposablePostgres:
    """A throwaway PostgreSQL 17 cluster with the tracked snapshot loaded, cloned per attempt."""

    def __init__(self, pg_bin: Path, port: int, keep: bool):
        self.pg_bin = pg_bin
        self.port = port
        self.keep = keep
        self.root = Path(tempfile.mkdtemp(prefix="scac-seal-"))
        self.env = {**os.environ, "LC_ALL": "C"}  # pg_ctl on macOS needs a C locale
        self.clones = 0

    def __enter__(self) -> "DisposablePostgres":
        data = self.root / "data"
        subprocess.run([str(self.pg_bin / "initdb"), "-D", str(data), "-U", "carr_ci", "--auth=trust",
                        "--encoding=UTF8", "--no-locale"], check=True, capture_output=True, env=self.env)
        subprocess.run([str(self.pg_bin / "pg_ctl"), "-D", str(data), "-l", str(self.root / "pg.log"),
                        "-o", f"-h 127.0.0.1 -p {self.port}", "-w", "start"], check=True, capture_output=True, env=self.env)
        self.psql("postgres", "create database base")
        self.psql("base", "create role neondb_owner")
        subprocess.run([str(self.pg_bin / "psql"), "-h", "127.0.0.1", "-p", str(self.port), "-U", "carr_ci", "-d", "base",
                        "-v", "ON_ERROR_STOP=1", "-q", "-f", str(REPO / "db" / "schema.sql")],
                       check=True, capture_output=True, env=self.env)
        return self

    def __exit__(self, *_exc) -> None:
        subprocess.run([str(self.pg_bin / "pg_ctl"), "-D", str(self.root / "data"), "-m", "fast", "stop"],
                       capture_output=True, env=self.env)
        if not self.keep:
            shutil.rmtree(self.root, ignore_errors=True)

    def psql(self, database: str, statement: str) -> str:
        return subprocess.run([str(self.pg_bin / "psql"), "-h", "127.0.0.1", "-p", str(self.port), "-U", "carr_ci",
                               "-d", database, "-Atqc", statement], check=True, capture_output=True, text=True,
                              env=self.env).stdout.strip()

    def fresh_clone(self) -> str:
        self.clones += 1
        name = f"attempt{self.clones}"
        self.psql("postgres", f"create database {name} template base")
        return name

    def migrate(self, database: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(REPO / ".venv" / "bin" / "python"), str(REPO / "tools" / "migrate.py"), "--apply", "--yes"],
                              cwd=REPO, capture_output=True, text=True,
                              env={**self.env, "DATABASE_URL": f"postgres://carr_ci@127.0.0.1:{self.port}/{database}"})


def render(ordinal: int) -> None:
    sh("node", str(GENERATOR), "--write-successor-migration", str(ordinal))
    sh("node", str(GENERATOR), "--write-successor-runtime", str(ordinal))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--slug", required=True, help="snake_case name for the migration and SQL tags, e.g. delta_seal")
    parser.add_argument("--label", required=True, help="human label used in the migration's own error messages")
    parser.add_argument("--reason", required=True, help="the reviewed reason recorded on the fixture patch (>= 40 chars)")
    parser.add_argument("--pg-bin", default="/opt/homebrew/opt/postgresql@17/bin", type=Path)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--keep", action="store_true", help="keep the disposable cluster directory for inspection")
    parser.add_argument("--redo", action="store_true",
                        help="the newest entry is an UNMERGED seal for this same slug (a later edit reopened it, or main "
                             "moved and it must be renumbered): drop it and its artifacts, then seal again")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.slug):
        parser.error("--slug must be snake_case")
    if not (args.pg_bin / "initdb").exists():
        parser.error(f"no initdb under {args.pg_bin}")

    config = scac_successors.load_config()
    seals = json.loads(SEALS_PATH.read_text())
    if args.redo:
        stale = config["successors"].pop()
        if stale["slug"] != args.slug:
            raise SystemExit(f"--redo refuses: the newest entry is v{stale['version']} '{stale['slug']}', not '{args.slug}'")
        stale_version = scac_successors.registry_version(stale["version"])
        seals.pop(stale_version, None)
        fixture_path = REPO / "ops" / "config" / "scac-registry-source-inventory-fixtures.v1.json"
        fixture = json.loads(fixture_path.read_text())
        fixture["patches"] = [patch for patch in fixture["patches"] if patch["version"] != f"v{stale['version']}"]
        fixture_path.write_text(json.dumps(fixture, indent=2) + "\n")
        for stale_file in (REPO / stale["migration"],
                           REPO / "mcp-server" / "src" / f"scac-mutation-registry.v{stale['version']}.generated.js"):
            stale_file.unlink(missing_ok=True)
        print(f"--redo: dropped unmerged v{stale['version']} ({stale['migration']}), its patch, seal and artifacts")
    previous = config["successors"][-1]
    ordinal = previous["version"] + 1
    # The next free migration number in the tree, not the previous seal plus one:
    # a domain migration may have been numbered in between.
    number = max(int(path.name[:4]) for path in (REPO / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql")) + 1
    migration = f"migrations/{number:04d}_{args.slug}_scac_successor.sql"
    predecessor_sha = hashlib.sha256((REPO / previous["migration"]).read_bytes()).hexdigest()
    if any(entry["version"] == ordinal for entry in config["successors"]):
        raise SystemExit(f"v{ordinal} is already declared")
    entry = {
        "version": ordinal, "slug": args.slug, "label": args.label, "migration": migration,
        "predecessor_migration": previous["migration"], "predecessor_sha256": predecessor_sha,
        "catalog_baseline": {"secdef_execute": {"count": 0, "digest": PLACEHOLDER}},
        "ledger_variable": f"{args.slug.upper()}_REGISTRY_APPLIED",
    }
    config["successors"].append(entry)
    seals[scac_successors.registry_version(ordinal)] = PLACEHOLDER

    def write_config() -> None:
        scac_successors.CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
        SEALS_PATH.write_text(json.dumps(seals, indent=2) + "\n")

    write_config()
    print(f"v{ordinal}: config entry appended ({migration}), predecessor pinned at {predecessor_sha[:12]}…")
    patch = sh("node", str(GENERATOR), "--write-successor-fixture-patch", str(ordinal), args.reason).stdout.strip()
    print(f"fixture patch: {patch}")
    render(ordinal)

    with DisposablePostgres(args.pg_bin, args.port or free_port(), args.keep) as pg:
        # Round 1: the catalog preflight reports the observed secdef_execute category.
        first = pg.migrate(pg.fresh_clone())
        drift = CATALOG_DRIFT.search(first.stderr)
        if first.returncode == 0 or drift is None:
            print(first.stderr[-2000:], file=sys.stderr)
            raise SystemExit("round 1 did not report the catalog category as expected; nothing bound")
        category, count, digest = drift.group(1), int(drift.group(2)), drift.group(3)
        entry["catalog_baseline"] = {category: {"count": count, "digest": digest}}
        write_config()
        render(ordinal)
        print(f"round 1 bound: {category} count {count}, digest {digest[:19]}…")
        # Round 2: the seed check reports the actual entry-set seal.
        second = pg.migrate(pg.fresh_clone())
        seal = SEAL_DRIFT.search(second.stderr)
        if second.returncode == 0 or seal is None:
            print(second.stderr[-2000:], file=sys.stderr)
            raise SystemExit("round 2 did not report the entry-set seal as expected; catalog bound, seal not bound")
        seals[scac_successors.registry_version(ordinal)] = seal.group(1)
        write_config()
        render(ordinal)
        print(f"round 2 bound: entry-set seal {seal.group(1)[:19]}…")
        # Round 3: a fresh clone must now apply cleanly.
        third = pg.migrate(pg.fresh_clone())
        if third.returncode != 0:
            print(third.stderr[-3000:], file=sys.stderr)
            raise SystemExit("round 3 apply failed after binding; inspect the cluster with --keep")
        row = pg.psql(f"attempt{pg.clones}", "select registry_version, entry_count, source_entry_count from ops.scac_mutation_registry_version "
                      f"where registry_version='{scac_successors.registry_version(ordinal)}'")
        print(f"round 3 applied cleanly: {row}")

    sh("node", str(GENERATOR), "--check-source-inventory-frontier")
    sh("node", str(GENERATOR), "--check-generated-frontier")
    frontier = scac_successors.frontier()
    print(f"sealed v{ordinal}: live {frontier['live_version']}, predecessor {frontier['predecessor_version']} "
          f"digest {frontier['predecessor_digest'][:19]}… counts {frontier['predecessor_entry_counts']}")
    print("next: ./run.sh local-db-ci --class migration --port <free>, then commit the config, seals, fixture patch, migration and runtime together")
    return 0


if __name__ == "__main__":
    sys.exit(main())

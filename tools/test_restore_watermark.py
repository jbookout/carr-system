"""Tests for tools/restore-watermark.py and bin/restore-rehearse.sh phase 5 (V5-F08 item 4).

Two layers:
  * unit — the COPY-block counter, the exact comparison, the receipt builder and
    the CLI exit codes, on fixture text;
  * end to end — a real pg_dump of a throwaway local cluster, restored with the
    rehearsal's OWN restore filter into a second throwaway database, counted with
    the rehearsal's OWN phase-5 SQL (both read out of bin/restore-rehearse.sh,
    so the script text is what is tested), compared, receipted, and evaluated by
    mcp-server/bin/recovery-matrix-evaluate.mjs. Then one restored row is deleted and
    the same path must fail. Skips (exit status unchanged, reason printed) only
    when the PostgreSQL client/server binaries are not installed.

The encrypt/decrypt step is not exercised here: it is the rehearsal's existing,
unchanged `age --decrypt` pipe, and the digest is taken over whatever bytes are
stored, so a plain file stands in for the ciphertext.

  .venv/bin/python -m unittest tools/test_restore_watermark.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "restore-watermark.py"
REHEARSE = REPO / "bin" / "restore-rehearse.sh"
EVALUATOR = REPO / "mcp-server" / "bin" / "recovery-matrix-evaluate.mjs"

spec = importlib.util.spec_from_file_location("restore_watermark", TOOL)
rw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rw)

DUMP = '''--
-- PostgreSQL database dump
--
CREATE TABLE public.party (id integer, name text);
COPY public.party (id, name) FROM stdin;
1\tAcme
2\tline\\nbreak
3\tback\\\\.slash
\\.
COPY public."Odd ""Name""" (id) FROM stdin;
7
\\.
COPY public.empty_t (id) FROM stdin;
\\.
COPY ops.run (id) FROM stdin;
1
2
\\.
-- PostgreSQL database dump complete
'''

COPY = {
    "copy_id": "fixture-copy-1",
    "custody_domain": "fixture-independent-store",
    "primary_domain": "fixture-primary",
    "produced_at": "2026-09-24T03:00:00Z",
    "producer_id": "fixture-producer",
    "recorded_artifact_digest": "sha256:" + "a" * 64,
}


def run_tool(*args, stdin=None):
    return subprocess.run([sys.executable, str(TOOL), *args], input=stdin, text=True, capture_output=True)


class CountAndCompare(unittest.TestCase):
    def test_counts_rows_per_qualified_table_including_quoted_and_empty(self):
        counts = rw.count_copy_rows(DUMP.splitlines(keepends=True))
        self.assertEqual(counts, {"public.party": 3, 'public.Odd "Name"': 1, "public.empty_t": 0, "ops.run": 2})

    def test_escaped_terminator_inside_a_row_is_a_row(self):
        # "3\tback\\\\.slash" is a data row whose text contains \. — never the terminator.
        self.assertEqual(rw.count_copy_rows(DUMP.splitlines())["public.party"], 3)

    def test_truncated_stream_raises(self):
        truncated = DUMP.split("\\.\nCOPY ops.run")[0].rsplit("\\.", 1)[0]
        with self.assertRaises(ValueError):
            rw.count_copy_rows(truncated.splitlines())

    def test_stream_without_copy_blocks_raises(self):
        with self.assertRaises(ValueError):
            rw.count_copy_rows(["-- nothing here\n"])

    def test_compare_is_exact_both_ways(self):
        a = {"public.party": 3, "ops.run": 2}
        self.assertEqual(rw.compare(a, dict(a)), [])
        self.assertEqual(rw.compare(a, {"public.party": 3, "ops.run": 1}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": 1}])
        self.assertEqual(rw.compare(a, {"public.party": 3}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": None}])
        self.assertEqual(rw.compare(a, {**a, "public.extra": 0}),
                         [{"table": "public.extra", "artifact_rows": None, "restored_rows": 0}])

    def test_restored_counts_parse_rejects_garbage(self):
        self.assertEqual(rw.parse_restored_counts("public.party|3\nops.run|2\n"), {"public.party": 3, "ops.run": 2})
        for bad in ("public.party 3", "public.party|x", "a|1\na|2"):
            with self.assertRaises(ValueError):
                rw.parse_restored_counts(bad)

    def test_receipt_refuses_production_and_unknown_copy_fields(self):
        kw = dict(oracle_id="o", observed_digest=COPY["recorded_artifact_digest"], artifact={"public.t": 1},
                  restored={"public.t": 1}, started_at="2026-09-24T10:00:00Z", finished_at="2026-09-24T10:01:00Z")
        self.assertEqual(rw.build_receipt(copy=COPY, target_kind="disposable_branch", **kw)["receipt_kind"],
                         "restore-exercise-receipt.v1")
        with self.assertRaises(ValueError):
            rw.build_receipt(copy=COPY, target_kind="production", **kw)
        with self.assertRaises(ValueError):
            rw.build_receipt(copy={**COPY, "trusted": True}, target_kind="disposable_branch", **kw)


class Cli(unittest.TestCase):
    def test_count_digest_compare_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            out = run_tool("count", stdin=DUMP)
            self.assertEqual(out.returncode, 0, out.stderr)
            (t / "a.json").write_text(out.stdout)
            (t / "r.txt").write_text('ops.run|2\npublic.Odd "Name"|1\npublic.empty_t|0\npublic.party|3\n')
            self.assertEqual(run_tool("compare", "--artifact", str(t / "a.json"), "--restored", str(t / "r.txt")).returncode, 0)
            (t / "r.txt").write_text('ops.run|2\npublic.Odd "Name"|1\npublic.empty_t|0\npublic.party|2\n')
            bad = run_tool("compare", "--artifact", str(t / "a.json"), "--restored", str(t / "r.txt"))
            self.assertEqual(bad.returncode, 1)
            self.assertIn("MISMATCH public.party: artifact=3 restored=2", bad.stdout)
            (t / "f").write_bytes(b"abc")
            self.assertEqual(run_tool("digest", str(t / "f")).stdout.strip(),
                             "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
            self.assertEqual(run_tool("count", stdin="COPY public.t (id) FROM stdin;\n1\n").returncode, 2)


def _pg_bins():
    """One directory holding a real server beside its client tools (libpq alone ships no server)."""
    names = ("initdb", "pg_ctl", "pg_dump", "psql", "postgres")
    dirs = [Path(p).parent for p in filter(None, [shutil.which("postgres")])]
    dirs += [Path(d) for d in ("/opt/homebrew/opt/postgresql@17/bin", "/usr/local/opt/postgresql@17/bin",
                               "/usr/lib/postgresql/17/bin", "/usr/lib/postgresql/16/bin")]
    for d in dirs:
        cand = {n: str(d / n) for n in names}
        if all(Path(p).exists() for p in cand.values()):
            return cand
    return None


def _script_block(pattern: str) -> str:
    text = REHEARSE.read_text()
    m = re.search(pattern, text, re.S)
    if not m:
        raise AssertionError(f"could not find {pattern!r} in bin/restore-rehearse.sh")
    return m.group(1)


@unittest.skipUnless(_pg_bins() and shutil.which("node"), "PostgreSQL binaries or node not installed")
class EndToEndDisposableCluster(unittest.TestCase):
    """A real dump, a real restore into a disposable database, the rehearsal's own SQL."""

    @classmethod
    def setUpClass(cls):
        cls.bins = _pg_bins()
        cls.tmp = Path(tempfile.mkdtemp(prefix="carr-f08-e2e-"))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = str(s.getsockname()[1])
        data = cls.tmp / "data"
        # The postmaster refuses to start without a valid locale in its environment on macOS.
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        subprocess.run([cls.bins["initdb"], "-D", str(data), "-U", "carr_ci", "--auth=trust", "-E", "UTF8",
                        "--locale=C"], check=True, capture_output=True, env=env)
        subprocess.run([cls.bins["pg_ctl"], "-D", str(data), "-l", str(cls.tmp / "log"), "-w", "-o",
                        f"-p {cls.port} -k {cls.tmp} -c listen_addresses=''", "start"],
                       check=True, capture_output=True, env=env)
        cls.data = data

    @classmethod
    def tearDownClass(cls):
        subprocess.run([cls.bins["pg_ctl"], "-D", str(cls.data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def dsn(self, db):
        return f"host={self.tmp} port={self.port} user=carr_ci dbname={db}"

    def psql(self, db, sql, *extra):
        return subprocess.run([self.bins["psql"], self.dsn(db), "-v", "ON_ERROR_STOP=1", "-At", *extra, "-c", sql],
                              check=True, capture_output=True, text=True).stdout

    def test_exact_restore_passes_and_one_lost_row_fails(self):
        self.psql("postgres", "create database src")
        self.psql("postgres", "create database restored")
        self.psql("src", '''
          create schema ops;
          create table public.party (id int primary key, name text);
          insert into public.party select g, 'p' || g from generate_series(1, 250) g;
          insert into public.party values (1001, E'multi\\nline'), (1002, E'back\\\\.slash');
          create table public."Odd ""Name""" (id int);
          insert into public."Odd ""Name""" values (1), (2);
          create table public.empty_t (id int);
          create table public.ev (id int, at date) partition by range (at);
          create table public.ev_2025 partition of public.ev for values from ('2025-01-01') to ('2026-01-01');
          create table public.ev_2026 partition of public.ev for values from ('2026-01-01') to ('2027-01-01');
          insert into public.ev values (1, '2025-05-01'), (2, '2026-05-01'), (3, '2026-06-01');
          create table ops.run (id int references public.party(id));
          insert into ops.run select g from generate_series(1, 40) g;
        ''')
        dump = self.tmp / "carr-fixture.sql"
        subprocess.run([self.bins["pg_dump"], "--no-owner", "--no-acl", "--schema=public", "--schema=ops",
                        "-f", str(dump), self.dsn("src")], check=True, capture_output=True)

        digest = run_tool("digest", str(dump)).stdout.strip()
        counted = run_tool("count", stdin=dump.read_text())
        self.assertEqual(counted.returncode, 0, counted.stderr)
        artifact = self.tmp / "artifact.json"
        artifact.write_text(counted.stdout)
        self.assertEqual(json.loads(counted.stdout), {
            'public.Odd "Name"': 2, "public.empty_t": 0, "public.ev_2025": 1, "public.ev_2026": 2,
            "public.party": 252, "ops.run": 40})

        restore_filter = _script_block(r"RESTORE_FILTER='([^']*)'")
        filtered = subprocess.run(["sed", "-E", restore_filter], input=dump.read_text(), text=True,
                                  capture_output=True, check=True).stdout
        subprocess.run([self.bins["psql"], self.dsn("restored"), "-v", "ON_ERROR_STOP=1", "-q"], input=filtered,
                       text=True, capture_output=True, check=True)

        wm_sql = _script_block(r'WM_COUNT_SQL="([^"]*)"')
        restored = self.tmp / "restored.txt"
        restored.write_text(self.psql("restored", wm_sql))
        cmp_ok = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertEqual(cmp_ok.returncode, 0, cmp_ok.stdout + cmp_ok.stderr)

        copy_json = self.tmp / "copy.json"
        copy_json.write_text(json.dumps({**COPY, "recorded_artifact_digest": digest}))
        receipt_args = ["receipt", "--copy-json", str(copy_json), "--target-kind", "disposable_local_cluster",
                        "--oracle-id", "restore-rehearse", "--observed-digest", digest,
                        "--artifact", str(artifact), "--restored", str(restored),
                        "--started-at", "2026-09-24T10:00:00Z", "--finished-at", "2026-09-24T10:02:00Z"]
        receipt = self.tmp / "receipt.json"
        built = run_tool(*receipt_args)
        self.assertEqual(built.returncode, 0, built.stderr)
        receipt.write_text(built.stdout)
        verdict = subprocess.run(["node", str(EVALUATOR), "restore", str(receipt)], capture_output=True, text=True)
        self.assertEqual(verdict.returncode, 0, verdict.stdout + verdict.stderr)
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "restore_exercise_exact")

        # One row lost in the restore: the comparison and the evaluator must both refuse.
        self.psql("restored", "delete from ops.run where id = 40")
        restored.write_text(self.psql("restored", wm_sql))
        cmp_bad = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertEqual(cmp_bad.returncode, 1)
        self.assertIn("MISMATCH ops.run: artifact=40 restored=39", cmp_bad.stdout)
        receipt.write_text(run_tool(*receipt_args).stdout)
        verdict = subprocess.run(["node", str(EVALUATOR), "restore", str(receipt)], capture_output=True, text=True)
        self.assertEqual(verdict.returncode, 1)
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "watermark_mismatch")

        # A copy whose stored bytes differ from what the producer recorded fails on hash.
        copy_json.write_text(json.dumps({**COPY, "recorded_artifact_digest": "sha256:" + "0" * 64}))
        self.psql("restored", "insert into ops.run values (40)")
        restored.write_text(self.psql("restored", wm_sql))
        receipt.write_text(run_tool(*receipt_args).stdout)
        verdict = subprocess.run(["node", str(EVALUATOR), "restore", str(receipt)], capture_output=True, text=True)
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "artifact_hash_mismatch")


if __name__ == "__main__":
    unittest.main()

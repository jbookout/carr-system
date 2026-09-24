"""Tests for tools/restore-watermark.py and bin/restore-rehearse.sh phase 5 (V5-F08 item 4).

Three layers:
  * unit — the COPY-block watermark (rows AND content digest), the exact
    comparison, the receipt builder and the CLI exit codes, on fixture text;
  * the copy record — fetch-copy / verify-receipt read the producer's
    "Backup artifact" Check and the artifact API through the REAL
    ops/backup-workflow-status.py matching and cross-check code, with only its
    `api` transport and the `gh` download replaced by fixtures. A run not on
    main, not scheduled or dispatched, or whose commit is not in main; a Check
    in a suite not bound to the run or stamped outside its window; a summary
    that disagrees with the store; or a receipt edited after the fact are all
    refused. The outbound census is read from a restored queue, not handed in;
  * end to end — a real pg_dump of a throwaway local cluster, restored with the
    rehearsal's OWN restore filter (read out of bin/restore-rehearse.sh, so the
    script text is what is tested) into a second throwaway database, read back
    with `restored` over a DSN passed in the environment, compared, receipted,
    and evaluated by mcp-server/bin/recovery-matrix-evaluate.mjs. Then one
    restored row is deleted, and separately one restored row is CHANGED with the
    count intact, and both must fail. Skips (reason printed) only when the
    PostgreSQL client/server binaries are not installed.

The encrypt/decrypt step is not exercised here: it is the rehearsal's existing,
unchanged `age --decrypt` pipe, and the digest is taken over whatever bytes are
stored, so a plain file stands in for the ciphertext.

  .venv/bin/python -m unittest tools/test_restore_watermark.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib import recovery_evidence  # noqa: E402
TOOL = REPO / "tools" / "restore-watermark.py"
REHEARSE = REPO / "bin" / "restore-rehearse.sh"
EVALUATOR = REPO / "mcp-server" / "bin" / "recovery-matrix-evaluate.mjs"

spec = importlib.util.spec_from_file_location("restore_watermark", TOOL)
if spec is None or spec.loader is None:
    raise ImportError(f"cannot load {TOOL}")
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


def sha(lines: list[str]) -> str:
    h = hashlib.sha256()
    for line in sorted(x.encode() for x in lines):
        h.update(line + b"\n")
    return "sha256:" + h.hexdigest()


def D(ch: str) -> str:
    return "sha256:" + ch * 64


COPY_RECORD = {
    "copy_id": "carr-backup-20260924-run-101-attempt-1",
    "custody_domain": "github-actions-artifacts",
    "primary_domain": "neon-primary",
    "produced_at": "2026-09-24T03:00:00Z",
    "producer_id": "backup-nightly-workflow",
    "recorded_artifact_digest": D("a"),
    "recorded_digest_source": {"kind": "github_actions_backup_check", "check_run_id": 555, "workflow_run_id": 101},
    "store_readback_digest": D("a"),
}


def run_tool(*args, stdin=None, env=None):
    return subprocess.run([sys.executable, str(TOOL), *args], input=stdin, text=True, capture_output=True,
                          env=env)


class Watermark(unittest.TestCase):
    def test_rows_and_content_digest_per_qualified_table_including_quoted_and_empty(self):
        wm = rw.watermark_from_dump(DUMP.splitlines(keepends=True))
        self.assertEqual({t: e["rows"] for t, e in wm.items()},
                         {"public.party": 3, 'public.Odd "Name"': 1, "public.empty_t": 0, "ops.run": 2})
        self.assertEqual(wm["ops.run"]["content_digest"], sha(["1", "2"]))
        self.assertEqual(wm["public.empty_t"]["content_digest"], sha([]))
        self.assertEqual(wm["public.party"]["copy_columns"], "(id, name)")
        self.assertEqual(rw.count_copy_rows(DUMP.splitlines())["public.party"], 3)

    def test_content_digest_is_order_free_but_value_exact(self):
        swapped = DUMP.replace("1\n2\n\\.\n-- PostgreSQL", "2\n1\n\\.\n-- PostgreSQL")
        changed = DUMP.replace("1\tAcme", "1\tAcmf")
        base = rw.watermark_from_dump(DUMP.splitlines())
        self.assertEqual(rw.watermark_from_dump(swapped.splitlines()), base)
        moved = rw.watermark_from_dump(changed.splitlines())["public.party"]
        self.assertEqual(moved["rows"], 3)
        self.assertNotEqual(moved["content_digest"], base["public.party"]["content_digest"])

    def test_escaped_terminator_inside_a_row_is_a_row_and_only_the_exact_line_ends_a_block(self):
        # "3\tback\\\\.slash" is a data row whose text contains \. — never the terminator.
        self.assertEqual(rw.count_copy_rows(DUMP.splitlines())["public.party"], 3)
        # A row that merely STARTS with backslash-dot is data, not the end of the block.
        tricky = "COPY public.t (v) FROM stdin;\n\\.x\n\\.\n"
        self.assertEqual(rw.count_copy_rows(tricky.splitlines()), {"public.t": 1})

    def test_two_copy_blocks_for_one_table_raise(self):
        twice = DUMP + "COPY ops.run (id) FROM stdin;\n3\n\\.\n"
        with self.assertRaisesRegex(ValueError, "two COPY blocks"):
            rw.count_copy_rows(twice.splitlines())

    def test_truncated_stream_and_stream_without_blocks_raise(self):
        truncated = DUMP.split("\\.\nCOPY ops.run")[0].rsplit("\\.", 1)[0]
        with self.assertRaisesRegex(ValueError, "truncated"):
            rw.count_copy_rows(truncated.splitlines())
        with self.assertRaisesRegex(ValueError, "no COPY blocks"):
            rw.count_copy_rows(["-- nothing here\n"])

    def test_quoted_identifier_with_doubled_quotes_unquotes_exactly(self):
        self.assertIn('public.Odd "Name"', rw.count_copy_rows(DUMP.splitlines()))

    def test_compare_is_exact_on_rows_and_content_both_ways(self):
        a = {"public.party": {"rows": 3, "content_digest": D("1")}, "ops.run": {"rows": 2, "content_digest": D("2")}}
        self.assertEqual(rw.compare(a, json.loads(json.dumps(a))), [])
        self.assertEqual(rw.compare(a, {**a, "ops.run": {"rows": 1, "content_digest": D("2")}}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": 1, "content_differs": False}])
        self.assertEqual(rw.compare(a, {**a, "ops.run": {"rows": 2, "content_digest": D("9")}}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": 2, "content_differs": True}])
        self.assertEqual(rw.compare(a, {"public.party": a["public.party"]}),
                         [{"table": "ops.run", "artifact_rows": 2, "restored_rows": None, "content_differs": True}])
        self.assertEqual(rw.compare(a, {**a, "public.extra": {"rows": 0, "content_digest": D("0")}}),
                         [{"table": "public.extra", "artifact_rows": None, "restored_rows": 0, "content_differs": True}])

    def test_receipt_refuses_production_and_unknown_or_missing_copy_fields_and_drops_column_lists(self):
        wm = {"public.t": {"rows": 1, "content_digest": D("1"), "copy_columns": "(id)"}}
        kw = dict(oracle_id="o", observed_digest=D("a"), artifact=wm, restored={"public.t": {"rows": 1, "content_digest": D("1")}},
                  started_at="2026-09-24T10:00:00Z", finished_at="2026-09-24T10:01:00Z")
        built = rw.build_receipt(copy=COPY_RECORD, target_kind="disposable_branch", **kw)
        self.assertEqual(built["receipt_kind"], "restore-exercise-receipt.v1")
        self.assertEqual(built["artifact_watermark"], {"public.t": {"rows": 1, "content_digest": D("1")}})
        with self.assertRaises(ValueError):
            rw.build_receipt(copy=COPY_RECORD, target_kind="production", **kw)
        with self.assertRaises(ValueError):
            rw.build_receipt(copy={**COPY_RECORD, "trusted": True}, target_kind="disposable_branch", **kw)
        partial = dict(COPY_RECORD)
        del partial["store_readback_digest"]
        with self.assertRaises(ValueError):
            rw.build_receipt(copy=partial, target_kind="disposable_branch", **kw)


class Cli(unittest.TestCase):
    def test_count_digest_compare_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            out = run_tool("count", stdin=DUMP)
            self.assertEqual(out.returncode, 0, out.stderr)
            (t / "a.json").write_text(out.stdout)
            restored = {k: {"rows": v["rows"], "content_digest": v["content_digest"]} for k, v in json.loads(out.stdout).items()}
            (t / "r.json").write_text(json.dumps(restored))
            self.assertEqual(run_tool("compare", "--artifact", str(t / "a.json"), "--restored", str(t / "r.json")).returncode, 0)
            restored["public.party"]["content_digest"] = D("f")
            (t / "r.json").write_text(json.dumps(restored))
            bad = run_tool("compare", "--artifact", str(t / "a.json"), "--restored", str(t / "r.json"))
            self.assertEqual(bad.returncode, 1)
            self.assertIn("MISMATCH public.party: artifact=3 restored=3 content_differs=true", bad.stdout)
            (t / "f").write_bytes(b"abc")
            self.assertEqual(run_tool("digest", str(t / "f")).stdout.strip(),
                             "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
            self.assertEqual(run_tool("count", stdin="COPY public.t (id) FROM stdin;\n1\n").returncode, 2)

    def test_restored_reads_its_dsn_from_the_environment_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.json").write_text("{}")
            env = {k: v for k, v in os.environ.items() if k != "RESTORE_DSN"}
            out = run_tool("restored", "--artifact", str(Path(tmp) / "a.json"), env=env)
            self.assertEqual(out.returncode, 2)
            self.assertIn("RESTORE_DSN is empty", out.stderr)
            # there is no argument that takes a DSN
            self.assertEqual(run_tool("restored", "--artifact", "x", "--dsn", "postgresql://h/d").returncode, 2)


class RestoredReader(unittest.TestCase):
    """`restored` against a stand-in driver: chunk boundaries, session settings, column lists."""

    def fake_psycopg(self, chunks_by_table, executed):
        class Copy:
            def __init__(self, chunks):
                self.chunks = chunks

            def __enter__(self):
                return iter(self.chunks)

            def __exit__(self, *exc):
                return False

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                executed.append((sql, params))

            def fetchall(self):
                return [tuple(k.split(".")) for k in chunks_by_table]

            def copy(self, statement):
                executed.append((statement, None))
                table = next(k for k in chunks_by_table if f'"{k.split(".")[1]}"' in statement)
                return Copy(chunks_by_table[table])

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

        return mock.Mock(connect=lambda dsn, autocommit: Conn())

    def test_rows_split_across_chunks_are_reassembled_and_hashed_like_the_artifact(self):
        executed: list = []
        driver = self.fake_psycopg({"public.party": [b"1\tAc", b"me\n2\tline\\nbreak\n", b"3\tback\\\\.slash\n"]}, executed)
        artifact = rw.watermark_from_dump(DUMP.splitlines())
        with mock.patch.dict(sys.modules, {"psycopg": driver}):
            got = rw.restored_watermark("dsn", artifact)
        self.assertEqual(got["public.party"], {k: artifact["public.party"][k] for k in ("rows", "content_digest")})
        self.assertIn(("select set_config(%s, %s, false)", ("default_transaction_read_only", "on")), executed)
        self.assertIn(("select set_config(%s, %s, false)", ("TimeZone", "UTC")), executed)
        self.assertIn(('COPY "public"."party" (id, name) TO STDOUT', None), executed)

    def test_output_that_does_not_end_in_a_newline_is_refused(self):
        driver = self.fake_psycopg({"public.party": [b"1\tAcme\n2\tcut"]}, [])
        with mock.patch.dict(sys.modules, {"psycopg": driver}):
            with self.assertRaisesRegex(ValueError, "did not end with a newline"):
                rw.restored_watermark("dsn", {})


# ── the copy record, read from the provider ──────────────────────────────────

RUN_ID, ATTEMPT, HEAD, SUITE, OTHER_SUITE = 101, 1, "a" * 40, 9001, 9002
REPO_SLUG = "jbookout/carr-system"


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("carr-20260924.sql.age", b"age-encrypted-bytes")
    return buf.getvalue()


ZIP = _zip_bytes()
ZIP_DIGEST = "sha256:" + hashlib.sha256(ZIP).hexdigest()


class FakeProvider:
    """The GitHub API as ops/backup-workflow-status.py sees it, for one nightly run."""

    def __init__(self):
        self.artifact = {
            "id": 77, "name": f"carr-backup-20260924-run-{RUN_ID}-attempt-{ATTEMPT}", "digest": ZIP_DIGEST,
            "size_in_bytes": len(ZIP), "created_at": "2026-09-24T03:05:00Z", "expires_at": "2099-01-01T00:00:00Z",
            "expired": False, "workflow_run": {"id": RUN_ID, "run_attempt": ATTEMPT, "head_sha": HEAD},
        }
        self.summary = {
            "artifact_id": 77, "artifact_name": self.artifact["name"], "artifact_digest": ZIP_DIGEST,
            "artifact_bytes": len(ZIP), "artifact_created_at": "2026-09-24T03:05:00Z",
            "artifact_expires_at": "2099-01-01T00:00:00Z", "required_steps": ["dump", "encrypt", "upload", "readback"],
        }
        self.check = {
            "id": 555, "name": "Backup artifact", "head_sha": HEAD, "status": "completed", "conclusion": "success",
            "external_id": json.dumps({"head_sha": HEAD, "repository": REPO_SLUG, "run_attempt": ATTEMPT, "run_id": RUN_ID},
                                      sort_keys=True, separators=(",", ":")),
            "app": {"id": 15368, "slug": "github-actions"}, "check_suite": {"id": SUITE},
            "started_at": "2026-09-24T03:01:00Z", "completed_at": "2026-09-24T03:06:00Z",
            "output": {"summary": None},
        }
        self.run = {"id": RUN_ID, "run_attempt": ATTEMPT, "head_sha": HEAD, "conclusion": "success",
                    "path": ".github/workflows/backup-nightly.yml", "check_suite_id": SUITE,
                    "head_branch": "main", "event": "schedule",
                    "run_started_at": "2026-09-24T03:00:30Z", "updated_at": "2026-09-24T03:07:00Z"}
        # As observed live: the Check lands in ANOTHER github-actions suite on the same head.
        self.suites = {OTHER_SUITE: {"id": OTHER_SUITE, "app": {"id": 15368, "slug": "github-actions"},
                                     "head_branch": "main", "head_sha": HEAD}}
        self.compare = {"status": "ahead", "ahead_by": 3, "behind_by": 0}
        self.extra_checks: list[dict] = []

    def api(self, path, *, method="GET", body=None, query=None):
        self.check["output"]["summary"] = json.dumps(self.summary)
        if path == f"/repos/{REPO_SLUG}/actions/runs/{RUN_ID}":
            return self.run
        if path == f"/repos/{REPO_SLUG}/commits/{HEAD}/check-runs":
            rows = [self.check, *self.extra_checks]
            return {"check_runs": rows, "total_count": len(rows)}
        if path == f"/repos/{REPO_SLUG}/actions/runs/{RUN_ID}/artifacts":
            return {"artifacts": [self.artifact], "total_count": 1}
        if path == f"/repos/{REPO_SLUG}/compare/{HEAD}...main":
            return self.compare
        if path.startswith(f"/repos/{REPO_SLUG}/check-suites/"):
            # an unknown suite answers with something that is not a suite object
            return self.suites.get(int(path.rsplit("/", 1)[1]), [])
        raise AssertionError(f"unexpected API path {path}")


class CopyRecordFromProvider(unittest.TestCase):
    def setUp(self):
        self.fake = FakeProvider()
        real = rw._status_module()
        real.api = self.fake.api
        patcher = mock.patch.object(rw, "_status_module", return_value=real)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_record_is_the_producers_check_cross_checked_against_the_store(self):
        rec = rw.read_copy_record(REPO_SLUG, RUN_ID)
        self.assertEqual(rec["recorded_artifact_digest"], ZIP_DIGEST)
        self.assertEqual(rec["store_readback_digest"], ZIP_DIGEST)
        self.assertEqual(rec["produced_at"], "2026-09-24T03:05:00Z")
        self.assertEqual(rec["recorded_digest_source"],
                         {"kind": "github_actions_backup_check", "check_run_id": 555, "workflow_run_id": RUN_ID})

    def test_g1_a_check_in_another_actions_suite_on_this_main_head_inside_the_run_window_is_accepted(self):
        # The live shape: GITHUB_TOKEN Checks do not land in their own run's suite.
        self.fake.check["check_suite"] = {"id": OTHER_SUITE}
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["recorded_digest_source"]["check_run_id"], 555)

    def test_g1_a_check_in_a_suite_not_bound_to_this_run_fails_closed(self):
        self.fake.check["check_suite"] = {"id": OTHER_SUITE}
        cases = [
            lambda s: s.update(app={"id": 1, "slug": "someone-else"}),
            lambda s: s.update(head_branch="feature"),
            lambda s: s.update(head_sha="b" * 40),
        ]
        for mutate in cases:
            original = json.loads(json.dumps(self.fake.suites[OTHER_SUITE]))
            mutate(self.fake.suites[OTHER_SUITE])
            with self.assertRaisesRegex(ValueError, "found 0 .*refusing"):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
            self.fake.suites[OTHER_SUITE] = original
        self.fake.check["check_suite"] = {"id": 424242}  # a suite the provider cannot read back
        with self.assertRaisesRegex(ValueError, "found 0"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_g1_a_check_stamped_outside_the_runs_own_window_is_not_the_runs(self):
        for field, value in (("started_at", "2026-09-24T03:00:29Z"), ("completed_at", "2026-09-24T03:07:01Z"),
                             ("completed_at", None)):
            original = self.fake.check[field]
            self.fake.check[field] = value
            with self.assertRaisesRegex(ValueError, "found 0"):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
            self.fake.check[field] = original
        # exactly at the run's own edges is inside
        self.fake.check.update(started_at="2026-09-24T03:00:30Z", completed_at="2026-09-24T03:07:00Z")
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["recorded_digest_source"]["check_run_id"], 555)

    def test_g1_only_a_scheduled_or_dispatched_run_on_main_whose_commit_is_in_main_counts(self):
        for field, bad, message in (("head_branch", "pr-branch", "not main"),
                                    ("event", "pull_request", "not schedule or workflow_dispatch"),
                                    ("event", "push", "not schedule or workflow_dispatch")):
            original = self.fake.run[field]
            self.fake.run[field] = bad
            with self.assertRaisesRegex(ValueError, message):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
            self.fake.run[field] = original
        self.fake.run["event"] = "workflow_dispatch"
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["copy_id"], self.fake.artifact["name"])
        for compare in ({"status": "diverged", "ahead_by": 2, "behind_by": 1},
                        {"status": "diverged", "ahead_by": 2, "behind_by": 0},  # status alone decides too
                        {"status": "behind", "ahead_by": 0, "behind_by": 4},
                        {"status": "ahead", "ahead_by": 1, "behind_by": 1}, {}, []):
            self.fake.compare = compare
            with self.assertRaisesRegex(ValueError, "not an ancestor of main"):
                rw.read_copy_record(REPO_SLUG, RUN_ID)
        self.fake.compare = {"status": "identical", "ahead_by": 0, "behind_by": 0}
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["copy_id"], self.fake.artifact["name"])

    def test_a_forged_second_check_with_the_same_name_and_envelope_does_not_count(self):
        forged = json.loads(json.dumps(self.fake.check))
        forged.update(id=556, app={"id": 1, "slug": "someone-else"})
        self.fake.extra_checks.append(forged)
        self.assertEqual(rw.read_copy_record(REPO_SLUG, RUN_ID)["recorded_digest_source"]["check_run_id"], 555)

    def test_two_authentic_checks_for_one_run_are_ambiguous_and_refused(self):
        twin = json.loads(json.dumps(self.fake.check))
        twin["id"] = 557
        self.fake.extra_checks.append(twin)
        with self.assertRaisesRegex(ValueError, "found 2"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_fetch_copy_refuses_an_archive_that_is_not_exactly_one_dump(self):
        for members in ({"carr-20260924.sql.age": b"x", "extra.txt": b"y"}, {"notes.txt": b"x"}):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for name, data in members.items():
                    zf.writestr(name, data)
            blob = buf.getvalue()
            self.fake.artifact.update(digest="sha256:" + hashlib.sha256(blob).hexdigest(), size_in_bytes=len(blob))
            self.fake.summary.update(artifact_digest=self.fake.artifact["digest"], artifact_bytes=len(blob))
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp)
                (t / "artifact.bin").write_bytes(blob)
                gh = t / "bin" / "gh"
                gh.parent.mkdir()
                gh.write_text(f"#!/bin/sh\ncat '{t / 'artifact.bin'}'\n")
                gh.chmod(0o755)
                with mock.patch.dict(os.environ, {"PATH": f"{gh.parent}:{os.environ['PATH']}"}):
                    with self.assertRaisesRegex(ValueError, "exactly one carr-YYYYMMDD.sql.age"):
                        rw.fetch_copy(REPO_SLUG, RUN_ID, t / "out")

    def test_a_summary_that_disagrees_with_the_store_is_refused(self):
        self.fake.artifact["digest"] = D("b")
        with self.assertRaisesRegex(ValueError, "does not match the artifact"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_a_run_of_another_workflow_or_a_failed_run_is_refused(self):
        self.fake.run["path"] = ".github/workflows/ci.yml"
        with self.assertRaisesRegex(ValueError, "not a run of"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)
        self.fake.run.update(path=".github/workflows/backup-nightly.yml", conclusion="failure")
        with self.assertRaisesRegex(ValueError, "did not conclude success"):
            rw.read_copy_record(REPO_SLUG, RUN_ID)

    def test_fetch_copy_downloads_extracts_and_writes_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "artifact.bin").write_bytes(ZIP)
            gh = t / "bin" / "gh"
            gh.parent.mkdir()
            gh.write_text(f"#!/bin/sh\ncat '{t / 'artifact.bin'}'\n")
            gh.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": f"{gh.parent}:{os.environ['PATH']}"}):
                archive, dump = rw.fetch_copy(REPO_SLUG, RUN_ID, t / "out")
            self.assertEqual(rw.file_digest(archive), ZIP_DIGEST)
            self.assertEqual(dump.read_bytes(), b"age-encrypted-bytes")
            record = json.loads((t / "out" / "copy.json").read_text())
            self.assertEqual(sorted(record), sorted(rw.COPY_KEYS))

    def test_verify_receipt_rereads_the_provider_and_names_every_edited_field(self):
        rec = rw.read_copy_record(REPO_SLUG, RUN_ID)
        rec.pop("_artifact_id")
        receipt = {"copy": rec, "oracle_id": "restore-rehearse"}
        now = datetime(2026, 9, 24, 10, 30, tzinfo=timezone.utc)
        differs, bound = rw.verify_receipt(receipt, REPO_SLUG, now)
        self.assertEqual(differs, [])
        # G4: the output is the receipt with the RE-READ copy block, bound.
        self.assertEqual(bound["verification"], {
            "verifier": "tools/restore-watermark.py verify-receipt", "verified_at": "2026-09-24T10:30:00Z",
            "facts_digest": recovery_evidence.facts_digest(receipt)})
        edited = json.loads(json.dumps(receipt))
        edited["copy"]["recorded_artifact_digest"] = D("c")
        edited["copy"]["produced_at"] = "2026-09-24T09:00:00Z"
        self.assertEqual(rw.verify_receipt(edited, REPO_SLUG), (["produced_at", "recorded_artifact_digest"], None))
        typed_in = json.loads(json.dumps(receipt))
        typed_in["copy"]["recorded_digest_source"]["kind"] = "operator_supplied"
        self.assertEqual(rw.verify_receipt(typed_in, REPO_SLUG),
                         (["recorded digest source is 'operator_supplied', not the producer's Check"], None))
        # A stamped receipt is never re-stamped.
        self.assertEqual(rw.verify_receipt(bound, REPO_SLUG), (["receipt already carries a verification block"], None))
        # The bound copy block is the one RE-READ from the provider, not the input's:
        # a field the provider does not report does not survive verification.
        padded = json.loads(json.dumps(receipt))
        padded["copy"]["note"] = "trust me"
        _, rebound = rw.verify_receipt(padded, REPO_SLUG, now)
        self.assertEqual(sorted(rebound["copy"]), sorted(rw.COPY_KEYS))

    def test_verify_receipt_cli_prints_only_a_bound_receipt_and_exits_1_on_any_difference(self):
        rec = rw.read_copy_record(REPO_SLUG, RUN_ID)
        rec.pop("_artifact_id")
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "good.json", Path(tmp) / "bad.json"
            good.write_text(json.dumps({"copy": rec}))
            bad.write_text(json.dumps({"copy": {**rec, "store_readback_digest": D("c")}}))
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(rw.main(["verify-receipt", "--repository", REPO_SLUG, str(good)]), 0)
            self.assertIn("verification", json.loads(out.getvalue()))
            out, err = io.StringIO(), io.StringIO()
            with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
                self.assertEqual(rw.main(["verify-receipt", "--repository", REPO_SLUG, str(bad)]), 1)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(json.loads(err.getvalue())["differs"], ["store_readback_digest"])


class RehearsalReceiptGate(unittest.TestCase):
    """G4: the rehearsal's own receipt block, run with a stub tool, writes a receipt only on a clean run."""

    def run_block(self, fails: int, verify_exit: int = 0):
        text = REHEARSE.read_text()
        m = re.search(r'(if \[ -n "\$BACKUP_RUN_ID" \]; then\n  mkdir -p "\$REPO/out"\n.*?\nfi\n)', text, re.S)
        if m is None:
            self.fail("receipt block not found in bin/restore-rehearse.sh")
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "out").mkdir()
            (t / "out" / "restore-exercise-receipt.json").write_text('{"stale": true}')
            calls = t / "calls.log"
            stub = t / "stub.sh"
            stub.write_text(f"#!/bin/sh\necho \"$2\" >> '{calls}'\n"
                            f"case \"$2\" in receipt) echo '{{\"unbound\":1}}';; verify-receipt) echo '{{\"bound\":1}}'; exit {verify_exit};; esac\n")
            stub.chmod(0o755)
            script = ("say() { print -r -- \"$*\"; }\n"
                      f"REPO='{t}'; WORKDIR='{t}'; COPYDIR='{t}'; PY='{stub}'; FAILS={fails}; BACKUP_RUN_ID=101\n"
                      "BACKUP_REPOSITORY=o/r; ARTIFACT_DIGEST=x; RESTORE_START_ISO=a; RESTORE_FINISHED_ISO=b\n"
                      + m.group(1) + "print -r -- \"FAILS=$FAILS\"\n")
            got = subprocess.run(["zsh", "-c", script], capture_output=True, text=True)
            receipt = t / "out" / "restore-exercise-receipt.json"
            return (got.stdout, calls.read_text().split() if calls.exists() else [],
                    receipt.read_text() if receipt.exists() else None)

    def test_a_clean_run_writes_the_verified_output_as_the_receipt(self):
        out, calls, receipt = self.run_block(0)
        self.assertEqual(calls, ["receipt", "verify-receipt"])
        self.assertEqual(json.loads(receipt), {"bound": 1})
        self.assertIn("FAILS=0", out)

    def test_a_failed_phase_4_writes_and_verifies_nothing_and_removes_a_stale_receipt(self):
        out, calls, receipt = self.run_block(1)
        self.assertEqual(calls, [])
        self.assertIsNone(receipt)
        self.assertIn("FAILS=1", out)

    def test_a_failed_verify_leaves_no_receipt_and_counts_a_failure(self):
        out, calls, receipt = self.run_block(0, verify_exit=1)
        self.assertEqual(calls, ["receipt", "verify-receipt"])
        self.assertIsNone(receipt)
        self.assertIn("FAILS=1", out)


class OutboundCensus(unittest.TestCase):
    """G2: the item list is read from the restored ops.notification_delivery, never handed in."""

    T = datetime(2026, 9, 24, 11, 0, 0, 123456, tzinfo=timezone.utc)

    def test_device_rows_map_pending_to_outcome_unknown_and_everything_final_to_settled(self):
        pending = rw.outbound_item("00000000-0000-4000-8000-000000000001", "n-1", "device", "pending", self.T)
        self.assertEqual(pending["state"], "outcome_unknown")
        self.assertEqual(pending["last_attempt_at"], "2026-09-24T11:00:00.123456Z")
        self.assertEqual(pending["envelope_digest"],
                         "sha256:" + hashlib.sha256(b'{"channel":"device","notification_id":"n-1"}').hexdigest())
        for final in ("delivered", "suppressed_quiet_hours", "failed"):
            self.assertEqual(rw.outbound_item("x", "n-1", "device", final, self.T)["state"], "settled")

    def test_the_census_digest_matches_the_evaluators_and_the_request_is_bound(self):
        items = [rw.outbound_item(f"0000000{i}-0000-4000-8000-000000000000", f"n-{i}", "device", s, self.T)
                 for i, s in ((2, "pending"), (1, "delivered"))]
        rb = [{"item_id": items[0]["item_id"], "idempotency_key": items[0]["envelope_digest"],
               "read_at": "2026-09-24T11:30:00Z", "readback": "effect_absent"}]
        req = rw.outbound_census(items, rb, "restore-1", datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(req["census"]["source"], "ops.notification_delivery:device")
        self.assertEqual(req["census"]["item_count"], 2)
        self.assertEqual(req["verification"]["verifier"], "tools/restore-watermark.py outbound-census")
        node = subprocess.run(["node", "--input-type=module", "-e",
                               "import {evaluateOutboundQueueRelease as e, v5OutboundCensusDigest as d} from "
                               f"'{REPO / 'mcp-server/src/recovery-matrix.v5.js'}';"
                               "let s='';process.stdin.on('data',c=>s+=c).on('end',()=>{const r=JSON.parse(s);"
                               "console.log(JSON.stringify({d:d(r.items),v:e(r,{now_ms:Date.parse('2026-09-24T12:00:00Z')})}))})"],
                              input=json.dumps(req), capture_output=True, text=True, check=True)
        got = json.loads(node.stdout)
        self.assertEqual(got["d"], req["census"]["digest"])
        self.assertEqual(got["v"]["decision"], "reconciled")
        by = {d["item_id"]: d["disposition"] for d in got["v"]["dispositions"]}
        self.assertEqual(by, {items[0]["item_id"]: "release_for_governed_send", items[1]["item_id"]: "already_settled"})

    def test_bind_never_restamps_and_digests_exactly_the_facts(self):
        facts = {"b": [1, "é"], "a": {"z": None, "y": True}}
        bound = recovery_evidence.bind(facts, "outbound_census", datetime(2026, 9, 24, 12, 0, 0, 999999, tzinfo=timezone.utc))
        self.assertEqual(bound["verification"]["verified_at"], "2026-09-24T12:00:00Z")  # floored, never rounded up
        self.assertEqual(bound["verification"]["facts_digest"],
                         "sha256:" + hashlib.sha256('{"a":{"y":true,"z":null},"b":[1,"é"]}'.encode()).hexdigest())
        with self.assertRaisesRegex(ValueError, "already carries"):
            recovery_evidence.bind(bound, "outbound_census")
        with self.assertRaises(KeyError):
            recovery_evidence.bind(facts, "made_up_kind")

    def test_readbacks_must_be_exactly_shaped(self):
        for bad in ({}, [{"item_id": "x", "idempotency_key": D("a"), "readback": "effect_absent"}], ["x"]):
            with self.assertRaises(ValueError):
                rw.outbound_census([], bad, "restore-1")

    def test_the_cli_reads_the_dsn_from_the_environment_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            rb = Path(tmp) / "rb.json"
            rb.write_text("[]")
            with mock.patch.dict(os.environ, {"RESTORE_DSN": ""}), mock.patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(rw.main(["outbound-census", "--restore-id", "r", "--readbacks", str(rb)]), 2)
            self.assertIn("environment only", err.getvalue())


# ── end to end on a disposable local cluster ─────────────────────────────────

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


def _psycopg_available() -> bool:
    return importlib.util.find_spec("psycopg") is not None


@unittest.skipUnless(_pg_bins() and shutil.which("node") and _psycopg_available(),
                     "PostgreSQL binaries, node or psycopg not installed")
class EndToEndDisposableCluster(unittest.TestCase):
    """A real dump, a real restore into a disposable database, the rehearsal's own filter."""

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
                        f"-p {cls.port} -k {cls.tmp} -c listen_addresses='' -c timezone=UTC", "start"],
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

    def restored(self, artifact: Path, out: Path):
        got = run_tool("restored", "--artifact", str(artifact), env={**os.environ, "RESTORE_DSN": self.dsn("restored")})
        self.assertEqual(got.returncode, 0, got.stderr)
        out.write_text(got.stdout)

    def evaluate(self, receipt_args, receipt: Path):
        built = run_tool(*receipt_args)
        self.assertEqual(built.returncode, 0, built.stderr)
        # The provider half of verify-receipt is proved in CopyRecordFromProvider;
        # here the local fixture copy has no provider, so the test stamps the
        # binding the evaluator requires with the same helper verify-receipt uses.
        receipt.write_text(json.dumps(recovery_evidence.bind(json.loads(built.stdout), "restore_exercise")))
        return subprocess.run(["node", str(EVALUATOR), "restore", str(receipt)], capture_output=True, text=True)

    def test_exact_restore_passes_and_a_lost_or_changed_row_fails(self):
        self.psql("postgres", "create database src")
        self.psql("postgres", "create database restored")
        self.psql("src", '''
          create schema ops;
          create table public.party (id int primary key, name text, at timestamptz, f float8, j jsonb,
                                     g int generated always as (id * 2) stored);
          insert into public.party (id, name, at, f, j)
            select g, 'p' || g, '2026-09-24 10:00:00+00'::timestamptz + g * interval '1 minute', g / 3.0, '{"a": 1}'
              from generate_series(1, 250) g;
          insert into public.party (id, name) values (1001, E'multi\\nline'), (1002, E'back\\\\.slash');
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
        self.assertEqual({t: e["rows"] for t, e in json.loads(counted.stdout).items()}, {
            'public.Odd "Name"': 2, "public.empty_t": 0, "public.ev_2025": 1, "public.ev_2026": 2,
            "public.party": 252, "ops.run": 40})

        restore_filter = _script_block(r"RESTORE_FILTER='([^']*)'")
        filtered = subprocess.run(["sed", "-E", restore_filter], input=dump.read_text(), text=True,
                                  capture_output=True, check=True).stdout
        subprocess.run([self.bins["psql"], self.dsn("restored"), "-v", "ON_ERROR_STOP=1", "-q"], input=filtered,
                       text=True, capture_output=True, check=True)

        restored = self.tmp / "restored.json"
        self.restored(artifact, restored)
        cmp_ok = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertEqual(cmp_ok.returncode, 0, cmp_ok.stdout + cmp_ok.stderr)

        record = self.tmp / "copy.json"
        record.write_text(json.dumps({**COPY_RECORD, "recorded_artifact_digest": digest, "store_readback_digest": digest}))
        receipt_args = ["receipt", "--copy-record", str(record), "--target-kind", "disposable_local_cluster",
                        "--oracle-id", "restore-rehearse", "--observed-digest", digest,
                        "--artifact", str(artifact), "--restored", str(restored),
                        "--started-at", "2026-09-24T10:00:00Z", "--finished-at", "2026-09-24T10:02:00Z"]
        receipt = self.tmp / "receipt.json"
        verdict = self.evaluate(receipt_args, receipt)
        self.assertEqual(verdict.returncode, 0, verdict.stdout + verdict.stderr)
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "restore_exercise_exact")

        # One row lost in the restore: the comparison and the evaluator must both refuse.
        self.psql("restored", "delete from ops.run where id = 40")
        self.restored(artifact, restored)
        cmp_bad = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertEqual(cmp_bad.returncode, 1)
        self.assertIn("MISMATCH ops.run: artifact=40 restored=39", cmp_bad.stdout)
        verdict = self.evaluate(receipt_args, receipt)
        self.assertEqual(verdict.returncode, 1)
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "watermark_mismatch")
        self.psql("restored", "insert into ops.run values (40)")

        # One row CHANGED, count intact: a row count alone would pass this; the content digest does not.
        self.psql("restored", "update public.party set name = 'p7x' where id = 7")
        self.restored(artifact, restored)
        cmp_changed = run_tool("compare", "--artifact", str(artifact), "--restored", str(restored))
        self.assertEqual(cmp_changed.returncode, 1)
        self.assertIn("MISMATCH public.party: artifact=252 restored=252 content_differs=true", cmp_changed.stdout)
        self.assertEqual(json.loads(self.evaluate(receipt_args, receipt).stdout)["reason_id"], "watermark_mismatch")
        self.psql("restored", "update public.party set name = 'p7' where id = 7")

        # A copy whose stored bytes differ from what the producer recorded fails on hash.
        record.write_text(json.dumps({**COPY_RECORD, "recorded_artifact_digest": D("0"), "store_readback_digest": D("0")}))
        self.restored(artifact, restored)
        self.assertEqual(json.loads(self.evaluate(receipt_args, receipt).stdout)["reason_id"], "artifact_hash_mismatch")

    def test_outbound_census_reads_every_device_row_of_the_restored_queue(self):
        self.psql("postgres", "create database outbound")
        self.psql("outbound", '''
          create schema ops;
          create table ops.notification_delivery (id uuid primary key default gen_random_uuid(), notification_id uuid not null,
            channel text not null, state text not null, attempted_at timestamptz not null default now());
          insert into ops.notification_delivery (notification_id, channel, state, attempted_at)
            select gen_random_uuid(), c, s, '2026-09-24 11:00:00.5+00'
              from (values ('device','pending'), ('device','delivered'), ('device','failed'), ('in_app','pending')) v(c, s);
        ''')
        rb = self.tmp / "readbacks.json"
        rb.write_text("[]")
        got = run_tool("outbound-census", "--restore-id", "restore-e2e", "--readbacks", str(rb),
                       env={**os.environ, "RESTORE_DSN": self.dsn("outbound")})
        self.assertEqual(got.returncode, 0, got.stderr)
        req = json.loads(got.stdout)
        self.assertEqual(req["census"]["item_count"], 3)  # in_app rows have no provider effect
        self.assertEqual(sorted(i["state"] for i in req["items"]), ["outcome_unknown", "settled", "settled"])
        self.assertEqual({i["last_attempt_at"] for i in req["items"]}, {"2026-09-24T11:00:00.500000Z"})
        verdict = subprocess.run(["node", str(EVALUATOR), "outbound", "-"], input=json.dumps(req),
                                 capture_output=True, text=True)
        self.assertEqual(verdict.returncode, 1, verdict.stderr)  # the pending row has no readback yet
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "outbound_items_quarantined")
        # Dropping the pending row from the census the reader emitted holds the whole queue.
        req["items"] = [i for i in req["items"] if i["state"] == "settled"]
        verdict = subprocess.run(["node", str(EVALUATOR), "outbound", "-"], input=json.dumps(req),
                                 capture_output=True, text=True)
        self.assertEqual(json.loads(verdict.stdout)["reason_id"], "outbound_evidence_not_reverified")


if __name__ == "__main__":
    unittest.main()

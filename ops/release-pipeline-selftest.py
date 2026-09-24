#!/usr/bin/env python3
"""release-pipeline-selftest.py — offline proof of ops/release-pipeline.py.

Every production command is a FAKE: the runner records argv and returns scripted
output, GitHub and the live /release endpoint are in-memory, and the record-layer
door is a list. Git is real, against a throwaway origin + clone built outside
the checkout under ops/git_env.fixture_env(), because batching and change
classification are statements about real git history.

What is pinned, one test class each:
  classification   doc-only and test-only batches release nothing
  batching         N merges since the last release ship as ONE release of the
                   latest SHA
  stop on failure  a nonzero step stops the lane, records step/rc/log, enqueues
                   exactly one diagnosis turn, and the same SHA is never retried
  staging guard    a failed staging step never reaches promotion
  kill switch      config flag, local override file, per-lane flag
  lock             a second concurrent tick runs nothing
  verifier≠maker   the maker, a human partner, the author and this pipeline are
                   refused as verifier
  blockers         a missing credential files one loop, once, and runs nothing
  dry run          prints commands, executes none, writes no state
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from git_env import fixture_env  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("release_pipeline", HERE / "release-pipeline.py")
assert _SPEC is not None and _SPEC.loader is not None
rp = importlib.util.module_from_spec(_SPEC)
sys.modules["release_pipeline"] = rp
_SPEC.loader.exec_module(rp)

FIXTURE_ENV = fixture_env()
VERSION = "0f1e2d3c-4b5a-4968-8776-655443322110"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), env=FIXTURE_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


class FakeRunner:
    """Answers by step name (the log file's name), records everything."""

    def __init__(self, fail_at: str | None = None, pending: int = 0, live: dict | None = None,
                 wrangler_out: str = "You are logged in with an OAuth Token"):
        self.fail_at, self.pending, self.live = fail_at, pending, live
        self.wrangler_out = wrangler_out
        self.calls: list[tuple[str, list[str]]] = []

    def run(self, argv, *, cwd, log, env, timeout=3600):
        stem = log.stem
        name = stem.split("-", 1)[1] if stem[:2].isdigit() else stem
        self.calls.append((name, list(argv)))
        assert "DATABASE_URL" not in env and not any(k.startswith("CARR_DB_") for k in env), \
            "a credential leaked into a step's environment"
        if name == self.fail_at:
            return rp.Result(7, "boom")
        if name == "release-key":
            key = argv[argv.index("--key") + 1]
            return rp.Result(0 if key.endswith("-01") else 2, "")
        out = {
            "staging-prepare": json.dumps({"ok": True, "receipt_id": "11111111-2222-4333-8444-555555555555"}),
            "migrate-plan": f"applied: 10   pending: {self.pending}",
            "upload": f"uploaded only\n  provider version: {VERSION}\n",
            "wrangler-auth": self.wrangler_out,
        }.get(name, "ok")
        if name == "promote" and self.live is not None:
            upload = next(a for n, a in self.calls if n == "upload")
            self.live["sha"] = upload[upload.index("--release-sha") + 1]
        return rp.Result(0, out)

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]


class FakeGitHub:
    def __init__(self, approve: bool = True, reviewer_line: str = ""):
        self.approve, self.reviewer_line = approve, reviewer_line

    def pr_for_commit(self, sha):
        return {"number": 100 + int(sha[:2], 16) % 50, "merged_at": "2026-09-30T00:00:00Z",
                "head": {"sha": "f" * 40}}

    def runs_for(self, head_sha):
        return [{"id": 42, "name": "CI", "event": "pull_request", "conclusion": "success",
                 "status": "completed"}]

    def jobs(self, run_id):
        return [{"name": "ops/ci.sh --strict", "conclusion": "success"},
                {"name": "ops/ci.sh --strict --only pushfloor unit secret", "conclusion": "success"}]

    def comments(self, pr):
        if not self.approve:
            return [{"body": "looks fine to me", "html_url": "x"}]
        return [{"body": "Independent review: PASS\n" + self.reviewer_line,
                 "html_url": f"https://github.com/o/r/pull/{pr}#issuecomment-9{pr}"}]

    def comment(self, cid):
        return {}

    def check_runs(self, sha):
        return [{"name": "test", "status": "completed", "conclusion": "success"}]


class Fixture:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.origin = tmp / "origin.git"
        self.repo = tmp / "repo"
        git(tmp, "init", "--bare", "-b", "main", str(self.origin))
        git(tmp, "clone", str(self.origin), str(self.repo))
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        self.base = self.commit({"README.md": "x"})
        self.cred = tmp / "cred"
        self.cred.mkdir()
        (self.cred / "db.env").write_text(
            "NEON_API_KEY='v'\nCARR_DB_JOBS_URL='v'\nCARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL='v'\n")
        (self.cred / "mcp-tokens.env").write_text("CARR_MCP_PROBE_TOKEN=v\n")

    def commit(self, files: dict[str, str]) -> str:
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "c")
        git(self.repo, "push", "-q", "origin", "HEAD:main")
        return git(self.repo, "rev-parse", "HEAD")

    def config(self, **over) -> dict:
        cfg = json.loads((HERE / "config" / "release-pipeline.v1.json").read_text())
        cfg["local_off_file"] = str(self.tmp / "release-pipeline.off")
        cfg["credential_dir"] = str(self.cred)
        cfg["app"]["enabled"] = False
        cfg.update(over)
        return cfg

    def pipeline(self, runner, *, cfg=None, github=None, live=None, dry_run=False, verbs=None):
        live = live if live is not None else {"sha": self.base}
        verbs = verbs if verbs is not None else []
        env = rp.child_env(FIXTURE_ENV)
        env.update({k: FIXTURE_ENV[k] for k in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")})
        return rp.Pipeline(cfg or self.config(), repo=self.repo, runner=runner,
                           github=lambda _r: github or FakeGitHub(),
                           http=lambda _u: {"git_sha": {"value": live["sha"]}},
                           call_verb=lambda verb, args: (verbs.append((verb, args)) or (True, {"ok": True})),
                           dry_run=dry_run, env=env, today="2026-09-30", out=lambda _s: None)

    def state(self) -> dict:
        p = self.repo / "out/release-pipeline/state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def records(self) -> list[dict]:
        p = self.repo / "out/release-pipeline/releases.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()


class Classification(unittest.TestCase):
    cfg = json.loads((HERE / "config" / "release-pipeline.v1.json").read_text())

    def test_doc_and_test_only_release_nothing(self):
        w = self.cfg["worker"]
        self.assertFalse(rp.classify(["README.md", "ops/ci.sh", "docs/x.md"], w)[0])
        self.assertFalse(rp.classify(["mcp-server/test/a.test.mjs", "mcp-server/README.md"], w)[0])
        self.assertFalse(rp.classify(["migrations/README.md"], w)[0])

    def test_worker_code_migrations_and_config_release(self):
        w = self.cfg["worker"]
        self.assertEqual(rp.classify(["mcp-server/src/tools.js", "README.md"], w),
                         (True, ["mcp-server/src/tools.js"]))
        self.assertTrue(rp.classify(["migrations/0600_x.sql"], w)[0])
        self.assertTrue(rp.classify(["mcp-server/wrangler.toml"], w)[0])
        self.assertTrue(rp.classify(["ops/config/doctorcre-artifact.v1.json"], w)[0])

    def test_app_lane_counts_everything_but_docs_and_tests(self):
        a = self.cfg["app"]
        self.assertFalse(rp.classify(["README.md", "test/x.test.mjs", "reports/a.json"], a)[0])
        self.assertTrue(rp.classify(["src/worker.js"], a)[0])


class Batching(Base):
    def test_many_merges_ship_once_at_the_latest_sha(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"mcp-server/src/b.js": "2"})
        latest = self.fx.commit({"docs/n.md": "3"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        uploads = [a for n, a in runner.calls if n == "upload"]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0][uploads[0].index("--release-sha") + 1], latest)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        shipped = [r for r in self.fx.records() if r["status"] == "shipped"]
        self.assertEqual([r["sha"] for r in shipped], [latest])
        self.assertEqual(shipped[0]["release_key"], "r-2026-09-30-02")  # -01 already exists
        promote = next(a for n, a in runner.calls if n == "promote")
        self.assertIn(VERSION, promote)
        self.assertEqual(runner.names().index("staging") + 1, runner.names().index("promote"))

    def test_doc_only_batch_advances_without_release(self):
        latest = self.fx.commit({"docs/n.md": "3", "mcp-server/test/x.test.mjs": "t"})
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        self.assertEqual(self.fx.records()[-1]["status"], "no_release_needed")

    def test_released_main_is_a_noop(self):
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])

    def test_migrations_apply_only_when_pending(self):
        self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, pending=0)
        self.fx.pipeline(runner, live=live).tick(["worker"])
        self.assertIn("migrate-plan", runner.names())
        self.assertNotIn("migrate-apply", runner.names())


class StopOnFailure(Base):
    def test_failure_stops_records_dispatches_and_is_never_retried(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner, verbs = FakeRunner(fail_at="staging-prepare"), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        names = runner.names()
        self.assertEqual(names[-1], "staging-prepare")
        for later in ("staging-app-writer", "migrate-plan", "upload", "staging", "promote"):
            self.assertNotIn(later, names)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"], rec["rc"]), ("failed", "staging-prepare", 7))
        self.assertTrue(rec["log"].endswith("-staging-prepare.log"))
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])
        body = verbs[0][1]["body"]
        self.assertTrue(body.startswith("@queue enqueue target=claude-desktop cap=repo-write "))
        self.assertIn(f"key=release-fix-{sha[:8]} ", body)
        self.assertEqual(verbs[0][1]["room"], "model-room")
        # the next tick on the same SHA runs nothing and dispatches nothing more
        again = FakeRunner()
        self.assertEqual(self.fx.pipeline(again, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(again.calls, [])
        self.assertEqual(len(verbs), 1)

    def test_a_fix_forward_merge_is_released_fresh(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.pipeline(FakeRunner(fail_at="upload")).tick(["worker"])
        fixed = self.fx.commit({"mcp-server/src/a.js": "2"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], fixed)
        self.assertIsNone(self.fx.state()["worker"]["failed_sha"])

    def test_live_readback_mismatch_after_promotion_is_a_failure(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(live=None)  # production keeps serving the old SHA
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertEqual(self.fx.records()[-1]["step"], "verify-live")


class StagingGuard(Base):
    def test_failed_staging_never_promotes(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner(fail_at="staging")
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertIn("upload", runner.names())
        self.assertNotIn("promote", runner.names())
        self.assertEqual(self.fx.records()[-1]["step"], "staging")


class KillSwitch(Base):
    def setUp(self):
        super().setUp()
        self.fx.commit({"mcp-server/src/a.js": "1"})

    def test_config_flag(self):
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, cfg=self.fx.config(enabled=False)).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records(), [])

    def test_local_override_file(self):
        (self.fx.tmp / "release-pipeline.off").write_text("")
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])

    def test_lane_flag(self):
        cfg = self.fx.config()
        cfg["worker"]["enabled"] = False
        runner = FakeRunner()
        self.fx.pipeline(runner, cfg=cfg).tick(["worker"])
        self.assertEqual(runner.calls, [])


class Lock(Base):
    def test_concurrent_tick_is_a_noop(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        root = self.fx.repo / "out/release-pipeline"
        with rp.single_run_lock(root) as held:
            self.assertTrue(held)
            self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        with rp.single_run_lock(root) as held:  # released afterwards
            self.assertTrue(held)


class VerifierIsNotMaker(unittest.TestCase):
    cfg = json.loads((HERE / "config" / "release-pipeline.v1.json").read_text())["worker"]

    def test_default_is_the_review_agent(self):
        self.assertEqual(rp.choose_verifier(self.cfg, {}, "Independent review: PASS"), "claude-review-agent")

    def test_event_and_comment_name_the_reviewer(self):
        self.assertEqual(rp.choose_verifier(self.cfg, {"reviewer": "Sonnet-Review"}, ""), "sonnet-review")
        self.assertEqual(rp.choose_verifier(self.cfg, {}, "Independent review: PASS\nVerifier: grok-review\n"),
                         "grok-review")

    def test_maker_humans_pipeline_and_author_are_refused(self):
        for bad in ("carr_jobs", "joe", "Dell", "release-pipeline", "jbookout"):
            with self.assertRaises(rp.Blocked, msg=bad):
                rp.choose_verifier(self.cfg, {"reviewer": bad}, "")
        with self.assertRaises(rp.Blocked):
            rp.choose_verifier(self.cfg, {"reviewer": "claude-a", "author_actor": "claude-a"}, "")
        with self.assertRaises(rp.Blocked):
            rp.choose_verifier(self.cfg, {}, "Independent review: PASS\nVerifier: carr_jobs")

    def test_upload_binds_the_verifier_and_its_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp))
            fx.commit({"mcp-server/src/a.js": "1"})
            live = {"sha": fx.base}
            runner = FakeRunner(live=live)
            fx.pipeline(runner, live=live).tick(["worker"])
            up = next(a for n, a in runner.calls if n == "upload")
            self.assertEqual(up[up.index("--verifier") + 1], "claude-review-agent")
            self.assertRegex(up[up.index("--verifier-evidence") + 1], r"^github:o/r/pull/\d+#issuecomment-\d+$")
            self.assertEqual(up[up.index("--test-evidence") + 1],
                             "github-actions:jbookout/carr-system/runs/42#ops-ci-strict")

    def test_a_merge_without_independent_review_is_blocked_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp))
            fx.commit({"mcp-server/src/a.js": "1"})
            runner = FakeRunner()
            self.assertEqual(fx.pipeline(runner, github=FakeGitHub(approve=False)).tick(["worker"]), 0)
            self.assertEqual(runner.calls, [])
            self.assertEqual(fx.records()[-1]["reason"], "no_independent_review")
            self.assertNotIn("failed_sha", fx.state().get("worker", {}))


class Blockers(Base):
    def test_missing_credential_files_one_loop_once(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        (self.fx.cred / "db.env").write_text("CARR_DB_JOBS_URL='v'\n")
        verbs: list = []
        for _ in range(2):
            runner = FakeRunner()
            self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 3)
            self.assertEqual(runner.calls, [])
        self.assertEqual([v for v, _ in verbs], ["add-loop"])
        self.assertEqual(verbs[0][1]["blocker"], "capability")
        self.assertIn("NEON_API_KEY", verbs[0][1]["blocker_detail"])

    def test_unauthenticated_wrangler_stops_before_any_staging_step(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner(wrangler_out="You are not authenticated. Please run `wrangler login`.")
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 3)
        self.assertEqual(runner.names()[-1], "wrangler-auth")
        self.assertEqual(verbs[0][0], "add-loop")
        self.assertIn("CLOUDFLARE_API_TOKEN", verbs[0][1]["blocker_detail"])


class DryRun(Base):
    def test_dry_run_prints_and_executes_nothing(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        lines: list[str] = []
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, dry_run=True)
        pipe.out = lines.append
        self.assertEqual(pipe.tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.state(), {})
        self.assertEqual(self.fx.records(), [])
        text = "\n".join(lines)
        for needle in ("staging-project-replacement.py prepare --apply --local-checks-green",
                       "provision-staging-app-writer.py", "bin/migrate-prod.sh",
                       f"bin/deploy-worker.sh --upload-version --release-sha {sha}",
                       "--env staging --recovery-step forward_fix", "--promote-version", "./run.sh health"):
            self.assertIn(needle, text)


class Report(Base):
    def test_report_lists_what_shipped(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        self.fx.pipeline(FakeRunner(live=live), live=live).tick(["worker"])
        store = rp.Store(self.fx.repo / "out/release-pipeline")
        day = self.fx.records()[-1]["ts"][:10]
        self.assertIn("SHIPPED worker", rp.report(store, day))


if __name__ == "__main__":
    unittest.main(verbosity=2)

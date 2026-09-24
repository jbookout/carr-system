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


HEAD_DATE = "2026-09-29T00:00:00Z"


def approve(pr, *, when="2026-09-30T00:00:00Z", assoc="OWNER", body=None, cid=None, reviewed=None):
    if body is None:
        body = f"Independent review: PASS\n\nReviewed-SHA: {reviewed or pr_head(pr)}\n"
    return {"id": cid or 900 + pr, "body": body, "created_at": when, "author_association": assoc,
            "user": {"login": "jbookout" if assoc == "OWNER" else "stranger"},
            "html_url": f"https://github.com/o/r/pull/{pr}#issuecomment-{cid or 900 + pr}"}


def pr_head(n: int) -> str:
    return f"feed{n:036x}"


class FakeGitHub:
    """Every PR approved by the owner after its head commit, CI green, canary
    green on every main commit, app check `test` green — unless overridden."""

    def __init__(self, approve_all: bool = True, comments: dict | None = None, canary: dict | None = None,
                 red_ci_prs: set | None = None, checks: list | None = None, raise_on: str | None = None):
        self.approve_all, self.comment_map = approve_all, comments or {}
        self.canary, self.red_ci_prs = canary, red_ci_prs or set()
        self.checks = checks if checks is not None else [
            {"name": "test", "status": "completed", "conclusion": "success"}]
        self.raise_on = raise_on
        self.heads: dict[str, int] = {}

    def pr_number(self, sha):
        return 100 + int(sha[:4], 16) % 800

    def pr_for_commit(self, sha):
        if self.raise_on == "pr_for_commit":
            raise ValueError("malformed GitHub JSON")
        n = self.pr_number(sha)
        self.heads[pr_head(n)] = n
        return {"number": n, "merged_at": "2026-09-30T00:00:00Z", "head": {"sha": pr_head(n)}}

    def commit_date(self, sha):
        return HEAD_DATE

    def runs_for(self, sha):
        if sha in self.heads:               # a PR head (see pr_for_commit)
            pr = self.heads[sha]
            ok = pr not in self.red_ci_prs
            return [{"id": 40000 + pr, "name": "CI", "event": "pull_request", "status": "completed",
                     "conclusion": "success" if ok else "failure"}]
        if self.canary is None:
            return [{"id": 7, "name": "main canary", "event": "push", "status": "completed",
                     "conclusion": "success"}]
        state = self.canary.get(sha)
        if state is None:
            return []
        status, conclusion = state
        return [{"id": 7, "name": "main canary", "event": "push", "status": status, "conclusion": conclusion}]

    def jobs(self, run_id):
        return [{"name": "ops/ci.sh --strict", "conclusion": "success"},
                {"name": "ops/ci.sh --strict --only pushfloor unit secret", "conclusion": "success"}]

    def comments(self, pr):
        if pr in self.comment_map:
            return self.comment_map[pr]
        return [approve(pr)] if self.approve_all else [{"id": 1, "body": "looks fine to me",
                                                       "created_at": "2026-09-30T00:00:00Z",
                                                       "author_association": "OWNER", "html_url": "x"}]

    def check_runs(self, sha):
        return self.checks


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
        cfg["app"]["repo_path"] = str(self.repo)
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


    def test_globs_use_github_path_filter_semantics(self):
        hit = rp._glob_hit
        self.assertTrue(hit("README.md", "**/*.md"))            # ** matches zero dirs
        self.assertTrue(hit("a/b/c.md", "**/*.md"))
        self.assertTrue(hit("mcp-server/README.md", "mcp-server/**/*.md"))
        self.assertTrue(hit("mcp-server/x/y/z.md", "mcp-server/**/*.md"))
        self.assertTrue(hit("out/a/b.json", "out/**"))
        self.assertFalse(hit("docs/deep/x.md", "docs/*.md"))     # * never crosses /
        self.assertTrue(hit("docs/x.md", "docs/*.md"))
        self.assertFalse(hit("src/ab.js", "src/?.js"))
        self.assertFalse(hit("layout/x", "out/**"))
        self.assertFalse(hit("README.mdx", "**/*.md"))


def _workflow_paths_ignore(text: str) -> list[str]:
    """The push trigger's paths-ignore list, read without a YAML dependency:
    the block list directly under the `paths-ignore:` key."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "paths-ignore:":
            indent = len(line) - len(line.lstrip())
            out = []
            for item in lines[i + 1:]:
                if not item.strip() or item.lstrip().startswith("#"):
                    continue
                if len(item) - len(item.lstrip()) <= indent or not item.lstrip().startswith("- "):
                    break
                out.append(item.lstrip()[2:].strip().strip("\"'"))
            return out
    raise AssertionError("no paths-ignore block found")


class CanaryGlobDrift(unittest.TestCase):
    """canary_ignored_globs must be exactly the canary workflow's paths-ignore:
    if they drift, the pipeline either waits forever on a canary that will
    never run or walks past a commit the canary would have judged."""

    def test_config_mirrors_main_canary_paths_ignore(self):
        cfg = json.loads((HERE / "config" / "release-pipeline.v1.json").read_text())
        wf = (HERE.parent / ".github" / "workflows" / "main-canary.yml").read_text()
        self.assertEqual(sorted(_workflow_paths_ignore(wf)),
                         sorted(cfg["worker"]["canary_ignored_globs"]))

    def test_parser_reads_a_block_list(self):
        text = ("on:\n  push:\n    paths-ignore:\n      - \"a/**\"\n"
                "      # c\n      - '**/*.md'\n  workflow_dispatch:\n")
        self.assertEqual(_workflow_paths_ignore(text), ["a/**", "**/*.md"])


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
        self.assertEqual(rp.choose_verifier(self.cfg, {}), "claude-review-agent")

    def test_only_the_merge_event_names_the_reviewer(self):
        self.assertEqual(rp.choose_verifier(self.cfg, {"reviewer": "Sonnet-Review"}), "sonnet-review")

    def test_comment_text_cannot_name_the_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp))
            sha = fx.commit({"mcp-server/src/a.js": "1"})
            n = FakeGitHub().pr_number(sha)
            gh = FakeGitHub(comments={n: [approve(n, body=f"Independent review: PASS\nVerifier: joe\nReviewed-SHA: {pr_head(n)}")]})
            live = {"sha": fx.base}
            runner = FakeRunner(live=live)
            fx.pipeline(runner, github=gh, live=live).tick(["worker"])
            up = next(a for name, a in runner.calls if name == "upload")
            self.assertEqual(up[up.index("--verifier") + 1], "claude-review-agent")

    def test_maker_humans_pipeline_and_author_are_refused(self):
        for bad in ("carr_jobs", "joe", "Dell", "release-pipeline", "jbookout"):
            with self.assertRaises(rp.Blocked, msg=bad):
                rp.choose_verifier(self.cfg, {"reviewer": bad})
        with self.assertRaises(rp.Blocked):
            rp.choose_verifier(self.cfg, {"reviewer": "claude-a", "author_actor": "claude-a"})

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
            self.assertRegex(up[up.index("--test-evidence") + 1],
                             r"^github-actions:jbookout/carr-system/runs/\d+#ops-ci-strict$")

    def test_a_merge_without_independent_review_is_blocked_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = Fixture(Path(tmp))
            fx.commit({"mcp-server/src/a.js": "1"})
            runner = FakeRunner()
            self.assertEqual(fx.pipeline(runner, github=FakeGitHub(approve_all=False)).tick(["worker"]), 0)
            self.assertEqual(runner.calls, [])
            self.assertEqual(fx.records()[-1]["reason"], "no_independent_review")
            self.assertNotIn("failed_sha", fx.state().get("worker", {}))


class ReviewGate(Base):
    """B1: the approval must be trusted, the latest verdict, and fresh."""

    def blocked_reason(self, comments_for_head):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        n = FakeGitHub().pr_number(sha)
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, github=FakeGitHub(comments={n: comments_for_head(n)}))
                         .tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        return self.fx.records()[-1]["reason"]

    def test_an_outsider_comment_is_not_an_approval(self):
        self.assertEqual(self.blocked_reason(lambda n: [approve(n, assoc="NONE")]), "no_independent_review")
        self.assertEqual(self.blocked_reason(lambda n: [approve(n, assoc="CONTRIBUTOR")]), "no_independent_review")

    def test_a_later_block_overrides_an_earlier_approve(self):
        self.assertEqual(self.blocked_reason(lambda n: [
            approve(n, when="2026-09-30T01:00:00Z", cid=1),
            approve(n, when="2026-09-30T02:00:00Z", cid=2, body="Independent review: BLOCK\nmissing test")]),
            "review_blocked")

    def test_an_outsider_cannot_override_with_a_later_approve(self):
        self.assertEqual(self.blocked_reason(lambda n: [
            approve(n, when="2026-09-30T02:00:00Z", cid=2, body="Independent review: BLOCK"),
            approve(n, when="2026-09-30T03:00:00Z", cid=3, assoc="NONE")]), "review_blocked")

    def test_an_approval_of_an_earlier_head_is_stale(self):
        # N1: approval for H1 posted after H2 was committed but before it was
        # pushed. Dates cannot tell; the exact Reviewed-SHA can.
        self.assertEqual(self.blocked_reason(lambda n: [approve(n, reviewed="ab" * 20,
                                                                when="2099-01-01T00:00:00Z")]),
                         "review_stale")

    def test_an_approval_without_reviewed_sha_is_stale(self):
        self.assertEqual(self.blocked_reason(lambda n: [approve(n, body="Independent review: PASS")]),
                         "review_stale")

    def test_a_short_sha_prefix_is_not_enough(self):
        self.assertEqual(self.blocked_reason(lambda n: [approve(
            n, body=f"Independent review: PASS at {pr_head(n)[:12]}")]), "review_stale")

    def test_the_exact_reviewed_sha_ships(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_verdict_comments_are_read_across_pages(self):
        gh = rp.GitHub("o/r", {})
        real = rp.subprocess.run

        class Done:
            returncode = 0
            stdout = json.dumps([[{"id": 1}], [{"id": 2}, {"id": 3}]])

        seen = []

        def fake(argv, **kw):
            seen.append(argv)
            return Done()
        rp.subprocess.run = fake
        try:
            self.assertEqual([c["id"] for c in gh.comments(5)], [1, 2, 3])
        finally:
            rp.subprocess.run = real
        self.assertIn("--paginate", seen[0])


class CanaryAndCI(Base):
    """B2: the nearest canary verdict decides; every release-path PR has green CI."""

    def test_a_docs_only_commit_cannot_hide_a_red_canary(self):
        code = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"docs/n.md": "x"})            # canary skipped this one: no run at all
        runner = FakeRunner()
        gh = FakeGitHub(canary={code: ("completed", "failure")})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_red")

    def test_an_uncanaried_code_head_holds(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, github=FakeGitHub(canary={})).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_pending")

    def test_a_cancelled_canary_on_a_code_head_cannot_ship_on_older_green(self):
        # N3: main-canary cancels in progress; head A cancelled, older Z green.
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        for conclusion in ("cancelled", "skipped"):
            runner = FakeRunner()
            gh = FakeGitHub(canary={z: ("completed", "success"), a: ("completed", conclusion)})
            self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
            self.assertEqual(runner.calls, [])
            self.assertEqual(self.fx.records()[-1]["reason"], "canary_pending", conclusion)

    def test_an_in_progress_canary_holds(self):
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={a: ("in_progress", None)})
        self.fx.pipeline(FakeRunner(), github=gh).tick(["worker"])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_pending")

    def test_a_cancelled_run_on_an_ignored_commit_may_be_walked_past(self):
        code = self.fx.commit({"mcp-server/src/a.js": "1"})
        docs = self.fx.commit({"docs/n.md": "x"})
        live = {"sha": self.fx.base}
        gh = FakeGitHub(canary={code: ("completed", "success"), docs: ("completed", "cancelled")})
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), github=gh, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_a_green_canary_below_a_docs_commit_passes(self):
        code = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"docs/n.md": "x"})
        live = {"sha": self.fx.base}
        gh = FakeGitHub(canary={code: ("completed", "success")})
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), github=gh, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")

    def test_red_ci_on_an_earlier_pr_in_the_batch_holds(self):
        first = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.commit({"mcp-server/src/b.js": "2"})
        runner = FakeRunner()
        gh = FakeGitHub(red_ci_prs={FakeGitHub().pr_number(first)})
        self.assertEqual(self.fx.pipeline(runner, github=gh).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "ci_not_green")
        self.assertIn(f"PR #{FakeGitHub().pr_number(first)}", rec["detail"])


class AppLane(Base):
    """B3: the app lane has a review gate and an empty check list is a hold."""

    def cfg(self):
        cfg = self.fx.config()
        cfg["app"]["enabled"] = True
        return cfg

    def test_empty_app_checks_hold(self):
        self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, cfg=self.cfg(), github=FakeGitHub(checks=[]))
        pipe.http = lambda _u: {"source_commit": self.fx.base, "environment": "production"}
        self.assertEqual(pipe.tick(["app"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "checks_missing")

    def test_app_needs_an_approved_pr(self):
        self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, cfg=self.cfg(), github=FakeGitHub(approve_all=False))
        pipe.http = lambda _u: {"source_commit": self.fx.base, "environment": "production"}
        self.assertEqual(pipe.tick(["app"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "no_independent_review")

    def test_reviewed_app_change_releases(self):
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner, cfg=self.cfg())
        live = {"source_commit": self.fx.base, "environment": "production"}
        pipe.http = lambda _u: live
        orig = runner.run

        def run(argv, **kw):
            res = orig(argv, **kw)
            if argv[:3] == ["npm", "run", "release:production"]:
                live["source_commit"] = sha
            return res
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(pipe.tick(["app"]), 0)
        self.assertEqual(runner.names()[:4], ["wrangler-auth", "app-worktree", "app-npm-ci", "app-release"])
        self.assertEqual(self.fx.records()[-1]["status"], "shipped")


class Robustness(Base):
    def test_unexpected_error_before_any_step_is_recorded_not_burned(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        rc = self.fx.pipeline(FakeRunner(), github=FakeGitHub(raise_on="pr_for_commit"), verbs=verbs).tick(["worker"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.fx.records()[-1]["status"], "error")
        self.assertIsNone(self.fx.state().get("worker", {}).get("failed_sha"))
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])

    def test_unexpected_error_after_a_mutation_is_a_failure(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        orig = runner.run

        def run(argv, **kw):
            if argv[:2] == ["npm", "ci"]:
                raise OSError("disk full")
            return orig(argv, **kw)
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual(self.fx.records()[-1]["step"], "unexpected")

    def test_failure_after_migrations_says_db_is_ahead(self):
        self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        verbs: list = []
        runner = FakeRunner(pending=2, fail_at="upload")
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertIn("migrate-apply", runner.names())
        self.assertIs(self.fx.records()[-1]["db_ahead_of_worker"], True)
        self.assertIn("db_ahead_of_worker: true", verbs[0][1]["body"])

    def test_failure_before_migrations_does_not_claim_db_ahead(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.pipeline(FakeRunner(fail_at="staging-prepare")).tick(["worker"])
        self.assertIs(self.fx.records()[-1]["db_ahead_of_worker"], False)

    def test_a_second_failure_of_the_same_sha_is_not_deduplicated_away(self):
        # N2: after clear-failed the same SHA fails differently; its turn must
        # land with its own msg_id and its own queue key.
        sha = self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        verbs: list = []
        self.fx.pipeline(FakeRunner(fail_at="staging-prepare"), verbs=verbs).tick(["worker"])
        rp.clear_failed(rp.Store(self.fx.repo / "out/release-pipeline"), "worker", sha, "retry")
        self.fx.pipeline(FakeRunner(pending=1, fail_at="upload"), verbs=verbs).tick(["worker"])
        turns = [a for v, a in verbs if v == "add-room-turn"]
        self.assertEqual(len(turns), 2)
        self.assertNotEqual(turns[0]["msg_id"], turns[1]["msg_id"])
        self.assertIn(f"key=release-fix-{sha[:8]} ", turns[0]["body"])
        self.assertIn(f"key=release-fix-{sha[:8]}-2 ", turns[1]["body"])
        self.assertIn("db_ahead_of_worker: true", turns[1]["body"])

    def test_a_deduplicated_room_answer_is_not_a_dispatch(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        pipe = self.fx.pipeline(FakeRunner(fail_at="upload"))
        pipe.call_verb = lambda verb, args: (True, {"ok": True, "deduplicated": True})
        self.assertEqual(pipe.tick(["worker"]), 1)
        self.assertIs(self.fx.records()[-1]["dispatched"], False)

    def test_a_hold_after_a_mutation_is_a_failure(self):
        sha = self.fx.commit({"migrations/0600_x.sql": "select 1;"})
        verbs: list = []
        runner = FakeRunner(pending=1)
        orig = runner.run

        def run(argv, **kw):
            if "release" in argv and "show" in argv:
                return rp.Result(0, "")          # every key exists: release_key_exhausted
            return orig(argv, **kw)
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "blocked:release_key_exhausted"))
        self.assertIs(rec["db_ahead_of_worker"], True)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual([v for v, _ in verbs], ["add-room-turn"])

    def test_clear_failed_lets_the_same_sha_run_again(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        self.fx.pipeline(FakeRunner(fail_at="upload")).tick(["worker"])
        store = rp.Store(self.fx.repo / "out/release-pipeline")
        with self.assertRaises(SystemExit):
            rp.clear_failed(store, "worker", "0" * 40, "wrong sha")
        rp.clear_failed(store, "worker", sha, "credential restored")
        self.assertEqual(self.fx.records()[-1]["status"], "failure_cleared")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)


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

    def test_unauthenticated_wrangler_stops_before_any_worktree(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner(wrangler_out="You are not authenticated. Please run `wrangler login`.")
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 3)
        self.assertEqual(runner.names(), ["wrangler-auth"])
        self.assertIsNone(self.fx.state().get("worker", {}).get("failed_sha"))
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

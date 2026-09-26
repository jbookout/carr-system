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
  schema supersede a new schema-snapshot PR closes the older open ones (close
                   only), leaves every other PR alone, and never fails on it
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
CF_TOKEN = "cf-selftest-token-must-never-be-echoed-9f8e7d"
DEPLOY_STEPS = {"wrangler-auth", "upload", "staging", "promote", "app-release"}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), env=FIXTURE_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


class FakeRunner:
    """Answers by step name (the log file's name), records everything."""

    def __init__(self, fail_at: str | None = None, pending: int = 0, live: dict | None = None,
                 wrangler_out: str = "You are logged in with an OAuth Token",
                 health_baseline_marker: bool = True, health_marker: bool = True,
                 health_baseline_findings: list | None = None, health_findings: list | None = None,
                 health_baseline_out_extra: str = "", health_out_extra: str = "",
                 health_baseline_write_json: bool = True, health_write_json: bool = True,
                 outputs: dict | None = None):
        self.fail_at, self.pending, self.live = fail_at, pending, live
        self.outputs = outputs or {}
        self.wrangler_out = wrangler_out
        # Defaults: a clean, COMPLETE health read with no findings, on both
        # the pre-promote baseline and the post-promote read — every scenario
        # above the HealthGate tests just wants the lane to finish. The
        # *_marker flags control whether tools/health-check.py's completion
        # line is the LAST NON-EMPTY line of stdout (point F); the
        # *_findings lists are what a real `--findings-json PATH` run would
        # have written (point C/D's schema: key/subject/count/hard_error/
        # time_rolling dicts); *_write_json=False simulates the file never
        # landing at all (a crash before _write_findings_json runs).
        self.health_baseline_marker = health_baseline_marker
        self.health_marker = health_marker
        self.health_baseline_findings = health_baseline_findings if health_baseline_findings is not None else []
        self.health_findings = health_findings if health_findings is not None else []
        self.health_baseline_out_extra = health_baseline_out_extra
        self.health_out_extra = health_out_extra
        self.health_baseline_write_json = health_baseline_write_json
        self.health_write_json = health_write_json
        self.calls: list[tuple[str, list[str]]] = []
        self.envs: dict[str, dict[str, str]] = {}
        # Which folder each named step ran in — added for point 1 of the
        # second round of review of PR #1237: the health baseline and the
        # post-promote health read must run in the SAME folder (the release
        # worktree, not the pipeline's own checkout), or a `db/schema.sql`
        # rewritten by this release's own migrate-apply spuriously diffs
        # against a baseline read from a different checkout.
        self.cwds: dict[str, str] = {}

    def _health_output(self, name, argv):
        """Write the --findings-json file (unless suppressed) and build the
        stdout text a real `./run.sh health` would print: any extra text the
        test asked for, then the completion marker last (unless suppressed)
        — mirroring tools/health-check.py's own contract exactly."""
        if name == "health-baseline":
            findings, extra, marker, write = (self.health_baseline_findings, self.health_baseline_out_extra,
                                              self.health_baseline_marker, self.health_baseline_write_json)
        else:
            findings, extra, marker, write = (self.health_findings, self.health_out_extra,
                                              self.health_marker, self.health_write_json)
        if "--findings-json" in argv and write:
            path = Path(argv[argv.index("--findings-json") + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"findings": findings, "generated_at": "2026-09-30T00:00:00Z"}))
        lines = [ln for ln in extra.splitlines() if ln]
        if marker:
            lines.append(rp.HEALTH_COMPLETE_MARKER)
        return "\n".join(lines) + ("\n" if lines else "")

    def run(self, argv, *, cwd, log, env, timeout=3600):
        stem = log.stem
        name = stem.split("-", 1)[1] if stem[:2].isdigit() else stem
        self.calls.append((name, list(argv)))
        self.envs[name] = dict(env)
        self.cwds[name] = str(cwd)
        assert "DATABASE_URL" not in env and not any(k.startswith("CARR_DB_") for k in env), \
            "a credential leaked into a step's environment"
        assert not any(CF_TOKEN in a for a in argv), "the deploy token reached argv"
        if name not in DEPLOY_STEPS:
            assert "CLOUDFLARE_API_TOKEN" not in env, f"the deploy token reached non-deploy step {name}"
        if name in ("health-baseline", "health"):
            return rp.Result(0 if name != self.fail_at else 7, self._health_output(name, argv))
        if name == self.fail_at:
            failed_out = self.outputs.get(name, "boom")
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(failed_out, encoding="utf-8")
            return rp.Result(7, failed_out)
        if name == "release-key":
            key = argv[argv.index("--key") + 1]
            return rp.Result(0 if key.endswith("-01") else 2, "")
        out = {
            "staging-prepare": json.dumps({"ok": True, "receipt_id": "11111111-2222-4333-8444-555555555555"}),
            "migrate-plan": f"applied: 10   pending: {self.pending}",
            "upload": f"uploaded only\n  provider version: {VERSION}\n",
            "wrangler-auth": self.wrangler_out,
            **self.outputs,
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
        self.slice_marks: list[tuple[str, str]] = []
        self.origin = tmp / "origin.git"
        self.repo = tmp / "repo"
        git(tmp, "init", "--bare", "-b", "main", str(self.origin))
        git(tmp, "clone", str(self.origin), str(self.repo))
        # Machine state is not history, exactly as the real checkout's
        # .gitignore has it. Without this, commit()'s `git add -A` swept the
        # stub `.venv/bin/python` (written below) into every test's first
        # post-base commit, so a commit meant to be docs-only was no longer
        # canary-ignored; and it swept the pipeline's own out/release-pipeline
        # state into any commit made after a tick.
        (self.repo / ".git" / "info" / "exclude").write_text(".venv\nout/\n")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "t")
        self.base = self.commit({"README.md": "x"})
        self.cred = tmp / "cred"
        self.cred.mkdir()
        (self.cred / "db.env").write_text(
            "NEON_API_KEY='v'\nCARR_DB_JOBS_URL='v'\nCARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL='v'\n")
        (self.cred / "mcp-tokens.env").write_text("CARR_MCP_PROBE_TOKEN=v\n")
        (self.cred / "tokens.env").write_text(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
        # A stub `.venv/bin/python`, matching the real checkout's layout, so
        # the fail-closed venv check added for the coordinator's dangling-
        # symlink fix (a real run found `ln -s` happily linking a MISSING
        # source venv, producing hard errors that read like a code bug
        # rather than an environment one) does not break every OTHER test
        # in this file, which is not testing that behavior. Tests for the
        # fail-closed behavior itself remove this stub (see FailClosedVenv).
        venv_python = self.repo / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True, exist_ok=True)
        venv_python.write_text("#!/bin/sh\nexit 0\n")
        venv_python.chmod(0o755)

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

    def pipeline(self, runner, *, cfg=None, github=None, live=None, dry_run=False, verbs=None,
                 slice_marker=None):
        live = live if live is not None else {"sha": self.base}
        verbs = verbs if verbs is not None else []
        # Step 10 never spawns the real marker here: every run records the
        # (release_key, sha) it would have marked in self.slice_marks.
        if slice_marker is None:
            slice_marker = lambda key, sha: (self.slice_marks.append((key, sha)) or {"rc": 0})  # noqa: E731
        env = rp.child_env(FIXTURE_ENV)
        env.update({k: FIXTURE_ENV[k] for k in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")})
        return rp.Pipeline(cfg or self.config(), repo=self.repo, runner=runner,
                           github=lambda _r: github or FakeGitHub(),
                           http=lambda _u: {"git_sha": {"value": live["sha"]}},
                           call_verb=lambda verb, args: (verbs.append((verb, args)) or (True, {"ok": True})),
                           slice_marker=slice_marker, dry_run=dry_run, env=env, today="2026-09-30", out=lambda _s: None)

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

    def test_a_shipped_release_runs_the_slice_marker_once_with_its_key(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), live=live).tick(["worker"]), 0)
        shipped = [r for r in self.fx.records() if r["status"] == "shipped"]
        self.assertEqual(self.fx.slice_marks, [(shipped[0]["release_key"], latest)])
        self.assertEqual(shipped[0]["slice_marker"], {"rc": 0})

    def test_a_failing_slice_marker_never_fails_the_release(self):
        latest = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}

        def boom(_key, _sha):
            raise RuntimeError("marker exploded")

        rc = self.fx.pipeline(FakeRunner(live=live), live=live, slice_marker=boom).tick(["worker"])
        self.assertEqual(rc, 0)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["sha"]), ("shipped", latest))
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], latest)
        self.assertIn("marker exploded", rec["slice_marker"]["error"])

    def test_no_slice_marker_without_a_shipped_release(self):
        self.fx.commit({"docs/n.md": "1"})                              # doc-only: nothing released
        self.assertEqual(self.fx.pipeline(FakeRunner()).tick(["worker"]), 0)
        self.fx.commit({"mcp-server/src/a.js": "1"})                    # a failed release
        self.assertEqual(self.fx.pipeline(FakeRunner(fail_at="staging-prepare")).tick(["worker"]), 1)
        self.fx.commit({"mcp-server/src/b.js": "1"})                    # a dry run
        self.assertEqual(self.fx.pipeline(FakeRunner(), dry_run=True).tick(["worker"]), 0)
        self.assertEqual(self.fx.slice_marks, [])

    def test_the_real_marker_is_started_detached_and_never_waited_for(self):
        # Step 10 must not hold the single-run lock: the marker is started in
        # its own session, its output goes to the run's log, and nothing waits
        # on it.
        pipe = self.fx.pipeline(FakeRunner())
        started = mock.MagicMock(pid=4242)
        with mock.patch.object(rp.subprocess, "Popen", return_value=started) as popen, \
                mock.patch.object(rp.subprocess, "run") as run:
            out = pipe._run_slice_marker("r-2026-09-30-01", "a" * 40)
        self.assertEqual(out, {"started": True, "pid": 4242, "release_sha": "a" * 40,
                               "log": str(pipe.run_dir / "slice-marker.log")})
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1:], [str(self.fx.repo / "ops" / "slice-done-marker.py"),
                                       "--release-key", "r-2026-09-30-01"])
        self.assertIs(kwargs["start_new_session"], True)
        self.assertIs(kwargs["stdin"], rp.subprocess.DEVNULL)
        self.assertEqual(set(kwargs["env"]) - {"HOME", "PATH", "LANG"}, set())
        run.assert_not_called()
        started.wait.assert_not_called()
        started.communicate.assert_not_called()

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


DO_V0 = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
DO_MARKER = f"DO migration applied: tag=v1-workflow-census-anchor from=none version={DO_V0}\n"


class DurableObjectMigration(Base):
    """bin/deploy-worker.sh applies a pending Durable Object migration inside
    the upload step; the pipeline carries its marker into the run record."""

    def test_no_migration_records_none(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        self.assertEqual(self.fx.pipeline(FakeRunner(live=live), live=live).tick(["worker"]), 0)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "shipped")
        self.assertIsNone(rec["do_migration"])

    def test_applied_migration_is_recorded_and_the_normal_path_continues(self):
        self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        live = {"sha": self.fx.base}
        # The migration deploy's own version is the candidate the wrapper prints.
        runner = FakeRunner(live=live, outputs={
            "upload": DO_MARKER + f"Durable Object migration release\n  provider version: {DO_V0}\n"})
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        names = runner.names()
        self.assertLess(names.index("upload"), names.index("staging"))
        self.assertLess(names.index("staging"), names.index("promote"))
        self.assertIn("health", names)
        promote = next(a for n, a in runner.calls if n == "promote")
        self.assertEqual(promote[promote.index("--promote-version") + 1], DO_V0)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "shipped")
        self.assertEqual(rec["provider_version_id"], DO_V0)
        self.assertEqual(rec["do_migration"], {"applied": True, "possibly_applied": False,
                                               "tag": "v1-workflow-census-anchor",
                                               "from_tag": None, "provider_version_id": DO_V0})

    def test_upload_failure_after_the_migration_dispatches_forward_fix(self):
        sha = self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        runner, verbs = FakeRunner(fail_at="upload", outputs={
            "upload": DO_MARKER + "REFUSED: the migration is applied, but Production /release did not read back\n"}), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertNotIn("staging", runner.names())
        self.assertNotIn("promote", runner.names())
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "upload"))
        self.assertEqual(rec["do_migration"]["tag"], "v1-workflow-census-anchor")
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        body = verbs[0][1]["body"]
        self.assertIn("A DURABLE OBJECT MIGRATION WAS APPLIED", body)
        self.assertIn("forward", body)

    def test_possibly_applied_migration_dispatches_forward_fix(self):
        self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        runner, verbs = FakeRunner(fail_at="upload", outputs={
            "upload": "DO migration possibly applied: tag=v1-workflow-census-anchor from=none version=unknown\n"
                      "REFUSED: the migration deploy exited 1 and the Worker reports tag unknown\n"}), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["do_migration"]["possibly_applied"], True)
        self.assertEqual(rec["do_migration"]["applied"], False)
        body = verbs[0][1]["body"]
        self.assertIn("A DURABLE OBJECT MIGRATION WAS POSSIBLY APPLIED", body)
        self.assertIn("forward", body)
        self.assertIn("blocks rollback", body)

    def test_upload_failure_without_the_marker_claims_no_migration(self):
        self.fx.commit({"mcp-server/wrangler.toml": "[[migrations]]\n"})
        runner, verbs = FakeRunner(fail_at="upload", outputs={
            "upload": "REFUSED: the target Worker's applied Durable Object migration tag could not be determined\n"}), []
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertIsNone(self.fx.records()[-1]["do_migration"])
        self.assertNotIn("DURABLE OBJECT MIGRATION", verbs[0][1]["body"])

    def test_marker_parser(self):
        self.assertIsNone(rp.parse_do_migration("uploaded only\n"))
        got = rp.parse_do_migration("x\nDO migration applied: tag=v2 from=v1 version=unknown\n")
        self.assertEqual(got, {"applied": True, "possibly_applied": False, "tag": "v2", "from_tag": "v1",
                               "provider_version_id": None})
        got = rp.parse_do_migration(f"DO migration possibly applied: tag=v2 from=none version={DO_V0}\n")
        self.assertEqual(got, {"applied": False, "possibly_applied": True, "tag": "v2", "from_tag": None,
                               "provider_version_id": DO_V0})


def _finding(key, detail, *, subject="", count=1, hard_error=False, time_rolling=False):
    return {"key": key, "subject": subject, "detail": detail, "count": count,
            "hard_error": hard_error, "time_rolling": time_rolling}


RULE_GAPS_98 = _finding("rule_enforcement", "98 active rule gaps", count=98)
RULE_GAPS_97 = _finding("rule_enforcement", "97 active rule gaps", count=97)
RULE_GAPS_99 = _finding("rule_enforcement", "99 active rule gaps", count=99)


class HealthCompleteMarkerDoesNotDrift(unittest.TestCase):
    """Point 3 of the third round of an independent review of PR #1237: a
    REAL run of health-preflight showed `complete=False` on every single
    read, baseline and live alike, even though tools/health-check.py had
    printed its completion marker as the true last line of stdout and
    written a well-formed findings.json. The cause: ops/release-pipeline.py's
    HEALTH_COMPLETE_MARKER constant was a truncated PREFIX of the actual
    `print(...)` call in tools/health-check.py — missing "; use --recovery
    --reason <why>." — so the exact-string-equality check in
    read_health_findings() never matched, no matter how clean the read.
    Every FakeRunner-based selftest passed anyway because FakeRunner builds
    its own fake marker text FROM ops/release-pipeline.py's constant, so
    both sides of the (wrong) comparison always agreed with each other, just
    never with the real file. This test reads tools/health-check.py's
    SOURCE TEXT directly (it cannot be imported — see tools/health-check-
    findings-selftest.py's module docstring) and proves the two literal
    strings are identical, so this specific drift can never come back
    silently."""

    def test_pipeline_marker_matches_health_checks_own_print_exactly(self):
        health_check_src = (HERE / "../tools/health-check.py").resolve().read_text(encoding="utf-8")
        m = re.search(r'_HEALTH_COMPLETION_MARKER = \(\s*"([^"]*)"\s*\n\s*"([^"]*)"\s*\)',
                      health_check_src)
        self.assertIsNotNone(m, "could not find _HEALTH_COMPLETION_MARKER in tools/health-check.py "
                                "— has it been renamed or reshaped?")
        health_check_marker = m.group(1) + m.group(2)
        self.assertEqual(rp.HEALTH_COMPLETE_MARKER, health_check_marker)
        # And both print call sites in tools/health-check.py actually use
        # the constant, not a re-typed literal that could drift from it on
        # its own.
        self.assertEqual(health_check_src.count("print(_HEALTH_COMPLETION_MARKER)"), 2,
                         "tools/health-check.py should print the marker constant, by name, "
                         "from exactly two places (the REFUSED early-return and the normal end)")


class HealthGate(Base):
    """The post-release health gate judges the release, not standing debt: it
    diffs by (key, subject) against a baseline taken before any mutation, and
    fails on a new pair, a risen count, or any hard_error — never on a
    standing finding unchanged since the baseline. Findings A-G below track
    the points of an independent review of PR #1237 by letter."""

    def test_standing_finding_present_before_and_after_does_not_fail(self):
        # D: "98 becoming 97 rule gaps" must not fail — and neither may an
        # UNCHANGED 98 -> 98, even though health-check.py's own rc stays 1
        # for as long as any rule gap exists (see test_D below for the
        # improving case; this is the unchanged case).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_98])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("health-baseline", runner.names())
        self.assertIn("health", runner.names())

    def test_new_finding_since_baseline_fails_the_release(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_finding = _finding("export_receipt", "LATEST FAILED vendors.xlsx (latest status failed)",
                               subject="vendors.xlsx")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_98, new_finding])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "health")
        self.assertIn("export_receipt", rec.get("detail", ""))
        # promotion already happened; the gate judges what promotion did, it
        # does not (and cannot) un-promote.
        self.assertIn("promote", runner.names())
        # G: the fix session is pointed at a small new-findings-only file,
        # not the full health output.
        findings_log = Path(rec["log"])
        self.assertTrue(findings_log.name.endswith("health-new-findings.log"))
        text = findings_log.read_text()
        self.assertIn("export_receipt", text)
        self.assertNotIn("rule_enforcement", text)  # the unchanged standing finding is NOT dumped in

    def test_an_unavailable_live_read_is_never_a_pass_even_with_no_findings(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_marker=False, health_findings=[])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertEqual(self.fx.records()[-1]["step"], "health")

    def test_A_nonzero_exit_with_no_finding_at_all_always_fails(self):
        # Point A's literal example: health exits nonzero but recorded no
        # finding to explain it (the historical "export receipts UNREADABLE"
        # bug, now fixed at the source in tools/health-check.py, but this is
        # the pipeline-side backstop against a future regression of that).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, fail_at="health",
                            health_baseline_findings=[], health_findings=[])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "health")
        self.assertIn("no finding at all", rec.get("detail", ""))

    def test_B_and_E_an_unavailable_baseline_is_a_pre_promote_block_not_a_failure(self):
        # B: the baseline must be taken before any --apply step; E: an
        # incomplete baseline is a BLOCKED hold, so the SHA is never consumed
        # (no failed_sha, no diagnosis dispatch, retried next tick) — not a
        # StepFailed after promotion already ran. Point 1 of the second round
        # of review: the baseline is read IN the release worktree (a checkout
        # only, no --apply), so "worktree" now runs before "health-baseline"
        # — that checkout is still trivially re-creatable and cleaned up by
        # remove_worktrees() on this Blocked path, so it does not turn an
        # incomplete baseline into a real failure.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_marker=False,
                            health_findings=[RULE_GAPS_98])
        # A clean Blocked hold with no capability to file returns 0 — "nothing
        # failed", per Blocked's own docstring; that is what "the SHA isn't
        # consumed" means in practice, not a nonzero pipeline exit.
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.records()[-1]["status"], "blocked")
        self.assertEqual(self.fx.records()[-1]["reason"], "health_baseline_unavailable")
        self.assertIsNone(self.fx.state().get("worker", {}).get("failed_sha"))
        # nothing after the baseline read ever ran: not staging-prepare, not
        # migrate-apply, not upload, not promote.
        for later in ("staging-prepare", "staging-app-writer", "migrate-plan",
                      "migrate-apply", "upload", "staging", "promote", "health"):
            self.assertNotIn(later, runner.names())
        self.assertEqual(runner.names(),
                         ["wrangler-auth", "worktree", "venv-link", "npm-ci", "health-baseline"])

    def test_B_baseline_runs_in_the_worktree_before_every_apply_step(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        names = runner.names()
        # "worktree" (a checkout, no --apply) still comes before the
        # baseline — see point 1 of the second round of review — but every
        # REAL --apply step must still come after the baseline read.
        self.assertLess(names.index("worktree"), names.index("health-baseline"))
        baseline_i = names.index("health-baseline")
        for apply_step in ("staging-prepare", "staging-app-writer", "migrate-plan"):
            if apply_step in names:
                self.assertLess(baseline_i, names.index(apply_step),
                                f"health-baseline must run before {apply_step}")

    def test_point1_baseline_and_post_read_run_in_the_same_folder(self):
        # Point 1 of the second round of review: the baseline and the
        # post-promote read must use the SAME cwd (the release worktree),
        # not two different checkouts — otherwise migrate-apply rewriting
        # db/schema.sql in the worktree spuriously "changes" repo_loose_work
        # against a baseline read from the pipeline's own checkout.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("health-baseline", runner.cwds)
        self.assertIn("health", runner.cwds)
        self.assertEqual(runner.cwds["health-baseline"], runner.cwds["health"])
        # and neither one is the pipeline's own repo checkout — it is a
        # dedicated release worktree.
        self.assertNotEqual(runner.cwds["health-baseline"], str(self.fx.repo))

    def test_point1_the_baselines_worktree_has_a_linked_venv(self):
        # Point 1 (BLOCKER) of the third round of review, reproduced with a
        # real run: venv-link must run BEFORE the health baseline, or
        # `./run.sh health` in the worktree falls back to the bare Homebrew
        # python3 (no psycopg, no openpyxl) and every worker release blocks
        # on a source_unreadable hard_error. "worktree" then "venv-link"
        # then "health-baseline", in that order, every time.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        names = runner.names()
        self.assertLess(names.index("worktree"), names.index("venv-link"))
        self.assertLess(names.index("venv-link"), names.index("health-baseline"))
        # FakeRunner does not touch the filesystem, so the real proof of a
        # working link is the venv-link argv itself: `ln -s <repo>/.venv
        # <worktree>/.venv`, with the LINK TARGET sitting directly inside
        # the exact folder the baseline read then runs in.
        venv_link_argv = next(argv for name, argv in runner.calls if name == "venv-link")
        self.assertEqual(venv_link_argv[0], "ln")
        self.assertTrue(venv_link_argv[-1].endswith("/.venv"))
        self.assertEqual(str(Path(venv_link_argv[-1]).parent), runner.cwds["health-baseline"])

    def test_B_baseline_runs_before_migrate_apply_when_a_migration_is_pending(self):
        # The probe's original concern named migrate-apply specifically ("the
        # baseline is taken after migrate-apply and the staging --apply
        # steps, so a release's own migration damage is forgiven"). Checked
        # with --only migrate-apply's precondition (pending > 0) actually
        # true, isolated from the unrelated schema-followup PR path that a
        # full green run with pending migrations also exercises.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, fail_at="migrate-apply", pending=1)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        names = runner.names()
        self.assertIn("health-baseline", names)
        self.assertIn("migrate-apply", names)
        self.assertLess(names.index("health-baseline"), names.index("migrate-apply"))

    def test_C_two_identical_failures_in_one_run_are_not_hidden_as_one_unchanged_line(self):
        # The exact-string scheme's blind spot: baseline has ONE occurrence
        # of a job's terminal-failure finding, live has TWO (same key and
        # subject, so a naive text diff sees "no new line" — this scheme
        # instead compares the accumulated COUNT and must catch the rise).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        one = _finding("job_terminal_failure", "nightly-export failed", subject="nightly-export", count=1)
        two = _finding("job_terminal_failure", "nightly-export failed", subject="nightly-export", count=2)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[one], health_findings=[two])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("count 1 -> 2", self.fx.records()[-1].get("detail", ""))

    def test_D_a_falling_count_is_an_improvement_and_passes(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_97])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_D_time_rolling_findings_are_reported_but_never_diffed(self):
        # A job's MISSING DUE date rolls forward daily; the SUBJECT text
        # differs from yesterday's baseline by construction, which would read
        # as "new" under a naive diff. time_rolling=True excludes it.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("job_missing_due", "cal MISSING DUE execution for 2026-09-23",
                             subject="cal", time_rolling=True)
        today = _finding("job_missing_due", "cal MISSING DUE execution for 2026-09-24",
                         subject="cal", time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_D_any_hard_error_fails_even_when_identical_to_the_baseline(self):
        # Point A/D together: hard_error is unconditional and is never
        # excused by a matching baseline entry — a LIVE structural read
        # failure that is not the SAME hard_error the baseline had (so it
        # does not trip point 2's earlier pre-promote block below) must
        # still fail on its own terms, not be waved through as "unchanged."
        # Modeled here as a baseline entry that reports the same (key,
        # subject) and count WITHOUT hard_error, and a live read that adds
        # hard_error=True to it — count-unchanged would normally read as a
        # pass, but hard_error overrides that.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        baseline_row = _finding("export_unreadable", "export receipts flaky", hard_error=False)
        live_row = _finding("export_unreadable", "export receipts UNREADABLE", hard_error=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[baseline_row],
                            health_findings=[live_row])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("hard_error", self.fx.records()[-1].get("detail", ""))

    def test_point2_a_hard_error_already_in_the_baseline_blocks_before_promote(self):
        # Point 2 of the second round of review: a baseline that ALREADY
        # shows a hard_error finding must block before any --apply step,
        # not ship and then predictably fail its own post-promote
        # comparison. This is a Blocked hold with a capability (files a
        # loop), so it returns 3, not a StepFailed.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        broken = _finding("export_unreadable", "export receipts UNREADABLE", hard_error=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[broken], health_findings=[broken])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 3)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "blocked")
        self.assertEqual(rec["reason"], "health_baseline_hard_error")
        self.assertTrue(rec.get("loop_filed"))
        for later in ("staging-prepare", "migrate-plan", "upload", "staging", "promote", "health"):
            self.assertNotIn(later, runner.names())

    def test_point2_credential_expiring_soon_alone_is_not_hard_error(self):
        # The other half of point 2: expiring_soon (and registry-audit data
        # errors) are counted findings, not hard_error — health-check.py's
        # own logic is the source of truth for that distinction and is
        # proven directly in tools/health-check-findings-selftest.py; this
        # only proves the pipeline's OWN gate does not treat a non-hard_error
        # baseline finding as a pre-promote block.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        expiring = _finding("credential_health", "1 of 4 credential(s) need attention "
                            "(failed=0 expiring_soon=1 unverifiable=0 unconfigured=0 ok=3)",
                            count=1, hard_error=False)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[expiring],
                            health_findings=[expiring])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_F_marker_present_but_not_the_last_line_is_incomplete(self):
        # A crash on the way out after the happy-path print (e.g. a
        # traceback appended to the same stdout) must not read as complete:
        # the marker printed, then something else printed after it, so it is
        # not the LAST non-empty line any more. FakeRunner's own
        # health_marker flag always puts the marker last when True, so this
        # is built directly as extra text with health_marker=False (the
        # marker text is embedded in the "extra" text itself, followed by
        # a traceback — exactly what a real crash-on-the-way-out looks like).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(
            live=live, health_marker=False,
            health_out_extra=f"{rp.HEALTH_COMPLETE_MARKER}\nTraceback (most recent call last):\n  boom")
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertEqual(self.fx.records()[-1]["step"], "health")

    def test_G_new_findings_file_never_contains_the_full_health_log_path(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_finding = _finding("export_receipt", "LATEST FAILED vendors.xlsx", subject="vendors.xlsx")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[], health_findings=[new_finding])
        self.fx.pipeline(runner, live=live).tick(["worker"])
        rec = self.fx.records()[-1]
        # "log" is what queue_turn's dispatched turn tells the fix session to
        # read; it must be the small new-findings file, not the raw
        # `./run.sh health` stdout capture (…-health.log).
        self.assertTrue(rec["log"].endswith("health-new-findings.log"))
        self.assertFalse(rec["log"].endswith("-health.log"))

    def test_point4_a_rolling_finding_still_fails_when_its_count_rises(self):
        # Point 4 of the second round of review, still true today only for a
        # time_rolling key that is NOT on HEALTH_REGRESSION_FIRST_APPEARANCE_
        # ALLOWLIST: doctrine_gate stays fully count-sensitive (round 12
        # left it untouched), so a rising count within an already-present
        # doctrine_gate finding still fails. (doctrine_stale moved to the
        # allowlist-only "subject set, never count" rule in round 12 — see
        # test_round12_allowlisted_existing_subject_count_rise_is_excused
        # below for its new behavior, and test_round7_doctrine_gate_is_
        # still_not_on_the_allowlist for confirmation it's excluded.)
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("doctrine_gate", "3 failures in 24h", count=3, time_rolling=True)
        today = _finding("doctrine_gate", "5 failures in 24h", count=5, time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("time_rolling count 3 -> 5", self.fx.records()[-1].get("detail", ""))

    def test_point4_a_rolling_finding_falling_still_passes(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("doctrine_gate", "5 failures in 24h", count=5, time_rolling=True)
        today = _finding("doctrine_gate", "3 failures in 24h", count=3, time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round12_allowlisted_existing_subject_count_rise_is_excused(self):
        # Round 12, point 2: the reviewer refuted the prior rule for an
        # ALLOWLISTED key. The gate runs once, after promote, and never
        # retries the SHA — an already-known-broken hourly job whose
        # job_missing_due count keeps climbing must not fail every release
        # forever. A subject already present in the baseline is now excused
        # no matter how far its count rises.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        yesterday = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 09:00",
                             subject="hourly-sync", time_rolling=True, count=1)
        today = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 14:00",
                         subject="hourly-sync", time_rolling=True, count=6)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[yesterday], health_findings=[today])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round13_allowlisted_new_subject_passes(self):
        # Round 13 reverses round 12's other half: making a brand-new
        # subject fail brought back exactly the clock-driven false failure
        # the allowlist existed to prevent — a job's own due window can
        # pass during the tens of minutes a release takes, turning it into
        # a "new" job_missing_due subject with no baseline entry, through
        # no fault of the release. A new subject on an allowlisted key is
        # now excused, same as an existing one, regardless of count.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        existing = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 09:00",
                            subject="hourly-sync", time_rolling=True, count=1)
        new_job = _finding("job_missing_due", "weekly-report MISSING DUE execution for 09-24",
                           subject="weekly-report", time_rolling=True, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[existing],
                            health_findings=[existing, new_job])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round13_a_new_non_time_rolling_key_still_fails(self):
        # A new (key, subject) pair that is NOT time_rolling at all (so not
        # eligible for the allowlist regardless of the key name) still
        # fails as an ordinary new regression — round 13 only takes
        # time_rolling, allowlisted keys out of the comparison.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_credential_gap = _finding("credential_health", "new-integration token expiring",
                                      subject="new-integration")
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[new_credential_gap])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("new-integration", self.fx.records()[-1].get("detail", ""))

    def test_round13_receipt_lists_the_unattributed_time_rolling_findings(self):
        # "Keep them in the findings and the receipt, visibly listed as
        # time-rolling, not attributed, so nothing is hidden": a release
        # that PASSES (no non-allowlisted regression) still carries every
        # allowlisted time_rolling finding forward into the shipped-release
        # receipt (releases.jsonl), explicitly labeled, even though none of
        # them influenced the pass/fail outcome.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        missing_due = _finding("job_missing_due", "hourly-sync MISSING DUE execution for 09:00",
                               subject="hourly-sync", time_rolling=True, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[], health_findings=[missing_due])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        record = self.fx.records()[-1]
        self.assertEqual(record.get("status"), "shipped")
        unattributed = record.get("health_time_rolling_not_attributed", [])
        self.assertEqual(len(unattributed), 1)
        self.assertIn("job_missing_due", unattributed[0])
        self.assertIn("hourly-sync", unattributed[0])
        self.assertIn("time-rolling, not attributed", unattributed[0])

    def test_round14_stale_then_failed_is_not_lost(self):
        # Round 14 (pre-existing since round 3): the baseline lookup keyed
        # only by (key, subject) let a time_rolling baseline row match an
        # ordinary live row for the same (key, subject) as its "prior".
        # export_receipt[vendors.xlsx] STALE (time_rolling=True) at
        # baseline, then LATEST FAILED (time_rolling=False, NOT hard_error)
        # after the release: the live row matched the STALE baseline row,
        # saw count 1 against 1 (unchanged), and vanished — not a
        # regression (count didn't rise) and not excused (not time_rolling)
        # — bad=[] and excused=[]. Keying the baseline lookup by (key,
        # subject, time_rolling) too means the FAILED live row has no
        # baseline entry of its OWN rolling-ness to match, so it is
        # correctly treated as a genuinely new (ordinary) finding and fails
        # the gate.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        stale = _finding("export_receipt", "STALE vendors.xlsx (last ok 2026-09-20)",
                         subject="vendors.xlsx", time_rolling=True, count=1)
        failed = _finding("export_receipt",
                          "LATEST FAILED vendors.xlsx (latest status error)",
                          subject="vendors.xlsx", time_rolling=False, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[stale], health_findings=[failed])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("vendors.xlsx", self.fx.records()[-1].get("detail", ""))

    def test_round14_failed_then_stale_is_excused_not_lost(self):
        # The reverse of the case above: export_receipt[vendors.xlsx]
        # LATEST FAILED (ordinary) at baseline, then STALE (time_rolling)
        # after the release. The live STALE row is on the allowlist, so
        # round 13's unconditional excuse for allowlisted time_rolling rows
        # applies regardless of what the baseline held — it is excused, not
        # silently dropped and not failed, and it shows up in the receipt.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        failed = _finding("export_receipt",
                          "LATEST FAILED vendors.xlsx (latest status error)",
                          subject="vendors.xlsx", time_rolling=False, count=1)
        stale = _finding("export_receipt", "STALE vendors.xlsx (last ok 2026-09-20)",
                         subject="vendors.xlsx", time_rolling=True, count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[failed], health_findings=[stale])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        unattributed = self.fx.records()[-1].get("health_time_rolling_not_attributed", [])
        self.assertEqual(len(unattributed), 1)
        self.assertIn("vendors.xlsx", unattributed[0])
        self.assertIn("time-rolling, not attributed", unattributed[0])

    def test_round12_rule_enforcement_count_rise_still_fails(self):
        # "Keep every other key count-sensitive": rule_enforcement is not
        # time_rolling at all, so a plain count rise (98 -> 99) still fails
        # exactly as it always has — round 12 only touches the three
        # allowlisted time_rolling keys.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[RULE_GAPS_98],
                            health_findings=[RULE_GAPS_99])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("count 98 -> 99", self.fx.records()[-1].get("detail", ""))

    def test_point2r4_a_new_doctrine_gate_finding_with_no_baseline_still_fails(self):
        # Point 2 of round 4 of an independent review of PR #1237:
        # doctrine_gate (and doctrine_stale) are time_rolling=True in
        # tools/health-check.py, but that must not blanket-excuse a
        # genuinely NEW finding just because it has no baseline entry — 40
        # gate failures appearing where the baseline had 0 is a real
        # regression this release introduced, not a clock crossing, and
        # must fail the gate.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_gate_failures = _finding("doctrine_gate", "40 failures in 24h", count=40,
                                     time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[new_gate_failures])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 1)
        self.assertIn("doctrine_gate", self.fx.records()[-1].get("detail", ""))

    def test_point2r4_allowlisted_keys_still_excuse_a_first_appearance(self):
        # Round 12 briefly reversed this (making a first appearance fail);
        # round 13 reverses it back and goes further — a key on the
        # allowlist is now out of the comparison entirely, so export_
        # receipt (the STALE branch) and job_missing_due are excused on a
        # first appearance with no baseline entry, exactly as through
        # round 11, AND (unlike round 11) an existing subject's count rise
        # is excused too (see test_round12_allowlisted_existing_subject_
        # count_rise_is_excused and test_round13_allowlisted_new_subject_
        # passes above, which cover that half explicitly).
        self.fx.commit({"mcp-server/src/a.js": "1"})
        stale_export = _finding("export_receipt", "STALE vendors.xlsx (last ok 2026-09-20)",
                                subject="vendors.xlsx", time_rolling=True)
        missing_due = _finding("job_missing_due", "cal MISSING DUE execution for 2026-09-24",
                               subject="cal", time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[stale_export, missing_due])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round7_doctrine_stale_first_appearance_is_excused(self):
        # Round 7 excused doctrine_stale's first appearance as clock noise;
        # round 12 briefly reversed it; round 13 restores the excuse (and
        # extends it to a count rise too) — a section crossing review_after
        # for the very first time, with nothing in the baseline to compare
        # against, is clock noise this release did not cause.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        new_stale = _finding("doctrine_stale", "2 stale sections", count=2,
                             time_rolling=True)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[],
                            health_findings=[new_stale])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_round7_doctrine_gate_is_still_not_on_the_allowlist(self):
        # Confirms the allowlist addition is scoped to doctrine_stale only:
        # doctrine_gate must remain excluded (see test_point2r4_a_new_
        # doctrine_gate_finding_with_no_baseline_still_fails above, which
        # already pins this behavior — this test pins the constant itself
        # so the two cannot silently drift apart).
        self.assertNotIn("doctrine_gate", rp.HEALTH_REGRESSION_FIRST_APPEARANCE_ALLOWLIST)
        self.assertIn("doctrine_stale", rp.HEALTH_REGRESSION_FIRST_APPEARANCE_ALLOWLIST)

    def test_point1_repo_loose_work_is_excluded_from_the_gate(self):
        # Point 1 of the second round of review: even reading baseline and
        # post-promote in the same worktree, migrate-apply legitimately
        # rewrites db/schema.sql as part of a normal migration release, so
        # repo_loose_work must never fail the gate on its own.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        loose_before = _finding("repo_loose_work", "0 actionable path(s)", count=0)
        loose_after = _finding("repo_loose_work", "1 actionable path(s)", count=1)
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live, health_baseline_findings=[loose_before],
                            health_findings=[loose_after])
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)

    def test_point5_an_incomplete_baseline_escalates_after_n_ticks(self):
        # Point 5 of the second round of review: an incomplete baseline must
        # not hold silently forever. The first HEALTH_BASELINE_ESCALATE_AFTER
        # - 1 ticks on the SAME sha are ordinary clean holds (capability=None,
        # tick returns 0, no loop filed); the Nth tick escalates to a filed
        # loop (capability set, tick returns 3).
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        n = rp.Pipeline.HEALTH_BASELINE_ESCALATE_AFTER
        verbs: list = []
        for attempt in range(1, n):
            runner = FakeRunner(live=live, health_baseline_marker=False)
            rc = self.fx.pipeline(runner, live=live, verbs=verbs).tick(["worker"])
            self.assertEqual(rc, 0, f"attempt {attempt} should still be a clean, silent hold")
            self.assertEqual(self.fx.records()[-1]["reason"], "health_baseline_unavailable")
            self.assertEqual(verbs, [], f"no loop should be filed before attempt {n}")
        runner = FakeRunner(live=live, health_baseline_marker=False)
        rc = self.fx.pipeline(runner, live=live, verbs=verbs).tick(["worker"])
        self.assertEqual(rc, 3, "the Nth consecutive incomplete baseline must escalate")
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "health_baseline_stalled")
        self.assertTrue(rec.get("loop_filed"))
        self.assertEqual(len(verbs), 1)

    def test_point5_a_completed_baseline_resets_the_incomplete_counter(self):
        # A baseline that eventually DOES complete clears the per-sha
        # counter, so a later transient blip does not inherit an escalation
        # that is already most of the way to firing.
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        n = rp.Pipeline.HEALTH_BASELINE_ESCALATE_AFTER
        for _ in range(n - 1):
            runner = FakeRunner(live=live, health_baseline_marker=False)
            self.fx.pipeline(runner, live=live).tick(["worker"])
        good_runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(good_runner, live=live).tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"].get("health_baseline_incomplete", {}).get(sha),
                         None)


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
        # distinct content per call: a repeat call must add a real code commit,
        # not an empty one (see Fixture's info/exclude)
        self._calls = getattr(self, "_calls", 0) + 1
        sha = self.fx.commit({"mcp-server/src/a.js": str(self._calls)})
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



class SquashGitHub(FakeGitHub):
    """FakeGitHub, except the squash commit `merged` maps to PR `number`
    whose real head is `head`, approved at `reviewed`."""

    def __init__(self, merged: str, number: int, head: str, reviewed: str):
        super().__init__()
        self.merged, self.number, self.real_head = merged, number, head
        self.comment_map = {number: [approve(number, reviewed=reviewed)]}

    def pr_for_commit(self, sha):
        if sha == self.merged:
            self.heads[self.real_head] = self.number
            return {"number": self.number, "merged_at": "2026-09-30T00:00:00Z",
                    "head": {"sha": self.real_head}}
        return super().pr_for_commit(sha)


class UpdateBranchReview(Base):
    """`gh pr update-branch` merges main into a PR after its review, so the
    merged head H is not the Reviewed-SHA R. R covers H only when R..H is
    nothing but main merges that leave the PR's own files alone. The PR is
    built in a separate author clone and squash-merged, as on GitHub, so the
    pipeline's checkout has neither R nor H until it fetches refs/pull/N/head."""

    N = 777

    def build(self, *, merge_touches_pr_file=False, merge_touches_other_file=False):
        fx = self.fx
        author = fx.tmp / "author"
        git(fx.tmp, "clone", "-q", str(fx.origin), str(author))
        git(author, "config", "user.email", "a@example.invalid")
        git(author, "config", "user.name", "a")
        git(author, "checkout", "-q", "-b", "pr")
        (author / "mcp-server/src").mkdir(parents=True, exist_ok=True)
        (author / "mcp-server/src/pr.js").write_text("reviewed\n")
        git(author, "add", "-A")
        git(author, "commit", "-q", "-m", "the PR")
        reviewed = git(author, "rev-parse", "HEAD")
        main_moved = fx.commit({"mcp-server/src/other.js": "main moved"})   # main advances
        git(author, "fetch", "-q", "origin", "main")
        git(author, "merge", "-q", "--no-ff", "--no-edit", "origin/main")   # what update-branch does
        if merge_touches_pr_file or merge_touches_other_file:
            rel = "mcp-server/src/pr.js" if merge_touches_pr_file else "mcp-server/src/sneak.js"
            (author / rel).write_text("changed inside the merge, after review\n")
            git(author, "add", "-A")
            git(author, "commit", "-q", "--amend", "--no-edit")
        head = git(author, "rev-parse", "HEAD")
        git(author, "push", "-q", "origin", f"HEAD:refs/pull/{self.N}/head")
        git(author, "checkout", "-q", "-B", "main", "origin/main")
        git(author, "merge", "-q", "--squash", head)
        git(author, "commit", "-q", "-m", f"the PR (#{self.N})")
        merged = git(author, "rev-parse", "HEAD")
        git(author, "push", "-q", "origin", "HEAD:main")
        git(fx.repo, "fetch", "-q", "origin", "main")
        self.assertNotEqual(main_moved, merged)
        return reviewed, head, merged

    def tick(self, merged, head, reviewed):
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        gh = SquashGitHub(merged, self.N, head, reviewed)
        return self.fx.pipeline(runner, live=live, github=gh).tick(["worker"]), runner

    def test_exact_rule_is_recorded(self):
        reviewed, head, merged = self.build()
        rc, _ = self.tick(merged, head, head)          # approval names H itself
        self.assertEqual(rc, 0)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "shipped")
        self.assertEqual((rec["review_rule"], rec["reviewed_sha"], rec["pr_head_sha"]), ("exact", head, head))
        self.assertIn({"pr": self.N, "rule": "exact", "reviewed_sha": head, "head_sha": head}, rec["reviews"])

    def test_main_merge_only_rule_accepts_update_branch(self):
        reviewed, head, merged = self.build()
        self.assertNotEqual(reviewed, head)
        rc, _ = self.tick(merged, head, reviewed)
        self.assertEqual(rc, 0)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["status"], "shipped")
        self.assertEqual((rec["review_rule"], rec["reviewed_sha"], rec["pr_head_sha"]),
                         ("main-merge-only", reviewed, head))

    def test_merge_that_touches_a_pr_file_holds(self):
        reviewed, head, merged = self.build(merge_touches_pr_file=True)
        rc, runner = self.tick(merged, head, reviewed)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["reason"]), ("blocked", "review_stale"))
        self.assertIn("mcp-server/src/pr.js", rec["detail"])
        self.assertIsNone(self.fx.state().get("worker", {}).get("failed_sha"))

    def test_merge_that_smuggles_a_non_pr_file_holds(self):
        reviewed, head, merged = self.build(merge_touches_other_file=True)
        rc, runner = self.tick(merged, head, reviewed)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["reason"]), ("blocked", "review_stale"))
        self.assertIn("non-PR file", rec["detail"])
        self.assertIn("mcp-server/src/sneak.js", rec["detail"])

    def test_non_merge_commit_after_review_holds(self):
        reviewed, head, merged = self.build()
        # an approval of an OLDER sha whose successor is an ordinary commit: R = parent of the PR commit
        older = git(self.fx.repo, "rev-parse", f"{self.fx.base}")
        rc, runner = self.tick(merged, head, older)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual(rec["reason"], "review_stale")
        self.assertIn("not a two-parent merge", rec["detail"])

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


class ReleaseTarget(Base):
    """The Worker ships up to the NEWEST green-canary commit, not HEAD.

    main-canary runs ~20 minutes with cancel-in-progress while merges land
    every 10-20 minutes, so HEAD's own run is nearly always in progress or
    cancelled; demanding HEAD itself be green starved the lane."""

    GREEN, RED = ("completed", "success"), ("completed", "failure")

    def ship(self, gh):
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        rc = self.fx.pipeline(runner, github=gh, live=live).tick(["worker"])
        return rc, runner

    def released(self, runner):
        return [a[a.index("--release-sha") + 1] for n, a in runner.calls if n == "upload"]

    def assert_shipped(self, rc, runner, target):
        self.assertEqual(rc, 0, self.fx.records()[-1])
        self.assertEqual(self.released(runner), [target])
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], target)
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["sha"]), ("shipped", target))
        return rec

    def test_head_in_progress_ships_the_older_green_commit(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        pr_a = FakeGitHub().pr_number(a)
        # HEAD's PR is unapproved and its CI is red: neither may matter, because
        # the batch now ends at z and every downstream check names z, not HEAD.
        gh = FakeGitHub(canary={z: self.GREEN, a: ("in_progress", None)}, red_ci_prs={pr_a},
                        comments={pr_a: [{"id": 1, "body": "looks fine", "created_at": HEAD_DATE,
                                          "author_association": "OWNER", "html_url": "x"}]})
        rc, runner = self.ship(gh)
        rec = self.assert_shipped(rc, runner, z)
        self.assertEqual(rec["pr"], FakeGitHub().pr_number(z))
        self.assertEqual(rec["pr_head_sha"], pr_head(FakeGitHub().pr_number(z)))
        self.assertNotIn(pr_a, rec["prs"])
        self.assertNotIn(a, [x for _, argv in runner.calls for x in argv])

    def test_head_cancelled_ships_the_older_green_commit(self):
        for conclusion in ("cancelled", "skipped"):
            with self.subTest(conclusion):
                self.tearDown()
                self.setUp()
                z = self.fx.commit({"mcp-server/src/z.js": "1"})
                a = self.fx.commit({"mcp-server/src/a.js": "1"})
                gh = FakeGitHub(canary={z: self.GREEN, a: ("completed", conclusion)})
                self.assert_shipped(*self.ship(gh), z)

    def test_uncanaried_commits_between_are_walked_past(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        b = self.fx.commit({"mcp-server/src/b.js": "1"})
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={z: self.GREEN, b: ("completed", "cancelled"), a: ("queued", None)})
        self.assert_shipped(*self.ship(gh), z)

    def test_a_red_commit_with_no_green_below_blocks(self):
        r = self.fx.commit({"mcp-server/src/r.js": "1"})
        h = self.fx.commit({"mcp-server/src/h.js": "1"})
        gh = FakeGitHub(canary={self.fx.base: self.GREEN, r: self.RED, h: ("in_progress", None)})
        rc, runner = self.ship(gh)
        self.assertEqual(rc, 0)
        self.assertEqual(runner.calls, [])
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["reason"]), ("blocked", "canary_red"))
        self.assertIn(r[:12], rec["detail"])
        self.assertNotIn("last_released_sha", self.fx.state().get("worker", {}))

    def test_a_red_commit_ships_only_the_green_below_it_and_nothing_past_it(self):
        g = self.fx.commit({"mcp-server/src/g.js": "1"})
        r = self.fx.commit({"mcp-server/src/r.js": "1"})
        h = self.fx.commit({"mcp-server/src/h.js": "1"})
        gh = FakeGitHub(canary={g: self.GREEN, r: self.RED, h: ("in_progress", None)})
        self.assert_shipped(*self.ship(gh), g)
        # next tick: HEAD still unverified, the red commit is now the whole story
        rc, runner = self.ship(gh)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_red")
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], g)
        # a newer red HEAD still ships nothing past the red commits
        gh.canary[h] = self.RED
        rc, runner = self.ship(gh)
        self.assertEqual(runner.calls, [])
        self.assertEqual(self.fx.records()[-1]["reason"], "canary_red")

    def test_a_green_fix_forward_above_a_red_commit_ships(self):
        r = self.fx.commit({"mcp-server/src/r.js": "1"})
        f = self.fx.commit({"mcp-server/src/r.js": "fixed"})
        gh = FakeGitHub(canary={r: self.RED, f: self.GREEN})
        self.assert_shipped(*self.ship(gh), f)

    def test_only_ignored_commits_since_the_release_ship_head_as_before(self):
        # dealroom/*.md is a release path AND canary-ignored: no canary runs,
        # and the verdict is the last released commit's own green run.
        self.fx.commit({"dealroom/a.md": "1"})
        head = self.fx.commit({"dealroom/b.md": "2"})
        gh = FakeGitHub(canary={self.fx.base: self.GREEN})
        self.assert_shipped(*self.ship(gh), head)

    def test_ignored_commits_directly_above_the_green_one_ride_along(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        d = self.fx.commit({"docs/n.md": "x"})
        self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={z: self.GREEN, d: ("completed", "cancelled")})
        self.assert_shipped(*self.ship(gh), d)

    def test_no_green_newer_than_the_last_release_holds(self):
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        b = self.fx.commit({"mcp-server/src/b.js": "1"})
        gh = FakeGitHub(canary={self.fx.base: self.GREEN, a: ("completed", "cancelled"),
                                b: ("in_progress", None)})
        rc, runner = self.ship(gh)
        self.assertEqual((rc, runner.calls), (0, []))
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["reason"], rec["sha"]), ("blocked", "canary_pending", b))
        self.assertNotIn("last_released_sha", self.fx.state().get("worker", {}))

    def test_a_failed_target_is_not_retried_while_head_is_unverified(self):
        z = self.fx.commit({"mcp-server/src/z.js": "1"})
        a = self.fx.commit({"mcp-server/src/a.js": "1"})
        gh = FakeGitHub(canary={z: self.GREEN, a: ("in_progress", None)})
        verbs: list = []
        self.assertEqual(self.fx.pipeline(FakeRunner(fail_at="upload"), github=gh, verbs=verbs)
                         .tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], z)
        b = self.fx.commit({"mcp-server/src/b.js": "1"})     # a fix-forward, not yet canaried
        gh.canary[b] = ("in_progress", None)
        again = FakeRunner()
        self.assertEqual(self.fx.pipeline(again, github=gh, verbs=verbs).tick(["worker"]), 0)
        self.assertEqual(again.calls, [])
        self.assertEqual(len(verbs), 1)
        gh.canary[b] = self.GREEN                             # the fix-forward goes green
        self.assert_shipped(*self.ship(gh), b)


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
        # The slice marker follows Worker releases only: the app lane records
        # no ops.release row for membership to attach to.
        self.assertEqual(self.fx.slice_marks, [])
        self.assertNotIn("slice_marker", self.fx.records()[-1])


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
        # Crashing at staging-prepare, which only runs AFTER the health
        # baseline has already succeeded and self.mutated is already True
        # (point 1 of the third round of review moved venv-link AND npm-ci
        # to run BEFORE the baseline, and neither of those sets
        # self.mutated on its own — see the next test). staging-prepare is
        # the first step downstream of a successful baseline, so a crash
        # here is unambiguously "after a mutation."
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        orig = runner.run

        def run(argv, **kw):
            if any("staging-project-replacement.py" in a for a in argv) and "prepare" in argv:
                raise OSError("disk full")
            return orig(argv, **kw)
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual(self.fx.records()[-1]["step"], "unexpected")

    def test_unexpected_error_during_npm_ci_is_not_yet_a_mutation(self):
        # Point 1 of the third round of review: venv-link and npm-ci now
        # run BEFORE the health baseline, and neither sets self.mutated —
        # "neither touches production state, so the SHA isn't consumed"
        # (the coordinator's own framing). An npm-ci crash is therefore
        # still a clean, pre-mutation error: retried next tick, no
        # failed_sha, same as a crash before the worktree even existed.
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        orig = runner.run

        def run(argv, **kw):
            if argv[:2] == ["npm", "ci"]:
                raise OSError("disk full")
            return orig(argv, **kw)
        runner.run = run  # type: ignore[method-assign]
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        self.assertNotIn("failed_sha", self.fx.state().get("worker", {}))
        self.assertEqual(self.fx.records()[-1]["status"], "error")

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

    def test_rejected_token_fails_and_dispatches_before_any_worktree(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner(wrangler_out="You are not authenticated. Please run `wrangler login`.")
        self.assertEqual(self.fx.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self.assertEqual(runner.names(), ["wrangler-auth"])
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertEqual(self.fx.records()[-1]["step"], "credential-missing")
        self.assertIn("credential rejected", self.fx.records()[-1]["detail"])
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])
        self.assertIn("credential rejected", verbs[0][1]["blocker_detail"])
        self.assertTrue(self.fx.records()[-1]["loop_filed"])


class DeployCredential(unittest.TestCase):
    """CLOUDFLARE_API_TOKEN comes from $HOME/.config/carr/tokens.env into the
    wrangler-running steps' env only. Every case runs under a temporary HOME,
    so the operator's real credential files are never read."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.home = tmp / "home"
        self.cred = self.home / ".config" / "carr"
        self.cred.mkdir(parents=True)
        self.fx = Fixture(tmp)
        for name in ("db.env", "mcp-tokens.env"):
            (self.cred / name).write_text((self.fx.cred / name).read_text())
        self._home = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._home.start()
        self.lines: list[str] = []

    def tearDown(self):
        self._home.stop()
        self._tmp.cleanup()

    def pipeline(self, runner, *, verbs=None, dry_run=False, app=False):
        cfg = self.fx.config(credential_dir="~/.config/carr")
        if app:
            cfg["worker"]["enabled"] = False
            cfg["app"]["enabled"] = True
            cfg["app"]["review_required_after"] = "2000-01-01T00:00:00Z"
        pipe = self.fx.pipeline(runner, cfg=cfg, verbs=verbs, dry_run=dry_run)
        pipe.out = self.lines.append
        return pipe

    def write_tokens(self, text: str) -> None:
        (self.cred / "tokens.env").write_text(text)

    def assert_never_echoed(self):
        self.assertFalse(any(CF_TOKEN in line for line in self.lines), "token printed")
        out = self.fx.repo / "out"
        for f in out.rglob("*") if out.exists() else []:
            if f.is_file():
                self.assertNotIn(CF_TOKEN, f.read_text(errors="replace"), f"token written to {f}")

    def test_token_reaches_only_the_wrangler_steps(self):
        self.write_tokens(f"# deploy\nOTHER=x\nexport CLOUDFLARE_API_TOKEN='{CF_TOKEN}'\n")
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live, cfg=self.fx.config(credential_dir="~/.config/carr"))
                         .tick(["worker"]), 0)
        self.assertEqual(self.fx.state()["worker"]["last_released_sha"], sha)
        for step in ("wrangler-auth", "upload", "staging", "promote"):
            self.assertEqual(runner.envs[step].get("CLOUDFLARE_API_TOKEN"), CF_TOKEN, step)
        for step in ("worktree", "npm-ci", "staging-prepare", "migrate-plan"):
            self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs[step], step)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", rp.child_env({"CLOUDFLARE_API_TOKEN": "x", "HOME": "/h"}))
        self.assert_never_echoed()

    def _assert_credential_failure(self, verbs, runner, sha):
        self.assertEqual(runner.calls, [])                       # before wrangler, before any worktree
        rec = self.fx.records()[-1]
        self.assertEqual((rec["status"], rec["step"]), ("failed", "credential-missing"))
        self.assertIn("credential missing: CLOUDFLARE_API_TOKEN", rec["detail"])
        self.assertIn(str(self.cred / "tokens.env"), rec["detail"])
        self.assertTrue(rec["dispatched"])
        self.assertTrue(rec["loop_filed"])
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])
        self.assertEqual(verbs[0][1]["blocker"], "capability")
        self.assertIn("CLOUDFLARE_API_TOKEN is absent", verbs[0][1]["blocker_detail"])
        self.assertIn("Joe grants it", verbs[0][1]["blocker_detail"])
        self.assertIn("chmod 600", verbs[0][1]["body"])
        self.assertIn("credential-missing", verbs[1][1]["body"])
        self.assertEqual(self.fx.state()["worker"]["failed_sha"], sha)
        self.assertFalse((self.fx.repo / "out/release-pipeline/worktrees").exists()
                         and any((self.fx.repo / "out/release-pipeline/worktrees").iterdir()))
        self.assertTrue(any("credential missing" in line for line in self.lines))

    def test_missing_file_fails_loudly_and_dispatches(self):
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner()
        self.assertEqual(self.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self._assert_credential_failure(verbs, runner, sha)

    def test_file_without_the_key_fails_loudly_and_dispatches(self):
        self.write_tokens("SOMETHING_ELSE=v\nCLOUDFLARE_API_TOKEN=\n")
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        verbs: list = []
        runner = FakeRunner()
        self.assertEqual(self.pipeline(runner, verbs=verbs).tick(["worker"]), 1)
        self._assert_credential_failure(verbs, runner, sha)

    def test_app_lane_release_gets_the_token_and_fails_without_it(self):
        self.write_tokens(f"CLOUDFLARE_API_TOKEN={CF_TOKEN}\n")
        sha = self.fx.commit({"src/worker.js": "1"})
        runner = FakeRunner()
        pipe = self.pipeline(runner, app=True)
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
        self.assertEqual(runner.envs["app-release"].get("CLOUDFLARE_API_TOKEN"), CF_TOKEN)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", runner.envs["app-npm-ci"])
        self.assert_never_echoed()

        (self.cred / "tokens.env").write_text("")
        sha2 = self.fx.commit({"src/worker.js": "2"})
        verbs: list = []
        runner2 = FakeRunner()
        pipe2 = self.pipeline(runner2, verbs=verbs, app=True)
        pipe2.http = lambda _u: live
        self.assertEqual(pipe2.tick(["app"]), 1)
        self.assertEqual(runner2.calls, [])
        self.assertEqual(self.fx.records()[-1]["step"], "credential-missing")
        self.assertEqual(self.fx.state()["app"]["failed_sha"], sha2)
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn"])

    def test_missing_token_files_its_loop_once_across_shas(self):
        verbs: list = []
        for i in range(2):
            self.fx.commit({"mcp-server/src/a.js": str(i)})
            self.assertEqual(self.pipeline(FakeRunner(), verbs=verbs).tick(["worker"]), 1)
        self.assertEqual([v for v, _ in verbs], ["add-loop", "add-room-turn", "add-room-turn"])
        self.assertNotIn("loop_filed", self.fx.records()[-1])
        self.assertIn("CLOUDFLARE_API_TOKEN", self.fx.state()["filed_blockers"])

    def test_dry_run_reports_the_missing_token_and_records_nothing(self):
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.pipeline(runner, dry_run=True).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])
        self.assertTrue(any("would FAIL here: credential missing" in line for line in self.lines))
        self.assertEqual(self.fx.records(), [])

    def test_read_env_value(self):
        f = self.cred / "t.env"
        f.write_text("A=1\nexport B=\"two\"\nC='3'\nB=last\nD=\n")
        self.assertEqual(rp.read_env_value(f, "A"), "1")
        self.assertEqual(rp.read_env_value(f, "B"), "last")
        self.assertEqual(rp.read_env_value(f, "C"), "3")
        self.assertIsNone(rp.read_env_value(f, "D"))
        self.assertIsNone(rp.read_env_value(f, "E"))
        self.assertIsNone(rp.read_env_value(self.cred / "absent.env", "A"))


class FailClosedVenv(Base):
    """The coordinator's own re-run of health-preflight at a8619391 caught
    this: with no real `.venv` on the machine, `ln -s <repo>/.venv
    <worktree>/.venv` happily created a DANGLING symlink anyway, and the
    health read then reported 5 hard errors for missing psycopg/openpyxl —
    reading exactly like a code bug when it was really this machine's
    environment never having a venv at all. `_release_worktree_ready` (used
    by both release_worker and health_preflight) now checks the SOURCE
    venv's `bin/python` before linking and fails closed with a clear
    message naming the real cause, instead of producing a dangling link
    whose failure mode looks unrelated."""

    def test_release_worker_fails_closed_when_the_source_venv_is_missing(self):
        (self.fx.repo / ".venv" / "bin" / "python").unlink()
        self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner).tick(["worker"]), 1)
        rec = self.fx.records()[-1]
        self.assertEqual(rec["step"], "venv-link")
        self.assertIn(".venv", rec.get("detail", ""))
        self.assertIn("bin/python", rec.get("detail", ""))
        # the real `ln -s` never ran — no dangling symlink was created.
        self.assertNotIn("venv-link", runner.names())
        self.assertNotIn("npm-ci", runner.names())

    def test_release_worker_still_ships_when_the_source_venv_is_present(self):
        # The fixture's stub `.venv/bin/python` (see Fixture.__init__) is
        # exactly what a real checkout has; this is the control case
        # proving the fail-closed check does not false-positive on a
        # perfectly fine venv.
        self.fx.commit({"mcp-server/src/a.js": "1"})
        live = {"sha": self.fx.base}
        runner = FakeRunner(live=live)
        self.assertEqual(self.fx.pipeline(runner, live=live).tick(["worker"]), 0)
        self.assertIn("venv-link", runner.names())

    def test_health_preflight_fails_closed_when_the_source_venv_is_missing(self):
        (self.fx.repo / ".venv" / "bin" / "python").unlink()
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        pipe = self.fx.pipeline(runner)
        lines: list[str] = []
        pipe.out = lines.append
        self.assertEqual(pipe.health_preflight(sha), 1)
        text = "\n".join(lines)
        self.assertIn(".venv", text)
        self.assertIn("bin/python", text)
        self.assertNotIn("health-baseline", runner.names())
        # the throwaway worktree was still cleaned up on this failure.
        self.assertFalse(pipe.worktrees)

    def test_dry_run_never_touches_the_real_filesystem_for_the_venv_check(self):
        # A dry run must stay purely descriptive — it must not fail closed
        # (or succeed) based on the real machine's venv state, the same
        # contract every other dry-run step already has.
        (self.fx.repo / ".venv" / "bin" / "python").unlink()
        sha = self.fx.commit({"mcp-server/src/a.js": "1"})
        runner = FakeRunner()
        self.assertEqual(self.fx.pipeline(runner, dry_run=True).tick(["worker"]), 0)
        self.assertEqual(runner.calls, [])


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


class SchemaSnapshotGhRunner(FakeRunner):
    """FakeRunner with a stubbed `gh pr list` / `gh pr close`: an in-memory
    set of open PRs, and chosen PR numbers whose close exits nonzero or
    raises."""

    def __init__(self, open_prs: dict[int, str], new_pr: int, *, close_rc: dict | None = None,
                 close_raises: set | None = None, list_rc: int = 0, forks: set | None = None,
                 pr_create_out: str | None = None, no_fork_flag: set | None = None):
        super().__init__()
        self.no_fork_flag = no_fork_flag or set()   # rows listed WITHOUT isCrossRepository
        self.forks = forks or set()          # PR numbers whose head lives in a fork
        self.pr_create_out = pr_create_out   # override what `gh pr create` prints
        self.open_prs, self.new_pr = dict(open_prs), new_pr
        self.close_rc, self.close_raises = close_rc or {}, close_raises or set()
        self.list_rc = list_rc
        self.closed: list[tuple[int, str]] = []

    def run(self, argv, *, cwd, log, env, timeout=3600):
        if argv[:3] == ["gh", "pr", "list"]:
            self.calls.append(("gh-pr-list", list(argv)))
            assert "isCrossRepository" in argv[argv.index("--json") + 1], "fork flag not requested"
            rows = [{"number": n, "headRefName": h, "isCrossRepository": n in self.forks}
                    for n, h in self.open_prs.items()]
            for row in rows:
                if row["number"] in self.no_fork_flag:
                    del row["isCrossRepository"]
            return rp.Result(self.list_rc, json.dumps(rows) if self.list_rc == 0 else "boom")
        if argv[:3] == ["gh", "pr", "close"]:
            self.calls.append(("gh-pr-close", list(argv)))
            num = int(argv[3])
            if num in self.close_raises:
                raise OSError("gh vanished")
            rc = self.close_rc.get(num, 0)
            if rc == 0:
                self.closed.append((num, argv[argv.index("--comment") + 1]))
                self.open_prs.pop(num, None)
            return rp.Result(rc, "")
        res = super().run(argv, cwd=cwd, log=log, env=env, timeout=timeout)
        if log.stem.endswith("schema-pr") and res.rc == 0:
            self.open_prs[self.new_pr] = argv[argv.index("--head") + 1]
            if self.pr_create_out is not None:
                return rp.Result(0, self.pr_create_out)
            return rp.Result(0, f"https://example.invalid/o/r/pull/{self.new_pr}\n")
        return res


class SchemaSnapshotSupersede(Base):
    """Every production release that applied migrations opens a cumulative
    `release/schema-snapshot-*` PR. Nothing merges them automatically, so the
    newest must close every older open one (close only: never merge, never
    label, never delete a branch), and a close failure must never fail the
    already-shipped release."""

    SHA = "abcdef0123456789abcdef0123456789abcdef01"
    NEW = 120
    OLDER = {101: "release/schema-snapshot-11111111", 108: "release/schema-snapshot-22222222"}
    OTHER = {110: "feature/unrelated", 111: "release/other-thing", 112: "schema-snapshot-lookalike"}

    def _followup(self, runner):
        pipe = self.fx.pipeline(runner)
        wt = self.fx.tmp / "release-wt"
        (wt / "db").mkdir(parents=True)
        (wt / "db" / "schema.sql").write_text("-- snapshot\n")
        (pipe.store.root / "worktrees" / f"schema-{self.SHA[:12]}" / "db").mkdir(parents=True)
        return pipe, pipe.schema_followup(wt, self.SHA)

    def test_older_snapshots_close_new_stays_open_others_untouched(self):
        runner = SchemaSnapshotGhRunner({**self.OLDER, **self.OTHER}, self.NEW)
        pipe, url = self._followup(runner)
        self.assertTrue(url.endswith(f"/pull/{self.NEW}"))
        self.assertEqual(sorted(n for n, _ in runner.closed), [101, 108])
        self.assertEqual(pipe.schema_superseded_closed, [101, 108])
        for _, comment in runner.closed:
            self.assertEqual(comment, f"Superseded by #{self.NEW}, which carries the cumulative "
                                      "production schema snapshot.")
        # the new PR stays open and every non-snapshot PR is never touched
        self.assertIn(self.NEW, runner.open_prs)
        closes = [a for n, a in runner.calls if n == "gh-pr-close"]
        touched = {int(a[3]) for a in closes}
        self.assertNotIn(self.NEW, touched)
        self.assertFalse(touched & set(self.OTHER))
        # close only: no merge, no label, no branch deletion, and only after
        # the new PR was created
        flat = [" ".join(a) for _, a in runner.calls]
        for banned in ("pr merge", "--add-label", "carr-automerge-pilot", "--delete-branch",
                       "push origin --delete", "branch -D"):
            self.assertFalse(any(banned in c for c in flat), banned)
        names = runner.names()
        self.assertLess(names.index("schema-pr"), names.index("gh-pr-list"))
        self.assertLess(max(i for i, n in enumerate(names) if n == "gh-pr-close"),
                        names.index("schema-worktree-remove"))

    def test_a_close_failure_is_logged_and_the_step_still_succeeds(self):
        runner = SchemaSnapshotGhRunner({**self.OLDER, 105: "release/schema-snapshot-33333333"}, self.NEW,
                                        close_rc={101: 1}, close_raises={105})
        pipe, url = self._followup(runner)   # must not raise
        self.assertTrue(url.endswith(f"/pull/{self.NEW}"))
        self.assertEqual(pipe.schema_superseded_closed, [108])
        self.assertIn("schema-worktree-remove", runner.names())

    def test_a_failed_listing_closes_nothing_and_does_not_fail(self):
        runner = SchemaSnapshotGhRunner(self.OLDER, self.NEW, list_rc=1)
        pipe, _ = self._followup(runner)
        self.assertEqual(pipe.schema_superseded_closed, [])
        self.assertNotIn("gh-pr-close", runner.names())

    def test_a_fork_pr_with_the_snapshot_branch_name_stays_open(self):
        # The repo is public: a fork may name its head release/schema-snapshot-*.
        runner = SchemaSnapshotGhRunner({**self.OLDER, 130: "release/schema-snapshot-99999999"}, self.NEW,
                                        forks={130})
        pipe, _ = self._followup(runner)
        self.assertEqual(pipe.schema_superseded_closed, [101, 108])
        self.assertIn(130, runner.open_prs)
        self.assertNotIn(130, {int(a[3]) for n, a in runner.calls if n == "gh-pr-close"})

    def test_a_snapshot_pr_without_a_readable_fork_flag_stays_open(self):
        # Fail closed: only an explicit isCrossRepository=false is closeable.
        runner = SchemaSnapshotGhRunner({**self.OLDER, 131: "release/schema-snapshot-88888888"}, self.NEW,
                                        no_fork_flag={131})
        pipe, _ = self._followup(runner)
        self.assertEqual(pipe.schema_superseded_closed, [101, 108])
        self.assertIn(131, runner.open_prs)

    def test_unreadable_pr_create_output_closes_nothing(self):
        for out in ("", "created, but no URL here\n", "https://example.invalid/o/r/pull/abc\n"):
            with self.subTest(out=out):
                runner = SchemaSnapshotGhRunner(self.OLDER, self.NEW, pr_create_out=out)
                pipe = self.fx.pipeline(runner)
                wt = self.fx.tmp / f"release-wt-{abs(hash(out))}"
                (wt / "db").mkdir(parents=True)
                (wt / "db" / "schema.sql").write_text("-- snapshot\n")
                fwt = pipe.store.root / "worktrees" / f"schema-{self.SHA[:12]}"
                (fwt / "db").mkdir(parents=True, exist_ok=True)
                pipe.schema_followup(wt, self.SHA)   # must not raise
                self.assertEqual(pipe.schema_superseded_closed, [])
                self.assertNotIn("gh-pr-close", runner.names())
                self.assertEqual(set(runner.open_prs), set(self.OLDER) | {self.NEW})

    def _direct(self, open_prs):
        """close_superseded_schema_prs on its own, with a fixed PR list, so a
        row can share ONE identity field with the new PR but not the other."""
        runner = SchemaSnapshotGhRunner(open_prs, self.NEW)
        pipe = self.fx.pipeline(runner)
        new_branch = f"{rp.SCHEMA_SNAPSHOT_PREFIX}{self.SHA[:8]}"
        closed = pipe.close_superseded_schema_prs(self.fx.tmp, f"https://example.invalid/o/r/pull/{self.NEW}",
                                                  new_branch)
        return runner, closed, new_branch

    def test_the_branch_exclusion_alone_protects_the_new_pr(self):
        # Kills the mutant that drops only `headRefName != new_branch`: a row
        # on the new branch under a DIFFERENT number must stay open.
        new_branch = f"{rp.SCHEMA_SNAPSHOT_PREFIX}{self.SHA[:8]}"
        runner, closed, _ = self._direct({**self.OLDER, self.NEW + 1: new_branch})
        self.assertEqual(closed, [101, 108])
        self.assertIn(self.NEW + 1, runner.open_prs)

    def test_the_number_exclusion_alone_protects_the_new_pr(self):
        # Kills the mutant that drops only `number != new_num`: the new PR's
        # NUMBER under a different snapshot branch name must stay open.
        runner, closed, _ = self._direct({**self.OLDER, self.NEW: "release/schema-snapshot-deadbeef"})
        self.assertEqual(closed, [101, 108])
        self.assertIn(self.NEW, runner.open_prs)

    def test_a_failed_pr_create_closes_nothing(self):
        runner = SchemaSnapshotGhRunner(self.OLDER, self.NEW)
        runner.fail_at = "schema-pr"
        with self.assertRaises(rp.StepFailed):
            self._followup(runner)
        self.assertNotIn("gh-pr-list", runner.names())
        self.assertEqual(runner.closed, [])


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

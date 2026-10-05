#!/usr/bin/env python3
"""Offline registry, attribution and caller-inventory regressions.

Vendor/Worker transports and receipt destinations are injected fixtures.
Universal budgets and billing hold are tested by jev-spend-authority-selftest.py
against disposable PostgreSQL and by the Worker's fake-vendor unit tests.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import multiprocessing
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env
ENV = fixture_env()

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("typesafe_client_sites", REPO / "ops" / "typesafe_client.py")
assert SPEC and SPEC.loader
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
REGISTRY_PATH = REPO / "ops" / "config" / "jev-call-sites.v1.json"


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ANSWER = {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}},
          "usage": {"input_tokens": 10, "output_tokens": 2}}


def site(caller, **overrides):
    entry = {"caller": caller, "trigger": "fixture", "runs_in": "fixture",
             "attribution": "session", "unattended": "off", "hourly_budget": 50,
             "daily_budget": 500, "owner": "orchestrator", "value": "fixture",
             "sources": ["ops/typesafe_client.py"]}
    entry.update(overrides)
    return entry


class Harness(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = root
        self.log = root / "calls.jsonl"
        self.registry = root / "sites.json"
        self.write_registry([site("hook_site"), site("job_site", attribution="session_or_job"),
                             site("brief:*", unattended="allowed")], hourly=100)
        self.enterContext(patch.object(client, "JEV_DAILY_CAP_LOG", str(self.log)))
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(self.registry)))
        self.enterContext(patch.dict(client.JEV_COST_CONFIG, daily_paid_call_cap=1000))
        self.requests = []

        def opener(request, timeout=None):
            self.requests.append(request)
            return FakeResponse(json.dumps(ANSWER).encode())
        self.enterContext(patch.object(urllib.request, "urlopen", opener))
        def worker(state, questions, **options):
            # The canonical JS predicate is the fixture's Worker admission too.
            attribution = {k: options.get(k) for k in ('caller', 'session_id', 'job_id', 'unattended')}
            attribution['session_id'] = None if attribution['session_id'] == 'unbound' else attribution['session_id']
            try:
                registry = json.loads(Path(client.JEV_CALL_SITES_PATH).read_text())
            except ValueError:
                return None, 'call_site_registry_invalid'
            script = """import {jevCallSite,costPolicy} from './mcp-server/src/jev-spend-authority.js';
                let input='';for await(const chunk of process.stdin)input+=chunk;
                const {who,registry}=JSON.parse(input);
                try {console.log(JSON.stringify({site:jevCallSite(who,registry,costPolicy)}));}
                catch(e){console.log(JSON.stringify({error:e.payload.error}));}"""
            run = subprocess.run(['node', '--input-type=module', '-e', script], cwd=REPO,
                input=json.dumps({'who':attribution,'registry':registry}), capture_output=True, text=True, check=True)
            outcome = json.loads(run.stdout)
            if outcome.get('error'): return None, outcome['error']
            request = urllib.request.Request('https://fixture.invalid', data=json.dumps({
                "state": state, "questions": questions, "model": options["model"]}).encode())
            with urllib.request.urlopen(request, timeout=options["timeout"]) as response:
                answer = json.load(response)
            return {**answer, "server_receipt": {"receipt_id": "offline-worker"}}, None
        self.enterContext(patch.object(client, "server_ask", worker))
        env = {k: v for k, v in os.environ.items()
               if k not in client.SESSION_ID_ENV_KEYS + ("CARR_JEV_JOB", "XPC_SERVICE_NAME", "CARR_JEV_WORKER",
                                                         "CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE")}
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.clock = self.enterContext(patch.object(client, "datetime", wraps=datetime))
        self.now(2026, 10, 4, 3, 10)

    def now(self, *parts):
        self.clock.now.return_value = datetime(*parts, tzinfo=timezone.utc)

    def write_registry(self, sites, hourly=100):
        self.registry.write_text(json.dumps({"schema": "carr-jev-call-sites/v1",
                                             "hourly_paid_call_cap": hourly, "sites": sites}))

    def ask(self, text, caller="hook_site", session="sess-1"):
        return client.ask(text, {"q": client.noul("Fixture judgment")},
                          calls_log=str(self.log), cache_ttl_seconds=0, caller=caller,
                          session_id=session)

    def rows(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]


class RecordingAttributionTests(Harness):
    def test_keyless_controller_execution_does_not_invent_job_attribution(self):
        spec = importlib.util.spec_from_file_location("keyless_controller", REPO / "tools/control-plane.py")
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        workflow = {"execution": {"entrypoint": "bin/nightly.sh", "args": [], "shadow_args": []}}
        with patch.dict(os.environ, {"CARR_JEV_JOB": "unrelated-parent"}), \
                patch.object(controller.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            controller._execute_deterministic(workflow, {}, timeout=10, mode="shadow")
        env = run.call_args.kwargs["env"]
        self.assertNotIn("CARR_JEV_JOB", env)
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(client._job_label())

    def test_controller_nightly_child_can_read_deals_without_agent_environment(self):
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(REGISTRY_PATH)))
        spec = importlib.util.spec_from_file_location("nightly_controller", REPO / "tools/control-plane.py")
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        manifest = json.loads(controller.MANIFEST_PATH.read_text())
        workflow = next(w for w in manifest["workflows"] if w["key"] == "nightly-record-layer")
        with patch.object(controller.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            controller._execute_deterministic(workflow, {"scheduled_for": "2026-10-04T03:00:00Z"},
                                              timeout=10, mode="live")
        env = run.call_args.kwargs["env"]
        self.assertFalse(any(key in env for key in client.SESSION_ID_ENV_KEYS))
        with patch.dict(os.environ, env, clear=True):
            client._refuse_offline("jev_deal_read")
            self.assertEqual(client._job_label(), "nightly-record-layer")
            spec = importlib.util.spec_from_file_location("jev_deal_read", REPO / "ops/jev_deal_read.py")
            reader = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(reader)
            bundle = {"has_evidence": True, "evidence_chars": 900, "name": "Fixture deal",
                      "client": "Fixture practice", "phase": "research", "deal_type": "startup",
                      "segment": "Dental", "city": "Fixture city", "owner": "fixture",
                      "next_step": "Review terms", "next_step_due": None,
                      "status_narrative": "Terms discussed", "history": [], "days_since_record_touched": 1}
            answers = {"movement": {"type": "score", "score": 1.0, "confidence": 0.9},
                       "waiting_on": {"type": "choice", "choice": "client", "confidence": 0.9},
                       "silence_is_bad": {"type": "noul", "noul": 0.1}}
            def opener(request, timeout=None):
                self.requests.append(request)
                return FakeResponse(json.dumps({**ANSWER, "answers": answers}).encode())
            with patch.object(reader, "ts", client), patch.object(urllib.request, "urlopen", opener), \
                    patch.dict(client.ask.__kwdefaults__, calls_log=str(self.log)):
                reading = reader.read_deal(bundle, api_key="offline-fixture")
            self.assertTrue(reading["judged"], reading.get("reason"))
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(self.rows()[-1]["job"], "nightly-record-layer")

    def test_quill_post_call_checks_work_without_agent_environment(self):
        self.enterContext(patch.object(client, "JEV_CALL_SITES_PATH", str(REGISTRY_PATH)))
        self.enterContext(patch.dict(os.environ, {"XPC_SERVICE_NAME": "com.digimata.quill"}))
        spec = importlib.util.spec_from_file_location("post_call_jev", REPO / "tools/dictation-rig/bin/post_call_jev.py")
        post = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(post)
        self.enterContext(patch.object(post, "_client", lambda: client))
        answers = {"deal_match": {"type": "choice", "choice": "deal-fixture", "confidence": 0.99},
                   "speaker_right": {"type": "noul", "noul": 0.99},
                   "details_supported": {"type": "noul", "noul": 0.99}}
        def opener(request, timeout=None):
            self.requests.append(request)
            return FakeResponse(json.dumps({**ANSWER, "answers": answers}).encode())
        self.enterContext(patch.object(urllib.request, "urlopen", opener))
        self.enterContext(patch.dict(client.ask.__kwdefaults__, calls_log=str(self.log)))
        pack = {"session": "recording-fixture", "joe_tasks": [
            {"id": "item-fixture", "deal_id": "deal-fixture", "task": "send details", "evidence": "send details"}]}
        post.check_distillation(pack, {"deals": [{"id": "deal-fixture", "name": "Fixture"}]},
                                {"segments": [{"speaker": "Me", "text": "send details"}]})
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("unavailable", pack["joe_tasks"][0]["checks"])
        self.assertTrue(self.rows(), "recording receipts stay in the isolated fixture log")
        self.assertEqual(client._job_label(), "com.digimata.quill")
        with patch.dict(os.environ, {"XPC_SERVICE_NAME": "com.unrelated.service"}):
            self.assertIsNone(client._job_label())


class RegistryAdmissionTests(Harness):
    def test_unregistered_caller_is_refused_before_transport_and_logged_once(self):
        for _ in range(3):
            with self.assertRaises(client.JevCallRefused) as caught:
                self.ask("x", caller="brand_new_burner")
            self.assertEqual(caught.exception.code, "unregistered_caller")
        self.assertEqual(self.requests, [])
        refusals = [r for r in self.rows() if r.get("error") == "unregistered_caller"]
        self.assertEqual(len(refusals), 3, "each Worker refusal retains its own transport receipt")
        self.assertFalse(refusals[0]["ok"])

    def test_refusals_never_count_toward_the_daily_cap_seed(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="brand_new_burner")
        day = "2026-10-04"
        self.assertEqual(client._logged_attempts(str(self.log), day), 0)

    def test_session_site_without_session_is_refused(self):
        with self.assertRaises(client.JevCallRefused) as caught:
            self.ask("x", session=None)
        self.assertEqual(caught.exception.code, "unattributed_call")
        self.assertEqual(self.requests, [])
        self.ask("x", session="sess-1")
        self.assertEqual(len(self.requests), 1)

    def test_job_attribution_comes_from_carr_job_or_launchd_label(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="job_site", session=None)
        with patch.dict(os.environ, XPC_SERVICE_NAME="application.com.apple.Terminal"):
            with self.assertRaises(client.JevCallRefused):
                self.ask("y", caller="job_site", session=None)
        with patch.dict(os.environ, XPC_SERVICE_NAME="com.carr.deal-read"):
            self.ask("z", caller="job_site", session=None)
        with patch.dict(os.environ, CARR_JEV_JOB="release-pipeline"):
            self.ask("w", caller="job_site", session=None)
        self.assertEqual(len(self.requests), 2)
        jobs = [r.get("job") for r in self.rows() if r.get("ok")]
        self.assertEqual(jobs, ["com.carr.deal-read", "release-pipeline"])

    def test_unattended_worker_runs_only_sites_that_allow_it(self):
        with patch.dict(os.environ, CARR_JEV_WORKER="off"):
            with self.assertRaises(client.JevCallRefused) as caught:
                self.ask("x")
            self.assertEqual(caught.exception.code, "unattended_worker_off")
            self.ask("explicit brief judgment", caller="brief:w3_builder")
        self.assertEqual(len(self.requests), 1)

    def test_fixture_and_ci_runs_never_pay_and_never_log(self):
        for flag in ("CARR_JEV_OFFLINE", "CARR_HOOK_FIXTURE"):
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: "1"}):
                with self.assertRaises(client.JevCallRefused) as caught:
                    self.ask("x")
                self.assertEqual(caught.exception.code, "fixture_offline")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.rows(), [], "fixture traffic stays out of the real call log")

    def test_prefix_entry_needs_a_suffix(self):
        with self.assertRaises(client.JevCallRefused):
            self.ask("x", caller="brief:")

    def test_unreadable_registry_admits_nothing(self):
        self.registry.write_text("{")
        with self.assertRaises(client.TypeSafeError):
            self.ask("x")
        self.assertEqual(self.requests, [])





class ChangeTollsPredicateTests(unittest.TestCase):
    def test_repeated_changed_paths_are_pure_and_do_not_transport(self):
        spec = importlib.util.spec_from_file_location('tolls_under_test', REPO/'ops/jev_change_tolls.py')
        tolls = importlib.util.module_from_spec(spec); spec.loader.exec_module(tolls)
        class Explosive:
            def __getattr__(self, name): raise AssertionError(name)
        state = {'files':['ops/ci.sh']}
        first = tolls.owed(state, client=Explosive())
        self.assertEqual(first,tolls.owed(state,client=Explosive()))
        self.assertEqual([n for _,n,_ in first],['ci_sh_reseal','inventory_reseal'])

    def test_collector_reads_changed_content_without_model(self):
        spec = importlib.util.spec_from_file_location('tolls_collector', REPO/'ops/jev_change_tolls.py')
        tolls = importlib.util.module_from_spec(spec); spec.loader.exec_module(tolls)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['git','init','-q',tmp],env=ENV,check=True)
            subprocess.run(['git','remote','add','origin',tmp],cwd=tmp,env=ENV,check=True)
            (root/'fixture.py').write_text('old')
            subprocess.run(['git','add','fixture.py'],cwd=tmp,env=ENV,check=True)
            subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],cwd=tmp,env=ENV,check=True)
            (root/'fixture.py').write_text('first')
            first=tolls.change(base='HEAD',repo=tmp)
            (root/'fixture.py').write_text('second')
            second=tolls.change(base='HEAD',repo=tmp)
            self.assertNotEqual(first['file_content_sha256'],second['file_content_sha256'])


# Files that make up the transport itself, or call it only through a site
# that is registered. Anything else that reaches a paid call must be listed.
INFRASTRUCTURE = {"ops/typesafe_client.py", "ops/jev_judge.py", "tools/judge/interface.py"}


def paid_call_sources():
    """Every tracked non-test Python file that invokes the Jev client or judge()."""
    tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.split()
    found = set()
    for rel in tracked:
        if (rel in INFRASTRUCTURE or "selftest" in rel or "/fixtures/" in rel or rel.startswith("evals/")
                or "/tests/" in rel or rel.startswith("tests/") or Path(rel).name.startswith("test_")):
            continue
        text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
        if not re.search(r"typesafe|jev_judge", text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        paid_methods = {"ask", "_ask_jev", "judge", "server_ask"}
        imported = {alias.asname or alias.name
                    for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    and (node.module or "").split(".")[-1] in {"typesafe_client", "jev_judge"}
                    for alias in node.names if alias.name in paid_methods}
        for node in ast.walk(tree):
            # Over-inclusive on purpose: any .ask()/.judge()/.server_ask() call in
            # a file that loads the client or the judge counts as a paid path.
            if (isinstance(node, ast.Call) and
                    ((isinstance(node.func, ast.Attribute) and node.func.attr in paid_methods) or
                     (isinstance(node.func, ast.Name) and node.func.id in imported))):
                found.add(rel)
                break
    return found


class RegistryCoverageTests(unittest.TestCase):
    def test_scan_covers_paid_imports_and_aliases_without_counting_unrelated_names(self):
        examples = {
            "attribute.py": "import typesafe_client as ts\nts.ask({}, {})",
            "direct.py": "from typesafe_client import ask, noul\nask({}, {'q': noul('fixture')})",
            "alias.py": "from ops.typesafe_client import ask as paid\npaid({}, {})",
            "judge.py": "from jev_judge import judge\njudge({}, {})",
            "judge_alias.py": "from ops.jev_judge import judge as assess\nassess({}, {})",
            "server_alias.py": "from typesafe_client import server_ask as remote\nremote({}, {})",
            "unused.py": "from typesafe_client import ask\nvalue = 1",
            "unrelated.py": "from other import ask\nfrom typesafe_client import noul\nask('fixture')",
        }
        with tempfile.TemporaryDirectory() as root:
            for name, source in examples.items():
                Path(root, name).write_text(source)
            with patch.dict(globals(), REPO=Path(root)), patch.object(
                    subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "\n".join(examples), "")):
                self.assertEqual(paid_call_sources(), set(examples) - {"unused.py", "unrelated.py"})

    def test_production_registry_is_valid(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        self.assertLessEqual(registry["hourly_paid_call_cap"] * 24,
                             client.JEV_COST_CONFIG["daily_paid_call_cap"] * 24)
        self.assertLessEqual(client.JEV_COST_CONFIG["daily_paid_call_cap"], 1000,
                             "global capacity is shared across site allocations")

    def test_every_paid_call_source_is_registered(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        registered = {s for e in registry["sites"].values() for s in e["sources"]}
        missing = sorted(paid_call_sources() - registered)
        self.assertEqual(missing, [], "a new paid Jev call path needs an entry in "
                         "ops/config/jev-call-sites.v1.json (caller, trigger, budgets, owner, value)")

    def test_every_registered_source_exists(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        for entry in registry["sites"].values():
            for rel in entry["sources"]:
                self.assertTrue((REPO / rel).is_file(), f"{entry['caller']}: {rel} does not exist")

    def test_registered_caller_names_match_their_source_module(self):
        registry = client.load_call_sites(REGISTRY_PATH)
        for caller, entry in registry["sites"].items():
            if caller.endswith("*"):
                continue
            stems = {Path(s).stem for s in entry["sources"]}
            named = caller in stems or any(
                caller in (REPO / s).read_text(encoding="utf-8", errors="replace")
                for s in entry["sources"])
            self.assertTrue(named, f"{caller}: the caller is the calling module's file name, "
                                   "or an explicit caller= string in one of its sources")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression tests for canonical retrieval and explicit Drive recovery."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("retrieve_source_boundary", ROOT / "tools/retrieve.py")
assert SPEC and SPEC.loader
retrieve: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retrieve)

checks = 0


def check(label: str, condition: bool) -> None:
    global checks
    if not condition:
        raise AssertionError(label)
    checks += 1


def invoke(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = retrieve.main(args)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


original_query = retrieve.query_store
original_recovery_hits = retrieve.recovery_hits
prior_vault = os.environ.get("CARR_VAULT")
try:
    os.environ["CARR_VAULT"] = "/definitely-not-readable/Google Drive/CARR AI"

    # Exercise the real query builder. The authenticated local-token verb derives
    # joe-local -> Joe server-side; the payload contains no actor/sponsor escape.
    original_run = retrieve.subprocess.run
    auth_call: dict[str, Any] = {}
    def fake_authenticated_run(command, **kwargs):
        auth_call["command"] = command
        auth_call["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"hits": [{"doc_slug": "joe-private", "section_key": "one",
                                          "title": "Personal", "snippet": "retained"}]}),
            stderr="",
        )
    retrieve.subprocess.run = fake_authenticated_run
    trusted_hits = original_query(["personal", "preference"], 4)
    command = auth_call["command"]
    payload = json.loads(command[-1])
    check("canonical query uses the existing authenticated call-verb path",
          command[-2] == "search-doctrine-situations" and command[1].endswith("tools/call-verb.py"))
    check("canonical query sends no caller actor or sponsor",
          set(payload) == {"q", "limit"})
    check("authenticated personal doctrine survives the adapter",
          trusted_hits[0]["doc_slug"] == "joe-private")
    retrieve.subprocess.run = original_run

    retrieve.query_store = lambda words, top: [
        ("runbook", "outage", "Outage", 1.0, "canonical result")
    ]
    retrieve.recovery_hits = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("normal mode entered Drive recovery")
    )
    code, out, err = invoke(["record", "outage"])
    check("normal retrieval succeeds from canonical store", code == 0)
    check("normal retrieval prints the canonical document", "runbook" in out)
    check("normal retrieval never announces or enters recovery", "RECOVERY" not in out + err)

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("offline")

    retrieve.query_store = unavailable
    code, out, err = invoke(["record", "outage"])
    check("normal retrieval fails closed when store is unavailable", code == retrieve.EX_UNAVAILABLE)
    check("normal refusal names the forbidden implicit fallback", "refuses Drive fallback" in err)

    code, _out, err = invoke(["--vault", "/tmp/vault", "record", "outage"])
    check("a vault argument without explicit recovery is rejected", code == 2)
    check("vault refusal tells the operator to select recovery", "pass --recovery" in err)

    seen: dict[str, object] = {}
    retrieve.recovery_hits = lambda vault, query, top, store_available: seen.update(
        vault=vault, query=query, top=top, store_available=store_available
    ) or []
    with tempfile.TemporaryDirectory() as td:
        code, out, err = invoke(["--recovery", "--vault", td, "record", "outage"])
    check("explicit recovery runs after a store outage", code == 0)
    check("explicit recovery is loud", "RECOVERY MODE" in err)
    check("recovery receives the exact explicit path", seen.get("vault") == Path(td))
    check("recovery knows canonical store was unavailable", seen.get("store_available") is False)

    run_sh = (ROOT / "run.sh").read_text()
    retrieve_case = next(line for line in run_sh.splitlines() if line.strip().startswith("retrieve)"))
    check("run.sh does not inject CARR_VAULT into normal retrieval", "CARR_VAULT" not in retrieve_case)
    check("run.sh brief-pack does not inject CARR_VAULT",
          'brief_pack()   { shift; CARR_VAULT=' not in run_sh)
    check("run.sh review-queue does not inject CARR_VAULT",
          'review_queue() { shift; CARR_VAULT=' not in run_sh)

    brief_source = (ROOT / "pipelines/brief_pack.py").read_text()
    review_source = (ROOT / "pipelines/review_queue.py").read_text()
    check("brief-pack resolves CARR_VAULT only after explicit recovery",
          'if a.recovery else None' in brief_source)
    check("normal prebriefs refuse noncanonical calendar exports",
          '"prebriefs" in wanted and not a.recovery' in brief_source
          and "will not produce a degraded brief" in brief_source)
    check("Monday decisions read canonical v_loops",
          "from v_loops" in brief_source and "marker = 'decision'" in brief_source)
    check("review-queue no longer has a Drive vault source",
          "CARR_VAULT" not in review_source and "GoogleDrive-" not in review_source)
    check("review-queue social lane reads canonical v_loops",
          "from v_loops" in review_source and "unblocks = %s" in review_source)

    # Exercise the actual SQL emitted by both normal loop readers.
    class Description:
        def __init__(self, name): self.name = name

    class Cursor:
        def __init__(self, columns, rows):
            self.description = [Description(name) for name in columns]
            self.rows = rows
            self.calls = []
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
        def fetchall(self): return self.rows
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    brief_spec = importlib.util.spec_from_file_location("brief_boundary", ROOT / "pipelines/brief_pack.py")
    assert brief_spec and brief_spec.loader
    brief: Any = importlib.util.module_from_spec(brief_spec)
    brief_spec.loader.exec_module(brief)
    brief.CURRENT_PRINCIPAL = "joe"
    brief_cursor = Cursor(["owner", "title", "body", "unblocks"],
                          [("Joe", "Approve the plan", None, "release")])
    decisions = brief.read_decisions(brief_cursor)
    brief_sql, brief_params = brief_cursor.calls[0]
    check("Monday query binds the established Joe principal", brief_params == ("joe",))
    check("Monday query enforces exact tier/personal scope",
          "tier = 'shared' and personal_to is null" in brief_sql
          and "tier = 'personal' and personal_to = %s" in brief_sql)
    check("Monday query returns the in-scope decision", decisions[0]["question"] == "Approve the plan.")

    review_spec = importlib.util.spec_from_file_location("review_boundary", ROOT / "pipelines/review_queue.py")
    assert review_spec and review_spec.loader
    review: Any = importlib.util.module_from_spec(review_spec)
    review_spec.loader.exec_module(review)
    review_cursor = Cursor(
        ["loop_id", "number", "owner", "title", "body", "marker", "due_on", "source_note"],
        [("loop-1", "17", "Joe", "Review 3 review-drafts", None, "bell", None, "record:17")],
    )
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def cursor(self): return review_cursor
    prior_psycopg = sys.modules.get("psycopg")
    fake_psycopg = ModuleType("psycopg")
    fake_psycopg.connect = lambda _url: Connection()  # type: ignore[attr-defined]
    sys.modules["psycopg"] = fake_psycopg
    try:
        social, _status = review.read_social("postgres://fixture", "joe")
    finally:
        if prior_psycopg is None:
            sys.modules.pop("psycopg", None)
        else:
            sys.modules["psycopg"] = prior_psycopg
    review_sql, review_params = review_cursor.calls[0]
    check("social query binds purpose and the established Joe principal",
          review_params == (review.SOCIAL_UNBLOCKS, "joe"))
    check("social query enforces exact tier/personal scope",
          "tier = 'shared' and personal_to is null" in review_sql
          and "tier = 'personal' and personal_to = %s" in review_sql)
    check("social query returns the in-scope canonical row", social[0]["item_id"] == "social-17")

    # Execute the real scheduled shell path in an isolated fake HOME. Normal
    # failure must write neither a degraded repo brief nor Drive; normal success
    # writes repo-local today.md only; Drive requires explicit recovery+reason.
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        repo = home / "carr-system"
        (repo / "bin").mkdir(parents=True)
        (repo / "out").mkdir()
        (home / ".config/carr").mkdir(parents=True)
        (home / ".config/carr/db.env").write_text("fixture=1\n")
        script = repo / "bin/local-briefs.sh"
        script.write_text((ROOT / "bin/local-briefs.sh").read_text())
        fake_run = repo / "run.sh"
        fake_run.write_text("""#!/bin/zsh
if [ \"$1\" = \"brief-pack\" ]; then
  [ \"${FAKE_BRIEF_FAIL:-0}\" = \"1\" ] && exit 69
  mkdir -p \"$HOME/carr-system/out/brief-pack\"
  print 'one' > \"$HOME/carr-system/out/brief-pack/one-thing.md\"
  print 'claim' > \"$HOME/carr-system/out/brief-pack/claim-card.md\"
  print 'renewal' > \"$HOME/carr-system/out/brief-pack/renewal-shortlist.md\"
fi
exit 0
""")
        fake_run.chmod(0o755)
        vault = home / "Google Drive/CARR AI"
        (vault / "00_Context").mkdir(parents=True)
        base_env = {**os.environ, "HOME": str(home), "CARR_VAULT": str(vault)}
        failed = subprocess.run(["/bin/zsh", str(script)], env={**base_env, "FAKE_BRIEF_FAIL": "1"},
                                text=True, capture_output=True)
        check("scheduled path fails before producing a degraded brief", failed.returncode == 1)
        check("failed scheduled path writes no repo or Drive today document",
              not (repo / "out/brief-pack/today.md").exists()
              and not (vault / "00_Context/today.md").exists())

        normal = subprocess.run(["/bin/zsh", str(script)], env=base_env,
                                text=True, capture_output=True)
        check("normal scheduled success writes canonical document output", normal.returncode == 0
              and (repo / "out/brief-pack/today.md").exists())
        check("normal scheduled success never projects to Drive",
              not (vault / "00_Context/today.md").exists())

        recovery = subprocess.run(
            ["/bin/zsh", str(script), "--recovery"],
            env={**base_env, "CARR_RECOVERY_REASON": "record layer outage exercise"},
            text=True, capture_output=True,
        )
        projected = (vault / "00_Context/today.md").read_text()
        check("Drive projection requires explicit receipted recovery", recovery.returncode == 0
              and projected.startswith("# RECOVERY NONCANONICAL PROJECTION")
              and "RECOVERY brief-pack reason=record layer outage exercise"
                  in (repo / "out/local-briefs.log").read_text())
finally:
    retrieve.query_store = original_query
    retrieve.recovery_hits = original_recovery_hits
    if prior_vault is None:
        os.environ.pop("CARR_VAULT", None)
    else:
        os.environ["CARR_VAULT"] = prior_vault

print(f"retrieve-source-boundary-selftest: {checks} checks passed")

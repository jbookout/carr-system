#!/usr/bin/env python3
"""Regression tests for canonical retrieval and explicit Drive recovery."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
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
          "if not RECOVERY_MODE:" in brief_source
          and "unavailable in normal mode" in brief_source)
    check("Monday decisions read canonical v_loops",
          "from v_loops" in brief_source and "marker = 'decision'" in brief_source)
    check("review-queue no longer has a Drive vault source",
          "CARR_VAULT" not in review_source and "GoogleDrive-" not in review_source)
    check("review-queue social lane reads canonical v_loops",
          "from v_loops" in review_source and "unblocks = %s" in review_source)
finally:
    retrieve.query_store = original_query
    retrieve.recovery_hits = original_recovery_hits
    if prior_vault is None:
        os.environ.pop("CARR_VAULT", None)
    else:
        os.environ["CARR_VAULT"] = prior_vault

print(f"retrieve-source-boundary-selftest: {checks} checks passed")

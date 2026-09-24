"""The V5-F09 census route, rewired to the durable server-attested census store.

WHAT CHANGED, first, because the change is the point.  Until this module was
rewired it returned one frozen ``available: false`` answer, reason
``handle_integrity_unprovable``, owed seam ``durable_signed_census_store_seam``
(PR #984).  That seam now exists: migration 0595 adds the append-only,
database-hash-chained ``ops.workflow_census_record``; ``record-workflow-census``
is its one write door and ``read-workflow-census`` its one read door
(``mcp-server/src/workflow-census.js``); ``ops/workflow-census-writer.py`` runs
the F09 classifier daily under launchd and records what it produced.

WHY THE OLD ROUTE WAS DELETED, compressed.  Ten review rounds forged the census
from inside the caller's own process -- a rebound snapshot function, a written
mint registry, a ``__del__`` running between two reads -- because the only
owner of "this is the census the control plane served" was a Python object in
that process.  The owner is now OUTSIDE it: this route never reads the database
through a local handle, never accepts a census from a caller, and takes no
argument.  It asks the deployed Worker for the chain (``./run.sh call
read-workflow-census``, the same credential-less door every script on this Mac
uses) and recomputes every hash itself in ``lib/workflow_census_attestation``.

WHEN IT ANSWERS ``available: true``, and only then, all of these hold:
  * the store's three guard triggers are ENABLE ALWAYS, as the server reports;
  * the chain verifies from row 1 to the latest row (seq, prev_hash, row_hash,
    the latest payload's digest, non-decreasing server times);
  * the chain's head equals the head the Worker recorded in the external anchor
    (a Durable Object outside the database) when it committed that row;
  * EVERY row's principal AND database session role are on the writer lists in
    ``ops/config/workflow-census-attestation.v1.json`` -- config-as-code, never
    the caller and never the server;
  * the latest row is younger than that file's freshness window, measured on the
    DATABASE's clock (``server_now`` from the same answer), never this Mac's.
Otherwise it fails closed: ``handle_integrity_unprovable`` (no answer, a
malformed answer, an empty or truncated chain, an unreachable anchor, missing
config), ``tampered`` (guards not enforced, or chain and anchor disagree),
``chain_break``, ``unknown_writer`` or ``stale``, always with disposition
``not_proven`` and a short ``detail``.  Unprovable never reads as proven.

WHAT ``available: true`` CLAIMS, AND THE LABELS ARE CHOSEN SO IT CANNOT SAY
MORE.  Reason ``census_attested``, disposition ``attested_record_only``, and a
``claim`` sentence: recorded under principal <X> at server time <T>; chain
intact as served and matches the external anchor; not proof that the scheduled
writer job wrote it (the principal is a bearer token's actor, and the local
token on the writer's Mac is readable by anything running as that user), not
proof that the observations inside it are true, and not proof against a
coordinated database-owner plus anchor rewrite.  The A01 label route
(``lib/assurance_health_sources``) still derives no health label from it.

ONE RE-READ, FOR ONE RACE.  The read verb reads the anchor before the chain, so
a census write that commits between the two leaves the chain one row ahead of
the anchor it was served beside.  On exactly that detail
(``anchor_behind_chain``) the route pauses and asks once more; a real rewrite
does not go away on a second read, so the re-read cannot turn tampering into
an attestation.

THE THREAT THIS DOES NOT CLOSE, NAMED.  A caller that rewrites this process's
code (rebinding ``subprocess.run``, or the verifier) can still forge what this
route returns inside that process; the 2026-09-11 amendment puts in-process code
rewriting out of scope.  What it can no longer do is make the STORE say
something it did not record, and any other process re-reading the store sees
the truth.

``CARR_WORKFLOW_CENSUS_OFFLINE=1`` makes the route answer fail-closed without a
network call.  It exists for hermetic suites; it can only ever produce the
unavailable answer, so it opens nothing.
"""
from __future__ import annotations

import functools as _functools
import json as _json
import os as _os
import subprocess as _subprocess
import time as _time
from pathlib import Path as _Path
from types import MappingProxyType as _MappingProxyType
from typing import Any as _Any, Callable as _Callable, Mapping as _Mapping

from lib import workflow_census_attestation as _attestation

__all__ = ["SCHEMA_VERSION", "workflow_truth_census"]

SCHEMA_VERSION = "control-plane-workflow-truth-census.v2"

_REPO = _Path(__file__).resolve().parents[1]
_CONFIG_PATH = _REPO / "ops" / "config" / "workflow-census-attestation.v1.json"
_RUN_SH = _REPO / "run.sh"
_OFFLINE_ENV = "CARR_WORKFLOW_CENSUS_OFFLINE"
_TIMEOUT_SECONDS = 90


def _freeze(value: _Any) -> _Any:
    """Deep-freeze an answer: mappings become read-only proxies, lists tuples."""
    if isinstance(value, dict):
        return _MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _checked_in_config() -> dict[str, _Any]:
    return _attestation.load_config(
        _json.loads(_CONFIG_PATH.read_text(encoding="utf-8")))


def _server_census_answer(max_rows: int) -> tuple[_Any, str | None]:
    """Ask the deployed Worker for the chain.  Returns (answer, failure detail).

    The child gets HOME, PATH and LANG only: no DATABASE_URL, no break-glass
    switch, so ``run.sh call`` can take nothing but its default HTTPS path to
    the Worker, whose read connection this route never holds.
    """
    if _os.environ.get(_OFFLINE_ENV) == "1":
        return None, "offline_by_environment"
    child_env = {"HOME": _os.environ.get("HOME", ""), "PATH": _os.environ.get("PATH", ""),
                 "LANG": _os.environ.get("LANG", "C")}
    try:
        proc = _subprocess.run(
            [str(_RUN_SH), "call", "read-workflow-census", _json.dumps({"max_rows": max_rows})],
            cwd=str(_REPO), env=child_env, capture_output=True, text=True,
            timeout=_TIMEOUT_SECONDS)
    except Exception:
        return None, "server_route_unreachable"
    if getattr(proc, "returncode", 1) != 0:
        return None, "server_route_unreachable"
    try:
        return _json.loads(getattr(proc, "stdout", "") or ""), None
    except ValueError:
        return None, "server_answer_unparseable"


def _one_verdict(transport: _Callable[[int], tuple[_Any, str | None]],
                 config: _Mapping[str, _Any]) -> dict[str, _Any]:
    answer, failure = transport(config["max_chain_rows"])
    if failure is not None:
        return _attestation.refusal(_attestation.REASON_UNPROVABLE, failure)
    return _attestation.verify_census_chain(answer, config)


def _census_answer(transport: _Callable[[int], tuple[_Any, str | None]],
                   config_source: _Callable[[], _Mapping[str, _Any]],
                   pause: _Callable[[], None] = _functools.partial(_time.sleep, 2.0),
                   ) -> _Mapping[str, _Any]:
    """One answer from one transport and one config.  Never raises."""
    try:
        try:
            config = config_source()
        except Exception:
            verdict = _attestation.refusal(_attestation.REASON_UNPROVABLE,
                                           "attestation_config_unavailable")
        else:
            verdict = _one_verdict(transport, config)
            if verdict.get("detail") == "anchor_behind_chain":
                pause()
                verdict = _one_verdict(transport, config)
    except Exception:
        verdict = _attestation.refusal(_attestation.REASON_UNPROVABLE, "route_fault")
    return _freeze({"schema_version": SCHEMA_VERSION, **verdict})


def _bind_census_route(transport: _Callable[[int], tuple[_Any, str | None]],
                       config_source: _Callable[[], _Mapping[str, _Any]]):
    """Build the no-argument route over one transport and one config source.

    MODULE-PRIVATE.  Production binds it once, below, to the Worker and the
    checked-in config.  The negative-path suite binds it to forged server
    answers.  The route closes over all three names, so rebinding any attribute
    of this module afterwards changes nothing about what it returns.
    """
    answer_for = _census_answer

    def workflow_truth_census() -> _Mapping[str, _Any]:
        """THE F09 CENSUS ROUTE: the store's attested census, or a fail-closed answer.

        No argument: nothing a caller holds reaches the verdict.
        """
        return answer_for(transport, config_source)

    return workflow_truth_census


workflow_truth_census = _bind_census_route(_server_census_answer, _checked_in_config)

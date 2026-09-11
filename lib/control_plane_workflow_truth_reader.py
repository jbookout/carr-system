"""READ the V5-F09 workflow census from the control plane that owns it.

WHY THIS IS ITS OWN MODULE.  ``lib/control_plane_workflow_truth`` is the pure
projection: it turns declarations, definitions, acceptances, surfaces and
completion rows into a census and touches nothing outside its arguments.  The
READING those arguments come from used to live inside ``tools/health-check.py``
as a private helper, which meant exactly one surface could perform it -- and any
OTHER consumer had to be handed a census by whoever called it.  A handed census
is the caller's assertion about the control plane, not a reading of it, and the
A01 assurance-health adapter was reachable through exactly that door.

So the reading moved here, where both consumers reach the SAME one:

  * ``tools/health-check.py`` renders the F09 census section from it;
  * ``lib/assurance_health_sources`` binds assurance-health scopes from it,
    performing the read itself rather than accepting a census argument.

WHAT IT READS, and nothing else.  Two checked-in configuration files
(``ops/config/control-plane-workflows.v1.json`` for the declarations and each
workflow's declared ``inventory.owner``, and
``ops/config/control-plane-scheduler-cutover.v1.json`` for the registered
scheduler surfaces and the observation window), plus two read-only SQL queries
through the canonical tap.  It creates no job, no registry and no effect.

UNAVAILABLE IS NOT EMPTY.  Every failure path returns ``available=False`` with
the reason, so a caller prints an absent reading as absent.  A machine with no
database tap, or with no server-derived completion tenant, has an ABSENT
reading, not a failed pipeline, and rule bd4a6d22 forbids printing a chosen
state as a permanent failure.

THE ONLY INPUT IS THE REQUEST TO READ.  This function takes no arguments.  There
is deliberately no parameter through which a caller could supply rows, a census,
a clock or a path: every one of those would be a route by which caller input
became the system's own answer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

__all__ = ["SCHEMA_VERSION", "read_workflow_truth_snapshot"]

SCHEMA_VERSION = "control-plane-workflow-truth-reader.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _query_python() -> str:
    venv = os.path.join(REPO_ROOT, ".venv/bin/python")
    return venv if os.path.exists(venv) else sys.executable


def _read_json(sql: str, timeout: int = 120) -> Any:
    """Run one single-column JSON read through the canonical tap.

    The tap sets ON_ERROR_STOP, so each read that may legitimately refuse gets
    its own call: a Completion Register read without a server-derived tenant
    must not abort the workflow rows beside it.
    """
    proc = subprocess.run(
        [_query_python(), os.path.join(REPO_ROOT, "tools/db-tap.py"), "sql", "/dev/stdin"],
        input=sql, cwd=REPO_ROOT, text=True, capture_output=True, timeout=timeout,
        env={k: v for k, v in os.environ.items() if k != "CARR_VAULT"},
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or "").strip().splitlines()[-1]
                           if (proc.stderr or "").strip() else "query failed")
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("no JSON row returned")


ROWS_SQL = """select json_build_object(
  'definitions', coalesce((select json_agg(json_build_object(
       'key',d.key,'version',d.version,'enabled',d.enabled,
       'execution_contract',d.execution_contract,'legacy_schedule',d.legacy_schedule,
       'legacy_disabled_at',d.legacy_disabled_at)
     order by d.key,d.version) from ops.job_definition d),'[]'::json),
  'acceptances', coalesce((select json_agg(json_build_object(
       'workflow_key',a.workflow_key,'workflow_version',a.workflow_version,
       'mode',a.mode,'status',a.status)
     order by a.workflow_key,a.workflow_version,a.mode,a.status)
     from ops.workflow_acceptance a),'[]'::json),
  'disable_receipts', coalesce((select json_object_agg(r.surface_id,r.receipt_ref)
     from ops.legacy_schedule_disable_receipt r),'{}'::json),
  'observations', coalesce((select json_object_agg(o.surface_id, json_build_object(
       'scheduler_state',o.scheduler_state,'observed_at',o.observed_at))
     from (select distinct on (surface_id) surface_id,scheduler_state,observed_at
             from ops.legacy_schedule_observation_receipt
            order by surface_id,observed_at desc,id desc) o),'{}'::json)
)::text"""

# 0431 derives the tenant from a server setting and RAISES without one. A
# separate call keeps that refusal from aborting the rows above, and the
# refusal is reported as unreadable completion evidence, never as none.
COMPLETION_SQL = """select coalesce((select json_object_agg(p.stable_key, json_build_object(
    'lifecycle_state',p.lifecycle_state,
    'first_path', exists(select 1 from ops.completion_current_observation o
                          where o.subject_id=p.subject_id
                            and o.observation_kind='workflow_trigger'
                            and o.authority_class='authoritative'
                            and o.expires_at>now())))
  from ops.completion_projection p),'{}'::json)::text"""


def read_workflow_truth_snapshot() -> dict[str, Any]:
    """Project the clean-start workflow census for every surface that needs it.

    Returns ``{"available": True, "census": ..., "surfaces": [...],
    "owners": {...}}`` on a completed reading, and ``{"available": False,
    "reason": ...}`` on any refusal.  The three keys beside the census are the
    two the A01 assurance-health seam needs and were read here, in the same
    reading, rather than composed by anybody downstream.
    """
    try:
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        from lib.control_plane_workflow_truth import UNREADABLE, workflow_truth
    except Exception as exc:
        return {"available": False, "reason": f"adapter unavailable ({type(exc).__name__}: {exc})"}
    try:
        with open(os.path.join(REPO_ROOT, "ops/config/control-plane-workflows.v1.json"),
                  encoding="utf-8") as fh:
            manifest = json.load(fh)
        with open(os.path.join(REPO_ROOT, "ops/config/control-plane-scheduler-cutover.v1.json"),
                  encoding="utf-8") as fh:
            registry = json.load(fh)
        max_age = int(registry["observation_max_age_seconds"])
    except Exception as exc:
        return {"available": False,
                "reason": f"checked-in registry unreadable ({type(exc).__name__}: {exc})"}

    try:
        payload = _read_json(ROWS_SQL)
    except Exception as exc:
        return {"available": False,
                "reason": f"control-plane rows unreadable ({type(exc).__name__}: {exc})"}

    completion_error = None
    try:
        completion = _read_json(COMPLETION_SQL)
    except Exception as exc:
        completion = UNREADABLE
        completion_error = f"{type(exc).__name__}: {exc}"

    surfaces = []
    for surface in registry.get("surfaces", []):
        surface_id = str(surface["surface_id"])
        surfaces.append({
            "workflow_key": str(surface["workflow_key"]),
            "workflow_version": int(surface["workflow_version"]),
            "surface_id": surface_id,
            "locator": str(surface["locator"]),
            "scheduler_kind": str(surface["scheduler_kind"]),
            "duplicate_group": surface.get("duplicate_group"),
            "disable_receipt_ref": (payload.get("disable_receipts") or {}).get(surface_id),
            "observation": (payload.get("observations") or {}).get(surface_id),
        })
    try:
        census = workflow_truth(
            declarations=manifest.get("workflows", []),
            definitions=payload.get("definitions", []),
            acceptances=payload.get("acceptances", []),
            surfaces=surfaces,
            completion=completion,
            observation_max_age_seconds=max_age,
            now=datetime.now(timezone.utc),
        )
    except Exception as exc:
        return {"available": False,
                "reason": f"workflow truth refused the inputs ({type(exc).__name__}: {exc})"}
    # The A01 assurance-health seam binds scopes out of THIS reading, so the two
    # inputs it needs beside the census travel with it: the surfaces whose
    # observation receipts are the one controller readback this reading holds,
    # and the owner each workflow declares for itself in the checked-in manifest.
    # Neither is a second reading; both were read above.
    owners = {}
    for declared in manifest.get("workflows", []):
        owner = ((declared.get("inventory") or {}).get("owner")
                 if isinstance(declared.get("inventory"), dict) else None)
        if isinstance(owner, str) and owner.strip():
            owners[f"{declared.get('key')}@v{declared.get('version')}"] = owner
    result: dict[str, Any] = {"available": True, "census": census,
                              "surfaces": surfaces, "owners": owners}
    if completion_error:
        result["completion_error"] = completion_error
    return result

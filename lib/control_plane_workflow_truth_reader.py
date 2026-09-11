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

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import InitVar, dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

__all__ = ["SCHEMA_VERSION", "READING_NOT_MINTED", "READING_PAYLOAD_REPLACED",
           "WorkflowTruthReading", "WorkflowTruthReadingError",
           "is_workflow_truth_reading", "read_workflow_truth_reading",
           "read_workflow_truth_snapshot", "verify_workflow_truth_reading"]

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


# ---------------------------------------------------------------------------
# ONE READING, HANDED TO EVERY SECTION THAT RENDERS IT
#
# THE DEFECT THIS EXISTS FOR.  Two consumers each calling
# ``read_workflow_truth_snapshot()`` perform TWO control-plane reads, so the
# workflow-census section and the assurance-health section of one health run
# could describe two different moments and be printed as one state of the world.
# Passing the first reading to the second consumer as a plain dict would fix the
# moment and reopen the door this module was split out to close: a dict is
# composable, so "the reading" would once again be whatever the caller handed
# over -- the exact route by which a review reproduced a healthy scope out of a
# hand-written census.
#
# SO THE READING TRAVELS AS AN OPAQUE RECEIPT.  ``read_workflow_truth_reading()``
# performs exactly one read and returns a handle this module minted.  The handle
# is registered by IDENTITY -- so a forged instance, a subclass, and an
# ``object.__new__`` shell are all rejected by ``is_workflow_truth_reading`` no
# matter what they contain.  A caller cannot manufacture one; the only way to
# hold a reading is for this module to have performed it.
#
# AND IDENTITY ALONE WAS NOT ENOUGH, which is the correction this block most
# recently took.  A review took a GENUINELY MINTED handle -- one that passed
# every identity check because it really was minted here -- replaced its
# ``_payload`` through ``object.__setattr__``, and projected a complete forged
# census through the A01 adapter as if this module had read it.  Identity proved
# the handle's provenance and said nothing about its CONTENTS, so the door the
# mint closed was reopened one attribute assignment later.
#
# THE CONTENTS ARE NOW BOUND TO THE HANDLE AT MINT TIME.  Three separate
# measures, because each one alone has a way around it:
#
#   1. THE PAYLOAD IS DEEP-IMMUTABLE.  What the handle holds is frozen on the way
#      in -- every mapping becomes a ``MappingProxyType``, every sequence a
#      tuple, all the way down -- so no consumer can reach into a nested dict or
#      list of the reading and edit it in place.  ``rendered()`` thaws a fresh
#      mutable copy for its caller, so what a consumer prints or projects from is
#      still its own and still cannot write back.
#   2. THE HANDLE ITSELF IS FROZEN.  It is a frozen, slotted dataclass: ordinary
#      attribute assignment raises, and there is no ``__dict__`` to grow a second
#      payload beside the first.
#   3. THE CONTENT DIGEST IS THE ONE THAT ACTUALLY BINDS, because 1 and 2 are
#      both reachable through ``object.__setattr__``.  At mint time a sha256 over
#      the canonical form of the payload is recorded in a MODULE-PRIVATE registry
#      keyed by the handle's object id -- deliberately NOT on the handle, where
#      whoever replaced the payload would simply replace the digest beside it.
#      ``rendered()`` and every consumer re-derive the digest from the payload the
#      handle is holding NOW and refuse with ``READING_PAYLOAD_REPLACED`` if it
#      differs from the one bound at mint.  The registry holds a strong reference
#      to each handle, so a minted id can never be reused by a later object.
# ---------------------------------------------------------------------------
_MINT = object()

# id(handle) -> (handle, digest-at-mint).  The handle is kept in the value, not
# merely keyed by, so the object stays alive for the process and its id can never
# be recycled onto something a caller built.
_MINTED: dict[int, tuple["WorkflowTruthReading", str]] = {}

READING_NOT_MINTED = "workflow_truth_reading_not_minted"
READING_PAYLOAD_REPLACED = "workflow_truth_reading_payload_replaced"


class WorkflowTruthReadingError(TypeError):
    """A reading was refused, carrying the machine-readable reason id.

    It subclasses ``TypeError`` because that is what every consumer of this
    module already refuses a non-reading with, and a payload that no longer
    matches the reading this module performed is the same kind of refusal: the
    value is not a reading, whatever it is shaped like.
    """

    def __init__(self, reason_id: str, message: str) -> None:
        self.reason_id = reason_id
        super().__init__(f"{reason_id}: {message}")


def _frozen(value: Any) -> Any:
    """The deep-immutable form of a reading: proxies for mappings, tuples for lists."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def _thawed(value: Any) -> Any:
    """A fresh mutable copy, for a consumer to render or project from."""
    if isinstance(value, Mapping):
        return {key: _thawed(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thawed(item) for item in value]
    return value


def _content_digest(value: Any) -> str:
    """A canonical sha256 over a reading's contents.

    Sorted keys and separators make it independent of dict ordering, and
    ``default=repr`` means a value JSON cannot encode still contributes its own
    identity rather than raising -- an unencodable value must not be a hole a
    replacement could hide in.
    """
    return hashlib.sha256(json.dumps(_thawed(value), sort_keys=True, default=repr,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, eq=False, repr=False)
class WorkflowTruthReading:
    """An opaque receipt for ONE reading THIS module performed.

    There is no public constructor: ``__init__`` refuses without the
    module-private mint token, which is an ``InitVar`` and is therefore never
    stored on the instance for a holder to read back off it.  Every accessor
    re-checks BOTH that this exact object is in the mint registry AND that what
    it is holding still digests to what was read, so neither bypassing
    ``__init__`` nor replacing the payload afterwards yields a reading.

    What it carries is never handed out by reference -- ``rendered()`` returns a
    fresh mutable copy, so a consumer that mutates what it printed cannot change
    what another consumer projects from.
    """

    _mint: InitVar[Any]
    _payload: Any

    def __post_init__(self, _mint: Any) -> None:
        if _mint is not _MINT:
            raise TypeError(
                "a workflow-truth reading is minted by read_workflow_truth_reading(); "
                "it cannot be constructed from caller-supplied data")

    def rendered(self) -> dict[str, Any]:
        """A private copy of this reading, for rendering or projection."""
        verify_workflow_truth_reading(self)
        return _thawed(self._payload)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        payload = getattr(self, "_payload", None)
        available = bool(payload.get("available")) if isinstance(payload, Mapping) else False
        return f"<WorkflowTruthReading available={available}>"


def is_workflow_truth_reading(value: Any) -> bool:
    """True only for a handle minted here for a reading performed here.

    Identity, not shape: the registry entry must be THIS object, which nothing a
    caller builds can satisfy, whatever its type name, attributes or contents.
    It says nothing about the contents -- ``verify_workflow_truth_reading`` is
    what binds those, and every consumer calls it.
    """
    if type(value) is not WorkflowTruthReading:
        return False
    entry = _MINTED.get(id(value))
    return entry is not None and entry[0] is value


def verify_workflow_truth_reading(value: Any) -> None:
    """Refuse unless ``value`` is a minted handle STILL holding what was read.

    The decision procedure, in order, and each step has its own reason id:

      1. Is this object one this module minted?  If not, ``READING_NOT_MINTED``.
      2. Does the payload it is holding right now digest to the digest bound at
         mint?  If not, ``READING_PAYLOAD_REPLACED`` -- the provenance is real and
         the contents are not, which is exactly the forgery identity alone let
         through.

    Returns ``None`` on success; it hands back no payload, so no caller can
    mistake the check for the reading.
    """
    if not is_workflow_truth_reading(value):
        raise WorkflowTruthReadingError(
            READING_NOT_MINTED,
            "a workflow-truth reading is minted by read_workflow_truth_reading(); "
            f"{type(value).__name__} is caller-supplied data")
    bound = _MINTED[id(value)][1]
    try:
        current = _content_digest(object.__getattribute__(value, "_payload"))
    except AttributeError:
        raise WorkflowTruthReadingError(
            READING_PAYLOAD_REPLACED,
            "this handle is no longer holding the reading it was minted for") from None
    if current != bound:
        raise WorkflowTruthReadingError(
            READING_PAYLOAD_REPLACED,
            "this handle was minted for a reading whose contents digested to "
            f"{bound[:12]}, and it is now holding contents that digest to "
            f"{current[:12]}; a replaced payload is caller-supplied data wearing a "
            "genuine receipt, and it is refused for the same reason a forged handle is")


def read_workflow_truth_reading() -> WorkflowTruthReading:
    """Perform exactly ONE reading and hand it back as an opaque receipt.

    Every consumer of a single health run takes its census from one of these, so
    the sections of that run describe ONE moment.  A refused reading is minted
    too -- the receipt then carries ``available=False`` with its reason, which is
    what every consumer prints, rather than one section going silent.
    """
    payload = _frozen(read_workflow_truth_snapshot())
    handle = WorkflowTruthReading(_MINT, payload)
    _MINTED[id(handle)] = (handle, _content_digest(payload))
    return handle

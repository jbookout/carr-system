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

  * ``tools/health-check.py`` performs it once per run and renders the F09
    census section from it;
  * ``lib/assurance_health_sources`` binds assurance-health scopes from THAT
    SAME reading, which it takes as the opaque handle this module minted --
    never as a census argument a caller composed.

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
import secrets
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
# AND IDENTITY ALONE WAS NOT ENOUGH.  A review took a GENUINELY MINTED handle --
# one that passed every identity check because it really was minted here --
# replaced its payload attribute through ``object.__setattr__``, and projected a
# complete forged census through the A01 adapter as if this module had read it.
# Identity proved the handle's PROVENANCE and said nothing about its CONTENTS, so
# the door the mint closed was reopened one attribute assignment later.
#
# NOR WAS A CONTENT DIGEST, and that is the correction this block takes NOW.
# Binding a sha256 of the payload at mint and re-deriving it before every render
# does check the contents -- but the check and the render are two SEPARATE
# traversals of an object a caller can still reach.  A review replaced the
# payload with a STATEFUL mapping that served authentic content while the digest
# was being derived and forged content while the census was being rendered; it
# passed verification on its way to projecting the forgery.  Every answer of the
# form "verify it, then go and read it again" has that shape, and adding a third
# traversal would only move the gap.
#
# SO THE CONTENTS ARE CAPTURED ONCE AND THE HANDLE NEVER HOLDS THEM.
# ``read_workflow_truth_reading()`` traverses the snapshot EXACTLY ONCE, into a
# private deep-immutable value -- a ``MappingProxyType`` over dicts built from
# that single traversal, a tuple for every sequence, all the way down -- and
# records ``key -> (handle, captured value, digest of that captured value)`` in a
# MODULE-PRIVATE registry.  The handle itself carries nothing but the opaque key.
#
# WHAT THAT BUYS, and it is structural rather than one more check: there is no
# payload attribute to replace, because there is no payload attribute; there is
# no second traversal of a caller-reachable object to serve different content to,
# because ``rendered()`` and the A01 adapter thaw their fresh mutable copy out of
# the CAPTURED value in the registry and out of nothing else.  A stateful mapping
# installed anywhere a caller can reach -- on the handle, or inside the snapshot
# source itself -- is simply never read a second time.  The digest is recorded as
# the identity of what was read, not as the thing standing between a caller and a
# forgery.
#
# THE HANDLE IS STILL A PROVENANCE RECEIPT, and that is still checked by IDENTITY.
# The registry entry reached through a handle's key must be THAT handle, so a
# forged instance, a subclass, an ``object.__new__`` shell and a genuine handle
# whose key was reassigned to another reading's are all refused.  The registry
# holds a strong reference to every handle it minted, so an entry outlives its
# caller and a key can never come to name something a caller built.
# ---------------------------------------------------------------------------
_MINT = object()

# opaque key -> (handle, captured reading, digest of that captured reading).
#
# THE CAPTURED READING LIVES HERE AND NOWHERE ELSE.  It is deliberately not an
# attribute of the handle: an attribute is a place a caller can write, and every
# forgery this module has taken went through one.  The handle is kept in the
# entry as well as reached through it, so identity has something to compare
# against and the object stays alive for the life of the process.
_MINTED: dict[str, tuple["WorkflowTruthReading", Any, str]] = {}

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
    """Capture a reading, ONCE, as a deep-immutable value.

    Proxies for mappings, tuples for sequences, all the way down.  This is the
    single traversal of whatever the snapshot came back as: a source that would
    answer differently on a second read never gets one, because everything
    downstream reads the value this returns.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def _thawed(value: Any) -> Any:
    """A fresh mutable copy OF THE CAPTURED READING, to render or project from.

    A consumer that mutates what it was handed changes nothing: the captured
    value it was copied from is immutable and lives in the mint registry.
    """
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
    stored on the instance for a holder to read back off it.

    IT HOLDS NO CONTENTS AT ALL.  Its one field is an opaque key into the
    module-private mint registry, where the reading captured at mint time lives.
    ``rendered()`` thaws its copy from THAT captured value, so there is no
    attribute on this object through which a caller could substitute a reading,
    and nothing is traversed a second time between verifying a handle and
    rendering it.  Every accessor re-checks that the entry the key reaches is
    THIS object, so neither bypassing ``__init__`` nor re-pointing the key
    afterwards yields a reading.
    """

    _mint: InitVar[Any]
    _key: str

    def __post_init__(self, _mint: Any) -> None:
        if _mint is not _MINT:
            raise TypeError(
                "a workflow-truth reading is minted by read_workflow_truth_reading(); "
                "it cannot be constructed from caller-supplied data")

    def rendered(self) -> dict[str, Any]:
        """A private mutable copy of the reading captured for this handle."""
        return _thawed(_captured_reading(self))

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        try:
            captured = _captured_reading(self)
        except WorkflowTruthReadingError:
            return "<WorkflowTruthReading unbound>"
        available = bool(captured.get("available")) if isinstance(captured, Mapping) else False
        return f"<WorkflowTruthReading available={available}>"


def _mint_entry(value: Any) -> tuple["WorkflowTruthReading", Any, str] | None:
    """The mint entry this object is bound to right now, or ``None``.

    Identity, not shape: the entry reached through the handle's own key must be
    THIS object, which nothing a caller builds can satisfy, whatever its type
    name, attributes or contents -- and which a genuine handle re-pointed at
    another reading's key cannot satisfy either.
    """
    if type(value) is not WorkflowTruthReading:
        return None
    try:
        key = object.__getattribute__(value, "_key")
    except AttributeError:
        return None
    if not isinstance(key, str):
        return None
    entry = _MINTED.get(key)
    if entry is None or entry[0] is not value:
        return None
    return entry


def _minted_digest(value: Any) -> str | None:
    """The digest of the reading this object was minted for, whatever key it now carries.

    It is what separates a handle this module never minted from one it did mint
    and which is no longer bound to its own reading -- two different refusals
    with two different reason ids.
    """
    for handle, _captured, digest in _MINTED.values():
        if handle is value:
            return digest
    return None


def is_workflow_truth_reading(value: Any) -> bool:
    """True only for a handle minted here, still bound to the reading it was minted for.

    Identity, not shape.  It cannot be answered incorrectly by contents, because
    a handle carries none: what it is bound to is the captured reading in the
    mint registry, which no caller can reach or replace.
    """
    return _mint_entry(value) is not None


def verify_workflow_truth_reading(value: Any) -> None:
    """Refuse unless ``value`` is a handle STILL bound to the reading it was minted for.

    The decision procedure, in order, and each step has its own reason id:

      1. Does this object's own key reach a mint entry holding THIS object?  Then
         it is a reading, and the contents it renders are the ones captured at
         mint -- there is nothing further to check, because there is nothing
         else it can render.
      2. Otherwise, was this exact object ever minted here?  If it was, its key
         no longer names its own reading: ``READING_PAYLOAD_REPLACED``.  The
         provenance is real and the binding is not, which is the forgery identity
         alone let through.
      3. Otherwise ``READING_NOT_MINTED``: whatever it is shaped like, this
         module never read it.

    Returns ``None`` on success; it hands back no reading, so no caller can
    mistake the check for the contents.
    """
    if _mint_entry(value) is not None:
        return
    bound = _minted_digest(value)
    if bound is not None:
        raise WorkflowTruthReadingError(
            READING_PAYLOAD_REPLACED,
            "this handle was minted for a reading whose contents digest to "
            f"{bound[:12]}, and it is no longer bound to that reading; a handle "
            "pointed at something else is caller-supplied data wearing a genuine "
            "receipt, and it is refused for the same reason a forged handle is")
    raise WorkflowTruthReadingError(
        READING_NOT_MINTED,
        "a workflow-truth reading is minted by read_workflow_truth_reading(); "
        f"{type(value).__name__} is caller-supplied data")


def _captured_reading(value: Any) -> Any:
    """The immutable reading captured at mint for ``value``.  MODULE-PRIVATE.

    Private because handing the captured value out by reference is the one thing
    that would let a consumer's copy and another consumer's copy be the same
    object.  Everything public goes through ``_thawed``.
    """
    verify_workflow_truth_reading(value)
    return _MINTED[object.__getattribute__(value, "_key")][1]


def read_workflow_truth_reading() -> WorkflowTruthReading:
    """Perform exactly ONE reading and hand it back as an opaque receipt.

    Every consumer of a single health run takes its census from one of these, so
    the sections of that run describe ONE moment.  A refused reading is minted
    too -- the receipt then carries ``available=False`` with its reason, which is
    what every consumer prints, rather than one section going silent.

    THE SNAPSHOT IS TRAVERSED EXACTLY ONCE, here, into the captured value the
    registry holds.  The digest is taken from that captured value rather than
    from the snapshot, so the thing digested and the thing every consumer renders
    are the same object and cannot diverge between the two acts.
    """
    captured = _frozen(read_workflow_truth_snapshot())
    key = secrets.token_hex(16)
    handle = WorkflowTruthReading(_MINT, key)
    _MINTED[key] = (handle, captured, _content_digest(captured))
    return handle

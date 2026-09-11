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
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from weakref import WeakKeyDictionary

__all__ = ["SCHEMA_VERSION", "READING_NOT_MINTED", "READING_PAYLOAD_REPLACED",
           "WorkflowTruthReading", "WorkflowTruthReadingError",
           "is_workflow_truth_reading", "read_workflow_truth_reading",
           "read_workflow_truth_snapshot", "render_reading",
           "verify_workflow_truth_reading"]

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
# SEVEN REVIEWS FAILED ON ONE CLASS, so what follows closes the CLASS rather
# than the seventh instance of it.  The class, stated once: ANY STATE A CALLER
# CAN READ OR WRITE ON THE HANDLE IS A LEVER ON WHAT THE HANDLE RENDERS.  Each
# round moved the lever rather than removing it, and each round was beaten:
#
#   1. A plain dict WAS the reading, so the caller composed it outright.
#   2. The handle held the payload, and a review replaced that attribute through
#      ``object.__setattr__``: identity proved PROVENANCE and said nothing about
#      CONTENTS.
#   3. A content digest was bound at mint and re-derived before each render, and
#      a review installed a STATEFUL mapping that served authentic content to the
#      digest traversal and forged content to the render traversal.  Every answer
#      of the form "verify it, then go and read it again" has that shape.
#   4. The contents moved into a private registry and the handle kept only an
#      opaque string key -- and a review installed a ``str`` SUBCLASS whose
#      ``__hash__`` mutated the handle mid-lookup, so the verified entry and the
#      consumed entry were two different readings.
#
# EVERY ONE OF THOSE NEEDED THE HANDLE TO CARRY SOMETHING.  So it carries
# NOTHING.  ``WorkflowTruthReading`` declares ``__slots__ = ()``, refuses
# ``__setattr__`` and ``__delattr__``, refuses to be subclassed, and defines no
# field, no property and no accessor a caller could read a value out of.  There
# is no attribute to replace, no key to re-point, and no lookup input to mutate
# during a lookup, because the only lookup input IS THE OBJECT ITSELF.
#
# THE REGISTRY IS KEYED BY OBJECT IDENTITY.  ``_MINTED`` is a
# ``WeakKeyDictionary`` whose key is the handle object, mapping to the single
# deep-immutable value captured at mint and the digest of that value.  A handle
# is looked up by BEING itself: ``render_reading()`` and the A01 adapter pass the
# object, never a string, an int or anything read off it.
#
# AND THE LOOKUP IS IDENTITY EVEN THOUGH A WEAK MAPPING HASHES ITS KEYS.  A weak
# reference hashes and compares as its referent, so a look-alike class defining
# ``__hash__`` and ``__eq__`` to match a genuine handle would otherwise reach a
# genuine entry.  ``_entry()`` therefore refuses anything whose type is not
# EXACTLY ``WorkflowTruthReading`` before the mapping is touched at all; that
# class defines neither ``__hash__`` nor ``__eq__``, so among the only objects
# that reach the mapping, equality IS identity.  Subclassing is refused at class
# creation so that exact-type test cannot be widened from outside.
#
# A HANDLE THIS MODULE DID NOT MINT IS SIMPLY ABSENT.  There is no shape to
# forge and no state to match: an ``object.__new__`` shell, a duck type, a
# subclass attempt, a deep copy and a pickle round-trip all fail to be a key in
# the registry, and refusal is the mapping having no entry for them rather than a
# check that could be satisfied.
#
# THE CAPTURE IS TRAVERSED EXACTLY ONCE.  ``read_workflow_truth_reading()``
# freezes the snapshot into a value built only from tuples at mint -- immutable in
# fact rather than by interface, because a read-only VIEW over a dict still has a
# writable dict one ``gc.get_referents()`` hop behind it;
# ``render_reading()`` thaws a fresh mutable copy of THAT value and of nothing
# else.  A
# stateful mapping installed in the snapshot source is read once and never again;
# a consumer that mutates its copy changes nothing any other consumer sees.
# ---------------------------------------------------------------------------
_MINT = object()


class _FrozenMapping(tuple):
    """A captured mapping, as an immutable tuple of ``(key, frozen value)`` pairs.

    WHY NOT ``MappingProxyType``, WHICH IS THE OBVIOUS CHOICE.  A proxy is a
    read-only VIEW over a dict that still exists and is still mutable: one
    ``gc.get_referents()`` hop reaches the backing dict and writes through it, and
    the proxy then serves the write.  A tuple has no backing anything.  Capturing
    into tuples of pairs makes the captured reading immutable in fact rather than
    by interface, so "the reading cannot change after mint" holds against
    introspection and not merely against assignment.

    It subclasses ``tuple`` purely so ``_thawed`` can tell a captured MAPPING from
    a captured SEQUENCE, which are otherwise the same shape.
    """

    __slots__ = ()


def _frozen(value: Any) -> Any:
    """Capture a reading, ONCE, as a deep-immutable value.

    Pair tuples for mappings, tuples for sequences, all the way down -- nothing
    in the result has a mutable backing object for anything to write through.
    This is the single traversal of whatever the snapshot came back as: a source
    that would answer differently on a second read never gets one, because
    everything downstream reads the value this returns.
    """
    if isinstance(value, Mapping):
        return _FrozenMapping((key, _frozen(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_frozen(item) for item in value)
    return value


def _thawed(value: Any) -> Any:
    """A fresh mutable copy OF THE CAPTURED READING, to render or project from.

    A consumer that mutates what it was handed changes nothing: the captured
    value it was copied from is immutable and lives in the mint registry.
    """
    if isinstance(value, _FrozenMapping):
        return {key: _thawed(item) for key, item in value}
    if isinstance(value, Mapping):
        return {key: _thawed(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thawed(item) for item in value]
    return value


def _content_digest(value: Any) -> str:
    """A canonical sha256 over a reading's contents.

    It is recorded as the IDENTITY of what was read -- what ``__repr__`` names a
    reading by -- and never as the thing standing between a caller and a forgery:
    that job belongs to the handle carrying no state at all.  Sorted keys and
    separators make it independent of dict ordering, and ``default=repr`` means a
    value JSON cannot encode still contributes its own identity rather than
    raising.
    """
    return hashlib.sha256(json.dumps(_thawed(value), sort_keys=True, default=repr,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


class _WeakReferenceable:
    """The ONE reason this base exists: weak-key registration.

    A ``WeakKeyDictionary`` needs weak-referenceable keys, and ``__slots__ = ()``
    on a direct subclass of ``object`` forbids weak references.  This declares
    exactly one slot -- the interpreter's own weak-reference list, which is
    machinery and not a place any value can be stored or read back -- so the
    handle class below can declare ``__slots__ = ()`` and still be a key.
    """

    __slots__ = ("__weakref__",)


class WorkflowTruthReading(_WeakReferenceable):
    """An opaque receipt for ONE reading THIS module performed.

    IT CARRIES NO CALLER-VISIBLE STATE AT ALL, and that is the whole design.  No
    payload, no key, no digest, no field, no property: ``__slots__`` is empty,
    ``__setattr__`` and ``__delattr__`` refuse, and the class cannot be
    subclassed.  Every forgery this module has taken went through some value a
    caller could read or write on the handle, so there is no longer one.

    WHAT BINDS IT TO A READING is the module-private ``_MINTED`` registry, which
    is keyed by THIS OBJECT.  The module function ``render_reading()`` looks the
    handle up by identity and thaws a copy of the value captured at mint.  A
    handle this module did not mint is not a key in that registry, so it renders
    nothing -- not because a check rejected it, but because there is no entry to
    find.

    AND THE HANDLE DEFINES NO METHOD A CONSUMER CALLS.  It used to carry
    ``rendered()``, and a method is dispatched through the instance: the raw base
    descriptor ``object.__dict__["__class__"].__set__(handle, Forger)`` re-points
    the type slot of an object this module really did mint, and ``handle.rendered()``
    then runs the caller's code.  The trusted path therefore calls NOTHING on the
    handle; it passes the object to ``render_reading()``, whose name resolves on
    this module and cannot be re-pointed by anything a caller does to an instance.

    There is no public constructor: ``__init__`` refuses without the
    module-private mint token, which is never stored.
    """

    __slots__ = ()

    @property  # type: ignore[misc]  # read-only on purpose; see the note below
    def __class__(self) -> Any:
        """Read-only, and it is the LAST piece of writable state an instance had.

        The ``type: ignore`` is the point rather than a wart: mypy objects that a
        read-only property cannot override ``object.__class__``, which is
        read-write, and making it read-only is exactly the correction.

        ``object.__setattr__(handle, "__class__", SomeForger)`` is a write that
        ``__slots__`` does not stop -- the layouts are compatible -- and it
        re-points METHOD DISPATCH: ``handle.rendered()`` then runs the forger's
        code and returns whatever the caller wants, on an object that is still the
        one the reader minted.  The A01 seam refuses afterwards, because
        ``type()`` reads the real type and no longer matches, but
        ``tools/health-check.py`` calls ``rendered()`` directly and would have
        printed the forgery.

        Declaring ``__class__`` as a property without a setter makes that
        assignment an ``AttributeError`` on both paths.  ``type()``, ``isinstance``,
        ``copy`` and ``pickle`` are unaffected: they read the real type slot, and
        this returns the same class they would find.

        WHAT IT DOES NOT STOP, WHICH IS WHY THERE IS NO METHOD LEFT TO RE-POINT.
        A property shadows the ATTRIBUTE, not the base descriptor underneath it:
        ``object.__dict__["__class__"].__set__(handle, SameLayoutForger)`` calls
        that descriptor directly and the type slot changes.  Nothing in a class
        body can close that route.  So the close is elsewhere: the trusted path
        calls no method on a handle at all -- ``render_reading()`` is a module
        function and ``_entry()`` reads the REAL type slot, so a retyped handle is
        refused as not minted rather than rendering the forger's answer.  This
        property is kept because it still costs a caller the cheap routes
        (``setattr`` and ``object.__setattr__``) and makes the expensive one
        visible.
        """
        return WorkflowTruthReading

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError(
            "WorkflowTruthReading is final: the mint registry admits this exact "
            "type and nothing else, and a subclass could define __hash__ and "
            "__eq__ that reach another handle's entry")

    def __init__(self, _mint: Any = None) -> None:
        if _mint is not _MINT:
            raise TypeError(
                "a workflow-truth reading is minted by read_workflow_truth_reading(); "
                "it cannot be constructed from caller-supplied data")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(
            "a workflow-truth reading carries no state: there is nothing on this "
            f"handle to set, and {name!r} would be caller-supplied data wearing a "
            "genuine receipt")

    def __delattr__(self, name: str) -> None:
        raise TypeError(
            "a workflow-truth reading carries no state: there is nothing on this "
            f"handle to delete, and {name!r} does not exist on it")

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        entry = _entry(self)
        if entry is None:
            return "<WorkflowTruthReading unbound>"
        captured, digest = entry
        available = bool(_thawed(captured).get("available")) \
            if isinstance(captured, _FrozenMapping) else False
        return f"<WorkflowTruthReading available={available} reading={digest[:12]}>"


# handle OBJECT -> (captured reading, digest of that captured reading).
#
# THE CAPTURED READING LIVES HERE AND NOWHERE ELSE, and the KEY IS THE HANDLE
# ITSELF rather than any value read off it -- a value read off a handle is a
# value a caller can influence, which is the class of defect this registry
# closes.  It is weak-keyed so an entry lasts exactly as long as the handle a
# consumer is holding: nothing leaks, and nothing outlives its receipt.
_MINTED: "WeakKeyDictionary[WorkflowTruthReading, tuple[Any, str]]" = WeakKeyDictionary()

READING_NOT_MINTED = "workflow_truth_reading_not_minted"

# RETIRED, AND KEPT VISIBLY SO RATHER THAN DELETED.  This was the refusal for a
# genuinely minted handle no longer bound to its own reading -- the shape that
# existed only while the handle carried a re-pointable key.  A handle now carries
# nothing to re-point, so this module can no longer raise it, and
# ``reader_refuses_only_by_identity_checks`` in ops/assurance-health-selftest.py
# pins that.  The name stays exported so an importing consumer does not break on
# a defect that was closed.
READING_PAYLOAD_REPLACED = "workflow_truth_reading_payload_replaced"


class WorkflowTruthReadingError(TypeError):
    """A reading was refused, carrying the machine-readable reason id.

    It subclasses ``TypeError`` because that is what every consumer of this
    module already refuses a non-reading with, and an object that is not a key in
    the mint registry is the same kind of refusal: the value is not a reading,
    whatever it is shaped like.
    """

    def __init__(self, reason_id: str, message: str) -> None:
        self.reason_id = reason_id
        super().__init__(f"{reason_id}: {message}")


def _entry(value: Any) -> tuple[Any, str] | None:
    """The mint entry for THIS OBJECT, or ``None``.  ONE lookup, by identity.

    The decision procedure, in order:

      1. Is the type EXACTLY ``WorkflowTruthReading``?  A weak mapping hashes and
         compares its keys as their referents, so this test -- and not the
         mapping -- is what makes the lookup identity: a look-alike defining
         ``__hash__``/``__eq__`` to match a genuine handle never reaches the
         mapping.  The class is final, so the test cannot be widened.
      2. Is that object a key in ``_MINTED``?  ``WorkflowTruthReading`` defines
         neither ``__hash__`` nor ``__eq__``, so for the only objects that get
         this far, "is a key" means "is this very object".
      3. Otherwise ``None``: this module never minted it.

    Nothing is read off ``value`` at any step, so there is no caller-visible
    input to this lookup and nothing that can change between the lookup and the
    use of what it returned.
    """
    if type(value) is not WorkflowTruthReading:
        return None
    try:
        return _MINTED[value]
    except (KeyError, TypeError):
        return None


def is_workflow_truth_reading(value: Any) -> bool:
    """True only for a handle minted here.

    Identity, not shape.  It cannot be answered incorrectly by contents, because
    a handle carries none: what it is bound to is the captured reading in the
    mint registry, which no caller can reach, replace, or key into.
    """
    return _entry(value) is not None


def _entry_or_refuse(value: Any) -> tuple[Any, str]:
    """The mint entry for ``value``, or the refusal.  MODULE-PRIVATE.

    Private because handing the captured value out by reference is the one thing
    that would let one consumer's copy and another consumer's copy be the same
    object.  Everything public goes through ``_thawed``.
    """
    entry = _entry(value)
    if entry is None:
        raise WorkflowTruthReadingError(
            READING_NOT_MINTED,
            "a workflow-truth reading is minted by read_workflow_truth_reading() and "
            "is recognised by being the very object that mint registered; "
            f"{type(value).__name__} is caller-supplied data")
    return entry


def verify_workflow_truth_reading(value: Any) -> None:
    """Refuse unless ``value`` is a handle THIS MODULE minted.

    One question, because there is only one left to ask: is this object a key in
    the mint registry?  If it is, the contents it renders are the ones captured
    at mint -- there is nothing further to check, because there is nothing else it
    can render and no state on it a caller could have changed since.  If it is
    not, ``READING_NOT_MINTED``: whatever it is shaped like, this module never
    read it.

    Returns ``None`` on success; it hands back no reading, so no caller can
    mistake the check for the contents.
    """
    _entry_or_refuse(value)


def render_reading(reading: Any) -> dict[str, Any]:
    """A private mutable copy of the reading captured for THIS handle.

    THE TRUSTED PATH'S ONLY WAY TO CONTENT, AND IT TOUCHES NOTHING ON THE HANDLE.
    Both consumers -- ``tools/health-check.py`` and the A01 adapter in
    ``lib/assurance_health_sources`` -- call this function.  No attribute is read,
    no method is dispatched, and nothing about the object is consulted except the
    type slot ``type()`` reads and the object's own identity.

    WHY A FUNCTION AND NOT A METHOD, which is the eighth review's defect.  A
    method is looked up through the instance, so re-pointing the instance's type
    re-points the method: ``object.__dict__["__class__"].__set__(handle, Forger)``
    is a raw base-descriptor write that no class body can intercept, and
    ``handle.rendered()`` afterwards ran the caller's code and returned the
    caller's census on an object the reader really had minted.  A module function
    resolves on THIS module; a caller who retypes a handle changes nothing about
    which code runs here.

    ONE RESOLUTION, AND NOTHING BETWEEN IT AND THE COPY.  ``_entry_or_refuse``
    performs the single registry lookup and the thaw is taken from what that
    lookup returned, so there is no window between a check and a use for anything
    to change -- a consumer that verified first and rendered second had one, and
    this is the shape that removes it rather than narrowing it.

    Two outcomes and no third: the value captured at mint, or
    ``WorkflowTruthReadingError(READING_NOT_MINTED)``.  A retyped handle takes the
    refusal, because ``_entry()`` compares the REAL type slot and a forged type is
    not ``WorkflowTruthReading``.  Caller content is not among the outcomes.
    """
    return _thawed(_entry_or_refuse(reading)[0])


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
    handle = WorkflowTruthReading(_MINT)
    _MINTED[handle] = (captured, _content_digest(captured))
    return handle

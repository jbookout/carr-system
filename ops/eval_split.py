"""Frozen train/development/final case bundles. No model or credential work.

The guard is an integrity boundary for cooperative Python tuning, not an OS
sandbox. It refuses final/raw-source reads and unmonitored child processes,
including attempts whose PermissionError the tuner catches. Final consumption
locks baseline, candidate, harness and model before opening final cases.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import runpy
import sys
import uuid
import re
from functools import wraps
from datetime import datetime, timezone
from typing import Any

PARTITIONS = ("train", "development", "final")
SCHEMA = "carr.eval-split/v1"


class SplitError(ValueError):
    pass


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                ensure_ascii=False).encode()).hexdigest()


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, doc):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(doc, handle, sort_keys=True, indent=2, ensure_ascii=False)
        handle.write("\n")


def _content(case):
    # Every harness input counts. Only declared identity, partition and grader
    # fields are excluded; adding an input must not silently weaken identity.
    inputs = {k: v for k, v in case.items()
                   if k not in {"id", "case_id", "split", "group", "label", "gold", "expected",
                                "required", "acceptable", "disputed"}}
    # Rerank relevance is a per-candidate grading label, not an input. Candidate
    # IDs, text, ranks and every other scored input remain part of identity.
    if isinstance(inputs.get("candidates"), list):
        inputs["candidates"] = [{k: v for k, v in c.items() if k != "relevance"}
                                if isinstance(c, dict) else c for c in inputs["candidates"]]
    return digest(inputs)


def freeze(cases, directory, *, seed, source, previously_seen, blocked_sources=()):
    """Provision once, before tuning. Caller supplies the exposure ledger.

    Previously scored cases/groups/content digests are ineligible even if a
    different random seed would place them in training. Never recycle a cohort.
    Source-related rows stay in one group. No final text enters the manifest.
    """
    if not source or not seed or not cases:
        raise SplitError("source, seed and nonempty cases required")
    ids, contents, groups = set(), set(), {}
    seen = set(previously_seen) | historical_exposure()
    for c in cases:
        cid = c.get("id", c.get("case_id"))
        if not isinstance(cid, str) or not cid or cid in ids:
            raise SplitError("missing or duplicate case id")
        group = c.get("group")
        content = _content(c)
        if cid in seen or content in seen:
            raise SplitError("previously seen case/group/content cannot enter a fresh cohort")
        if not isinstance(group, str) or not group.strip():
            raise SplitError("explicit nonempty source group required (synthetic cases must declare independence)")
        if group in seen:
            raise SplitError("previously seen case/group/content cannot enter a fresh cohort")
        if content in contents:
            raise SplitError("duplicate case content")
        ids.add(cid)
        contents.add(content)
        groups.setdefault(group, []).append(c)
    order = sorted(groups)
    random.Random(str(seed)).shuffle(order)
    if len(order) < 3:
        raise SplitError("at least three independent source groups required")
    ntrain = max(1, len(order) * 6 // 10)
    ndev = max(1, len(order) * 2 // 10)
    buckets = {"train": order[:ntrain], "development": order[ntrain:ntrain + ndev],
               "final": order[ntrain + ndev:]}
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": SCHEMA, "source": source, "seed": str(seed),
                "frozen_at": datetime.now(timezone.utc).isoformat(),
                "method": "seeded shuffle of source groups, 60/20/20; frozen before tuning",
                "previously_seen_digest": digest(sorted(seen)),
                "blocked_sources": [str(Path(p).resolve()) for p in blocked_sources],
                "partitions": {}}
    for p, gs in buckets.items():
        rows = [c for g in gs for c in groups[g]]
        path = directory / f"{p}.json"
        _write(path, rows)
        manifest["partitions"][p] = {"path": path.name, "sha256": file_digest(path),
            "members": [{"id": c.get("id", c.get("case_id")), "group": c["group"],
                         "content_digest": _content(c)} for c in rows]}
    manifest["digest"] = digest(manifest)
    path = directory / "manifest.json"
    _write(path, manifest)
    return path


def historical_exposure():
    """Inventory every existing scored collection, including its aliases.

    Snapshot digests record what was inspected; current files are read so newly
    exposed cases cannot evade the boundary by an unrefreshed inventory hash.
    """
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / "evals/split-uses.json").read_text())
    seen = set()
    def visit(value):
        if isinstance(value, dict):
            if "id" in value or "case_id" in value:
                cid = value.get("id", value.get("case_id"))
                if isinstance(cid, str):
                    seen.add(cid)
                    seen.add(_content(value))
            for v in value.values():
                visit(v)
        elif isinstance(value, list):
            for v in value:
                visit(v)
    for family in inventory["families"]:
        for glob in family["case_globs"]:
            for path in root.glob(glob):
                if path.suffix == ".jsonl":
                    for line in path.read_text().splitlines():
                        if line.strip():
                            visit(json.loads(line))
                else:
                    visit(json.loads(path.read_text()))
    # Actual reader projections carry aliases, normalized tool calls and
    # resolved {{REPO}} paths absent from the raw traces. Inventory both.
    import importlib.util
    spec = importlib.util.spec_from_file_location("historical_rule_cases", root / "evals/rule-delivery/run_eval.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    previous = os.environ.pop("CARR_EVAL_SPLIT", None)
    try:
        visit(reader.load_cases())
    finally:
        if previous is not None:
            os.environ["CARR_EVAL_SPLIT"] = previous
    return seen


def read_manifest(path):
    doc = json.loads(Path(path).read_text())
    if doc.get("schema") != SCHEMA or doc.get("digest") != digest({k: v for k, v in doc.items() if k != "digest"}):
        raise SplitError("split manifest digest/schema mismatch")
    if set(doc.get("partitions", {})) != set(PARTITIONS):
        raise SplitError("train, development and final partitions required")
    all_ids, all_content, all_groups, paths = set(), set(), set(), set()
    for p in PARTITIONS:
        part = doc["partitions"][p]
        members = part.get("members")
        resolved = (Path(path).parent / part["path"]).resolve()
        if resolved in paths:
            raise SplitError("partition file paths overlap")
        paths.add(resolved)
        if not isinstance(members, list) or not members:
            raise SplitError("empty partition")
        ids = {m["id"] for m in members}
        content = {m["content_digest"] for m in members}
        groups = {m["group"] for m in members}
        if any(not isinstance(g, str) or not g.strip() for g in groups):
            raise SplitError("explicit source group required")
        if len(ids) != len(members) or len(content) != len(members) or all_ids & ids or all_content & content or all_groups & groups:
            raise SplitError("case, content or source group overlap")
        all_ids.update(ids)
        all_content.update(content)
        all_groups.update(groups)
    return doc


def _load(path, doc, partition):
    part = doc["partitions"][partition]
    file = Path(path).parent / part["path"]
    data = file.read_bytes()
    if "sha256:" + hashlib.sha256(data).hexdigest() != part["sha256"]:
        raise SplitError(f"{partition} file digest mismatch")
    rows = json.loads(data)
    members = [{"id": c.get("id", c.get("case_id")), "group": c.get("group"),
                "content_digest": _content(c)} for c in rows]
    if members != part["members"]:
        raise SplitError(f"{partition} membership mismatch")
    return rows


def load_partition(path, partition="train"):
    if partition not in {"train", "development"}:
        raise SplitError("final cases are forbidden during tuning; lock a final evaluation")
    return _load(path, read_manifest(path), partition)


def development_provenance(cases, source):
    """Previously exposed suites are development evidence, regardless of name."""
    if any(c.get("split") in {"final", "final_test"} for c in cases):
        raise SplitError("final cases cannot enter a tuning/regression dataset")
    return {"evaluation_use": "development_only", "source": source,
            "dataset_digest": digest(cases), "final_score_eligible": False}


def guarded_tuning(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        manifest = os.environ.get("CARR_EVAL_SPLIT")
        if not manifest:
            return fn(*args, **kwargs)
        if any(a["path"] == Path(manifest).resolve() for a in _ACTIVE):
            return fn(*args, **kwargs)
        with tuning_guard(manifest):
            return fn(*args, **kwargs)
    return guarded


_ACTIVE: list[dict[str, Any]] = []


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _seal(doc):
    doc["digest"] = digest({k: v for k, v in doc.items() if k != "digest"})
    return doc


def _replace(path, doc):
    temp = Path(str(path) + "." + uuid.uuid4().hex)
    _write(temp, doc)
    os.replace(temp, path)


def tuning_history(path):
    """Derive the aggregate from every durable attempt, including interruptions."""
    path = Path(path).resolve()
    doc = read_manifest(path)
    attempts = [json.loads(p.read_text()) for p in sorted((path.parent / "tuning-attempts").glob("*.json"))]
    attempts.sort(key=lambda a: a["started_at"])
    if not attempts:
        raise SplitError("completed tuning history required")
    status = "passed" if all(a["status"] == "passed" for a in attempts) else "failed"
    return _seal({"schema": "carr.eval-tuning-history/v1", "split_digest": doc["digest"],
                  "attempts": attempts, "status": status,
                  "violations": [v for a in attempts for v in a["violations"]],
                  "started_at": attempts[0]["started_at"],
                  "completed_at": attempts[-1].get("completed_at")})


def start_tuning(path):
    path = Path(path).resolve()
    doc = read_manifest(path)
    if (path.parent / "final-lock.json").exists():
        raise SplitError("final cohort already consumed; tuning is closed")
    # Serialize tuning and final consumption at the bundle seam. A crashed
    # attempt leaves a running record and lease, so it can never look clean.
    try:
        _write(path.parent / "evaluation-active.json", {"operation": "tuning"})
    except FileExistsError:
        raise SplitError("unfinished or overlapping evaluation attempt") from None
    # A final consumer can have won between the first read and acquisition.
    if (path.parent / "final-lock.json").exists():
        (path.parent / "evaluation-active.json").unlink()
        raise SplitError("final cohort already consumed; tuning is closed")
    attempt = _seal({"schema": "carr.eval-tuning-access/v1", "attempt_id": uuid.uuid4().hex,
                     "split_digest": doc["digest"], "violations": [], "status": "running", "started_at": _stamp()})
    (path.parent / "tuning-attempts").mkdir(exist_ok=True)
    _write(path.parent / "tuning-attempts" / (attempt["attempt_id"] + ".json"), attempt)
    _replace(path.parent / "tuning-access.json", tuning_history(path))
    return attempt


def finish_tuning(path, attempt, *, failed=False):
    path = Path(path).resolve()
    stored = json.loads((path.parent / "tuning-attempts" / (attempt["attempt_id"] + ".json")).read_text())
    if stored["status"] != "running" or stored["split_digest"] != attempt["split_digest"]:
        raise SplitError("attempt already completed or belongs to another split")
    attempt["status"] = "failed" if failed or attempt["violations"] else "passed"
    attempt["completed_at"] = _stamp()
    _replace(path.parent / "tuning-attempts" / (attempt["attempt_id"] + ".json"), _seal(attempt))
    history = tuning_history(path)
    _replace(path.parent / "tuning-access.json", history)
    (path.parent / "evaluation-active.json").unlink()
    return history


def _validate_binding(binding):
    required = {"candidate_digest", "baseline_digest", "harness_digest", "model"}
    if set(binding) != required or not isinstance(binding["model"], str) or not binding["model"].strip():
        raise SplitError("exact candidate/baseline/harness/model binding required")
    if any(not isinstance(binding[k], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", binding[k])
           for k in required - {"model"}):
        raise SplitError("candidate/baseline/harness bindings must be SHA256 digests")


def _validate_history(audit, doc, locked_at):
    if (not isinstance(audit, dict) or audit.get("schema") != "carr.eval-tuning-history/v1" or audit.get("status") != "passed"
        or audit.get("violations") != [] or audit.get("split_digest") != doc["digest"]
        or audit.get("digest") != digest({k: v for k, v in audit.items() if k != "digest"})
        or not isinstance(audit.get("attempts"), list) or not audit["attempts"]):
        raise SplitError("clean completed aggregate tuning access audit required")
    previous = datetime.fromisoformat(doc["frozen_at"])
    ids = set()
    for attempt in audit["attempts"]:
        if (not isinstance(attempt, dict) or attempt.get("schema") != "carr.eval-tuning-access/v1" or attempt.get("status") != "passed"
            or attempt.get("violations") != [] or attempt.get("split_digest") != doc["digest"]
            or not isinstance(attempt.get("attempt_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", attempt["attempt_id"])
            or attempt["attempt_id"] in ids
            or attempt.get("digest") != digest({k: v for k, v in attempt.items() if k != "digest"})):
            raise SplitError("tuning attempt contract invalid")
        ids.add(attempt["attempt_id"])
        start, end = (datetime.fromisoformat(attempt[k]) for k in ("started_at", "completed_at"))
        if not previous <= start <= end <= datetime.fromisoformat(locked_at):
            raise SplitError("split freeze, tuning and final lock chronology mismatch")
        previous = end
    if audit["started_at"] != audit["attempts"][0]["started_at"] or audit["completed_at"] != audit["attempts"][-1]["completed_at"]:
        raise SplitError("aggregate audit chronology mismatch")


def _audit(event, args):
    for active in _ACTIVE:
        reason = None
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            target = Path(os.fsdecode(args[0])).resolve()
            if target in active["blocked"]:
                reason = "final_access"
            elif target.exists() and any(target.samefile(p) for p in active["blocked"] if p.exists()):
                reason = "final_access"
        elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "ctypes.dlopen"}:
            reason = "unmonitored_execution"
        if reason:
            active["audit"]["violations"].append({"event": event, "reason": reason})
            raise PermissionError(reason)


sys.addaudithook(_audit)


@contextmanager
def tuning_guard(path):
    path = Path(path).resolve()
    doc = read_manifest(path)
    blocked = {(path.parent / doc["partitions"]["final"]["path"]).resolve(),
               *(Path(p).resolve() for p in doc["blocked_sources"])}
    attempt = start_tuning(path)
    audit = {}
    active = {"path": path, "blocked": blocked, "audit": attempt}
    _ACTIVE.append(active)
    failed = False
    try:
        identities = {(p.stat().st_dev, p.stat().st_ino) for p in blocked if p.exists()}
        # Refuse inherited handles before any supported reader can consume
        # buffered bytes or a FileIO/os.read descriptor without an open event.
        fd_dir = "/proc/self/fd" if Path("/proc/self/fd").exists() else "/dev/fd"
        for name in os.listdir(fd_dir):
            try:
                stat = os.fstat(int(name))
            except (OSError, ValueError):
                continue
            if (stat.st_dev, stat.st_ino) in identities:
                attempt["violations"].append({"event": "inherited_descriptor", "reason": "final_access"})
                raise PermissionError("final_access: inherited final or raw-source handle")
        yield audit
    except BaseException as exc:
        failed = not (isinstance(exc, SystemExit) and exc.code in (None, 0))
        raise
    finally:
        _ACTIVE.remove(active)
        audit.update(finish_tuning(path, attempt, failed=failed))
        if attempt["violations"]:
            raise SplitError("tuning failed: final_access or unmonitored_execution")


def final_evaluation(path, binding, audit):
    """Consume final ONCE. Provider failure burns the cohort too; never retry.

    Baseline and one candidate are fixed together. The scorer receives rows
    only after lock persistence. Selection is over; another candidate requires
    fresh final cases. Receipt metadata contains no transcripts or labels.
    """
    path = Path(path).resolve()
    doc = read_manifest(path)
    _validate_binding(binding)
    if (path.parent / "final-lock.json").exists():
        raise SplitError("final cohort already consumed; collect fresh cases")
    try:
        _write(path.parent / "evaluation-active.json", {"operation": "final"})
    except FileExistsError:
        raise SplitError("unfinished or overlapping evaluation attempt") from None
    try:
        if audit != tuning_history(path):
            raise SplitError("aggregate audit must cover every current selection attempt")
        _validate_history(audit, doc, _stamp())
        lock = {"schema": "carr.eval-final-lock/v1", "split_digest": doc["digest"],
                **binding, "tuning_access": audit, "locked_at": _stamp()}
        lock["digest"] = digest(lock)
        lock_path = path.parent / "final-lock.json"
        try:
            _write(lock_path, lock)
        except FileExistsError:
            raise SplitError("final cohort already consumed; collect fresh cases") from None
    finally:
        (path.parent / "evaluation-active.json").unlink()
    rows = _load(path, doc, "final")
    provenance = {"schema": SCHEMA, "manifest": str(path), "manifest_digest": doc["digest"],
                  "score_partition": "final", "final_lock": str(lock_path),
                  "final_lock_digest": lock["digest"], **binding,
                  "partition_digests": {p: doc["partitions"][p]["sha256"] for p in PARTITIONS},
                  "counts": {p: len(doc["partitions"][p]["members"]) for p in PARTITIONS}}
    return rows, provenance


def provenance_errors(provenance, root=None):
    try:
        if not isinstance(provenance, dict) or provenance.get("schema") != SCHEMA or provenance.get("score_partition") != "final":
            raise SplitError("final split provenance required")
        resolve = lambda p: (Path(root or ".") / p).resolve()
        doc = read_manifest(resolve(provenance["manifest"]))
        lock = json.loads(resolve(provenance["final_lock"]).read_text())
        if lock.get("schema") != "carr.eval-final-lock/v1":
            raise SplitError("final lock schema invalid")
        _validate_binding({k: lock[k] for k in ("candidate_digest", "baseline_digest", "harness_digest", "model")})
        if lock.get("digest") != digest({k: v for k, v in lock.items() if k != "digest"}) or lock["digest"] != provenance["final_lock_digest"] or lock["split_digest"] != doc["digest"] or provenance["manifest_digest"] != doc["digest"]:
            raise SplitError("split/lock digest mismatch")
        for k in ("candidate_digest", "baseline_digest", "harness_digest", "model"):
            if provenance[k] != lock[k]:
                raise SplitError("final candidate binding mismatch")
        _validate_history(lock["tuning_access"], doc, lock["locked_at"])
        if provenance["partition_digests"] != {p: doc["partitions"][p]["sha256"] for p in PARTITIONS} or provenance["counts"] != {p: len(doc["partitions"][p]["members"]) for p in PARTITIONS}:
            raise SplitError("split provenance counts/digests mismatch")
        return []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"split provenance: {exc}"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    tune = sub.add_parser("tune")
    tune.add_argument("manifest")
    tune.add_argument("script")
    tune.add_argument("args", nargs=argparse.REMAINDER)
    load = sub.add_parser("load")
    load.add_argument("manifest")
    load.add_argument("partition", choices=("train", "development"))
    start = sub.add_parser("audit-start", help="Node adapter: reserve a durable tuning attempt")
    start.add_argument("manifest")
    finish = sub.add_parser("audit-finish", help="Node adapter: finish the reserved attempt")
    finish.add_argument("manifest")
    finish.add_argument("attempt")
    finish.add_argument("--failed", action="store_true")
    provision = sub.add_parser("freeze")
    provision.add_argument("cases", help="fresh cases JSON list, with source group IDs")
    provision.add_argument("directory", help="new sealed bundle directory")
    provision.add_argument("--seed", required=True)
    provision.add_argument("--source", required=True)
    provision.add_argument("--seen", required=True, help="JSON list of previously exposed IDs, groups and content digests")
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            print(freeze(json.loads(Path(args.cases).read_text()), args.directory,
                         seed=args.seed, source=args.source,
                         previously_seen=json.loads(Path(args.seen).read_text()), blocked_sources=[args.cases]))
        elif args.command == "load":
            print(json.dumps(load_partition(args.manifest, args.partition)))
        elif args.command == "audit-start":
            print(json.dumps(start_tuning(args.manifest)))
        elif args.command == "audit-finish":
            print(json.dumps(finish_tuning(args.manifest, json.loads(args.attempt), failed=args.failed)))
        else:
            os.environ["CARR_EVAL_SPLIT"] = str(Path(args.manifest).resolve())
            sys.argv = [args.script, *args.args]
            with tuning_guard(args.manifest) as audit:
                try:
                    runpy.run_path(args.script, run_name="__main__")
                except SystemExit as exc:
                    if exc.code not in (None, 0):
                        raise
        return 0
    except (SplitError, PermissionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

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
    # IDs and old split names must not disguise duplicate source content.
    inputs = {k: case[k] for k in ("input", "state", "prompt", "situation", "tool_calls") if k in case}
    return digest(inputs or {k: v for k, v in case.items()
                             if k not in {"id", "case_id", "split", "group", "label", "gold", "expected"}})


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
        group = c.get("group", cid)
        content = _content(c)
        if cid in seen or group in seen or content in seen:
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
            "members": [{"id": c.get("id", c.get("case_id")), "group": c.get("group", c.get("id", c.get("case_id"))),
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
        if len(ids) != len(members) or len(content) != len(members) or all_ids & ids or all_content & content or all_groups & groups:
            raise SplitError("case, content or source group overlap")
        all_ids.update(ids)
        all_content.update(content)
        all_groups.update(groups)
    return doc


def _load(path, doc, partition):
    part = doc["partitions"][partition]
    file = Path(path).parent / part["path"]
    if file_digest(file) != part["sha256"]:
        raise SplitError(f"{partition} file digest mismatch")
    rows = json.loads(file.read_text())
    members = [{"id": c.get("id", c.get("case_id")), "group": c.get("group", c.get("id", c.get("case_id"))),
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
        with tuning_guard(manifest):
            return fn(*args, **kwargs)
    return guarded


_ACTIVE: list[dict[str, Any]] = []


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
    audit = {"schema": "carr.eval-tuning-access/v1", "split_digest": doc["digest"],
             "violations": [], "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    active = {"blocked": blocked, "audit": audit}
    _ACTIVE.append(active)
    try:
        yield audit
    finally:
        _ACTIVE.remove(active)
        audit["status"] = "failed" if audit["violations"] else "passed"
        audit["completed_at"] = datetime.now(timezone.utc).isoformat()
        audit["digest"] = digest(audit)
        if audit["violations"]:
            raise SplitError("tuning failed: final_access or unmonitored_execution")


def final_evaluation(path, binding, audit):
    """Consume final ONCE. Provider failure burns the cohort too; never retry.

    Baseline and one candidate are fixed together. The scorer receives rows
    only after lock persistence. Selection is over; another candidate requires
    fresh final cases. Receipt metadata contains no transcripts or labels.
    """
    path = Path(path).resolve()
    doc = read_manifest(path)
    required = {"candidate_digest", "baseline_digest", "harness_digest", "model"}
    if set(binding) != required or any(not binding[k] for k in required):
        raise SplitError("exact candidate/baseline/harness/model binding required")
    for k in required - {"model"}:
        value = binding[k]
        if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:") or any(c not in "0123456789abcdef" for c in value[7:]):
            raise SplitError("candidate/baseline/harness bindings must be SHA256 digests")
    if audit.get("split_digest") != doc["digest"] or audit.get("status") != "passed" or audit.get("violations") != [] or audit.get("digest") != digest({k: v for k, v in audit.items() if k != "digest"}):
        raise SplitError("clean completed tuning access audit required")
    lock = {"schema": "carr.eval-final-lock/v1", "split_digest": doc["digest"],
            **binding, "tuning_access": audit, "locked_at": datetime.now(timezone.utc).isoformat()}
    lock["digest"] = digest(lock)
    lock_path = path.parent / "final-lock.json"
    try:
        _write(lock_path, lock)
    except FileExistsError:
        raise SplitError("final cohort already consumed; collect fresh cases") from None
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
        if lock.get("digest") != digest({k: v for k, v in lock.items() if k != "digest"}) or lock["digest"] != provenance["final_lock_digest"] or lock["split_digest"] != doc["digest"] or provenance["manifest_digest"] != doc["digest"]:
            raise SplitError("split/lock digest mismatch")
        for k in ("candidate_digest", "baseline_digest", "harness_digest", "model"):
            if provenance[k] != lock[k]:
                raise SplitError("final candidate binding mismatch")
        audit = lock["tuning_access"]
        if audit.get("status") != "passed" or audit.get("violations") != [] or audit.get("split_digest") != doc["digest"] or audit.get("digest") != digest({k: v for k, v in audit.items() if k != "digest"}):
            raise SplitError("tuning access audit invalid")
        times = [datetime.fromisoformat(x) for x in (doc["frozen_at"], audit["started_at"], audit["completed_at"], lock["locked_at"])]
        if times != sorted(times):
            raise SplitError("split freeze, tuning and final lock chronology mismatch")
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
        else:
            os.environ["CARR_EVAL_SPLIT"] = str(Path(args.manifest).resolve())
            sys.argv = [args.script, *args.args]
            with tuning_guard(args.manifest) as audit:
                runpy.run_path(args.script, run_name="__main__")
            _write(Path(args.manifest).parent / "tuning-access.json", audit)
        return 0
    except (SplitError, PermissionError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

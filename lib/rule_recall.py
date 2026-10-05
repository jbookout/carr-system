from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

POLICY = "ops/config/rule-recall-policy.v1.json"
PROOF_SOURCES = (
    "lib/rule_recall.py", "lib/rule_routes.py", "lib/rule_delivery_preuse.py",
    "lib/rule_delivery_shadow.py", "lib/claude_rule_delivery_dedupe.py",
    "hooks/rule-pack-preuse-reselection.py", "ops/rule_trigger_delivery.py",
    "ops/rule_trigger_compile.py", "ops/machine_envelope.py", "ops/typesafe_client.py",
    "ops/config/hooks.json", "ops/config/codex-hooks.json", "ops/config/rule-classes.v1.json",
    "ops/config/rule-enforcement-map.json", "ops/config/rule-jit-triggers.v1.json",
    "ops/config/rule-jev-triggers.v1.json", "ops/config/rule-selection-corpus.v1.json",
    "ops/config/rule-routes.v1.json", "lib/rule_boot_gate.py", "hooks/rule-boot-gate.py",
    "mcp-server/src/rule-boot.js",
)
SHORT_ID = re.compile(r"^[0-9a-f]{8}$")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def retain_in_boot(rule_id, classification, proofs):
    if classification.get("always_on"):
        return True
    proof = proofs.get(rule_id)
    if not isinstance(proof, dict):
        return True
    if proof.get("verified_binding") is not True:
        return True
    for source in ("benchmark", "real_turns"):
        rows = proof.get(source)
        if not isinstance(rows, list) or not rows:
            return True
        required = [r for r in rows if isinstance(r, dict) and r.get("requires") is True]
        if not required or not any(isinstance(r, dict) and r.get("requires") is False for r in rows):
            return True
        if any(r.get("complete_text") is not True or r.get("before_event") is not True for r in required):
            return True
    return False


def source_bindings_match(repo, sources):
    if not isinstance(sources, dict) or not set(PROOF_SOURCES) <= set(sources):
        return False
    root = Path(repo).resolve()
    for name, expected in sources.items():
        if not isinstance(name, str):
            return False
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            return False
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return False
        except OSError:
            return False
    return True


def load_proofs(repo):
    """Reject stale, unbound and hand-entered summaries by default."""
    repo = Path(repo)
    try:
        policy = json.loads((repo / POLICY).read_text())
        corpus = json.loads((repo / "ops/config/rule-selection-corpus.v1.json").read_text())
        routes = json.loads((repo / "ops/config/rule-routes.v1.json").read_text())["rules"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if policy.get("schema") != "rule-recall-policy/v1":
        return {}
    statements = {r["id"]: r["statement"] for r in corpus["rules"]}
    out = {}
    for rid, binding in (policy.get("proofs") or {}).items():
        if (not isinstance(binding, dict) or rid not in statements or rid not in routes
                or not source_bindings_match(repo, binding.get("sources"))):
            continue
        if (binding.get("statement_sha256") != hashlib.sha256(statements[rid].encode()).hexdigest()
                or binding.get("route_sha256") != digest(routes[rid])):
            continue
        proof = {}
        for source in ("benchmark", "real_turns"):
            ref = binding.get(source) or {}
            name = ref.get("path")
            if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
                break
            try:
                raw = (repo / name).read_bytes()
                evidence = json.loads(raw)
            except (OSError, ValueError):
                break
            if (hashlib.sha256(raw).hexdigest() != ref.get("sha256")
                    or evidence.get("schema") != "rule-recall-observations/v1"
                    or evidence.get("rule_id") != rid
                    or evidence.get("source") != source
                    or evidence.get("statement_sha256") != binding["statement_sha256"]
                    or evidence.get("route_sha256") != binding["route_sha256"]):
                break
            rows = evidence.get("observations")
            if not isinstance(rows, list):
                break
            if source == "benchmark":
                fixture = repo / "ops/fixtures/rule-delivery-eval/cases.v2.json"
                try:
                    raw_cases = fixture.read_bytes()
                    cases = [c for c in json.loads(raw_cases)["cases"] if c.get("split") == "test"]
                except (OSError, ValueError, KeyError):
                    break
                labelled = {c["id"]: rid in c["gold"] for c in cases}
                if (evidence.get("fixture_sha256") != hashlib.sha256(raw_cases).hexdigest() or
                        len(rows) != len(labelled) or
                        {r.get("source_ref"): r.get("requires") for r in rows if isinstance(r, dict)} != labelled):
                    break
            if any(not isinstance(row, dict) or type(row.get("requires")) is not bool or not row.get("source_ref") or
                   (row.get("requires") is True and not row.get("receipt_id")) for row in rows):
                break
            if source == "real_turns" and not complete_real_frame(evidence, rows, repo):
                break
            proof[source] = [dict(row, complete_text=observation_delivered(row, rid, statements[rid]),
                                  before_event=observation_delivered(row, rid, statements[rid])) for row in rows]
        if set(proof) == {"benchmark", "real_turns"}:
            proof["verified_binding"] = True
            proof["statement_sha256"] = binding["statement_sha256"]
            out[rid] = proof
    return out


def complete_real_frame(evidence, rows, repo):
    """Bind labels to every turn in a separately captured native sampling frame."""
    ref = evidence.get("sampling_frame")
    if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
        return False
    path = Path(ref["path"])
    if path.is_absolute() or ".." in path.parts:
        return False
    try:
        raw = (Path(repo) / path).read_bytes()
        frame = json.loads(raw)
    except (OSError, ValueError):
        return False
    if not isinstance(frame, dict) or hashlib.sha256(raw).hexdigest() != ref.get("sha256") or frame.get("schema") != "rule-recall-native-frame/v1":
        return False
    turns = frame.get("turns")
    if not isinstance(turns, list) or len(turns) < 200 or len(rows) != len(turns):
        return False
    if any(not isinstance(t, dict) or not t.get("source_ref") or not timestamp(t.get("event_at")) for t in turns):
        return False
    by_ref = {t["source_ref"]: t for t in turns}
    if len(by_ref) != len(turns) or len({r.get("source_ref") for r in rows}) != len(rows):
        return False
    if set(by_ref) != {r.get("source_ref") for r in rows}:
        return False
    return all(row.get("event_at") == by_ref[row["source_ref"]]["event_at"] and
               row.get("receipt") == by_ref[row["source_ref"]].get("receipt") for row in rows)


def observation_delivered(row, rule_id, statement):
    """Read the receipt and ordering, rather than trusting a claimed verdict."""
    receipt = row.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("receipt_id") != row.get("receipt_id"):
        return False
    delivered = timestamp(receipt.get("observed_at"))
    event = timestamp(row.get("event_at"))
    if not delivered or not event or delivered > event:
        return False
    return any(r.get("id") == rule_id and r.get("statement") == statement
               for r in receipt.get("rules", []) if isinstance(r, dict))


def log_delivery(path, receipt_id, ids, *, observed_at=None):
    """Write only confirmed full-text ids; a missing meter never changes a gate."""
    import fcntl
    row = {"schema": "rule-recall-delivery-observation/v1", "receipt_id": receipt_id,
           "observed_at": observed_at or datetime.now(timezone.utc).isoformat(), "delivered": sorted(set(ids))}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def timestamp(value):
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value, timezone.utc)
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def delivered_ids(row):
    schema = row.get("schema", "")
    if schema in {"rule-route-trigger-delivery/v1", "rule-jev-message-delivery/v3",
                  "rule-jit-trigger-delivery/v1", "rule-delivery-preuse-reselection/v1"}:
        return [r["id"] for r in row.get("rules", []) if isinstance(r, dict)
                and r.get("statement") and SHORT_ID.fullmatch(str(r.get("id", "")))]
    if row.get("schema") in {"rule-recall-boot-observation/v1", "rule-recall-delivery-observation/v1"}:
        return row.get("delivered", [])
    if isinstance(row.get("delivered"), list) and row.get("bind_status") is not None:
        return [r for r in row["delivered"] if isinstance(r, str) and SHORT_ID.fullmatch(r)]
    return []


def delivery_counts(rows, active_ids, now, days):
    now = timestamp(now)
    if now is None:
        raise ValueError("measurement time is required")
    start = now - timedelta(days=days)
    counts = Counter({rid: 0 for rid in active_ids})
    observed = 0
    seen = set()
    oldest = newest = None
    for row in rows:
        at = timestamp(row.get("observed_at", row.get("ts", row.get("at"))))
        if at is None or not start <= at <= now:
            continue
        ids = delivered_ids(row)
        if not ids:
            continue
        key = row.get("receipt_id") or digest(row)
        if key in seen:
            continue
        seen.add(key)
        observed += 1
        oldest = min(oldest, at) if oldest else at
        newest = max(newest, at) if newest else at
        counts.update(rid for rid in set(ids) if rid in counts)
    return {"counts": dict(counts), "readable": bool(observed), "receipts": observed,
            "start": start.isoformat(), "end": now.isoformat(),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
            "zero": sorted(rid for rid, n in counts.items() if n == 0)}

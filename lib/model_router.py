"""Task fit times expiring subscription headroom, with one auditable PR family ledger."""
from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "ops/config"
BUILD_KINDS = {"design/UI", "non-trivial build", "mechanical build"}


class RouteUnavailable(ValueError):
    """No eligible model. Refresh telemetry or wait for the named window reset."""


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def current_fit(budget_dir):
    pointer = Path(budget_dir) / "model-fit-current.json"
    if not pointer.exists():
        return load_json(CONFIG / "model-fit.v1.json")
    name = load_json(pointer)["revision_file"]
    if Path(name).name != name or not name.startswith("model-fit."):
        raise ValueError("invalid model-fit revision pointer")
    candidate = load_json(Path(budget_dir) / name)
    if candidate.get("schema") != "model-fit/v1":
        raise ValueError("unsupported learned model-fit schema")
    return candidate


def epoch(value):
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        value = value.timestamp()
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("invalid timestamp")
    return float(value)


def read_codex_usage(sessions):
    """Newest rate-limit observation by timestamp, never file mtime or credit balance."""
    latest = None
    for path in Path(sessions).expanduser().rglob("*.jsonl"):
        try:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        row = json.loads(line)
                        limits = row.get("payload", {}).get("rate_limits") or row.get("rate_limits")
                        if not isinstance(limits, dict) or not limits.get("primary"):
                            continue
                        observed = epoch(row["timestamp"])
                        snap = {"observed_at": observed, "source": "codex-session", "windows": {
                            key: {field: value[field] for field in ("used_percent", "resets_at", "window_minutes")}
                            for key in ("primary", "secondary") if (value := limits.get(key))}}
                        if latest is None or observed > latest["observed_at"]:
                            latest = snap
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            continue
    return latest


def claude_snapshot(payload, observed_at=None):
    """Only persist the documented statusLine rate-limit fields, never the session body."""
    try:
        limits = payload["rate_limits"]
        short, weekly = limits["five_hour"], limits["seven_day"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Claude rate_limits must include five_hour and seven_day") from exc
    result = {"five_hour_pct": short["used_percentage"], "weekly_pct": weekly["used_percentage"],
              "five_hour_resets_at": epoch(short["resets_at"]),
              "weekly_resets_at": epoch(weekly["resets_at"]),
              "observed_at": time.time() if observed_at is None else epoch(observed_at)}
    for key in ("five_hour_pct", "weekly_pct"):
        number(result[key], 0, 100)
    return result


def claude_usage(snapshot):
    observed = epoch(snapshot["observed_at"])
    return {"observed_at": observed, "source": "claude-snapshot", "windows": {
        "weekly": {"used_percent": snapshot["weekly_pct"], "window_minutes": 10080,
                   "resets_at": epoch(snapshot["weekly_resets_at"])},
        "five_hour": {"used_percent": snapshot["five_hour_pct"], "window_minutes": 300,
                      "resets_at": epoch(snapshot.get("five_hour_resets_at", observed + 5 * 3600))}}}


def collect_usage(budget_dir, *, macbook=True):
    """MacBook and local Codex are observations of ONE allowance, never summed."""
    snapshots, sources = {}, {}
    local = read_codex_usage(Path.home() / ".codex/sessions")
    if local:
        snapshots["codex"] = local
    if macbook:
        # Send this reader's own source; remote output contains only percentages and reset times.
        probe = Path(__file__).read_text(encoding="utf-8") + '\nprint(json.dumps(read_codex_usage(Path.home() / ".codex/sessions")))\n'
        try:
            result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "macbook",
                                     "python3 -"], input=probe, text=True, capture_output=True, timeout=15)
            if result.returncode:
                raise ValueError("ssh unavailable")
            remote = json.loads(result.stdout)
            if remote and (not local or epoch(remote["observed_at"]) > epoch(local["observed_at"])):
                remote["source"] = "codex-macbook"
                snapshots["codex"] = remote
            sources["macbook"] = "observed" if remote else "no rate-limit observation"
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired, KeyError):
            sources["macbook"] = "unavailable; local observation retained"
    for pool in ("claude", "grok", "flash"):
        path = Path(budget_dir) / f"{pool}-usage.json"
        try:
            raw = load_json(path)
            snapshots[pool] = claude_usage(raw) if pool == "claude" else raw
        except (OSError, ValueError, KeyError, TypeError):
            sources[pool] = "missing or malformed usage; refresh snapshot before dispatch"
    return snapshots, sources


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"number must be in [{low}, {high}]")
    return float(value)


def headroom(snapshot, now, reserve, policy):
    if not snapshot:
        raise ValueError("missing usage; refresh snapshot")
    age = now - epoch(snapshot["observed_at"])
    if age < 0 or age > policy["max_age_seconds"]:
        raise ValueError("stale or future usage; refresh snapshot")
    rates, resets = [], []
    for name, window in snapshot["windows"].items():
        used = number(window["used_percent"], 0, 100)
        reset = epoch(window["resets_at"])
        duration = number(window["window_minutes"], 1, 525600)
        if reset <= now:
            raise ValueError("expired usage window; refresh snapshot")
        if used >= policy["cutoff_pct"]:
            raise ValueError(f"{name} cutoff {used:g}%; wait for reset {reset:g}")
        reserved = reserve if duration >= 10080 else 0
        available = (100 - used - reserved) / 100
        if available <= 0:
            raise ValueError(f"{name} reserve protected; wait for reset {reset:g}")
        rates.append(available / max((reset - now) / 3600, 1 / 60))
        resets.append(reset)
    if not rates:
        raise ValueError("missing usage windows; refresh snapshot")
    return min(rates), min(resets)


def normalize_kind(kind, fit, floors):
    if kind in fit["task_routes"]:
        return kind
    for rule in floors["floors"]:
        if any(match.lower() in kind.lower() for match in rule["match"]):
            return "design/UI"
    aliases = {"code": "non-trivial build", "build": "non-trivial build", "escalate": "design/UI",
               "script": "mechanical build", "direct": "mechanical build"}
    if kind not in aliases:
        raise ValueError(f"unknown task kind {kind!r}; classify it using model-routes.v1.json")
    return aliases[kind]


def floor_constraints(request, kind, floor, fit, floors):
    tier = fit["default_floors"][kind]
    rank = floors["tier_rank"]
    if floor is not None:
        if floor not in rank:
            raise ValueError(f"unknown floor {floor!r}")
        tier = max((tier, floor), key=rank.__getitem__)
    voice_floor = None
    for rule in floors["floors"]:
        if any(match.lower() in request.lower() for match in rule["match"]):
            tier = max((tier, rule["min_tier"]), key=rank.__getitem__)
            # Existing Claude voice floors carry no measured OpenAI equivalence.
            voice_floor = "claude"
    return rank[tier], voice_floor


def route(task_kind, floor=None, *, pr_id=None, builder_model=None, usage=None, now=None,
          budget_dir=None, fit=None, reserve_pct=None):
    """Return {model, effort, pool, reason}; audit success AND refusal under one file lock.

    Review needs a PR/job key with a prior builder, or an explicit builder model.
    All dispatchers for a PR must share budget_dir and the same stable repo:branch key.
    This selects a model; execution remains at the existing authenticated Model Room desk.
    """
    fixed_now = now
    now = time.time() if fixed_now is None else epoch(fixed_now)
    budget = Path(budget_dir or os.environ.get("MODEL_ROUTER_BUDGET_DIR", REPO / "out/orch/budget"))
    fit = fit or current_fit(budget)
    floors, routes = load_json(CONFIG / "model-floors.json"), load_json(CONFIG / "model-routes.v1.json")
    pr_id = pr_id or os.environ.get("MODEL_ROUTER_PR")
    builder_model = builder_model or os.environ.get("MODEL_ROUTER_BUILDER_MODEL")
    reserve = number(reserve_pct if reserve_pct is not None else
                     float(os.environ.get("MODEL_ROUTER_CLAUDE_RESERVE_PCT", fit["budget"]["claude_weekly_reserve_pct"])), 0, 100)
    source_errors = {}
    if usage is None:
        usage, source_errors = collect_usage(budget)
        if fixed_now is None:
            now = time.time()
    budget.mkdir(parents=True, exist_ok=True)
    with (budget / "router.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        row = {"at": now, "task_kind": task_kind, "pr_id": pr_id, "floor": floor,
               "builder_model": builder_model, "usage": usage, "source_errors": source_errors,
               "reserve_pct": reserve, "fit_revision": fit["revision"], "candidates": [], "excluded": {}}
        try:
            kind = normalize_kind(task_kind, fit, floors)
            row["normalized_kind"] = kind
            policy_route = fit["task_routes"][kind]
            if policy_route not in routes["routes"]:
                raise RouteUnavailable("task route absent from model-routes.v1.json")
            ledger = budget / "router.jsonl"
            prior = None
            if pr_id and ledger.exists():
                with ledger.open(encoding="utf-8") as stream:
                    for line in stream:
                        old = json.loads(line)
                        if old.get("pr_id") == pr_id and old.get("status") == "routed" and old.get("normalized_kind") in BUILD_KINDS:
                            prior = old["result"]["model"]
            if prior and builder_model and fit["models"][prior]["family"] != fit["models"][builder_model]["family"]:
                raise RouteUnavailable("builder family conflicts with persisted PR ledger")
            builder = prior or builder_model
            family = fit["models"][builder]["family"] if builder else None
            if kind in ("review", "review-fix") and family not in ("claude", "openai"):
                raise RouteUnavailable("review needs Claude/OpenAI builder context; provide PR key or builder model")
            min_rank, voice_family = floor_constraints(task_kind, kind, floor, fit, floors)
            for model, entry in fit["models"].items():
                try:
                    quality = number(entry["fit"][kind], 0, 1)
                    if quality == 0 or floors["tier_rank"][entry["tier"]] < min_rank:
                        raise ValueError("task fit or tier floor")
                    if voice_family and entry["family"] != voice_family:
                        raise ValueError("existing Claude voice floor")
                    if pr_id and kind in BUILD_KINDS and entry["family"] not in ("claude", "openai"):
                        raise ValueError("PR build needs Claude/OpenAI review alternation")
                    if kind == "review" and entry["family"] != ("openai" if family == "claude" else "claude"):
                        raise ValueError("review must alternate Claude/OpenAI families")
                    if (kind in BUILD_KINDS or kind == "review-fix") and family and entry["family"] != family:
                        raise ValueError("PR builder family is fixed")
                    value, reset = headroom(usage.get(entry["pool"]), now,
                                            reserve if entry["pool"] == "claude" else 0, fit["budget"])
                    row["candidates"].append({"model": model, "score": quality * value,
                                              "fit": quality, "headroom": value, "resets_at": reset})
                except (ValueError, KeyError, TypeError) as exc:
                    row["excluded"][model] = str(exc)
            if not row["candidates"]:
                raise RouteUnavailable("no eligible pool: " + "; ".join(f"{m}: {why}" for m, why in row["excluded"].items()))
            best_score = max(c["score"] for c in row["candidates"])
            ties = [c for c in row["candidates"] if math.isclose(c["score"], best_score, rel_tol=1e-12, abs_tol=1e-15)]
            picked = min(ties, key=lambda c: (c["resets_at"], c["model"]))
            entry = fit["models"][picked["model"]]
            reason = f"{policy_route}: fit {picked['fit']:.3f} x headroom {picked['headroom']:.6f} = {picked['score']:.6f}; earliest reset breaks ties; reserve {reserve:g}%"
            result = {"model": picked["model"], "effort": entry["effort"], "pool": entry["pool"], "reason": reason}
            row.update(status="routed", result=result, reason=reason)
        except (ValueError, KeyError, TypeError) as exc:
            row.update(status="blocked", reason=str(exc))
            result = None
        with (budget / "router.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if result is None:
            raise RouteUnavailable(row["reason"])
        return result


def log_outcome(path, attribution):
    """Never infer the executor from a script filename or an unlabelled CI/review log."""
    if not attribution.get("model") or not attribution.get("task_kind"):
        return None
    text = Path(path).read_text(encoding="utf-8")
    reviews = re.findall(r"^(?:ROUND \d+ )?(?:REVIEW: )?(APPROVE|BLOCKED)\s*$", text, re.M)
    ci = re.findall(r"^CI[ :_-]+(red|green)\s*$", text, re.I | re.M)
    identity = attribution.get("id") or "log:" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()
    result = {**attribution, "id": identity}
    no_progress = bool(re.search(r"^NO-PROGRESS\s*$", text, re.M))
    approved = bool(reviews and reviews[-1] == "APPROVE")
    if not approved and not no_progress and not attribution.get("completed"):
        return None
    if reviews:
        result["first_pass_approve"] = approved and "BLOCKED" not in reviews
        if approved:
            result["rounds_to_approve"] = len(reviews)
    if ci:
        result["ci_first_push"] = ci[0].lower() == "green"
    if no_progress:
        result["no_progress"] = True
    return result


def learn(fit, outcomes, *, day, floors=None):
    """Update numerical fit only. Minimum tiers and forbidden task cells never change."""
    candidate = copy.deepcopy(fit)
    floors = floors or load_json(CONFIG / "model-floors.json")
    groups = {}
    applied = set(fit.get("learned_ids", []))
    seen = set(applied)
    for outcome in outcomes:
        identity = outcome.get("id")
        if not identity or identity in seen:
            continue
        seen.add(identity)
        model, request = outcome.get("model"), outcome.get("task_kind")
        if model not in fit["models"] or not isinstance(request, str):
            continue
        try:
            kind = normalize_kind(request, fit, floors)
            rank, voice = floor_constraints(request, kind, None, fit, floors)
            entry = fit["models"][model]
            if floors["tier_rank"][entry["tier"]] < rank or (voice and entry["family"] != voice) or not entry["fit"][kind]:
                continue
            metrics = []
            for name in ("first_pass_approve", "ci_first_push", "no_progress"):
                if name in outcome:
                    if not isinstance(outcome[name], bool):
                        raise ValueError("outcome flags must be boolean")
                    metrics.append(float(not outcome[name] if name == "no_progress" else outcome[name]))
            if "rounds_to_approve" in outcome:
                metrics.append(1 / number(outcome["rounds_to_approve"], 1, 1000))
            if metrics:
                groups.setdefault((model, kind), []).append((identity, sum(metrics) / len(metrics)))
        except (ValueError, KeyError, TypeError):
            continue
    changes = []
    for (model, kind), samples in groups.items():
        if len(samples) < fit["learning"]["min_samples"]:
            continue
        entry = candidate["models"][model]
        before = entry["fit"][kind]
        mean = sum(score for _, score in samples) / len(samples)
        bound = fit["learning"]["max_step"]
        step = max(-bound, min(bound, fit["learning"]["rate"] * (mean - before)))
        after = max(entry["min_fit"][kind], min(1, before + step))
        entry["fit"][kind] = round(after, 6)
        applied.update(identity for identity, _ in samples)
        changes.append({"model": model, "task_kind": kind, "samples": len(samples), "before": before, "after": entry["fit"][kind], "mean": mean})
    candidate.update(revision=f"learned-{day}", learned_ids=sorted(applied), learning_changes=changes)
    return candidate

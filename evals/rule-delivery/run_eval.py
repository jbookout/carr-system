#!/usr/bin/env python3
"""run_eval.py — replay events through CARR's deterministic rule-delivery layer.

The system under test is the trigger/pack layer with the Jev judgment OFF:

  prompt event  ops/rule_trigger_delivery.advise (compiled prompt_regex rows
                plus always-on; rank/ask stubbed to raise, so no Jev call),
                then the UserPromptSubmit hook's own pack-layer filter
                (lib/rule_delivery_preuse.semantic_delivery);
  tool event    hooks/rule-pack-preuse-reselection.py's own decision code:
                the scheduled rail (_matches), the route rail
                (routed_rule_ids over ops/config/rule-routes.v1.json) and the
                compiled-table rail (matched_triggers), unioned as process()
                unions them, deduped per (rule, tool) inside one case as
                lib/rule_routes.fresh_ids dedupes per session.

It calls the production functions; it does not re-implement them. It writes
nothing to production logs or caches (log_path=os.devnull, no session id).

GRADER (programmatic, expected rule ids per case):
  expected  = gold - disputed - boot-delivered rules, restricted to rules the
              trigger layer owes (a trigger/path route, or pack-layer member).
  recall    = |delivered & expected| / |expected|, per case and micro.
  false     = delivered rules outside the case's full gold set (and not
              disputed). A should-not-fire case has no expected rule.
  tokens    = estimated context tokens of the receipts the events would inject
              (render replicated offline; chars / 4, no offline tokenizer).

Usage:
  run_eval.py --variant baseline --split train
  run_eval.py --variant v1 --split all
  run_eval.py --compare baseline v1 --split test
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "ops"))
import eval_split as E  # noqa: E402

V2_CASES = os.path.join(REPO, "ops", "fixtures", "rule-delivery-eval", "cases.v2.json")
HARD_CASES = os.path.join(HERE, "hard_cases.v1.json")
RUNS = os.path.join(HERE, "runs")
CHARS_PER_TOKEN = 4.0
METRIC_IDS = ("recall", "false_deliveries", "tokens")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------- cases

def hard_split(case_id):
    """Deterministic 50/50 split for the hand-written cases, fixed by id."""
    digest = hashlib.sha256(("rule-delivery-hard-v1:" + case_id).encode()).digest()
    return "test" if digest[0] % 2 else "train"


def load_cases(split=None):
    """Every case as {id, source, stratum, split, prompt, tool_calls, gold,
    disputed, kind, note}. v2 cases keep the split the repo fixed on
    2026-09-27 (ops/rule_gold_label.assign_splits); hard cases are split by
    hash of their id above."""
    if os.environ.get("CARR_EVAL_SPLIT"):
        partition = split or "train"
        return [{**case, "split": partition} for case in E.load_partition(os.environ["CARR_EVAL_SPLIT"], partition)]
    if split == "final":
        raise E.SplitError("historical exposed cases cannot produce a final score")
    ev = _load(os.path.join(REPO, "ops", "rule_delivery_eval.py"), "rde_cases")
    cases = []
    for case in ev.load_cases(V2_CASES):
        cases.append({"id": case["id"], "source": "v2", "stratum": case["stratum"],
                      "split": case["split"], "prompt": case["prompt"],
                      "tool_calls": case["tool_calls"], "gold": case["gold"],
                      "disputed": case["disputed"], "kind": "trace", "note": ""})
    with open(HARD_CASES, "r", encoding="utf-8") as handle:
        doc = json.load(handle)
    for row in doc["cases"]:
        cases.append({"id": row["id"], "source": "hard", "stratum": row["kind"],
                      "split": hard_split(row["id"]), "prompt": row.get("prompt", ""),
                      "tool_calls": row.get("tool_calls", []),
                      "required": sorted(row["required"]),
                      "gold": sorted(set(row["required"]) | set(row["acceptable"])),
                      "disputed": [],
                      "kind": row["kind"], "note": row["why"]})
    cases.extend(replay_cases())
    return [c for c in cases if split in (None, "all", c["split"])]


READ_ONLY_HEAD = re.compile(
    r"^(?:ls|cat|head|tail|wc|grep|rg|pwd|date|which|stat|file|echo|cd|"
    r"sed -n|git (?:status|log|diff|show|rev-parse|--version|branch --show-current|"
    r"branch --list)|find(?!.*(?:-delete|-exec)))\b")
MUTATING = re.compile(r"[>]|\b(?:rm|mv|cp|push|merge|commit|checkout|reset|curl|tee|kill|"
                      r"install|mkdir|touch|chmod)\b|git branch -[dD]")


def read_only_command(command):
    """True when every &&/;/| segment of a recorded Bash command is a plain read."""
    segments = [s.strip() for s in re.split(r"&&|;|\|\|?|\n", command) if s.strip()]
    return bool(segments) and not MUTATING.search(command) and all(
        READ_ONLY_HEAD.match(s) for s in segments)


def replay_cases():
    """Recorded tool calls from ops/fixtures/real-replay that are routine reads.

    These fixture reads target routine source/history paths. Their quiet labels
    apply to those paths only; a read of a CARR surface can bind review rules
    and is checked separately in selftest.py. The fixtures carry no rule labels."""
    out = []
    base = os.path.join(REPO, "ops", "fixtures", "real-replay")

    def fix(value):
        if isinstance(value, str):
            return value.replace("{{REPO}}", REPO)
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value
    for fname, keep in (("read-calls.jsonl", lambda row: True),
                        ("bash-commands.jsonl",
                         lambda row: read_only_command(row["tool_input"].get("command", "")))):
        with open(os.path.join(base, fname), "r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if not keep(row):
                    continue
                case_id = f"rr-{fname.split('-')[0]}-{row['id']}"
                out.append({"id": case_id, "source": "real-replay", "stratum": "routine_read",
                            "split": hard_split(case_id), "prompt": "",
                            "tool_calls": [{"tool_name": row["tool_name"],
                                            "tool_input": fix(row["tool_input"])}],
                            "gold": [], "disputed": [], "required": [], "kind": "should_not_fire",
                            "note": "Recorded routine read: inspecting files or history binds no taught rule."})
    return out


# ----------------------------------------------------------------- world

class World:
    """Everything the replay reads, loaded once from the working tree."""

    def __init__(self):
        from lib import rule_delivery_preuse as preuse
        from lib import rule_routes
        self.preuse, self.rule_routes = preuse, rule_routes
        ev = _load(os.path.join(REPO, "ops", "rule_delivery_eval.py"), "rde_world")
        self.ev = ev
        self.rtd = ev._quiet_rule_trigger_delivery(REPO, "eval")
        self.hook = _load(os.path.join(REPO, "hooks", "rule-pack-preuse-reselection.py"),
                          "preuse_hook_world")
        with open(os.path.join(REPO, "ops", "config", "rule-selection-corpus.v1.json"),
                  "r", encoding="utf-8") as handle:
            self.statements = {r["id"]: r.get("statement") or ""
                               for r in json.load(handle)["rules"]}
        meta = ev.rule_meta(REPO)
        self.layer = {rid: row["layer"] for rid, row in meta.items()}
        self.boot = ev.boot_always_on_ids(REPO) | {
            rid for rid, row in meta.items() if row["layer"] == "layer0"}
        routes = self.rule_routes.load_routes(__import__("pathlib").Path(REPO))["rules"]
        owed = set()
        for rid, entry in routes.items():
            if any(route.get("kind") in ("trigger", "path_rule") for route in entry["routes"]):
                owed.add(rid)
        owed |= {rid for rid, layer in self.layer.items() if layer == "pack"}
        self.owed = owed - self.boot
        self.labelled = set(self.statements)

    def expected(self, case):
        if "required" in case:  # hand-written case: the labeller already said which
            return sorted(case["required"])
        gold = set(case["gold"]) - set(case["disputed"])
        return sorted(gold & self.owed)

    # ---- delivery

    def prompt_ids(self, case):
        if not case["prompt"].strip():
            return []
        rows = self.rtd.advise(case["prompt"], session_id=None, log_path=os.devnull,
                               rank=_raise, ask=_raise)
        kept, _packs = self.preuse.semantic_delivery(__import__("pathlib").Path(REPO),
                                                     [row["id"] for row in rows])
        return sorted(kept)

    def tool_ids(self, case, index, call):
        payload = {"hook_event_name": "PreToolUse", "tool_name": call["tool_name"],
                   "tool_input": call.get("tool_input"), "session_id": "rule-delivery-eval",
                   "tool_use_id": f"eval-{case['id']}-{index}"}
        if self.hook._matches(payload):
            return sorted(self.hook.scheduled_rule_ids())
        rows = self.hook.matched_triggers(payload)
        routed = self.hook.routed_rule_ids(payload)
        table = self.preuse.merge_trigger_delivery(rows)[2] if rows else []
        return sorted(set(routed) | set(table))

    # ---- token cost

    def receipt_chars(self, ids, *, prompt):
        """Characters of the receipt an event with these fresh ids injects.

        Replicates hooks/rule-pack-preuse-reselection._route_delivery's render
        (same keys, the real ROUTE_INSTRUCTION, the real fit_rules under the
        real cap) with fixed-width stand-ins for digests and identities. The
        prompt receipt has the same skeleton plus probabilities."""
        rr = self.rule_routes
        pad = "0" * 64
        base = {"schema": rr.ROUTE_RECEIPT_SCHEMA, "client": "claude",
                "session_id": "0" * 36, "turn_id": None, "tool_use_id": "toolu_" + "0" * 22,
                "tool_name": "Bash", "tool_input_sha256": pad, "routes_digest": pad,
                "triggers_digest": pad, "map_digest": pad, "source_digest": pad,
                "trigger_ids": [], "route_rule_ids": [], "packs": [],
                "identity": {"agent_principal_id": "0" * 36,
                             "runtime_principal": "joe-local", "sponsoring_human_id": "0" * 36},
                "rule_ids": list(ids), "not_found": [], "instruction": rr.ROUTE_INSTRUCTION,
                "rule_delivery": {"mode": "shadow", "declared_packs": [], "packs_not_found": []},
                "receipt_id": pad}

        def render(full, overflow):
            row = dict(base, rules=[{"id": r["id"], "statement": r["statement"]} for r in full],
                       overflow=overflow)
            if prompt:
                row["probabilities"] = {r["id"]: 1.0 for r in full}
            return self.preuse.canonical(row).decode("utf-8")
        rules = [{"id": rid, "statement": self.statements.get(rid, "")} for rid in sorted(ids)]
        full, overflow = rr.fit_rules(rules, render, always_on=set())
        return rr.context_chars(render(full, overflow)), bool(overflow)


def _raise(*_args, **_kwargs):
    raise RuntimeError("Jev judgment disabled for the deterministic eval")


# ----------------------------------------------------------------- replay

def replay(world, case):
    """[{kind, ids, fresh, tokens, overflow}] for the case's events, in order."""
    events = []
    ids = world.prompt_ids(case)
    if case["prompt"].strip():
        chars, over = world.receipt_chars(ids, prompt=True) if ids else (0, False)
        events.append({"kind": "prompt", "tool": None, "ids": ids, "fresh": ids,
                       "tokens": chars / CHARS_PER_TOKEN, "overflow": over})
    seen = set()
    for index, call in enumerate(case["tool_calls"]):
        got = world.tool_ids(case, index, call)
        fresh = [rid for rid in got if (rid, call["tool_name"]) not in seen]
        seen.update((rid, call["tool_name"]) for rid in fresh)
        chars, over = world.receipt_chars(fresh, prompt=False) if fresh else (0, False)
        events.append({"kind": "tool", "tool": call["tool_name"], "ids": got, "fresh": fresh,
                       "tokens": chars / CHARS_PER_TOKEN, "overflow": over})
    return events


def grade(world, case, events):
    expected = set(world.expected(case))
    gold = set(case["gold"]) | set(case["disputed"])
    delivered = {rid for ev in events for rid in ev["ids"]}
    hit = delivered & expected
    false = {rid for rid in delivered - gold if rid in world.labelled}
    tokens = sum(ev["tokens"] for ev in events)
    return {"expected": sorted(expected), "delivered": sorted(delivered),
            "hit": sorted(hit), "missed": sorted(expected - delivered),
            "false": sorted(false), "events": len(events), "tokens": tokens,
            "recall": (len(hit) / len(expected)) if expected else None,
            "over_cap_events": sum(1 for ev in events if ev["overflow"])}


@E.guarded_tuning
def run(split, variant, out_dir=None):
    world = World()
    rows = []
    for case in load_cases(split):
        events = replay(world, case)
        g = grade(world, case, events)
        rows.append({"prompt_id": case["id"], "rep": 0, "prompt": case["prompt"][:400],
                     "tags": [case["stratum"], case["split"], case["source"],
                              "should_not_fire" if not g["expected"] else "owes_rules"],
                     "split": case["split"], "source": case["source"],
                     "stratum": case["stratum"], "kind": case["kind"], "note": case["note"],
                     "tool_names": [c["tool_name"] for c in case["tool_calls"]],
                     "status": "ok", "stop_reason": "end_turn", "model": "deterministic",
                     "detail": g,
                     "grade": _grade_dict(g)})
    if out_dir:
        write_run(out_dir, variant, rows)
    return rows


def _grade_dict(g):
    """Per-case metrics for the report. recall exists only where a rule is owed
    and clean only where none is (a should-not-fire case), so each column's
    mean is over the cases it is about."""
    grade = {"false_deliveries": len(g["false"]), "tokens": round(g["tokens"], 1)}
    if g["expected"]:
        grade["recall"] = g["recall"]
    else:
        grade["clean"] = 0.0 if g["false"] else 1.0
    return grade


def write_run(out_dir, variant, rows):
    vdir = os.path.join(out_dir, variant)
    os.makedirs(os.path.join(vdir, "traces"), exist_ok=True)
    result_path = os.path.join(vdir, "results.jsonl")
    existing = {}
    if os.path.exists(result_path):
        with open(result_path, encoding="utf-8") as handle:
            existing = {row["prompt_id"]: row for row in (json.loads(line) for line in handle if line.strip())}
    existing.update({row["prompt_id"]: row for row in rows})
    with open(result_path, "w", encoding="utf-8") as handle:
        for row in (existing[key] for key in sorted(existing)):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    for row in rows:
        trace = [{"role": "user", "content": row["prompt"] or "(tool-only turn)"},
                 {"role": "assistant", "content": json.dumps(row["detail"], indent=1)}]
        with open(os.path.join(vdir, "traces", f"{row['prompt_id']}_rep0.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(trace, handle)


# ----------------------------------------------------------------- metrics

def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else float("nan")


def summarize(rows):
    """Aggregate metrics for a set of result rows."""
    d = [r["detail"] for r in rows]
    owes = [x for x in d if x["expected"]]
    quiet = [x for x in d if not x["expected"]]
    n_exp = sum(len(x["expected"]) for x in d)
    n_hit = sum(len(x["hit"]) for x in d)
    n_del = sum(len(x["delivered"]) for x in d)
    n_false = sum(len(x["false"]) for x in d)
    events = sum(x["events"] for x in d)
    return {
        "cases": len(rows), "owes_cases": len(owes), "should_not_fire_cases": len(quiet),
        "events": events,
        "recall_micro": n_hit / n_exp if n_exp else float("nan"),
        "recall_macro": _mean(x["recall"] for x in owes),
        "expected_rules": n_exp, "hit_rules": n_hit,
        "delivered_rules": n_del, "false_rules": n_false,
        "precision": (n_del - n_false) / n_del if n_del else float("nan"),
        "false_per_event": n_false / events if events else float("nan"),
        "sn_dirty_cases": sum(1 for x in quiet if x["false"]),
        "sn_false_rules": sum(len(x["false"]) for x in quiet),
        "sn_any_delivery_cases": sum(1 for x in quiet if x["delivered"]),
        "tokens_total": sum(x["tokens"] for x in d),
        "tokens_per_event": sum(x["tokens"] for x in d) / events if events else float("nan"),
        "over_cap_events": sum(x["over_cap_events"] for x in d),
    }


def per_case_vectors(rows):
    return {r["prompt_id"]: r["detail"] for r in rows}


def paired_bootstrap(base_rows, new_rows, stat, reps=2000, seed=20260929):
    """(delta, lo, hi): 95% percentile interval of stat(new) - stat(base) over
    cases resampled with replacement, same resample for both variants."""
    ids = sorted(set(r["prompt_id"] for r in base_rows) & set(r["prompt_id"] for r in new_rows))
    b = {r["prompt_id"]: r for r in base_rows}
    n = {r["prompt_id"]: r for r in new_rows}
    rng = random.Random(seed)
    point = stat([n[i] for i in ids]) - stat([b[i] for i in ids])
    draws = []
    for _ in range(reps):
        pick = [rng.choice(ids) for _ in ids]
        draws.append(stat([n[i] for i in pick]) - stat([b[i] for i in pick]))
    draws.sort()
    return point, draws[int(0.025 * reps)], draws[int(0.975 * reps) - 1]


def boot_ci(rows, stat, reps=2000, seed=20260929):
    rng = random.Random(seed)
    ids = list(range(len(rows)))
    draws = sorted(stat([rows[rng.choice(ids)] for _ in ids]) for _ in range(reps))
    return draws[int(0.025 * reps)], draws[int(0.975 * reps) - 1]


STATS = {
    "false_per_event": lambda rs: summarize(rs)["false_per_event"],
    "recall_micro": lambda rs: summarize(rs)["recall_micro"],
    "false_rules": lambda rs: summarize(rs)["false_rules"],
    "sn_false_rules": lambda rs: summarize(rs)["sn_false_rules"],
    "tokens_per_event": lambda rs: summarize(rs)["tokens_per_event"],
    "precision": lambda rs: summarize(rs)["precision"],
}


def verdict(base, new, goal):
    """Select frozen candidates on development; historical runs remain train-only."""
    partition = "development" if os.environ.get("CARR_EVAL_SPLIT") else "train"
    reasons, keep = [], True
    b, n = load_run(base, partition), load_run(new, partition)
    d = {partition: {name: paired_bootstrap(b, n, stat) for name, stat in STATS.items()}}
    stats = d[partition]
    target, sign = (("recall_micro", 1) if goal == "recall" else ("tokens_per_event", -1))
    t_pt, t_lo, t_hi = stats[target]
    if not ((t_lo > 0) if sign > 0 else (t_hi < 0)):
        keep = False
        reasons.append(f"{partition} {target} delta {t_pt:+.4f} [{t_lo:+.4f}, {t_hi:+.4f}] not clear of zero")
    if stats["sn_false_rules"][0] > 0:
        keep = False
        reasons.append(f"{partition} should-not-fire false deliveries rose {stats['sn_false_rules'][0]:+.0f}")
    if stats["false_per_event"][0] > 1e-9:
        keep = False
        reasons.append(f"{partition} false deliveries per event rose {stats['false_per_event'][0]:+.4f}")
    if goal == "tokens" and stats["recall_micro"][0] < -1e-9:
        keep = False
        reasons.append(f"{partition} recall fell {stats['recall_micro'][0]:+.4f}")
    return keep, reasons, d


def load_run(variant, split=None):
    path = os.path.join(RUNS, variant, "results.jsonl")
    with open(path, "r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    return [r for r in rows if split in (None, "all", r["split"])]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--split", default="development" if os.environ.get("CARR_EVAL_SPLIT") else "train", choices=("train", "development", "test", "all"))
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "NEW"))
    parser.add_argument("--print", action="store_true", help="print the summary only; write nothing")
    parser.add_argument("--verdict", nargs=3, metavar=("BASE", "NEW", "GOAL"),
                        help="keep/revert call for NEW against BASE; GOAL is recall or tokens")
    args = parser.parse_args(argv)
    if args.verdict:
        keep, reasons, _d = verdict(*args.verdict)
        print("KEEP" if keep else "REVERT", "; ".join(reasons))
        return 0
    if args.compare:
        base, new = args.compare
        partitions = ("train", "development") if os.environ.get("CARR_EVAL_SPLIT") else ("train", "test")
        for split in (partitions if args.split == "all" else (args.split,)):
            b, n = load_run(base, split), load_run(new, split)
            print(f"== {split}: {base} -> {new}")
            for name, stat in STATS.items():
                point, lo, hi = paired_bootstrap(b, n, stat)
                print(f"  {name:18s} {stat(b):9.4f} -> {stat(n):9.4f}  delta {point:+.4f}  [{lo:+.4f}, {hi:+.4f}]")
        return 0
    rows = run(args.split, args.variant, None if args.print else RUNS)
    for split in sorted({r["split"] for r in rows}):
        sub = [r for r in rows if r["split"] == split]
        if sub:
            print(split, json.dumps(summarize(sub), sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

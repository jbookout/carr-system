#!/usr/bin/env python3
"""make_report.py — write runs/_state.json, run the report builder, write receipt.json.

The report builder is the claude-api skill's lite builder
(shared/evals/report/build-report-lite.mjs). Pass its path with --builder, or
set CLAUDE_API_SKILL_DIR; without one, only receipt.json and results.md are
written."""
import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run_eval as R  # noqa: E402

KEPT = "kept"
SOURCE_PATHS = (
    "lib/rule_routes.py", "ops/config/rule-routes.v1.json", "ops/rule-jit-compile.py",
    "ops/config/rule-jit-triggers.v1.json", "ops/fixtures/rule-delivery-eval/cases.v2.json",
    "ops/rule-pack-preuse-reselection-selftest.py", "ops/rule-trigger-compile-selftest.py",
    "evals/rule-delivery/README.md", "evals/rule-delivery/explain.py",
    "evals/rule-delivery/freeze_split.py", "evals/rule-delivery/hard_cases.v1.json",
    "evals/rule-delivery/noise.py", "evals/rule-delivery/split.json",
    "evals/rule-delivery/run_eval.py", "evals/rule-delivery/round.sh",
    "evals/rule-delivery/make_report.py", "evals/rule-delivery/selftest.py",
)


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def source_manifest():
    return {path: sha(os.path.join(REPO, path)) for path in SOURCE_PATHS}


def receipt_source_matches(receipt=None):
    if receipt is None:
        with open(os.path.join(HERE, "receipt.json"), encoding="utf-8") as handle:
            receipt = json.load(handle)
    commit = receipt.get("code_sha", "")
    if not isinstance(commit, str) or len(commit) != 40:
        return False
    if receipt.get("source_manifest") != source_manifest():
        return False
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                              cwd=REPO, capture_output=True)
    if ancestor.returncode:
        return False
    for path, expected in receipt["source_manifest"].items():
        blob = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=REPO,
                              capture_output=True)
        if blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != expected:
            return False
    return True


def ledger():
    rows = []
    path = os.path.join(R.RUNS, "ledger.jsonl")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    return rows


def state(best):
    with open(os.path.join(HERE, "split.json"), "r", encoding="utf-8") as handle:
        frozen = json.load(handle)
    train = sorted(i for i, s in frozen["split"].items() if s == "train")
    test = sorted(i for i, s in frozen["split"].items() if s == "test")
    doc = {"current_round": max([r["round"] for r in ledger()] or [0]), "reps": 1,
           "goal": {"target": "recall", "direction": "higher", "hold": ["false_deliveries"]},
           "best": {"round": best, "test_score": None},
           "train_ids": train, "test_ids": test,
           "metrics": [{"id": "recall", "kind": "float", "label": "Recall"},
                       {"id": "clean", "kind": "float", "label": "Quiet clean"},
                       {"id": "false_deliveries", "kind": "float", "label": "False deliv.",
                        "better": "lower"},
                       {"id": "tokens", "kind": "float", "label": "Tokens", "better": "lower"}],
           "perf_fields": []}
    with open(os.path.join(R.RUNS, "_state.json"), "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=1)


def table(variants):
    lines = ["| variant | split | recall (micro) | recall (macro) | false deliveries / event | "
             "quiet cases with a false delivery | tokens / event | precision |",
             "|---|---|---|---|---|---|---|---|"]
    for name in variants:
        for split in ("train", "test"):
            s = R.summarize(R.load_run(name, split))
            lines.append(
                f"| {name} | {split} | {s['recall_micro']:.3f} | {s['recall_macro']:.3f} | "
                f"{s['false_per_event']:.3f} | {s['sn_dirty_cases']}/{s['should_not_fire_cases']} | "
                f"{s['tokens_per_event']:.0f} | {s['precision']:.3f} |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--best", required=True, help="variant name of the code state shipped")
    parser.add_argument("--builder", default=None)
    args = parser.parse_args()
    rounds = ledger()
    kept = [r for r in rounds if r["decision"] == KEPT]
    best_round = max([r["round"] for r in kept] or [0])
    state(best_round)
    if args.builder:
        subprocess.run(["node", args.builder, R.RUNS], check=True)
    variants = ["baseline"] + [f"v{r['round']}" for r in rounds]
    md = ["# Rule-delivery eval results", "", table(variants), "", "## Rounds", ""]
    for r in rounds:
        md.append(f"* v{r['round']} ({r['decision']}, goal {r['goal']}): {r['change']}. {r['gate']}")
    with open(os.path.join(R.RUNS, "results.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(md) + "\n")

    receipt = {"schema": "rule-delivery-eval-receipt/v1",
               "flow": "rule-delivery", "shipped_variant": args.best,
               "evaluation_status": "exploratory_test_used_for_candidate_selection",
               "evaluation_note": ("Historical rounds v1-v3 consulted the test split for keep/revert. "
                                   "Their test intervals are descriptive and are not untouched-holdout evidence."),
               "code_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                                          text=True).stdout.strip(),
               "source_manifest": source_manifest(),
               "split_sha256": json.load(open(os.path.join(HERE, "split.json")))["sha256"],
               "inputs": {"v2_cases": sha(R.V2_CASES), "hard_cases": sha(R.HARD_CASES)},
               "metrics": {},
               "rounds": rounds}
    for name in dict.fromkeys(["baseline", args.best, "pr1325"]):
        if not os.path.exists(os.path.join(R.RUNS, name, "results.jsonl")):
            continue
        receipt["metrics"][name] = {split: R.summarize(R.load_run(name, split))
                                    for split in ("train", "test")}
    deltas = {}
    for split in ("train", "test"):
        b, n = R.load_run("baseline", split), R.load_run(args.best, split)
        deltas[split] = {}
        for name, stat in R.STATS.items():
            point, lo, hi = R.paired_bootstrap(b, n, stat)
            deltas[split][name] = {"baseline": stat(b), "shipped": stat(n), "delta": point,
                                   "ci95": [lo, hi]}
    receipt["shipped_vs_baseline"] = deltas
    with open(os.path.join(HERE, "receipt.json"), "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=1, sort_keys=True, default=str)
    print(table(variants))


if __name__ == "__main__":
    main()

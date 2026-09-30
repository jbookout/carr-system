"""Paired, provider-neutral runner over hash-bound, frozen real judge inputs.

Library entrypoints: freeze(receipts), run(corpus, jev, decisions), cli(argv).
CLI via python -c 'from tools.judge.paired_eval import cli; cli()'. Receipts
need request (state/questions/model), its digest and provenance. Digest-only
usage logs cannot be replayed. No synthetic reconstruction of missing inputs.
No promotion side effects: a report never changes the provider switch.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def _client():
    spec = importlib.util.spec_from_file_location("judge_eval_client", ROOT / "ops/typesafe_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze(receipts):
    cases, seen = [], set()
    for receipt in receipts:
        request = receipt.get("request")
        if (not isinstance(request, dict) or set(request) != {"state", "questions", "model"}
                or not isinstance(request["questions"], dict) or not request["questions"]
                or not isinstance(request["model"], str) or not request["model"]):
            raise ValueError("receipt lacks complete frozen input state/questions/model")
        if receipt.get("work_class") != "system_work":
            raise ValueError("paired evaluation accepts system_work only; runtime is pinned")
        if receipt.get("request_sha256") != digest(request):
            raise ValueError("frozen input digest mismatch")
        rid = receipt.get("receipt_id")
        if not isinstance(rid, str) or not rid or rid in seen:
            raise ValueError("receipt id missing or duplicated")
        seen.add(rid)
        cases.append(copy.deepcopy(receipt))
    if not cases:
        raise ValueError("no frozen real inputs")
    return {"schema": "carr-judge-paired-corpus/v1", "cases": cases,
            "corpus_sha256": digest(cases)}


def percentile(values, q):
    return sorted(values)[max(0, math.ceil(q * len(values)) - 1)] if values else None


def run(corpus, jev, decisions, *, rates=None, repeats=1, clock=time.perf_counter):
    """Both adapters receive identical independent copies, in alternating order.

    Agreement is top-distribution agreement (Noul threshold 0.5). Calibration
    uses gold labels only: binary Brier for Noul, multiclass Brier otherwise,
    plus ten-bin expected calibration error. No gold means null, not accuracy.
    Cost requires supplied USD/million token rates for each named provider.
    Latency is measured afresh per successful adapter call, never historical.
    """
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if corpus.get("corpus_sha256") != digest(corpus.get("cases")):
        raise ValueError("corpus digest mismatch")
    freeze(corpus["cases"])
    ts = _client()
    rows = []
    aggregates = {name: {"latency": [], "tokens": [], "brier": [], "confidence": [],
                         "errors": 0, "models": set()} for name in ("jev", "decisions")}
    same = paired = total_questions = 0
    for repeat in range(repeats):
        for index, case in enumerate(corpus["cases"]):
            request = case["request"]
            answers = {}
            order = [("jev", jev), ("decisions", decisions)]
            if (repeat + index) % 2:
                order.reverse()
            for name, adapter in order:
                metrics = aggregates[name]
                started = clock()
                try:
                    result = adapter(copy.deepcopy(request["state"]), copy.deepcopy(request["questions"]), model=request["model"])
                    elapsed = max(0, (clock() - started) * 1000)
                    if not ts.usable_judgment(result, request["questions"]):
                        raise ValueError("invalid typed judgment or unmeasured usage")
                    distributions = {qid: ts.answer_distribution(q, result["answers"][qid])
                                     for qid, q in request["questions"].items()}
                    if not all(d["distribution_complete"] for d in distributions.values()):
                        raise ValueError("incomplete probability distribution")
                    briers, confidence = [], []
                    for qid, gold in case.get("gold", {}).items():
                        d = distributions[qid]
                        target = str(gold).lower() if isinstance(gold, bool) else str(gold)
                        if target not in d["distribution"]:
                            raise ValueError("gold label not in offered distribution")
                        if d["type"] == "noul":
                            brier = (d["distribution"]["true"] - (target == "true")) ** 2
                        else:
                            brier = sum((p - (label == target)) ** 2 for label, p in d["distribution"].items())
                        briers.append(brier)
                        confidence.append((d["top_probability"], d["top"] == target))
                    answers[name] = distributions
                    metrics["latency"].append(elapsed)
                    metrics["tokens"].append(result["usage"])
                    metrics["models"].add(result["model"])
                    metrics["brier"].extend(briers)
                    metrics["confidence"].extend(confidence)
                    rows.append({"receipt_id": case["receipt_id"], "repeat": repeat, "provider": name,
                                 "status": "ok", "model": result["model"], "latency_ms": elapsed,
                                 "usage": result["usage"], "answers": result["answers"]})
                except Exception as error:
                    metrics["errors"] += 1
                    # Provider exceptions can quote a request or key. Only the
                    # stub's constant is safe; never serialize arbitrary text.
                    reason = ("decisions contract not yet verified / no key" if
                              str(error) == "decisions contract not yet verified / no key" else type(error).__name__)
                    rows.append({"receipt_id": case["receipt_id"], "repeat": repeat,
                                 "provider": name, "status": "error", "reason": reason})
            if len(answers) == 2:
                paired += 1
                for qid in request["questions"]:
                    total_questions += 1
                    same += answers["jev"][qid]["top"] == answers["decisions"][qid]["top"]
    providers = {}
    for name, m in aggregates.items():
        confidences = m["confidence"]
        ece = None
        if confidences:
            ece = 0
            for bucket in range(10):
                group = [(p, correct) for p, correct in confidences if min(9, int(p * 10)) == bucket]
                if group:
                    ece += abs(sum(p for p, _ in group) - sum(c for _, c in group)) / len(confidences)
        rate = (rates or {}).get(name)
        cost = None
        if rate is not None and m["tokens"]:
            if (set(rate) != {"input_usd_per_million", "output_usd_per_million"} or
                    any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in rate.values())):
                raise ValueError("invalid explicit token prices")
            cost = sum(u["input_tokens"] * rate["input_usd_per_million"] +
                       u["output_tokens"] * rate["output_usd_per_million"] for u in m["tokens"]) / 1e6
        providers[name] = {"models": sorted(m["models"]), "successes": len(m["latency"]), "errors": m["errors"],
                           "p50_latency_ms": percentile(m["latency"], .5), "p95_latency_ms": percentile(m["latency"], .95),
                           "brier": sum(m["brier"]) / len(m["brier"]) if m["brier"] else None,
                           "labelled_questions": len(m["brier"]), "ece": ece, "cost_usd": cost,
                           "usage": m["tokens"], "rates": rate}
    return {"schema": "carr-judge-paired-report/v1", "corpus_sha256": corpus["corpus_sha256"],
            "status": "complete" if paired == len(corpus["cases"]) * repeats else "incomplete",
            "paired_successes": paired, "paired_questions": total_questions,
            "agreement": same / total_questions if total_questions else None,
            "providers": providers, "rows": rows, "promotion": "not_performed"}


def cli(argv=None):
    parser = argparse.ArgumentParser(description="Paired judge evaluation; never flips routing")
    parser.add_argument("receipts", help="JSON array of frozen, input-bearing real receipts")
    parser.add_argument("--output", required=True)
    parser.add_argument("--rates", help="JSON provider token prices, supplied explicitly")
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args(argv)
    ts = _client()
    def live_jev(state, questions, **options):
        return ts._ask_jev(state, questions, caller="judge_paired_eval", cache_ttl_seconds=0, **options)
    report = run(freeze(json.loads(Path(args.receipts).read_text())), live_jev,
                 ts.JUDGE.provider_decisions, repeats=args.repeats,
                 rates=json.loads(Path(args.rates).read_text()) if args.rates else None)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    return report

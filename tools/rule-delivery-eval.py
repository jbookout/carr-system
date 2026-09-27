#!/usr/bin/env python3
"""rule-delivery-eval.py — replay labelled cases through every rule-delivery
path and report precision, recall and F1 per path and for the system.

The logic lives in ops/rule_delivery_eval.py (a library, for the reason
ops/typesafe_client.py documents); this is its command line.

    tools/rule-delivery-eval.py                       # committed synthetic fixture, Jev off
    tools/rule-delivery-eval.py --cases out/rule-delivery-eval/gold.jsonl \\
        --jev live --workers 4 --corpus out/rule-delivery-eval/corpus-live.json

DRY RUN ALWAYS. No production log, cache or audit file is written (see the
library docstring); the only files written are report.json and report.md in
--out-dir. With --jev live the Jev-backed paths make real Jev requests (about
29 per human case) whose call receipts go to --calls-log (default: nowhere).

Real labelled situations contain partners' words and must stay under out/
(gitignored). The committed fixture under ops/fixtures/ is synthetic.
"""
import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO, "ops", "fixtures", "rule-delivery-eval", "cases.v1.json")


def _library():
    import importlib.util
    path = os.path.join(REPO, "ops", "rule_delivery_eval.py")
    spec = importlib.util.spec_from_file_location("rule_delivery_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", default=FIXTURE)
    parser.add_argument("--jev", choices=("off", "live"), default="off")
    parser.add_argument("--workers", type=int, default=1,
                        help="cases judged concurrently on the Jev-backed paths")
    parser.add_argument("--corpus", help="live corpus JSON ({rules:[{id, statement}]}) "
                        "for one-line summaries and untagged live ids")
    parser.add_argument("--calls-log", default=os.devnull,
                        help="where Jev call receipts go (default: discarded)")
    parser.add_argument("--out-dir", default=os.path.join(REPO, "out", "rule-delivery-eval"))
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--rescore", help="a saved report.json: re-score its deliveries "
                        "against --cases instead of running the paths again (no Jev)")
    args = parser.parse_args(argv)

    ev = _library()
    cases = ev.load_cases(args.cases)
    statements, live_ids = {}, []
    if args.corpus:
        with open(args.corpus, "r", encoding="utf-8") as handle:
            rows = json.load(handle).get("rules") or []
        statements = {row["id"]: row.get("statement") or "" for row in rows}
        live_ids = list(statements)
    else:
        with open(os.path.join(REPO, "ops", "config", "rule-selection-corpus.v1.json"),
                  "r", encoding="utf-8") as handle:
            statements = {row["id"]: row.get("statement") or ""
                          for row in json.load(handle).get("rules") or []}
    meta = ev.rule_meta(REPO, corpus=live_ids)
    if args.rescore:
        with open(args.rescore, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        deliveries, errors = ev.deliveries_from_report(saved), saved.get("errors") or {}
        jev_mode = saved.get("jev")
    else:
        adapters = ev.build_adapters(REPO, jev=args.jev, calls_log=args.calls_log)
        deliveries, errors = ev.run_adapters(cases, adapters, workers=max(1, args.workers))
        jev_mode = args.jev
    ev.add_system_rows(deliveries)
    report = ev.score(cases, deliveries, ev.universes(meta, list(deliveries)), meta,
                      labelled=set(live_ids) if live_ids else None)
    report["dry_run"] = True
    report["jev"] = jev_mode
    report["errors"] = {name: errs for name, errs in errors.items() if errs}
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
    markdown = ev.render_markdown(report, statements, top=args.top)
    with open(os.path.join(args.out_dir, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(markdown)
    print(markdown)
    if report["errors"]:
        print("adapter errors (scored as empty delivery):",
              json.dumps({k: len(v) for k, v in report["errors"].items()}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

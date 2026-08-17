#!/usr/bin/env python3
"""Keep the first isolated-staging retrieval measurement honest and immutable."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "evals" / "retrieval" / "observations"
QUERY_FRAGMENTS = (
    "record layer outage diagnosis runbook",
    "playbook self improvement review cycle",
    "database service unavailable troubleshooting steps",
    "how the operating playbook learns from mistakes",
    "outage communication template",
    "retired retrieval lifecycle fixture",
    "review cycle after a record layer outage",
)


def check(label: str, condition: bool) -> None:
    print(f"  {'ok' if condition else 'FAIL'}  {label}")
    if not condition:
        raise AssertionError(label)


def main() -> int:
    policy_observations = sorted(ROOT.glob("situation-retrieval.*.measured.*.json"))
    selections = sorted(ROOT.glob("situation-retrieval-policy-selection.measured.*.json"))
    check("exactly two policy observations and one selection report are frozen",
          len(policy_observations) == 2 and len(selections) == 1)

    measured = [json.loads(path.read_text(encoding="utf-8")) for path in policy_observations]
    report = json.loads(selections[0].read_text(encoding="utf-8"))
    check("both shipped policies were measured in isolated staging",
          {row["policy_id"] for row in measured} == {"lexical-dominant-v1", "coequal-normalized-v1"}
          and all(row.get("status") == "measured" and row.get("environment") == "staging" for row in measured))
    check("the measured selector fails closed", report.get("status") == "fail")
    check("each real policy scorecard records two passes and five failures",
          all(card.get("summary") == {"failed": 5, "overall": "fail", "passed": 2}
              for card in report.get("scorecards", [])))
    check("observation artifacts carry no raw D2 query text",
          not any(fragment in path.read_text(encoding="utf-8")
                  for path in policy_observations for fragment in QUERY_FRAGMENTS))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    check("README forbids using the failed baseline as completion evidence",
          "must not be used to freeze or" in readme and "complete WR-AI-006" in readme)
    print("PASS: measured retrieval failure remains explicit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

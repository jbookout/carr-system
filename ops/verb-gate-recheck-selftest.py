#!/usr/bin/env python3
"""verb-gate-recheck-selftest.py — unit-level proof for hooks/verb_gate_recheck.py's
parser (bypass audit C33, 2026-09-24).

ops/guard-selftest.py already proves the end-to-end artifact (the real
guard-unattended.py process, fed a Bash payload, judged on its exit code) --
that is the test that matters for "does the door actually close." This file
is narrower and cheaper: it imports parse_run_sh_call directly to pin down the
parsing edge cases (flags before the verb, quoting, unrelated commands,
malformed JSON) without paying for a subprocess spawn per case, and it is not
a substitute for the guard-selftest.py cases.
"""
import os
import sys

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks")
sys.path.insert(0, os.path.abspath(HOOKS))

from verb_gate_recheck import parse_run_sh_call, GATES_FOR_VERB, VERB_NAMES  # noqa: E402

CASES = []


def case(name, cmd, expect):
    CASES.append((name, cmd, expect))


case("plain call", './run.sh call add-loop \'{"kind":"idea"}\'',
     ("add-loop", {"kind": "idea"}))
case("no leading ./", 'run.sh call teach \'{"statement":"x"}\'',
     ("teach", {"statement": "x"}))
case("break-glass flags before the verb",
     './run.sh call --branch rehearse-0031 --reason "why" add-loop \'{"a":1}\'',
     ("add-loop", {"a": 1}))
case("reason only", './run.sh call --reason "why" record-defect \'{"claimed":"a","actual":"b"}\'',
     ("record-defect", {"claimed": "a", "actual": "b"}))
case("unrelated verb is not ours to gate",
     './run.sh call read-loop \'{"id":"1"}\'', None)
case("list-verbs is not a gated verb",
     "./run.sh call list-verbs '{}'", None)
case("no run.sh call at all", "echo hello", None)
case("run.sh but not call", "./run.sh health", None)
case("malformed JSON fails open (no recheck, not a false deny)",
     "./run.sh call add-loop '{not json'", None)
case("JSON that is not an object fails open",
     "./run.sh call add-loop '[1,2,3]'", ("add-loop", {}))
case("chained command truncates at the separator",
     './run.sh call add-loop \'{"kind":"idea"}\' && echo done',
     ("add-loop", {"kind": "idea"}))
case("activate-rule with short id",
     "./run.sh call activate-rule '{\"rule_id\":\"abc123\"}'",
     ("activate-rule", {"rule_id": "abc123"}))


def main():
    fails = []
    for name, cmd, expect in CASES:
        got = parse_run_sh_call(cmd)
        ok = got == expect
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -> got {got!r}, want {expect!r}"))
        if not ok:
            fails.append(name)

    # Sanity: every verb the four gates actually watch has a mapping, and the
    # mapping never names a gate file that does not exist.
    for verb in VERB_NAMES:
        gates = GATES_FOR_VERB.get(verb)
        if not gates:
            print(f"  FAIL no GATES_FOR_VERB mapping for {verb!r}")
            fails.append(f"mapping:{verb}")
            continue
        for gate in gates:
            path = os.path.join(os.path.abspath(HOOKS), gate)
            if not os.path.exists(path):
                print(f"  FAIL {verb!r} names missing gate file {gate!r}")
                fails.append(f"missing-gate:{verb}:{gate}")

    print(f"\nverb-gate-recheck-selftest: {len(CASES) + len(VERB_NAMES) - len(fails)}/"
          f"{len(CASES) + len(VERB_NAMES)} passed")
    if fails:
        print("FAILED: " + "; ".join(fails))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

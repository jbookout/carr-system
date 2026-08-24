#!/usr/bin/env python3
"""Acceptance fixtures for the completion-evidence Stop gate.

WRITTEN BEFORE THE WIDENING (rule e65efc68). The CASES block below is the
original narrow gate's regression set and every one of it still has to pass:
a widening that quietly drops an existing fire is a weakening.

CLAUSE_CASES is the new half, and it is the acceptance test for fix A of the
2026-08-23 completion-integrity council. What it has to prove:

  1. THE CASE STUDY FIRES. An order with two clauses ("recategorize the rules"
     AND "make them load into every session"), a session that finishes the
     first and closes, blocks — naming the clause that has no receipt. This is
     the slice-standing-for-an-order shape Grok's chair named as the actual
     defect, and Joe's stated success signal: at least one correct fire on it.

  2. IT IS NOT A BANNED-WORD LIST. The same transcript blocks when the close
     says "Wrapped up \u2014 should be good", and blocks again when the close
     contains no completion vocabulary at all. If a weaker verb or plain
     silence were an escape, the gate would be theater within a week \u2014 which
     is Grok's explicit design constraint on this fix.

  3. NAMING THE RESIDUAL IS THE EXIT. The only way past an unaccounted clause
     is a receipt from its consumer surface, or saying plainly that the clause
     is not done. That is the whole predicate.

  4. THE DUAL BINDS. Where the session's own record holds a landed receipt, it
     may not close by calling the work unbuilt. That is the 24-rebuild failure
     (ops/built_unclosed.py header) in its Stop-gate form.

  5. THE FLOOR HOLDS. An order that yields no clause still gets the original
     predicate. Widening never makes the gate quieter.

RUNNING IT. No database, no network, no production access:

    .venv/bin/python ops/completion-evidence-gate-selftest.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "completion_evidence", os.path.join(REPO, "hooks", "completion-evidence-gate.py")
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text):
    return {"type": "assistant", "message": {"role": "assistant", "content": text}}


def tool(name, value=None):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": value or {}}
    ]}}


def codex_user(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": value},
        ],
    }}


def codex_wrapper(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "The following is the Codex agent history added since your last approval"},
            {"type": "input_text", "text": value},
        ],
    }}


def codex_tool(name, value):
    return {"type": "response_item", "payload": {
        "type": "custom_tool_call", "name": name, "input": value,
    }}


def codex_assistant(value):
    return {"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": value},
        ],
    }}


CASES = [
    ("ordinary answer", [user("status"), assistant("The board is current.")], False),
    ("one file patch is friction-free", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x"}), assistant("Done.")], False),
    ("unsupported completion after multi-file patch", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done.")], True),
    ("fresh read permits completion", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), tool("Read"), assistant("Done and verified.")], False),
    ("explicit disclosure permits close", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done, but unverified because the server is unavailable.")], False),
    ("no tests failed is not a disclosure", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done; no tests failed.")], True),
    ("skipped none is not a disclosure", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done; skipped none.")], True),
    ("verification failed needs a reason", [user("fix it"), tool("apply_patch", {"command": "*** Update File: a.py\n+x\n*** Update File: b.py\n+y"}), assistant("Done, but verification failed because the test server is unavailable.")], False),
    ("record write requires evidence", [user("reconcile"), tool("mcp__carr__update-deal"), assistant("Done.")], True),
    ("generic call-verb write requires evidence", [user("reconcile"), tool("mcp__carr__call-verb", {"verb": "update-deal", "args": {}}), assistant("Done.")], True),
    ("recipient required for delivery", [user("update it"), tool("mcp__carr__update-deal"), assistant("Delivered after the update.")], True),
    ("named recipient permits delivery", [user("update it"), tool("mcp__carr__update-deal"), tool("Read"), assistant("Delivered to Dell after a fresh read.")], False),
    ("deploy gets checked", [user("release"), tool("Bash", {"command": "npx wrangler deploy"}), assistant("Deployed.")], True),
    ("unrelated historical tool does not matter", [user("status"), tool("Read"), assistant("Done with the explanation.")], False),
    ("Codex nested CARR write requires evidence", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "const row = await tools.mcp__carr__update_deal({ id: 'd1', stage: 'LOI' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex nested generic call verb requires evidence", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__call_verb({ verb: 'update-deal', args: { id: 'd1' } });"),
        codex_assistant("Done."),
    ], True),
    ("Codex nested CARR read is fresh evidence", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
        codex_tool("exec", "const fresh = await tools.mcp__carr__get_deal({ id: 'd1' });"),
        codex_assistant("Done and verified."),
    ], False),
    ("Codex history wrapper cannot reset the mutation window", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
        codex_wrapper("Earlier work and environment context."),
        codex_assistant("Done."),
    ], True),
    ("Codex create national market deal requires evidence", [
        codex_user("add market"),
        codex_tool("exec", "await tools.mcp__carr__create_national_market_deal({ account: 'Musicologie' });"),
        codex_assistant("Done."),
    ], True),
    ("Codex patch deal field requires evidence", [
        codex_user("update market"),
        codex_tool("exec", "await tools.mcp__carr__patch_deal_field({ id: 'd1', field: 'phase' });"),
        codex_assistant("Done."),
    ], True),
    ("Codex doctrine write requires evidence", [
        codex_user("write doctrine"),
        codex_tool("exec", "await tools.mcp__carr__write_doctrine_section({ doctrine_id: 'x' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex workflow acceptance requires evidence", [
        codex_user("accept the canary"),
        codex_tool("exec", "await tools.mcp__carr__accept_workflow({ workflow_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex legacy schedule disable requires evidence", [
        codex_user("retire the old schedule"),
        codex_tool("exec", "await tools.mcp__carr__disable_legacy_schedule({ workflow_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex sourced problem capture requires evidence", [
        codex_user("capture this operating problem"),
        codex_tool("exec", "await tools.mcp__carr__report_problem({ idempotency_key: 'fixture' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex Work Request triage requires evidence", [
        codex_user("review and triage this request"),
        codex_tool("exec", "await tools.mcp__carr__review_and_triage({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex sourced ready-plan proposal requires evidence", [
        codex_user("prepare the bounded plan"),
        codex_tool("exec", "await tools.mcp__carr__propose_ready_plan({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex sourced ready-plan acceptance requires evidence", [
        codex_user("accept the exact plan"),
        codex_tool("exec", "await tools.mcp__carr__accept_ready_plan({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex outcome-feedback proposal requires evidence", [
        codex_user("propose observed outcome feedback"),
        codex_tool("exec", "await tools.mcp__carr__propose_outcome_feedback({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("Codex outcome-feedback acceptance requires evidence", [
        codex_user("accept the exact observed outcome"),
        codex_tool("exec", "await tools.mcp__carr__accept_outcome_feedback({ human_ref: 'WR-000001' });"),
        codex_assistant("Completed."),
    ], True),
    ("CARR read action permits completion", [
        codex_user("reconcile Musicologie"),
        codex_tool("exec", "await tools.mcp__carr__patch_deal_field({ id: 'd1' });"),
        codex_tool("exec", "await tools.mcp__carr__review_queue({});"),
        codex_assistant("Done and verified."),
    ], False),
]


# --------------------------------------------------------------- clause half
# Fixtures for fix A: the gate binds on ANY completion claim against an
# originating order, requiring the order's clauses, each clause's named
# consumer surface, and a fresh consumer-side receipt per delivered clause.


def read(path, is_error=False):
    """A Read call plus its result, so a fixture can say whether the file was there."""
    return [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": path}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error,
             "content": "no such file" if is_error else "1\tcontents"}]}},
    ]


def patch(*paths):
    body = "".join(f"*** Update File: {p}\n+x\n" for p in paths)
    return tool("apply_patch", {"command": body})


def flat(rows):
    out = []
    for row in rows:
        out.extend(row if isinstance(row, list) else [row])
    return out


# The case study, as a transcript. Joe ordered two clauses; the session
# finished the first one and closed against it.
CASE_STUDY_ORDER = ("recategorize the 218 rules into enforcement classes "
                    "and make them load into every session")
CASE_STUDY_WORK = [
    user(CASE_STUDY_ORDER),
    patch("ops/config/rule-enforcement-map.json", "ops/rule-enforcement-map-check.py"),
    tool("Bash", {"command": ".venv/bin/python -m pytest ops/rule-map-selftest.py"}),
]

CLAUSE_CASES = [
    # 1. THE SUCCESS SIGNAL: a slice standing for the order.
    ("case study: finished slice closes against a two-clause order",
     CASE_STUDY_WORK + [assistant("The enforcement map is complete and reviewed.")],
     True, "load into every session"),

    # 2. NOT A BANNED-WORD LIST. A weaker verb is not an escape...
    ("weaker verb is not an escape",
     CASE_STUDY_WORK + [assistant("Wrapped up \u2014 should be good.")],
     True, "load into every session"),

    # ...and neither is saying nothing at all.
    ("silence about the clause fires too",
     CASE_STUDY_WORK + [assistant("The map now carries 218 rows across four classes.")],
     True, "load into every session"),

    # 3. NAMING THE RESIDUAL IS THE EXIT.
    ("disclosed residual closes the clause",
     CASE_STUDY_WORK + [assistant(
         "The enforcement map is complete. Scoped loading into every session is "
         "not built \u2014 no loader exists yet, so nothing loads at boot.")],
     False, ""),

    # ...as is an actual receipt from the clause's consumer surface.
    ("live-boot receipt closes the clause",
     CASE_STUDY_WORK + [
         tool("mcp__carr__standing-context"),
         assistant("The map is complete and standing-context now loads all 218.")],
     False, ""),

    # A generic read is NOT a receipt for a live-surface clause. This is the
    # difference between the old gate and this one: builder-context evidence
    # cannot account for a clause whose consumer is somewhere else.
    ("a plain file read does not account for a live-surface clause",
     CASE_STUDY_WORK + [tool("Read", {"file_path": "ops/config/rule-enforcement-map.json"}),
                        assistant("Complete and verified.")],
     True, "load into every session"),

    # 4. STANDING CLAUSES (Joe's ruling): scope decay across turns.
    ("unaccounted clause survives a later turn",
     CASE_STUDY_WORK + [
         assistant("Map recategorized."),
         user("now fix the failing tests"),
         patch("tests/a.py", "tests/b.py"),
         tool("Bash", {"command": ".venv/bin/python -m pytest"}),
         assistant("Fixed, and the suite passes.")],
     True, "load into every session"),

    ("an explicit retirement drops the standing clause",
     CASE_STUDY_WORK + [
         assistant("Map recategorized."),
         user("skip the loading part for now, just fix the failing tests"),
         patch("tests/a.py", "tests/b.py"),
         tool("Bash", {"command": ".venv/bin/python -m pytest"}),
         assistant("Fixed, and the suite passes.")],
     False, ""),

    ("a receipt on the earlier turn drops the standing clause",
     CASE_STUDY_WORK + [
         tool("mcp__carr__standing-context"),
         assistant("Map recategorized and loading at boot."),
         user("now fix the failing tests"),
         patch("tests/a.py", "tests/b.py"),
         tool("Bash", {"command": ".venv/bin/python -m pytest"}),
         assistant("Fixed, and the suite passes.")],
     False, ""),

    # 5. CONSUMER SURFACE PER CLAUSE KIND.
    ("deploy clause needs a live probe, not wrangler's exit",
     [user("deploy the worker to production"),
      tool("Bash", {"command": "npx wrangler deploy"}),
      assistant("Deployed.")],
     True, "production"),

    ("a live catalog probe closes the deploy clause",
     [user("deploy the worker to production"),
      tool("Bash", {"command": "npx wrangler deploy"}),
      tool("Bash", {"command": "./run.sh health"}),
      assistant("Deployed; the live catalog still carries every verb.")],
     False, ""),

    ("partner-ready clause needs first use or an honest unconfirmed",
     [user("get the deal room page ready for Dell to use"),
      patch("dealroom/page.tsx", "dealroom/api.ts"),
      tool("Bash", {"command": ".venv/bin/python -m pytest dealroom"}),
      assistant("Ready.")],
     True, "human"),

    ("released-unconfirmed is an honest close for a partner clause",
     [user("get the deal room page ready for Dell to use"),
      patch("dealroom/page.tsx", "dealroom/api.ts"),
      tool("Bash", {"command": ".venv/bin/python -m pytest dealroom"}),
      assistant("The page builds and its tests pass. Dell has not used it yet, "
                "so it is released-unconfirmed rather than live.")],
     False, ""),

    # 6. NOISE CONTROL. Ordinary repo work with its own evidence must stay quiet.
    ("multi-clause repo order with evidence stays quiet",
     [user("write the selftest and then implement the gate"),
      patch("ops/x-selftest.py", "hooks/x.py"),
      tool("Bash", {"command": ".venv/bin/python ops/x-selftest.py"}),
      assistant("Both landed and the selftest passes.")],
     False, ""),

    ("a single mechanical edit is still friction-free",
     [user("add a docstring to foo.py"),
      tool("apply_patch", {"command": "*** Update File: foo.py\n+x"}),
      assistant("Done.")],
     False, ""),
]

# 7. THE DUAL. Where consumer receipts exist, a session may not close by
#    calling the work unbuilt because an attestation verb was skipped.
DUAL_CASES = [
    ("landed receipt contradicts an unbuilt close",
     flat([user("capability-program says 0/51 completed \u2014 is the scoped loader built?"),
           read("hooks/scoped-loader.py"),
           assistant("The scoped loader was never built; nothing is on disk. "
                     "I'll start building it.")]),
     True, "unbuilt"),

    ("no landed receipt, so an unbuilt close is honest",
     flat([user("capability-program says 0/51 completed \u2014 is the scoped loader built?"),
           read("hooks/scoped-loader.py", is_error=True),
           assistant("The scoped loader was never built; nothing is on disk. "
                     "I'll start building it.")]),
     False, ""),

    ("reading a file is not by itself an unbuilt contradiction",
     flat([user("what does the loader do?"),
           read("hooks/scoped-loader.py"),
           assistant("It maps applicability tags onto the boot set.")]),
     False, ""),
]


def machine_text_boundary():
    """Injected machine text is data, never an order.

    Every string here is from a real 2026-08 transcript and every one produced
    a false fire before order_text() existed.
    """
    checks = [
        ('[hooks/drift-assertion-gate.py]: DRIFT ASSERTION \u2014 you are about to '
         'tell Joe that a present state is WRONG', 0),
        ('<scheduled-task name="nightly-record-layer" file="/Users/booko/x.md">'
         'run the nightly chain every night</scheduled-task>', 0),
        ("<system-reminder>send the summary to Dell when done</system-reminder>", 0),
        ("COMPLETION EVIDENCE GATE \u2014 deploy claim needs a live probe", 0),
        # 47 of 51 recipient fires in one replay pass were this single line:
        # a gate's own deny banner read as an order to tell Joe something. The
        # real attribution format carries a space inside its brackets, which
        # the first version of MACHINE_LINE did not allow for.
        ("[/usr/bin/python3 /Users/booko/carr-system/hooks/drift-assertion-gate.py]: "
         "DRIFT ASSERTION \u2014 you are about to tell Joe that a present state is WRONG", 0),
        # ...and a real order alongside injected text still yields its clause.
        ("deploy the worker to production\n<system-reminder>be careful</system-reminder>", 1),
    ]
    outcomes = []
    for raw, count in checks:
        got = mod.order_clauses(mod.order_text(raw))
        ok = len(got) == count
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  machine text yields {len(got)} clauses "
              f"(want {count}): {raw[:58]!r}")
    return all(outcomes)


def prose_is_not_an_order():
    """Description is not instruction.

    Every fragment here is verbatim from a 2026-08 transcript and every one of
    them produced a false clause before is_directive() existed. They are the
    reason this gate is not simply a verb matcher: Joe writes long messages
    full of analysis and quoted output, and a sentence inside one of them is
    not an order just because it contains "deploy".
    """
    prose = [
        "The write-door version of this check only fires when a record gets filed",
        "I am not asking you to deploy anything or to take my word for any of it",
        "tested behind a feature gate, but not promoted to the production default",
        "- Invokes the underlying tool the way this project runs it",
        "Seed prompt must never contain the nonce",
        "tool not installed",
        "The user can send it another message",
        # Second replay pass \u2014 each still fired after the first round of fixes.
        "is Joe's call to schedule",
        "Do not merge, deploy, push, or open a PR",
        "still no rehearsal receipt, so nothing here is closer to production",
        "Tell the user to open /hooks once (reloads config) or restart",
        # Third replay pass.
        "Promotion counted violations only when the rule base table was readable",
        "nightly: it branches STAGING, guarded on the pinned production project id",
        "Loop 420, the first release approval, with Joe's signature",
        "Every handler among the thirty-eight ungated write verbs was read for its SQL",
        '"description": "Name of an already-configured MCP server to invoke",',
    ]
    orders = [
        "deploy the worker to production",
        "wire eventkit to run unattended",
        "make them load into every session",
        "please send the brief to Dell",
        "you should schedule the audit every night",
    ]
    outcomes = []
    for line in prose:
        got = mod.order_clauses(line)
        outcomes.append(not got)
        print(f"{'PASS' if not got else 'FAIL'}  prose yields no clause: {line[:56]!r}")
    for line in orders:
        got = mod.order_clauses(line)
        outcomes.append(bool(got))
        print(f"{'PASS' if got else 'FAIL'}  order still yields a clause: {line[:56]!r}")
    return all(outcomes)


def dual_precision():
    """The dual needs the artifact's whole name, not one shared word."""
    near_miss = flat([
        user("is the loader built?"),
        read("hooks/guard-unattended.py"),
        assistant("The scoped loader does not exist; nothing was ever built."),
    ])
    # "unbuilt now 38" is a COUNT from ops/built_unclosed.py, and a screenshot
    # is not the work. Both fired on real logs.
    metric = flat([
        user("what does the map check say?"),
        read("/tmp/scratch/iso-render.png"),
        assistant("Enforcement map check: OK, active render parity exact, unbuilt now 38."),
    ])
    cases = [("one shared word is not a contradiction", near_miss),
             ("an unbuilt COUNT is not an absence claim", metric)]
    outcomes = []
    for name, recs in cases:
        got, reason = mod.evaluate(recs)
        outcomes.append(not got)
        print(f"{'PASS' if not got else 'FAIL'}  {name} ({reason})")
    return all(outcomes)


def clause_extraction_coverage():
    """The order must decompose into the clauses a human would name."""
    checks = [
        (CASE_STUDY_ORDER, 2, {"repo", "runtime"}),
        ("deploy the worker to production", 1, {"production"}),
        ("send the weekly brief to Dell", 1, {"recipient"}),
        ("schedule the audit to run every night", 1, {"scheduler"}),
        ("admit the 218 rules into the production store", 1, {"store"}),
        ("get the deal room page ready for Dell to use", 1, {"human"}),
        ("what is the status of the map?", 0, set()),
        # Noun forms are not orders. Both of these fired as live-surface
        # clauses before the verb-position check existed, and the first one
        # is from a real 2026-08 transcript.
        ("fix my media encoder install for me", 1, {"repo"}),
        ("fix the deploy script", 1, {"repo"}),
    ]
    outcomes = []
    for text_in, count, classes in checks:
        clauses = mod.order_clauses(text_in)
        got = {c.consumer for c in clauses}
        ok = len(clauses) == count and got == classes
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  clauses of {text_in!r}: "
              f"{len(clauses)} {sorted(got)} (want {count} {sorted(classes)})")
    return all(outcomes)


def floor_preserved():
    """Every original fire must still fire after the widening."""
    outcomes = []
    for name, recs, expected in CASES:
        if not expected:
            continue
        got, _ = mod.evaluate(recs)
        outcomes.append(got)
        if not got:
            print(f"FAIL  widening silenced an original fire: {name}")
    ok = all(outcomes)
    print(f"{'PASS' if ok else 'FAIL'}  floor preserved: "
          f"{sum(outcomes)}/{len(outcomes)} original fires still fire")
    return ok



def registry_prefix_coverage():
    """Keep the family classifier honest against the local live registry when present."""
    registry = os.path.join(REPO, "mcp-server", "src", "tools.js")
    if not os.path.exists(registry):
        print("SKIP  live registry unavailable")
        return True
    script = (
        'import { TOOLS } from "./src/tools.js"; '
        'console.log(JSON.stringify(Object.keys(TOOLS).filter((n) => TOOLS[n].write).sort()))'
    )
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            cwd=os.path.join(REPO, "mcp-server"), text=True,
                            capture_output=True, timeout=30)
    if result.returncode:
        print("SKIP  live registry could not load")
        return True
    writes = json.loads(result.stdout)
    missing = [name for name in writes if not mod.is_write_action(name)]
    reads = ["review-queue", "get-deal", "list-verbs", "catch-me-up", "deal-board", "find"]
    false_writes = [name for name in reads if mod.is_write_action(name)]
    ok = not missing and not false_writes
    print(f"{'PASS' if ok else 'FAIL'}  live registry write coverage: "
          f"{len(writes) - len(missing)}/{len(writes)} writes classified"
          + (f"; missing={','.join(missing)}" if missing else "")
          + (f"; read false positives={','.join(false_writes)}" if false_writes else ""))
    return ok


def authority_family_coverage():
    """Human-only acceptance/retirement and future proposal/approval writes stay gated."""
    actions = [
        "accept-workflow", "disable-legacy-schedule", "approve-work-request",
        "accept-outcome-feedback",
        "propose-cognition-job", "decide-guidance-import-batch",
        "deactivate-guidance-registry",
    ]
    missing = [action for action in actions if not mod.is_write_action(action)]
    ok = not missing
    print(f"{'PASS' if ok else 'FAIL'}  authority workflow family coverage"
          + (f"; missing={','.join(missing)}" if missing else ""))
    return ok


def real_hook_case(kind, non_carr=False):
    if kind == "codex":
        records = [
            codex_user("reconcile"),
            codex_tool("exec", "await tools.mcp__carr__update_deal({ id: 'd1' });"),
            codex_assistant("Done."),
        ]
    else:
        records = [user("reconcile"), tool("mcp__carr__update-deal"), assistant("Done.")]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        for row in records:
            fh.write(json.dumps(row) + "\n")
        path = fh.name
    try:
        payload = {"transcript_path": path, "session_id": "selftest", "stop_hook_active": False}
        if kind == "codex":
            payload = {"transcriptPath": path, "sessionId": "selftest", "stop_hook_active": False,
                       "hook_event_name": "Stop"}
        if non_carr:
            payload["cwd"] = "/private/tmp/non-carr-app"
        hook = os.path.join(REPO, "hooks", "completion-evidence-gate.py")
        result = subprocess.run([os.sys.executable, hook], input=json.dumps(payload), text=True,
                                capture_output=True, timeout=20)
        body = json.loads(result.stdout or "{}")
        return body.get("decision") == "block"
    finally:
        os.unlink(path)


def main():
    outcomes = []
    for name, recs, expected in CASES:
        got, reason = mod.evaluate(recs)
        ok = got == expected
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got} ({reason})")
    for kind in ("claude", "codex"):
        got = real_hook_case(kind)
        outcomes.append(got)
        print(f"{'PASS' if got else 'FAIL'}  {kind} structured Stop block")
    non_carr = not real_hook_case("claude", non_carr=True)
    outcomes.append(non_carr)
    print(f"{'PASS' if non_carr else 'FAIL'}  non-CARR cwd is out of scope")
    for name, recs, expected, reason_part in CLAUSE_CASES + DUAL_CASES:
        got, reason = mod.evaluate(recs)
        ok = got == expected and (not expected or reason_part in reason)
        outcomes.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got} ({reason})")
    outcomes.append(prose_is_not_an_order())
    outcomes.append(dual_precision())
    outcomes.append(machine_text_boundary())
    outcomes.append(clause_extraction_coverage())
    outcomes.append(floor_preserved())
    outcomes.append(registry_prefix_coverage())
    outcomes.append(authority_family_coverage())
    print(f"completion-evidence-gate-selftest: {sum(outcomes)}/{len(outcomes)} passed")
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

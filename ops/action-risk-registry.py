#!/usr/bin/env python3
"""Emit the canonical action-risk registry, DERIVED from the live verb registry.

THE COUNCIL MADE THIS BLOCKING before any mutation UI, on the finding that "the
authority ladders currently diverge across documents". The ladders do diverge, but
a survey of all four established something sharper: NONE of them reaches the
runtime. No R, W, A, O or Level token appears anywhere in the server source. So
this registry is not a reconciliation of four documents. It is a description of
what actually protects each action, derived from the code that runs.

DERIVED, NEVER HAND-MAINTAINED. A hand-typed table of 105 rows is stale the day a
verb changes and nobody notices, which is the failure mode every one of the four
ladders already demonstrates. This reads the live registry, so the registry cannot
drift from the thing it describes.

THE MODEL: authority here is not one axis. Five protections exist and an action
carries exactly one, assigned in this order because each supersedes the next.

  human_boundary          humanOnly. The action's effect BINDS BOTH PARTNERS, so a
                          machine credential is refused. Rule 4039b9b5 names this
                          bearer-token-versus-human, not model-versus-model.
  optimistic_concurrency  base_version required. A stale version is refused and
                          surfaces as a question rather than a silent overwrite.
  upsert_last_writer_wins ON CONFLICT DO UPDATE. Correct for a lease or an
                          idempotent re-assert; deliberate, not accidental.
  append_only             Inserts only. Nothing to lose, so nothing to guard.
  read_only               No write.
  NONE                    Updates an existing row with no version check. Two
                          concurrent callers can silently lose one another's work.
                          THIS IS THE GAP, and it is the only category that is one.

WHY THE COUNT FELL FROM 61 TO 13. Counting only humanOnly made everything protected
by another mechanism look ungoverned. 61 ungated write verbs became 47 once the
doctrine and investigation families were shown to be under optimistic concurrency,
the claim mechanism, or append-only semantics; 38 once the version-checked were
separated; 13 once each handler was read for the SQL it actually runs. Every
reduction came from reading the code rather than the surface.

Run: python3 ops/action-risk-registry.py > control-room/contracts/action-risk-registry.v1.json
"""
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_JS = os.path.join(REPO, "mcp-server", "src", "tools.js")

# The live registry is JavaScript, so node is the only honest reader of it. Parsing
# it with a regex is what produced a wrong count of 82 twice; the verb-count gate
# already imports the module for the same reason.
NODE_DUMP = r"""
import { TOOLS } from './mcp-server/src/tools.js';
const out = {};
for (const [n, t] of Object.entries(TOOLS)) {
  const schema = JSON.stringify(t.inputSchema || {});
  out[n] = {
    write: !!t.write,
    humanOnly: !!t.humanOnly,
    // ANY *_base_version field counts. attach-to-campaign names its guard
    // piece_base_version and an exact-name check missed it, classifying a
    // protected verb as the gap. A version field is a version field.
    base_version: /base_version/.test(schema),
    summary: (t.description || '').split('.')[0].slice(0, 160),
  };
}
process.stdout.write(JSON.stringify(out));
"""


def live_registry():
    r = subprocess.run(["node", "--input-type=module", "-e", NODE_DUMP],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("could not import the verb registry:\n" + r.stderr)
    return json.loads(r.stdout)


def handler_sql(names):
    """Per-verb SQL shape, sliced from the source between one key and the next."""
    src = open(TOOLS_JS, encoding="utf-8").read()
    marks = sorted(((src.find('\n  "%s":' % n), n) for n in names), key=lambda x: x[0])
    marks = [(i, n) for i, n in marks if i > 0]
    shapes = {}
    for idx, (start, name) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(src)
        body = src[start:end].lower()
        shapes[name] = {
            "updates": len(re.findall(r"\bupdate\s+[a-z_.]+\s+set\b", body)),
            "deletes": len(re.findall(r"\bdelete\s+from\b", body)),
            "inserts": len(re.findall(r"\binsert\s+into\b", body)),
            "upserts": len(re.findall(r"on conflict[\s\S]{0,40}do update", body)),
        }
    return shapes


def classify(meta, sql):
    if not meta["write"]:
        return "read_only"
    if meta["humanOnly"]:
        return "human_boundary"
    if meta["base_version"]:
        return "optimistic_concurrency"
    if sql["updates"] == 0 and sql["deletes"] == 0 and sql["upserts"] > 0:
        return "upsert_last_writer_wins"
    if sql["updates"] == 0 and sql["deletes"] == 0:
        return "append_only"
    return "NONE"


def main():
    reg = live_registry()
    sql = handler_sql(reg.keys())
    rows, counts = {}, {}
    for name, meta in sorted(reg.items()):
        shape = sql.get(name, {"updates": 0, "deletes": 0, "inserts": 0, "upserts": 0})
        prot = classify(meta, shape)
        counts[prot] = counts.get(prot, 0) + 1
        rows[name] = {
            "protection": prot,
            "write": meta["write"],
            "binds_both_partners": meta["humanOnly"],
            "base_version_required": meta["base_version"],
            "sql": shape,
            "does": meta["summary"],
        }

    print(json.dumps({
        "contract": "carr-action-risk-registry",
        "version": "1.0.0",
        "status": "phase1_derived",
        "generated_by": "ops/action-risk-registry.py — DERIVED, never hand-edited",
        "purpose": (
            "One row per action the runtime can perform, naming which protection "
            "actually applies. The council made a canonical action-risk registry "
            "blocking before any mutation UI; a survey of all four planning "
            "documents found that none of their ladders reaches the runtime, so "
            "this describes what runs instead of reconciling what is written."),
        "protections": {
            "human_boundary": "humanOnly — the effect binds both partners, so a machine credential is refused",
            "optimistic_concurrency": "base_version required; a stale version is refused as a question, never a silent overwrite",
            "upsert_last_writer_wins": "ON CONFLICT DO UPDATE — deliberate for a lease or an idempotent re-assert",
            "append_only": "inserts only; nothing to lose, so nothing to guard",
            "read_only": "no write",
            "NONE": "updates an existing row with no version check — two callers can silently lose each other's work. THE GAP.",
        },
        "counts": counts,
        "the_gap": sorted(n for n, r in rows.items() if r["protection"] == "NONE"),
        "open_questions": [
            "set-market-agent and set-national-account-owner assign ownership across the team the way reassign-deal does, and reassign-deal IS human_boundary. That asymmetry is Joe's ruling to make, because it changes what a machine credential may do.",
            "attach-to-campaign carries piece_base_version on its reattach path only. The guard sits on the destructive path and the additive path stays cheap, which is correct — but that plain attach refuses an already-attached piece is implied by the reattach contract and was not separately verified.",
        ],
        "verbs": rows,
    }, indent=1))


if __name__ == "__main__":
    main()

# Clause typing — the measured second increment for the completion-evidence gate

Joe approved this on 2026-08-23 as a **second increment with its own
measurement pass**, deliberately not folded into fix A. Fix A shipped at
`14c9a6cc` (`hooks/completion-evidence-gate.py`, 58 fixtures in
`ops/completion-evidence-gate-selftest.py`).

## The gap, verified rather than assumed

Fix A types a clause by its **consumer surface** — production, store, runtime,
scheduler, recipient, human, repo. That answers WHERE a receipt comes from. It
does not answer WHAT KIND of receipt counts, and the two are orthogonal axes.

Run against the shipped gate on 2026-08-23:

    audit the nightly chain and tell me which steps are blocked   -> NO CLAUSE
    figure out why the retrieval lane stalled                     -> NO CLAUSE
    decide whether to retire the exporter                         -> NO CLAUSE
    rule on whether Dell owns the calendar path                   -> NO CLAUSE
    decline the vendor request and close it out                   -> NO CLAUSE
    confirm Dell owns the migration through October               -> NO CLAUSE
    deploy the worker to production                               -> production
    make them load into every session                             -> runtime

The gate models build-shaped clauses only. Analysis, decision and commitment
orders yield no clause and it stays silent on them — fail-quiet rather than
fail-wrong, but silent.

## Why it matters

Sol's chair put disposition-appropriate proof in the council's definition of
done (§2.6): "A decline needs authority and rationale, not an impossible
acceptance test." The 2026-08-22 completion contract that made every decline
impossible to close was exactly a DECISION clause being handed BUILD-clause
proof. In fix A that shape is not merely mishandled — it is invisible, because
a decision clause has no consumer surface in the taxonomy.

## The design

Type each clause the way a What-tree types its leaves, and let the receipt kind
follow the type. Source: `out/x-nurijanian-retrieval.md` (McKinsey issue-tree
method piece, retrieved 2026-08-23), routed in by a peer session.

| Clause type | What it produces | What closes it |
|---|---|---|
| ANALYSIS | evidence | the delivered evidence |
| DECISION | a ruling by an authority | the recorded ruling — NEVER acceptance tests |
| COMMITMENT | ownership or scope | the named owner's confirmation |
| BUILD / SYNTHESIS | an artifact that lands | consumer-side receipt, per fix A |

This generalises the decline lesson instead of special-casing it.

## Two notes for whoever builds it, from replay data rather than theory

1. **Precision, not recall, is the binding constraint.** Fix A's first version
   fired 92 times per 916 Stop points and almost all of it was noise: a gate's
   own deny banner read as an order (47 fires from one line), expository prose
   read as instruction, noun forms read as verbs, prohibitions demanding
   evidence for forbidden work. Six replay passes to reach 18. Analysis and
   decision verbs — audit, review, assess, decide, confirm, determine — are far
   commoner in ordinary prose than deploy or migrate, so a naive add will be
   noisier than fix A was. Budget for the measurement, not just the schema.

2. **It rides on existing structure; no rewrite.** `RECEIPT_PATTERNS` keys the
   surface, `clause_accounted()` already branches per class, and the `recipient`
   and `human` classes prove a non-surface receipt shape works (a named
   recipient; real first use). A decision clause closing on a recorded ruling is
   the same shape again.

## Replay harness

The measurement method is the load-bearing part and should be reused. Replay
both the old and new predicate over seven days of `~/.claude/projects/*/*.jsonl`,
one evaluation point per human turn, and compare block sets. Fix A's numbers:
127 transcripts, 916 points, 57 blocks today, 71 with the widening, 18 new,
roughly 15 correct. Every false positive found this way became a fixture
quoting the real transcript.

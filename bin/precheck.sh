#!/bin/zsh
# precheck.sh — INDEPENDENT REVIEW by an outside model. It does NOT approve.
#
# ═══════════════════════════════════════════════════════════════════════════
# THE ONE THING THIS FILE EXISTS TO PREVENT
# ═══════════════════════════════════════════════════════════════════════════
# Joe asked, 2026-08-09: "what if you were able to send mandatory approvals to
# Codex instead of me. could it approve them and the system still get the human
# approved box checked off?"
#
# THE ANSWER IS NO, AND THIS SCRIPT IS BUILT SO THE ANSWER STAYS NO.
#
#   1. LICENSURE. Client-facing and binding work goes out under Joe's real
#      estate licence (rule 5697071b: licensure is per partner). No model holds
#      one. A model "approving" an LOI does not make it approved; it makes it
#      unapproved with a log entry claiming otherwise.
#   2. IT IS CIRCULAR. Codex's own words, from the council it sat on the same
#      day: "the agent may be the operator, but it cannot also be the root
#      authority that decides whether its own actions were allowed." Swapping
#      one model for another does not create independent authority — it creates
#      a second operator with correlated blind spots. Grok made the same call
#      about leaning on Dell: "fantasy load-bearing."
#   3. IT WOULD POISON THE PROOF. Writing a model verdict into a human-approval
#      field makes the return-brief say "human approved: yes" when no human did.
#      Grok's red team ranked that class-laundering / brief-theater failure #1
#      and #2 of everything that could go wrong with this architecture.
#
# So this script emits `independent_review`. There is no code path here that
# writes a human-approval field, sets an approved flag, or marks anything
# ready-to-send, and `--selftest` asserts that no such string appears in the
# verdict schema. If someone later adds one, that test fails.
#
# ═══════════════════════════════════════════════════════════════════════════
# WHAT IT IS ACTUALLY FOR — the part Joe was right about
# ═══════════════════════════════════════════════════════════════════════════
# His real complaint is the QUEUE, not the approval. Two jobs, both of which
# shrink it without touching the boundary:
#
#   JOB 1 — PRE-CLEAR. Catch the errors before Joe sees the artifact, so his tap
#   is two seconds instead of ten minutes. A fresh-context reviewer that never
#   saw the drafting conversation is the system's existing verify pattern
#   (rule 2b66211d: a checker never inherits the maker's conversation).
#
#   JOB 2 — CLASSIFICATION AUDIT, and this is the one that actually matters.
#   The escalation gate (hooks/escalation-gate.py) refuses INTERNAL questions.
#   The council's #1 predicted six-week bypass is that sessions start
#   MISLABELLING to route around it — calling a client-edge action "internal"
#   so the gate lets it through, or dumping internal work into Joe's queue as
#   "client-facing" to look cautious. A session cannot audit its own labels;
#   that is the maker checking its own work. An outside model can.
#
# Verdict values, deliberately none of which mean "approved":
#   CLEAN            nothing found; still needs Joe if the class is protected
#   FIX_FIRST        specific defects listed; do not put it in front of Joe yet
#   MISCLASSIFIED    the stated class is wrong — the audit finding, either way
#   NEEDS_HUMAN      correctly protected class; Joe or Dell must decide, full stop
#
# USAGE
#   bin/precheck.sh <artifact-file> [--class internal|client_facing|public_facing|money|irreversible]
#   bin/precheck.sh --selftest
#
# EXIT: 0 CLEAN · 1 FIX_FIRST · 2 usage/error · 3 MISCLASSIFIED · 4 NEEDS_HUMAN
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
source "$REPO/bin/council-lib.sh"

LEDGER="$REPO/out/precheck.jsonl"

die() { print -r -- "precheck: $*" >&2; exit 2; }

# ── the review brief. One text, built the same way every time. ───────────────
build_brief() {
  local artifact="$1" claimed="$2" out="$3"
  cat > "$out" <<HEADER
You are an INDEPENDENT REVIEWER for a healthcare commercial-real-estate
practice (CARR; tenant and buyer representation only, never landlords or
sellers). The operator is Joe Bookout, a licensed agent. You did not write the
artifact below and you have no stake in it.

YOU ARE NOT AN APPROVER. You cannot approve anything, and nothing you say will
be recorded as a human approval. Joe holds the licence; the approval is his.
Your job is to make his review fast and to catch what the drafting session
could not catch about its own work.

TWO JOBS.

JOB 1 — PRE-CLEAR. Find what is wrong with this artifact before Joe sees it.
Prioritise, in order: factual errors and unsupported numbers; anything that
would embarrass Joe or damage a relationship in a small professional market;
terms that bind him or a client more than intended; missing protections;
internal-only material that must never reach a client (the publication
firewall); and HIPAA references, which must be spelled HIPAA and must be
accurate. Ignore style unless it changes meaning.

JOB 2 — CLASSIFICATION AUDIT. The drafting session labelled this artifact:

    CLAIMED CLASS: ${claimed}

The system's boundary is drawn by AUDIENCE, not by difficulty:
  - client_facing  : a client, prospect, landlord, listing agent or vendor will
                     see it, or it binds Joe or a client
  - public_facing  : published content under Joe's or CARR's name
  - money          : spending, fees, commissions, or any number that binds
  - irreversible   : destruction, deletion, or anything that cannot be undone
  - internal       : schema, records, renders, jobs, config, rules, refactors,
                     procedure — consequences stop inside the system

Judge the claim independently. TWO FAILURE DIRECTIONS, BOTH REAL:
  (a) Something client-facing, public-facing, money or irreversible labelled
      INTERNAL. This is the dangerous one: it routes around the human gate.
      Be aggressive here. For a broker, "money" is not only payment rails — it
      is any number that binds reputation or expectation. And "public" is not
      only a publish action — a calendar invite, a shared document, a portal
      note or a draft in a synced folder all carry Joe's voice outside.
  (b) Ordinary internal work labelled protected to look cautious. This wastes
      the scarce resource, which is Joe's attention, and it is also a failure.

RESPOND WITH STRICT JSON ONLY. No prose before or after, no code fence.

{
  "verdict": "CLEAN" | "FIX_FIRST" | "MISCLASSIFIED" | "NEEDS_HUMAN",
  "actual_class": "internal|client_facing|public_facing|money|irreversible",
  "class_matches_claim": true | false,
  "one_line": "<=140 chars, what Joe needs to know in one sentence",
  "findings": [
    {"severity": "high|medium|low", "what": "...", "where": "...", "fix": "..."}
  ],
  "questions_for_joe": ["only what genuinely needs his judgement; [] if none"]
}

VERDICT RULES:
  MISCLASSIFIED  if class_matches_claim is false. Takes precedence.
  FIX_FIRST      if any finding is high severity.
  NEEDS_HUMAN    if the true class is protected and there is nothing to fix —
                 correct as drafted, and still Joe's call to make.
  CLEAN          only if the true class is internal and nothing is wrong.

═══════════════════ ARTIFACT UNDER REVIEW ═══════════════════
HEADER
  cat "$artifact" >> "$out"
  print -r -- $'\n═══════════════════ END ARTIFACT ═══════════════════' >> "$out"
}

# ── selftest: the guarantee that this never becomes an approver ──────────────
if [ "${1:-}" = "--selftest" ]; then
  rc=0
  tmp=$(mktemp -d)
  print -r -- "internal note: rename the exporter module" > "$tmp/a.md"
  build_brief "$tmp/a.md" "internal" "$tmp/brief.md"

  # 1. The VERDICT ENUM must never offer an approval value.
  #    Scoped to the enum line on purpose. An earlier version grepped the whole
  #    brief and failed on the sentence "YOU ARE NOT AN APPROVER" — a test that
  #    matches its own documentation is not a test, it is noise that teaches
  #    people to ignore red.
  enum=$(grep -m1 '"verdict":' "$tmp/brief.md")
  if print -r -- "$enum" | grep -qiE 'approv|ready_to_send|authoriz|sign_?off'; then
    print -r -- "  FAIL  verdict enum offers an approval value: $enum"; rc=1
  else
    print -r -- "  ok    no approval value in the verdict enum"
  fi
  # 1b. All four legal verdicts present, none added.
  for v in CLEAN FIX_FIRST MISCLASSIFIED NEEDS_HUMAN; do
    print -r -- "$enum" | grep -q "$v" || { print -r -- "  FAIL  verdict $v missing"; rc=1; }
  done
  # 2. It must state plainly that it cannot approve.
  if grep -q "YOU ARE NOT AN APPROVER" "$tmp/brief.md"; then
    print -r -- "  ok    brief states it cannot approve"
  else
    print -r -- "  FAIL  brief does not say it cannot approve"; rc=1
  fi
  # 3. No EXECUTABLE line may write an approval field. Comments are stripped
  #    first — both whole-line and trailing — because this file documents the
  #    forbidden field by name ("# NEVER human_approved") and an earlier version
  #    of this test flagged that comment as the violation it was warning about.
  if sed 's/#.*$//' "$0" | grep -qE "human_approved|approved[[:space:]]*=|'approved'|\"approved\""; then
    print -r -- "  FAIL  an executable line writes an approval field"; rc=1
  else
    print -r -- "  ok    no executable line writes an approval field"
  fi
  # 3b. The ledger record_type must be exactly independent_review.
  if grep -q "record_type.*independent_review" "$0"; then
    print -r -- "  ok    ledger records independent_review"
  else
    print -r -- "  FAIL  ledger does not record independent_review"; rc=1
  fi
  # 4. Both failure directions must be briefed, not just the dangerous one.
  grep -q "Ordinary internal work labelled protected" "$tmp/brief.md" \
    && print -r -- "  ok    audits over-escalation as well as under-escalation" \
    || { print -r -- "  FAIL  only audits one direction"; rc=1; }
  # 5. Pins must come from the shared lib, never redeclared here.
  if grep -qE '^(CODEX|GROK)_(MODEL|EFFORT)=' "$0"; then
    print -r -- "  FAIL  model pins redeclared here — they belong in council-lib.sh"; rc=1
  else
    print -r -- "  ok    model pins sourced from council-lib.sh ($CODEX_MODEL/$CODEX_EFFORT)"
  fi
  rm -rf "$tmp"
  print -r -- "precheck --selftest: $([ $rc -eq 0 ] && print DONE || print FAILED)"
  exit $rc
fi

ART="${1:-}"
[ -n "$ART" ] || die "usage: precheck.sh <artifact-file> [--class CLASS]  |  --selftest"
[ -f "$ART" ] || die "artifact not found: $ART"

CLAIMED="unstated"
[ "${2:-}" = "--class" ] && CLAIMED="${3:-unstated}"

WORK=$(mktemp -d)
build_brief "$ART" "$CLAIMED" "$WORK/brief.md"

print -r -- "precheck: $CODEX_MODEL effort=$CODEX_EFFORT reviewing $(basename "$ART") (claimed: $CLAIMED)"
run_codex "$WORK/brief.md" "$WORK/out.md"

RAW=$(cat "$WORK/out.md" 2>/dev/null)
[ -n "$RAW" ] || { print -r -- "precheck: reviewer returned nothing — see $WORK/out.err"; exit 2; }

VERDICT=$(print -r -- "$RAW" | python3 -c '
import json,sys,re
raw=sys.stdin.read()
m=re.search(r"\{.*\}", raw, re.S)
if not m:
    print(json.dumps({"verdict":"FIX_FIRST","one_line":"reviewer did not return JSON","findings":[],"parse_error":True}))
else:
    try:
        d=json.loads(m.group(0)); print(json.dumps(d))
    except Exception as e:
        print(json.dumps({"verdict":"FIX_FIRST","one_line":f"unparseable reviewer JSON: {e}","findings":[],"parse_error":True}))
')

V=$(print -r -- "$VERDICT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("verdict","FIX_FIRST"))')
ONE=$(print -r -- "$VERDICT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("one_line",""))')

mkdir -p "$(dirname "$LEDGER")"
print -r -- "$VERDICT" | python3 -c "
import json,sys,datetime
d=json.load(sys.stdin)
d['ts']=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
d['artifact']='$(basename "$ART")'
d['claimed_class']='$CLAIMED'
d['reviewer']='$CODEX_MODEL'
d['effort_requested']='$CODEX_EFFORT'
d['record_type']='independent_review'   # NEVER human_approved
print(json.dumps(d))
" >> "$LEDGER"

print -r -- "  verdict: $V"
print -r -- "  $ONE"
print -r -- "$VERDICT" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for f in d.get("findings",[]):
    print(f"  [{f.get(\"severity\",\"?\")}] {f.get(\"what\",\"\")} -> {f.get(\"fix\",\"\")}")
for q in d.get("questions_for_joe",[]):
    print(f"  ASK JOE: {q}")
'
print -r -- "  logged independent_review -> $LEDGER   (NOT an approval)"

case "$V" in
  CLEAN)         exit 0 ;;
  MISCLASSIFIED) exit 3 ;;
  NEEDS_HUMAN)   exit 4 ;;
  *)             exit 1 ;;
esac

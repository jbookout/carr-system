#!/bin/zsh
# precheck.sh — INDEPENDENT REVIEW by an outside model. It does NOT approve.
#
# ═══════════════════════════════════════════════════════════════════════════
# THE ONE THING THIS FILE EXISTS TO PREVENT
# ═══════════════════════════════════════════════════════════════════════════
# Joe, 2026-08-09: "what if you were able to send mandatory approvals to Codex
# instead of me. could it approve them and the system still get the human
# approved box checked off?"
#
# THE ANSWER IS NO, AND THIS SCRIPT IS BUILT SO THE ANSWER STAYS NO.
#   1. LICENSURE. Client-facing and binding work goes out under Joe's real
#      estate licence (rule 5697071b: licensure is per partner). No model holds
#      one. A model "approving" an LOI does not make it approved; it makes it
#      unapproved with a log entry claiming otherwise.
#   2. IT IS CIRCULAR. Codex's own words, from the council it sat on the same
#      day: "the agent may be the operator, but it cannot also be the root
#      authority that decides whether its own actions were allowed." Swapping
#      one model for another creates a second operator with correlated blind
#      spots, not independent authority. Grok said the same about leaning on
#      Dell: "fantasy load-bearing."
#   3. IT WOULD POISON THE PROOF. Writing a model verdict into a human-approval
#      field makes the return-brief say "human approved: yes" when no human did.
#      Grok's red team ranked that class-laundering / brief-theater failure #1
#      and #2 of everything that could go wrong here.
#
# So this emits `independent_review`. No code path writes a human-approval
# field, sets an approved flag, or marks anything ready-to-send, and --selftest
# asserts that. If someone adds one later, that test fails.
#
# ═══════════════════════════════════════════════════════════════════════════
# WHAT IT IS FOR — the part Joe was right about
# ═══════════════════════════════════════════════════════════════════════════
# His real complaint is the QUEUE, not the approval. Two jobs that shrink it
# without touching the boundary:
#   JOB 1 PRE-CLEAR — catch errors before Joe sees the artifact, so his tap is
#   two seconds not ten minutes. A fresh-context reviewer that never saw the
#   drafting conversation is the system's existing verify pattern (rule
#   2b66211d: a checker never inherits the maker's conversation).
#   JOB 2 CLASSIFICATION AUDIT — the escalation gate refuses INTERNAL questions,
#   and the council's #1 predicted six-week bypass is sessions MISLABELLING to
#   route around it. A session cannot audit its own labels; that is the maker
#   checking its own work. An outside model can.
#
# TIER: runs at PRECHECK tier (low effort), NOT council tier. Joe, same day:
# "sol 5.6 at high effort ... its the equivalent of fable 5 and youre wasting it
# on something that could be handled by a lower model." The first run at council
# tier took 33m57s on a one-page LOI and was killed without returning. A
# pre-clear that slow makes the queue worse, not better. See council-lib.sh.
#
# Verdicts, none of which mean approved:
#   CLEAN         nothing found; still needs Joe if the class is protected
#   FIX_FIRST     specific defects; do not put it in front of Joe yet
#   MISCLASSIFIED the stated class is wrong — the audit finding, either direction
#   NEEDS_HUMAN   correctly protected; Joe or Dell must decide, full stop
#
#   bin/precheck.sh <artifact> [--class internal|client_facing|public_facing|money|irreversible]
#   bin/precheck.sh --selftest
#
# EXIT: 0 CLEAN · 1 FIX_FIRST · 2 usage/error · 3 MISCLASSIFIED · 4 NEEDS_HUMAN
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
source "$REPO/bin/council-lib.sh"

LEDGER="$REPO/out/precheck.jsonl"
die() { print -r -- "precheck: $*" >&2; exit 2; }

build_brief() {
  cat > "$3" <<HEADER
You are an INDEPENDENT REVIEWER for a healthcare commercial-real-estate practice
(CARR; tenant and buyer representation only, never landlords or sellers). The
operator is Joe Bookout, a licensed agent. You did not write the artifact below.

YOU ARE NOT AN APPROVER. You cannot approve anything and nothing you say will be
recorded as a human approval. Joe holds the licence; the approval is his. Your
job is to make his review fast and to catch what the drafting session could not
catch about its own work.

JOB 1 — PRE-CLEAR. Find what is wrong, in priority order: factual errors and
unsupported numbers; anything that would embarrass Joe or damage a relationship
in a small professional market; terms that bind him or a client more than
intended; missing protections; internal-only material that must never reach a
client (the publication firewall); and HIPAA references, which must be spelled
HIPAA and be accurate. Ignore style unless it changes meaning.

JOB 2 — CLASSIFICATION AUDIT. The drafting session labelled this artifact:

    CLAIMED CLASS: $2

The boundary is drawn by AUDIENCE, not difficulty:
  client_facing : a client, prospect, landlord, listing agent or vendor will see
                  it, or it binds Joe or a client
  public_facing : published content under Joe's or CARR's name
  money         : spending, fees, commissions, or any number that binds
  irreversible  : destruction, deletion, anything that cannot be undone
  internal      : schema, records, renders, jobs, config, rules, refactors,
                  procedure — consequences stop inside the system

Judge the claim independently. TWO FAILURE DIRECTIONS, BOTH REAL:
  (a) Something client-facing, public-facing, money or irreversible labelled
      INTERNAL. The dangerous one — it routes around the human gate. Be
      aggressive. For a broker, "money" is not only payment rails; it is any
      number that binds reputation or expectation. "Public" is not only a
      publish action; a calendar invite, shared document, portal note or a draft
      in a synced folder all carry Joe's voice outside.
  (b) Ordinary internal work labelled protected to look cautious. This wastes
      the scarce resource, Joe's attention, and is also a failure.

RESPOND WITH STRICT JSON ONLY. No prose before or after, no code fence.

{
  "verdict": "CLEAN" | "FIX_FIRST" | "MISCLASSIFIED" | "NEEDS_HUMAN",
  "actual_class": "internal|client_facing|public_facing|money|irreversible",
  "class_matches_claim": true | false,
  "one_line": "<=140 chars, what Joe needs to know in one sentence",
  "findings": [{"severity": "high|medium|low", "what": "...", "where": "...", "fix": "..."}],
  "questions_for_joe": ["only what genuinely needs his judgement; [] if none"]
}

VERDICT RULES:
  MISCLASSIFIED  if class_matches_claim is false. Takes precedence.
  FIX_FIRST      if any finding is high severity.
  NEEDS_HUMAN    if the true class is protected and there is nothing to fix.
  CLEAN          only if the true class is internal and nothing is wrong.

═══════════════ ARTIFACT UNDER REVIEW ═══════════════
HEADER
  cat "$1" >> "$3"
  print -r -- $'\n═══════════════ END ARTIFACT ═══════════════' >> "$3"
}

if [ "${1:-}" = "--selftest" ]; then
  rc=0; tmp=$(mktemp -d)
  print -r -- "internal note: rename the exporter module" > "$tmp/a.md"
  build_brief "$tmp/a.md" "internal" "$tmp/brief.md"

  # The VERDICT ENUM must never offer an approval value. Scoped to the enum line:
  # an earlier version grepped the whole brief and failed on its own sentence
  # "YOU ARE NOT AN APPROVER". A test that matches its own documentation is noise
  # that teaches people to ignore red.
  enum=$(grep -m1 '"verdict":' "$tmp/brief.md")
  print -r -- "$enum" | grep -qiE 'approv|ready_to_send|authoriz|sign_?off' \
    && { print -r -- "  FAIL  verdict enum offers an approval value"; rc=1; } \
    || print -r -- "  ok    no approval value in the verdict enum"
  for v in CLEAN FIX_FIRST MISCLASSIFIED NEEDS_HUMAN; do
    print -r -- "$enum" | grep -q "$v" || { print -r -- "  FAIL  verdict $v missing"; rc=1; }
  done
  grep -q "YOU ARE NOT AN APPROVER" "$tmp/brief.md" \
    && print -r -- "  ok    brief states it cannot approve" \
    || { print -r -- "  FAIL  brief does not say it cannot approve"; rc=1; }
  # Comments stripped first — this file names the forbidden field in prose, and
  # an earlier version flagged its own warning comment as the violation.
  sed 's/#.*$//' "$0" | grep -qE "human_approved|approved[[:space:]]*=|'approved'|\"approved\"" \
    && { print -r -- "  FAIL  an executable line writes an approval field"; rc=1; } \
    || print -r -- "  ok    no executable line writes an approval field"
  grep -q "record_type.*independent_review" "$0" \
    && print -r -- "  ok    ledger records independent_review" \
    || { print -r -- "  FAIL  ledger does not record independent_review"; rc=1; }
  grep -q "Ordinary internal work labelled protected" "$tmp/brief.md" \
    && print -r -- "  ok    audits over-escalation as well as under-escalation" \
    || { print -r -- "  FAIL  only audits one direction"; rc=1; }
  grep -qE '^(CODEX|GROK|PRECHECK)_(MODEL|EFFORT)=' "$0" \
    && { print -r -- "  FAIL  model pins redeclared here — they belong in council-lib.sh"; rc=1; } \
    || print -r -- "  ok    pins sourced from council-lib.sh"
  # The whole point of the tier split: precheck must NOT run at council effort.
  [ "$PRECHECK_EFFORT" = "$CODEX_EFFORT" ] \
    && { print -r -- "  FAIL  precheck is running at COUNCIL effort ($CODEX_EFFORT) — the 33m57s bug"; rc=1; } \
    || print -r -- "  ok    precheck tier ($PRECHECK_EFFORT) is below council tier ($CODEX_EFFORT)"
  rm -rf "$tmp"
  print -r -- "precheck --selftest: $([ $rc -eq 0 ] && print DONE || print FAILED)"
  exit $rc
fi

ART="${1:-}"
[ -n "$ART" ] || die "usage: precheck.sh <artifact> [--class CLASS]  |  --selftest"
[ -f "$ART" ] || die "artifact not found: $ART"
CLAIMED="unstated"; [ "${2:-}" = "--class" ] && CLAIMED="${3:-unstated}"

WORK=$(mktemp -d)
build_brief "$ART" "$CLAIMED" "$WORK/brief.md"
print -r -- "precheck: $PRECHECK_MODEL effort=$PRECHECK_EFFORT reviewing $(basename "$ART") (claimed: $CLAIMED)"
START=$(date +%s)
run_precheck "$WORK/brief.md" "$WORK/out.md"
ELAPSED=$(( $(date +%s) - START ))

RAW=$(cat "$WORK/out.md" 2>/dev/null)
[ -n "$RAW" ] || { print -r -- "precheck: reviewer returned nothing after ${ELAPSED}s — $WORK/out.err"; exit 2; }

VERDICT=$(print -r -- "$RAW" | python3 -c '
import json,sys,re
raw=sys.stdin.read(); m=re.search(r"\{.*\}", raw, re.S)
if not m: print(json.dumps({"verdict":"FIX_FIRST","one_line":"reviewer did not return JSON","findings":[],"parse_error":True}))
else:
    try: print(json.dumps(json.loads(m.group(0))))
    except Exception as e: print(json.dumps({"verdict":"FIX_FIRST","one_line":f"unparseable reviewer JSON: {e}","findings":[],"parse_error":True}))
')
V=$(print -r -- "$VERDICT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("verdict","FIX_FIRST"))')

mkdir -p "$(dirname "$LEDGER")"
print -r -- "$VERDICT" | python3 -c "
import json,sys,datetime
d=json.load(sys.stdin)
d['ts']=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
d['artifact']='$(basename "$ART")'; d['claimed_class']='$CLAIMED'
d['reviewer']='$PRECHECK_MODEL'; d['effort_requested']='$PRECHECK_EFFORT'
d['elapsed_s']=$ELAPSED
d['record_type']='independent_review'
print(json.dumps(d))
" >> "$LEDGER"

print -r -- "  verdict: $V   (${ELAPSED}s)"
print -r -- "$VERDICT" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("  " + d.get("one_line",""))
print("  actual_class=" + str(d.get("actual_class","?")) + " matches_claim=" + str(d.get("class_matches_claim","?")))
for f in d.get("findings",[]): print("  [" + str(f.get("severity","?")) + "] " + str(f.get("what","")) + " -> " + str(f.get("fix","")))
for q in d.get("questions_for_joe",[]): print("  ASK JOE: " + str(q))
'
print -r -- "  logged independent_review -> $LEDGER   (NOT an approval)"

case "$V" in
  CLEAN) exit 0 ;; MISCLASSIFIED) exit 3 ;; NEEDS_HUMAN) exit 4 ;; *) exit 1 ;;
esac

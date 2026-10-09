#!/bin/zsh
export PATH=/Users/booko/carr-system/out/orch/bin:$PATH   # gh limiter + call log (2026-10-06 secondary rate limit)
# Workers skip per-step paid Jev hook calls (carr PR 1495; Joe 2026-10-02: credits drained by worker hooks).
export CARR_JEV_WORKER=off
# pr-loop.sh <owner/repo> <pr> <worktree|-> [maxrounds]: (fix if last review blocked) -> CI green -> review; repeat until APPROVE. Merging stays with merge-queue.sh via auto-enqueue.
R=$1 N=$2 W=$3 MAX=${4:-3} S=${0:A:h}; LOG=$S/loop-${R:t}-$N.log
# one loop per PR: two loops meant two fixers pushing one branch (2026-10-01, PRs 1453/1454/1455)
K=$S/locks/pr-loop-${R:t}-$N.pid; mkdir -p $S/locks
if [ -f $K ] && kill -0 $(cat $K) 2>/dev/null; then echo "$(date +%T) another pr-loop ($(cat $K)) owns $R#$N; exiting" >> $LOG; exit 0; fi
echo $$ > $K; trap 'rm -f $K' EXIT
# An APPROVE counts only when its Reviewed-SHA is the current head; an older approval is reported as
# "APPROVE-STALE" so a pushed fix round gets reviewed (2026-10-01: app PR 117's fix commit 2f7d04d was
# never reviewed because round 1 read the approval of the earlier head e876d80 and exited).
last(){ local h=$(gh pr view $N -R $R --json headRefOid -q .headRefOid)
  gh pr view $N -R $R --json comments -q '([.comments[].body|select(startswith("APPROVE") or startswith("REVIEW: BLOCKED"))|select(test("^APPROVE\\r?\\nReviewed-SHA: [0-9a-f]{40}\\r?\\n(\\r?\\n)?Orchestrator( merge queue:|: verified exact head)")|not)]|last // "")' |
  python3 -c 'import sys,re,subprocess as sp;b=sys.stdin.read();h=sys.argv[1];d=sys.argv[2];f=b.split("\n")[0]
m=re.search(r"Reviewed-SHA:\s*([0-9a-f]{7,40})",b)
stale=not (m and h.startswith(m.group(1)))
if "Reviewer: ChatGPT Dot" in b: stale=not (m and m.group(1)==h)
# 2026-10-04 (Joe: no pointless re-reviews): pstack shipping step 3. A head that differs from the approved SHA
# only by merging main keeps its verdict when the PR own patch (merge-base..sha) has the same stable patch-id.
def pid(sha):
  try:
    mb=sp.run(["git","-C",d,"merge-base","origin/main",sha],capture_output=True,text=True,check=True).stdout.strip()
    diff=sp.run(["git","-C",d,"diff",mb,sha],capture_output=True,check=True).stdout
    out=sp.run(["git","patch-id","--stable"],input=diff,capture_output=True,check=True).stdout.split()
    return out[0].decode() if out else None
  except Exception: return None
if stale and m and d and "Reviewer: ChatGPT Dot" not in b:
  sp.run(["git","-C",d,"fetch","-q","origin","main",h,m.group(1)],capture_output=True)
  a,c=pid(m.group(1)),pid(h)
  if a and a==c: stale=False; sys.stderr.write(f"same patch-id {a[:12]} on {h[:8]} as approved {m.group(1)[:8]}; verdict kept\n")
print("APPROVE-STALE" if f.startswith("APPROVE") and stale else "BLOCKED-STALE" if f.startswith("REVIEW: BLOCKED") and stale else f)' "$h" "$(repodir)" 2>>$LOG; }
repodir(){ case $R in jbookout/carr-system) echo /Users/booko/carr-system;; jbookout/doctorcre-app) echo /Users/booko/doctorcre-app;; jbookout/software-factory) echo /Users/booko/software-factory;; esac; }
# A BLOCKED review of an older head is "BLOCKED-STALE": review the current head instead of fixing from stale findings
# (2026-10-03: app PR 131 ran a fix round off the 8d9c078 review after 869e87a had already fixed it).
# A "-" worktree used to mean "never fix", so blocked reviews repeated with no fix between them (2026-10-01: app 123 and
# carr-system 1467 hit "UNRESOLVED after 3 rounds" with zero fix rounds). Now "-" gets its own worktree on the PR branch.
wt(){ [ "$W" != - ] && return; local D; case $R in jbookout/carr-system) D=/Users/booko/carr-system;; jbookout/doctorcre-app) D=/Users/booko/doctorcre-app;; jbookout/software-factory) D=/Users/booko/software-factory;; esac
  local B=$(gh pr view $N -R $R --json headRefName -q .headRefName); W=$(zsh $S/branch-wt.sh $D $B $D-fix-$N) || W=-
  # 2026-10-05: when the branch already sits in a worktree with uncommitted changes (a killed fixer's partial work),
  # branch-wt refuses and W stayed "-"; fix-pr then ran `cd -` and three fixers worked from out/orch inside the
  # canonical main checkout. Reuse the worktree that holds the branch (keeping the partial fix); else stop the loop.
  [ "$W" = - ] && W=$(git -C $D worktree list --porcelain | awk -v b="refs/heads/$B" '/^worktree /{w=substr($0,10)} $0=="branch "b{print w}' | head -1)
  [ -n "$W" ] && [ -d "$W" ] || { echo "$(date +%T) NO-WORKTREE for $B; loop ends" >> $LOG; exit 4; }; }
# An approval on a head whose CI is red or that conflicts with main is not mergeable (2026-10-01: app 120, 114 and
# carr-system 1470, 1469 sat APPROVED and unmergeable). Hand those to ci-fix, then review the new head.
# CI state via REST check-runs (2026-10-04): `gh pr checks` is GraphQL, and when that hourly pool is spent it fails,
# which this loop read as "CI red" (carr 1516 was all green) and skipped the CI wait. REST has its own pool.
ci(){ local sha=$(gh api repos/$R/pulls/$N --jq .head.sha 2>/dev/null) || { echo unknown; return; }
  # 2026-10-04: one REST call per check instead of two; REST budget ran out twice in one evening.
  gh api "repos/$R/commits/$sha/check-runs?per_page=100" --jq '[.check_runs[]]|if length==0 or any(.status!="completed") then "pending" elif any(.conclusion=="failure" or .conclusion=="timed_out" or .conclusion=="action_required") then "red" else "green" end' 2>/dev/null || echo unknown; }
ciwait(){ local i=0; while (( i++ < 30 )); do case $(ci) in green|red) return;; esac; sleep 180; done; }
ready(){ [ "$(ci)" = green ] && [ "$(gh pr view $N -R $R --json mergeable -q .mergeable)" != CONFLICTING ]; }
for r in $(seq 1 $MAX); do
  # 2026-10-04: a merged or closed PR ends the loop (software-factory 37 got a full paid review after it merged).
  [ "$(gh pr view $N -R $R --json state -q .state)" != OPEN ] && { echo "$(date +%T) PR no longer open; loop ends" >> $LOG; exit 0; }
  v=$(last); echo "$(date +%T) round $r start: ${v:-no review}" >> $LOG
  if [[ $v == APPROVE* && $v != APPROVE-STALE ]]; then
    if ready; then echo "$(date +%T) APPROVED" >> $LOG; exit 0; fi
    echo "$(date +%T) approved but CI red or conflicting: ci-fix" >> $LOG; NOLOOP=1 zsh $S/ci-fix.sh $R $N; sleep 90; v=""; fi
  # No-progress stop: a fix round that pushes nothing, or a refused budget, ends the loop instead of re-reviewing the same head.
  if [[ $v == "REVIEW: BLOCKED"* ]]; then wt; h0=$(gh pr view $N -R $R --json headRefOid -q .headRefOid)
    zsh $S/fix-pr.sh $R $N $W; echo "$(date +%T) fix done" >> $LOG; sleep 90
    [ -n "$(tail -1 $S/budget/STOPPED 2>/dev/null | grep "$R#$N ")" ] && { echo "$(date +%T) BUDGET-STOP" >> $LOG; exit 3; }
    [ "$(gh pr view $N -R $R --json headRefOid -q .headRefOid)" = "$h0" ] && { echo "$(date +%T) NO-PROGRESS: fix pushed nothing" >> $LOG; exit 2; }; fi
  ciwait; echo "$(date +%T) CI $(ci)" >> $LOG
  zsh $S/review-pr.sh $R $N >> $LOG 2>&1
  rc=$?
  [ "$rc" = 20 ] && { echo "$(date +%T) REVIEW-QUEUED: free-first independent handoff" >> $LOG; exit 0; }
  [ "$rc" != 0 ] && { echo "$(date +%T) REVIEW-ROUTING-FAILED rc=$rc" >> $LOG; exit $rc; }
done
v=$(last); [[ $v == APPROVE* && $v != APPROVE-STALE ]] && { echo "$(date +%T) APPROVED" >> $LOG; exit 0; }
echo "$(date +%T) UNRESOLVED after $MAX rounds" >> $LOG; exit 1

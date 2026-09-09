#!/usr/bin/env python3
"""repo-hygiene-janitor.py — R07 disabled-by-default hygiene planner and applicator.

WHAT THIS IS. R03 stopped safely at 312 local branches and handed forward 265
branches for which it found NO machine deletion evidence. This module is the
standing janitor that R03's one-shot sweep became: it plans branch, registered-
helper-worktree, and cache cleanup against an immutable snapshot, re-verifies
every fact immediately before any mutation, and writes one queryable receipt per
candidate — including for the candidates it refuses to touch.

WHAT THIS IS NOT, and the distinction is the whole point. The 265-branch tail is
an input for HUMAN ADJUDICATION. It is not a deletion allowlist, and no code path
here can turn it into one: a tail branch without a per-branch adjudication record
is KEEP, and the reason is recorded by name. The `<= 40` branch target that
motivated the program is a target, never authority — it appears nowhere in the
decision procedure, because a count can never outrank the deletion law.

THE DELETION LAW, inherited verbatim from tools/r03-settlement-sweep-runner.py
(rule a8c55a47 — the manual path and the automated path are the same law):

  ancestry-merged   the branch's CURRENT tip is still an ancestor of a freshly
                    pinned target, plus a verified tip backup -> `git branch -d`
  squash-merged     a MERGED-into-target pull request whose RECORDED HEAD OID
                    equals the current tip exactly, plus a verified tip backup
                    -> `git branch -D`
  everything else   KEEP, with the reason named.

"Verified backup" means all four of: local `git update-ref`, local readback,
`git push --atomic` to the remote, and an exact `git ls-remote` readback. A
backup that cannot be read back is not a backup, and the deletion never starts.

DEFAULT-OFF IS STRUCTURAL, NOT A FLAG DEFAULT. `main()` plans and reports; it
mutates nothing. Mutation additionally requires `--execute` AND a non-empty
CARR_REPO_HYGIENE_EFFECT_PACKET naming a separately reviewed live-effect packet,
because that separate packet is where live-run authority actually lives. Absent
it, an execute request is refused and receipted as such. This module installs no
schedule and enables no service.

EVERY EXTERNAL DEPENDENCE IS INJECTED — the git port, the pull-request provider,
the clock, the worktree liveness probe, the R09 entrant reader, and the receipt
sink. That is what lets the test suite drive real git mutations inside its own
disposable repositories while every other edge stays a fake, and it is why no
test here needs to reach a real remote, a real provider, or a real schedule.

INTEROPERATION, all read-only against code this module must never edit:
  hooks/worktree-self-plumb.py    the one repository-maintenance mutex, at
                                  out/worktree-reap.lock — exclusive create,
                                  two-hour stale threshold, owner unlinks.
  tools/room-bridge/worktree_runtime_isolation.py
                                  read_lock_posture(root, owner_alive) — an
                                  `active` or `stale_uncertain` entrant
                                  preserves its worktree regardless of TTL.
  bin/worktree.sh --remove <path> the only worktree removal door.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
import uuid

CANONICAL_CHECKOUT = Path("/Users/booko/carr-system")

# The exact protocol hooks/worktree-self-plumb.py implements. Both numbers are
# that hook's, not new ones: a second mutex, or a second staleness threshold,
# would be a second answer to a question that already has one.
MAINTENANCE_LOCK_RELPATH = ("out", "worktree-reap.lock")
LOCK_STALE_SECONDS = 2 * 3600

# A worktree whose newest file mtime is younger than this may still belong to a
# live session. bin/worktree.sh --sweep uses the same shape of guard.
DEFAULT_WORKTREE_TTL_SECONDS = 6 * 3600

# Volatile roots. A path here can be reclaimed by the operating system between
# the plan and the apply, so its identity cannot be pinned and it is never
# eligible. /private/tmp is macOS's realpath for /tmp.
VOLATILE_PREFIXES = ("/private/tmp", "/private/var/tmp", "/tmp", "/var/tmp")

# The 265-branch tail is exactly these three markers in R03's appendix:
# 42 closed-unmerged + 189 no-PR + 34 reused-name. The other 46 kept rows
# (36 worktree-held, 4 open-PR, 6 assurance-held) are not tail rows.
TAIL_MARKERS = {
    "KEEP-CLOSED-UNMERGED": "closed_unmerged_pull_request",
    "KEEP-NO-PR": "unmerged_without_pull_request",
    "RETAIN": "reused_branch_name",
}
NON_TAIL_MARKERS = {
    "KEEP-WORKTREE": "worktree_held",
    "KEEP-OPEN-PR": "open_pull_request",
    "HOLD": "assurance_held",
}
_MANIFEST_ROW = re.compile(
    r"^#\s+(?P<marker>KEEP-[A-Z-]+|RETAIN|HOLD)\s+(?P<tip>[0-9a-f]{40})\s+(?P<name>\S+)"
)

DELETED, KEPT, REFUSED, UNKNOWN = "deleted", "kept", "refused", "unknown"


class JanitorRefusal(RuntimeError):
    """Fail closed. Raised only for conditions that invalidate the whole run."""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_volatile(path: str | Path) -> bool:
    resolved = str(Path(path))
    return any(resolved == p or resolved.startswith(p + os.sep) for p in VOLATILE_PREFIXES)


# ─────────────────────────────────────────────────────────────────────────────
# Ports. Everything the janitor cannot compute for itself arrives through one of
# these, so a test can supply a fake without the decision procedure knowing.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GitResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def out(self) -> str:
        return self.stdout.strip()


class GitPort:
    """Real git, against one explicit repository, with git's location variables
    scrubbed. `git -C` is not isolation: GIT_DIR outranks the directory, and
    every git hook exports it (see ops/git_env.py for the incident)."""

    def __init__(self, repository: Path, env: Mapping[str, str] | None = None, timeout: int = 120):
        self.repository = Path(repository)
        self.timeout = timeout
        self._env = dict(env) if env is not None else self._scrubbed()

    @staticmethod
    def _scrubbed() -> dict[str, str]:
        env = dict(os.environ)
        for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                    "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES", "GIT_PREFIX",
                    "GIT_WORK_TREE_OVERRIDE"):
            env.pop(var, None)
        return env

    def run(self, argv: Sequence[str]) -> GitResult:
        full = ("git", "-C", str(self.repository), *argv)
        proc = subprocess.run(full, capture_output=True, text=True,
                              env=self._env, timeout=self.timeout, check=False)
        return GitResult(tuple(full), proc.returncode, proc.stdout, proc.stderr)


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str          # "open" | "closed" | "merged"
    base_ref: str
    head_oid: str
    evidence_id: str
    provider: str = "github"


class PullRequestProvider(Protocol):
    def pull_requests_for_branch(self, branch: str) -> Sequence[PullRequest]: ...


class EntrantReader(Protocol):
    """The R09 contract: read_lock_posture(root, owner_alive) -> {state, entrant}
    where state is one of none | active | stale_uncertain."""

    def __call__(self, root: Path) -> Mapping[str, Any]: ...


class ReceiptSink(Protocol):
    def available(self) -> bool: ...
    def emit(self, receipt: Mapping[str, Any]) -> None: ...


class JsonlReceiptSink:
    """Append-only local sink. `available()` is checked BEFORE any mutation, so a
    deletion can never happen into a sink that cannot record it."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def available(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                return True
        except OSError:
            return False

    def emit(self, receipt: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# The one repository-maintenance mutex, reusing hooks/worktree-self-plumb.py's
# protocol byte for byte rather than inventing a second one.
# ─────────────────────────────────────────────────────────────────────────────


class MaintenanceMutex:
    def __init__(self, canonical_root: Path, clock: Callable[[], float] = time.time):
        self.path = Path(canonical_root).joinpath(*MAINTENANCE_LOCK_RELPATH)
        self.clock = clock
        self.held = False

    def acquire(self) -> bool:
        """True when this process now owns the mutex. False on live contention —
        the caller stops; it does not wait and it does not steal."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if self.clock() - os.path.getmtime(self.path) < LOCK_STALE_SECONDS:
                    return False                       # a live reaper holds it
                os.unlink(self.path)                   # dead reaper's leftovers
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                return False
        except OSError:
            return False
        with os.fdopen(fd, "w") as handle:
            handle.write(str(os.getpid()))
        self.held = True
        return True

    def release(self) -> None:
        if not self.held:
            return
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self.held = False

    def __enter__(self) -> "MaintenanceMutex":
        if not self.acquire():
            raise JanitorRefusal("repository maintenance mutex is held; refusing to plan")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ─────────────────────────────────────────────────────────────────────────────
# Immutable inputs: R03's 265-branch tail and R01's never-cleanable register.
# Both are read-only. Neither is ever written, and neither becomes an allowlist.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TailRow:
    marker: str
    tip: str
    name: str
    reason: str
    in_tail: bool


def load_settlement_tail(path: Path) -> dict[str, TailRow]:
    rows: dict[str, TailRow] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _MANIFEST_ROW.match(line)
        if not match:
            continue
        marker = match.group("marker")
        if marker in TAIL_MARKERS:
            reason, in_tail = TAIL_MARKERS[marker], True
        elif marker in NON_TAIL_MARKERS:
            reason, in_tail = NON_TAIL_MARKERS[marker], False
        else:
            continue
        rows[match.group("name")] = TailRow(marker, match.group("tip").lower(),
                                            match.group("name"), reason, in_tail)
    if not rows:
        raise JanitorRefusal(f"settlement tail manifest yielded no rows: {path}")
    return rows


@dataclass(frozen=True)
class NeverCleanableRegister:
    never_clean: frozenset[str]
    exact_root_only: frozenset[str]

    def verdict(self, path: str | Path) -> str | None:
        """None when the path is eligible for exact-root receipted pruning."""
        resolved = str(Path(path))
        for root in self.never_clean:
            if resolved == root or resolved.startswith(root + os.sep):
                return "never_cleanable_register"
        for root in self.exact_root_only:
            if resolved == root or resolved.startswith(root + os.sep):
                return None
        return "cache_root_not_registered"


def load_never_cleanable_register(path: Path) -> NeverCleanableRegister:
    never, exact = set(), set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[1].startswith("/"):
            continue
        realpath, protection = cells[1].rstrip("/"), cells[4]
        (never if "NEVER CLEAN" in protection else exact).add(realpath)
    if not never and not exact:
        raise JanitorRefusal(f"never-cleanable register yielded no rows: {path}")
    return NeverCleanableRegister(frozenset(never), frozenset(exact))


# ─────────────────────────────────────────────────────────────────────────────
# Candidates in, plan rows out.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BranchCandidate:
    name: str
    tip: str | None                     # None means the tip could not be read
    remote_ref_exists: bool = False
    worktree_held: bool = False
    adjudication: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class WorktreeCandidate:
    path: str
    registered: bool
    bare: bool = False
    locked: bool = False
    dirty: bool = True                  # the safe default is "someone's work"
    idle_seconds: float | None = None   # None means liveness is unknown
    isolation_root: str | None = None   # the R09 registry root, when one exists


@dataclass(frozen=True)
class CacheCandidate:
    path: str
    observed_digest: str | None = None
    expected_digest: str | None = None
    refetch_command: Sequence[str] | None = None
    refetch_proof: bool = False


@dataclass(frozen=True)
class PlanRow:
    kind: str                           # branch | worktree | cache
    identity: str
    action: str                         # keep | delete_ancestry | delete_squash
                                        # | remove_worktree | prune_cache
    reason: str
    preimage: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @property
    def mutating(self) -> bool:
        return self.action != "keep"


@dataclass(frozen=True)
class Plan:
    snapshot_id: str
    pinned_target: str
    pinned_ref: str
    taken_at: float
    rows: tuple[PlanRow, ...]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for row in self.rows:
            out[row.action] = out.get(row.action, 0) + 1
        return out


def _keep(kind: str, identity: str, reason: str, preimage: str | None = None,
          **evidence: Any) -> PlanRow:
    return PlanRow(kind, identity, "keep", reason, preimage, evidence)


# ─────────────────────────────────────────────────────────────────────────────


class RepoHygieneJanitor:
    """Plan against one immutable snapshot; re-verify before every mutation."""

    def __init__(
        self,
        *,
        repository: Path,
        git: GitPort,
        pull_requests: PullRequestProvider,
        receipt_sink: ReceiptSink,
        tail: Mapping[str, TailRow],
        register: NeverCleanableRegister,
        entrant_reader: EntrantReader | None = None,
        clock: Callable[[], float] = time.time,
        target_ref: str = "refs/remotes/origin/main",
        target_base_branch: str = "main",
        backup_namespace: str = "refs/backup/r07-janitor",
        worktree_ttl_seconds: float = DEFAULT_WORKTREE_TTL_SECONDS,
        worktree_remove_command: Sequence[str] = ("zsh", "bin/worktree.sh", "--remove"),
        effect_packet: str | None = None,
    ):
        self.repository = Path(repository)
        self.git = git
        self.pull_requests = pull_requests
        self.receipt_sink = receipt_sink
        self.tail = dict(tail)
        self.register = register
        self.entrant_reader = entrant_reader
        self.clock = clock
        self.target_ref = target_ref
        self.target_base_branch = target_base_branch
        self.backup_namespace = backup_namespace.rstrip("/")
        self.worktree_ttl_seconds = worktree_ttl_seconds
        self.worktree_remove_command = tuple(worktree_remove_command)
        self.effect_packet = effect_packet or ""
        self._seen_keys: dict[str, dict[str, Any]] = {}

    # ── snapshot ────────────────────────────────────────────────────────────

    def pin_target(self) -> str:
        """One fresh target OID. An unreadable target is missing evidence for
        every branch decision, so it refuses the whole plan rather than letting
        a stale pin decide a deletion."""
        result = self.git.run(("rev-parse", "--verify", self.target_ref))
        if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", result.out):
            raise JanitorRefusal(f"fresh pinned target unavailable: {self.target_ref}")
        return result.out

    def _current_tip(self, branch: str) -> str | None:
        result = self.git.run(("rev-parse", "--verify", f"refs/heads/{branch}"))
        return result.out.lower() if not result.returncode else None

    def _is_ancestor(self, tip: str, pinned: str) -> bool:
        return self.git.run(("merge-base", "--is-ancestor", tip, pinned)).returncode == 0

    def backup_ref_for(self, branch: str) -> str:
        return f"{self.backup_namespace}/{branch}"

    # ── the branch decision procedure, in order ─────────────────────────────

    def classify_branch(self, candidate: BranchCandidate, pinned: str) -> PlanRow:
        name, tip = candidate.name, (candidate.tip or "").lower()
        row = self.tail.get(name)

        if tip == "":
            return _keep("branch", name, "missing_evidence_unreadable_tip")
        if candidate.worktree_held:
            return _keep("branch", name, "worktree_held", tip)
        if row is not None and row.in_tail and not self._adjudicated(candidate, tip):
            # The load-bearing refusal. Being in R03's tail is a reason to look,
            # never a reason to delete.
            return _keep("branch", name, "tail_requires_human_adjudication", tip,
                         tail_marker=row.marker, tail_reason=row.reason)
        if row is not None and not row.in_tail and row.reason == "assurance_held":
            return _keep("branch", name, "assurance_held", tip)

        prs = list(self.pull_requests.pull_requests_for_branch(name))
        if any(pr.state == "open" for pr in prs):
            open_pr = next(pr for pr in prs if pr.state == "open")
            return _keep("branch", name, "open_pull_request", tip,
                         pull_request=_pr_evidence(open_pr))

        if self._is_ancestor(tip, pinned):
            return PlanRow("branch", name, "delete_ancestry", "ancestry_merged_into_pinned_target",
                           tip, {"pinned_target": pinned,
                                 "backup_ref": self.backup_ref_for(name)})

        squash = self._squash_evidence(prs, tip)
        if squash is not None:
            return PlanRow("branch", name, "delete_squash",
                           "squash_merged_pull_request_head_equals_current_tip", tip,
                           {"pinned_target": pinned, "pull_request": squash,
                            "backup_ref": self.backup_ref_for(name)})

        if candidate.remote_ref_exists:
            return _keep("branch", name, "remote_unmerged", tip)
        if any(pr.state == "closed" for pr in prs):
            closed = next(pr for pr in prs if pr.state == "closed")
            return _keep("branch", name, "closed_unmerged_pull_request", tip,
                         pull_request=_pr_evidence(closed))
        if prs:
            # A merged PR exists but its recorded head is not this tip: the name
            # was reused and the current commits are unmerged work. This is the
            # exact FIX-1 shape that would have destroyed live commits.
            return _keep("branch", name, "reused_branch_name", tip,
                         pull_request=_pr_evidence(prs[0]))
        return _keep("branch", name, "unmerged_without_pull_request", tip)

    def _adjudicated(self, candidate: BranchCandidate, tip: str) -> bool:
        """A per-branch human adjudication that names THIS tip. An adjudication
        recorded against an older tip decided a different commit."""
        record = candidate.adjudication
        if not isinstance(record, Mapping):
            return False
        required = ("adjudicated_by", "adjudicated_at", "decision", "evidence_id", "tip")
        if any(not record.get(key) for key in required):
            return False
        return record["decision"] == "eligible" and str(record["tip"]).lower() == tip

    def _squash_evidence(self, prs: Iterable[PullRequest], tip: str) -> dict[str, Any] | None:
        for pr in prs:
            if (pr.state == "merged"
                    and pr.base_ref == self.target_base_branch
                    and pr.head_oid.lower() == tip):
                return _pr_evidence(pr)
        return None

    # ── the worktree decision procedure, in order ───────────────────────────

    def classify_worktree(self, candidate: WorktreeCandidate) -> PlanRow:
        path = candidate.path
        if Path(path).resolve() == CANONICAL_CHECKOUT.resolve():
            return _keep("worktree", path, "canonical_tree")
        if not candidate.registered:
            return _keep("worktree", path, "unregistered_worktree")
        if candidate.bare:
            return _keep("worktree", path, "bare_worktree")
        if candidate.locked:
            return _keep("worktree", path, "locked_worktree")
        if _is_volatile(path):
            return _keep("worktree", path, "volatile_private_tmp_path")
        if candidate.dirty:
            return _keep("worktree", path, "dirty_worktree")

        # Entrant posture is read BEFORE the TTL guard on purpose: an active or
        # uncertain R09 entrant preserves its worktree regardless of how idle
        # the tree looks, and the receipt should say so by name.
        posture = self._entrant_posture(candidate)
        if posture["state"] == "active":
            return _keep("worktree", path, "active_r09_entrant", entrant=posture)
        if posture["state"] == "stale_uncertain":
            return _keep("worktree", path, "uncertain_r09_entrant_liveness", entrant=posture)
        if posture["state"] == "unreadable":
            return _keep("worktree", path, "missing_evidence_entrant_unreadable", entrant=posture)

        if candidate.idle_seconds is None:
            return _keep("worktree", path, "missing_evidence_unknown_liveness")
        if candidate.idle_seconds < self.worktree_ttl_seconds:
            return _keep("worktree", path, "recently_live_worktree",
                         idle_seconds=candidate.idle_seconds)
        return PlanRow("worktree", path, "remove_worktree", "registered_stale_clean_no_entrant",
                       None, {"idle_seconds": candidate.idle_seconds, "entrant": posture})

    def _entrant_posture(self, candidate: WorktreeCandidate) -> dict[str, Any]:
        if candidate.isolation_root is None or self.entrant_reader is None:
            return {"state": "none", "entrant": None}
        try:
            posture = self.entrant_reader(Path(candidate.isolation_root))
        except Exception as exc:                       # preserve on any refusal
            return {"state": "unreadable", "entrant": None, "error": str(exc)}
        state = posture.get("state")
        if state not in ("none", "active", "stale_uncertain"):
            return {"state": "unreadable", "entrant": None, "error": f"unknown state {state!r}"}
        return {"state": state, "entrant": posture.get("entrant")}

    # ── the cache decision procedure, in order ──────────────────────────────

    def classify_cache(self, candidate: CacheCandidate) -> PlanRow:
        path = candidate.path
        if self._under_canonical():
            return _keep("cache", path, "canonical_cache_cleanup_forbidden")
        if _is_volatile(path):
            return _keep("cache", path, "volatile_private_tmp_path")
        verdict = self.register.verdict(path)
        if verdict is not None:
            return _keep("cache", path, verdict)
        if not candidate.expected_digest or not candidate.observed_digest:
            return _keep("cache", path, "cache_reproducibility_missing")
        if candidate.observed_digest.lower() != candidate.expected_digest.lower():
            return _keep("cache", path, "cache_reproducibility_mismatch",
                         observed=candidate.observed_digest, expected=candidate.expected_digest)
        if not candidate.refetch_proof or not candidate.refetch_command:
            return _keep("cache", path, "cache_refetch_proof_missing")
        return PlanRow("cache", path, "prune_cache", "content_addressed_reproducible",
                       candidate.observed_digest.lower(),
                       {"refetch_command": list(candidate.refetch_command),
                        "expected_digest": candidate.expected_digest.lower()})

    def _under_canonical(self) -> bool:
        try:
            resolved, canonical = self.repository.resolve(), CANONICAL_CHECKOUT.resolve()
        except OSError:
            return True                                # unresolvable: assume canonical
        return resolved == canonical or canonical in resolved.parents

    # ── planning ────────────────────────────────────────────────────────────

    def plan(
        self,
        *,
        branches: Sequence[BranchCandidate] = (),
        worktrees: Sequence[WorktreeCandidate] = (),
        caches: Sequence[CacheCandidate] = (),
    ) -> Plan:
        pinned = self.pin_target()
        rows: list[PlanRow] = []
        for candidate in branches:
            rows.append(self.classify_branch(candidate, pinned))
        for candidate in worktrees:
            rows.append(self.classify_worktree(candidate))
        for candidate in caches:
            rows.append(self.classify_cache(candidate))
        return Plan(uuid.uuid4().hex, pinned, self.target_ref, self.clock(), tuple(rows))

    # ── applying ────────────────────────────────────────────────────────────

    def apply(self, plan: Plan, *, execute: bool = False,
              idempotency_prefix: str | None = None) -> list[dict[str, Any]]:
        """Re-verify each row against live state and emit one receipt per row.

        `execute=False` is the default and the production posture: every fact is
        re-read, every receipt is written, and nothing is mutated.
        """
        if execute and not self.effect_packet:
            raise JanitorRefusal(
                "execute requires a separately reviewed live-effect packet "
                "(CARR_REPO_HYGIENE_EFFECT_PACKET); refusing to mutate")
        if execute and not self.receipt_sink.available():
            # Checked before the first mutation, never after: a deletion that
            # cannot be receipted is a deletion nobody can audit.
            raise JanitorRefusal("receipt sink unavailable; refusing to mutate")

        prefix = idempotency_prefix or plan.snapshot_id
        receipts: list[dict[str, Any]] = []
        for index, row in enumerate(plan.rows):
            key = f"{prefix}:{row.kind}:{row.identity}:{index}"
            if key in self._seen_keys:
                receipts.append(self._seen_keys[key])   # replay, never re-mutate
                continue
            receipt = self._apply_row(plan, row, key, execute)
            self._seen_keys[key] = receipt
            self.receipt_sink.emit(receipt)
            receipts.append(receipt)
        return receipts

    def _apply_row(self, plan: Plan, row: PlanRow, key: str, execute: bool) -> dict[str, Any]:
        started = self.clock()
        base = {
            "operation_id": uuid.uuid4().hex,
            "idempotency_key": key,
            "snapshot_id": plan.snapshot_id,
            "target_kind": row.kind,
            "target_identity": row.identity,
            "observed_preimage": row.preimage,
            "pinned_target": plan.pinned_target,
            "planned_action": row.action,
            "planned_reason": row.reason,
            "evidence": dict(row.evidence),
            "argv": [],
            "started_at": started,
        }
        if not row.mutating:
            return self._close(base, KEPT, row.reason, current=row.preimage)
        if not execute:
            return self._close(base, KEPT, f"dry_run_would_{row.action}", current=row.preimage)
        if row.kind == "branch":
            return self._apply_branch(plan, row, base)
        if row.kind == "worktree":
            return self._apply_worktree(row, base)
        return self._apply_cache(row, base)

    def _close(self, base: dict[str, Any], result: str, reason: str, *,
               current: str | None = None, exit_code: int | None = None,
               stdout: str = "", stderr: str = "", **extra: Any) -> dict[str, Any]:
        base.update({
            "current_tip": current,
            "finished_at": self.clock(),
            "exit_code": exit_code,
            "stdout_digest": _digest(stdout),
            "stderr_digest": _digest(stderr),
            "result": result,
            "reason": reason,
            **extra,
        })
        return base

    # ── branch application: re-verify, back up, delete, confirm ─────────────

    def _apply_branch(self, plan: Plan, row: PlanRow, base: dict[str, Any]) -> dict[str, Any]:
        name = row.identity

        fresh_pin = self.git.run(("rev-parse", "--verify", plan.pinned_ref))
        base["argv"].append(list(fresh_pin.argv))
        if fresh_pin.returncode or fresh_pin.out != plan.pinned_target:
            return self._close(base, REFUSED, "pinned_target_drifted_since_plan",
                               exit_code=fresh_pin.returncode, stderr=fresh_pin.stderr)

        current = self._current_tip(name)
        if current is None:
            return self._close(base, REFUSED, "branch_disappeared_before_apply")
        if current != row.preimage:
            return self._close(base, REFUSED, "branch_tip_moved_since_plan", current=current)

        if row.action == "delete_ancestry":
            if not self._is_ancestor(current, plan.pinned_target):
                return self._close(base, REFUSED, "ancestry_not_reconfirmed", current=current)
        else:
            prs = list(self.pull_requests.pull_requests_for_branch(name))
            if self._squash_evidence(prs, current) is None:
                return self._close(base, REFUSED, "pull_request_head_mismatch_at_apply",
                                   current=current)
            if any(pr.state == "open" for pr in prs):
                return self._close(base, REFUSED, "open_pull_request_at_apply", current=current)

        backup = self._verify_backup(name, current, base)
        if backup["result"] != "ok":
            return self._close(base, REFUSED, backup["reason"], current=current,
                               backup_ref=backup["ref"],
                               backup_local_readback=backup.get("local"),
                               backup_remote_readback=backup.get("remote"))

        flag = "-d" if row.action == "delete_ancestry" else "-D"
        deletion = self.git.run(("branch", flag, name))
        base["argv"].append(list(deletion.argv))
        after = self._current_tip(name)

        extra = {"backup_ref": backup["ref"], "backup_local_readback": backup["local"],
                 "backup_remote_readback": backup["remote"]}
        if deletion.returncode == 0 and after is None:
            return self._close(base, DELETED, row.reason, current=current,
                               exit_code=0, stdout=deletion.stdout, stderr=deletion.stderr, **extra)
        if deletion.returncode != 0 and after == current:
            return self._close(base, REFUSED, "deletion_command_failed_branch_intact",
                               current=current, exit_code=deletion.returncode,
                               stdout=deletion.stdout, stderr=deletion.stderr, **extra)
        # Exit code and observed state disagree. That is a partial operation: it
        # is UNKNOWN, it is not retried, and a human reads the backup ref.
        return self._close(base, UNKNOWN, "partial_deletion_state_unknown_no_automatic_retry",
                           current=current, exit_code=deletion.returncode,
                           stdout=deletion.stdout, stderr=deletion.stderr, **extra)

    def _verify_backup(self, name: str, tip: str, base: dict[str, Any]) -> dict[str, Any]:
        """Local update-ref, local readback, atomic remote push, remote readback.
        All four, or there is no backup and no deletion."""
        ref = self.backup_ref_for(name)
        update = self.git.run(("update-ref", ref, tip))
        base["argv"].append(list(update.argv))
        if update.returncode:
            return {"result": "failed", "reason": "backup_update_ref_failed", "ref": ref}

        local = self.git.run(("rev-parse", "--verify", ref))
        base["argv"].append(list(local.argv))
        if local.returncode or local.out.lower() != tip:
            return {"result": "failed", "reason": "backup_local_readback_mismatch",
                    "ref": ref, "local": local.out or None}

        push = self.git.run(("push", "--atomic", "origin", f"refs/heads/{name}:{ref}"))
        base["argv"].append(list(push.argv))
        if push.returncode:
            return {"result": "failed", "reason": "backup_remote_push_failed",
                    "ref": ref, "local": local.out}

        remote = self.git.run(("ls-remote", "--refs", "origin", ref))
        base["argv"].append(list(remote.argv))
        fields = remote.out.split()
        if remote.returncode or len(fields) != 2 or fields[0].lower() != tip or fields[1] != ref:
            return {"result": "failed", "reason": "backup_remote_readback_mismatch",
                    "ref": ref, "local": local.out, "remote": remote.out or None}
        return {"result": "ok", "reason": "backup_verified", "ref": ref,
                "local": local.out, "remote": fields[0].lower()}

    # ── worktree and cache application ──────────────────────────────────────

    def _apply_worktree(self, row: PlanRow, base: dict[str, Any]) -> dict[str, Any]:
        path = row.identity
        listing = self.git.run(("worktree", "list", "--porcelain"))
        base["argv"].append(list(listing.argv))
        registered = {line.split(" ", 1)[1] for line in listing.stdout.splitlines()
                      if line.startswith("worktree ")}
        if path not in registered and str(Path(path).resolve()) not in registered:
            return self._close(base, REFUSED, "worktree_registration_lost_since_plan")

        status = self.git.run(("-C", path, "status", "--porcelain"))
        # `git -C repo -C path` resolves the second -C relative to the first;
        # both forms land on the same tree, and a dirty tree refuses either way.
        base["argv"].append(list(status.argv))
        if status.returncode or status.stdout.strip():
            return self._close(base, REFUSED, "worktree_dirty_at_apply",
                               exit_code=status.returncode, stdout=status.stdout)

        argv = [*self.worktree_remove_command, path]
        base["argv"].append(list(argv))
        proc = subprocess.run(argv, cwd=self.repository, capture_output=True,
                              text=True, check=False)
        if proc.returncode == 0 and not Path(path).exists():
            return self._close(base, DELETED, row.reason, exit_code=0,
                               stdout=proc.stdout, stderr=proc.stderr)
        if proc.returncode != 0 and Path(path).exists():
            return self._close(base, REFUSED, "worktree_removal_refused_tree_intact",
                               exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
        return self._close(base, UNKNOWN, "partial_worktree_removal_unknown_no_automatic_retry",
                           exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def _apply_cache(self, row: PlanRow, base: dict[str, Any]) -> dict[str, Any]:
        path = Path(row.identity)
        if self._under_canonical():
            return self._close(base, REFUSED, "canonical_cache_cleanup_forbidden")
        if self.register.verdict(row.identity) is not None:
            return self._close(base, REFUSED, "never_cleanable_register_at_apply")
        if not path.exists():
            return self._close(base, REFUSED, "cache_path_disappeared_before_apply")
        argv = ["rm", "-rf", "--", str(path)]
        base["argv"].append(argv)
        try:
            shutil.rmtree(path)
        except OSError as exc:
            return self._close(base, UNKNOWN, "partial_cache_removal_unknown_no_automatic_retry",
                               exit_code=1, stderr=str(exc))
        if path.exists():
            return self._close(base, UNKNOWN, "partial_cache_removal_unknown_no_automatic_retry",
                               exit_code=0)
        return self._close(base, DELETED, row.reason, exit_code=0)


def _pr_evidence(pr: PullRequest) -> dict[str, Any]:
    return {"provider": pr.provider, "number": pr.number, "state": pr.state,
            "base_ref": pr.base_ref, "head_oid": pr.head_oid.lower(),
            "evidence_id": pr.evidence_id}


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint. Plans and reports; it does not mutate and it installs no schedule.
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="R07 repo-hygiene janitor (disabled by default; plans only).")
    parser.add_argument("--repository", default=str(CANONICAL_CHECKOUT))
    parser.add_argument("--tail-manifest",
                        default=str(CANONICAL_CHECKOUT / "out" / "repo-hygiene-program"
                                    / "settlement-manifest-branches.txt"))
    parser.add_argument("--never-cleanable-register",
                        default=str(CANONICAL_CHECKOUT / "out" / "repo-hygiene-program"
                                    / "never-cleanable-register.md"))
    parser.add_argument("--receipts", default=None,
                        help="JSONL receipt path; defaults to stdout-only reporting.")
    parser.add_argument("--execute", action="store_true",
                        help="Refused unless CARR_REPO_HYGIENE_EFFECT_PACKET names a "
                             "separately reviewed live-effect packet.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.execute and not os.environ.get("CARR_REPO_HYGIENE_EFFECT_PACKET"):
        print("refused: --execute requires a separately reviewed live-effect packet "
              "named in CARR_REPO_HYGIENE_EFFECT_PACKET; nothing was mutated",
              file=sys.stderr)
        return 2

    tail = load_settlement_tail(Path(args.tail_manifest))
    register = load_never_cleanable_register(Path(args.never_cleanable_register))
    in_tail = sum(1 for row in tail.values() if row.in_tail)
    print(json.dumps({
        "mode": "plan_only_disabled_by_default",
        "repository": args.repository,
        "settlement_rows": len(tail),
        "human_adjudication_tail": in_tail,
        "never_cleanable_roots": len(register.never_clean),
        "exact_root_only_roots": len(register.exact_root_only),
        "note": ("the tail is an adjudication input, never a deletion allowlist; "
                 "no candidate census is enumerated and nothing was mutated"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

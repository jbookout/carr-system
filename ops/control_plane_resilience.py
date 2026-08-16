"""Portable, deterministic resilience primitives for CARR control-plane drills.

This module intentionally owns no scheduler, credential, provider client, or
canonical write.  It turns four recovery boundaries into inspectable facts:
provider attempt traces, cache invalidation states, lease receipts, and budget
admission/refusal.  The ledger adapter may persist equivalent facts later; the
tests use this module to prove the policy independent of any provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class ProviderRoute:
    """A provider-neutral route declaration used only for selection order."""

    key: str
    priority: int
    enabled: bool
    health: str

    @property
    def eligible(self) -> bool:
        return self.enabled and self.health in {"healthy", "degraded"}


@dataclass(frozen=True)
class ProviderAttempt:
    route_key: str
    outcome: str
    error_class: str | None = None


@dataclass(frozen=True)
class FailoverResult:
    selected_route: str | None
    output: Any | None
    attempts: tuple[ProviderAttempt, ...]
    refusal: str | None = None


def run_with_failover(routes: Iterable[ProviderRoute], execute: Callable[[str], Any]) -> FailoverResult:
    """Try each eligible route once, ordered by priority, and retain the trace.

    There is no silent retry and no provider-specific exception contract.  The
    caller receives a refusal when no route can produce a result, rather than a
    made-up response or an implicit canonical write.
    """
    eligible = sorted((route for route in routes if route.eligible),
                      key=lambda route: (route.priority, route.key))
    if not eligible:
        return FailoverResult(None, None, (), "no_eligible_provider")

    attempts: list[ProviderAttempt] = []
    for route in eligible:
        try:
            output = execute(route.key)
        except Exception as exc:  # Provider adapters have no common error tree.
            attempts.append(ProviderAttempt(route.key, "failed", type(exc).__name__))
            continue
        attempts.append(ProviderAttempt(route.key, "succeeded"))
        return FailoverResult(route.key, output, tuple(attempts))
    return FailoverResult(None, None, tuple(attempts), "all_eligible_routes_failed")


@dataclass(frozen=True)
class CacheRead:
    state: str
    value: Any | None
    observed_at: int


@dataclass
class _CacheEntry:
    value: Any
    expires_at: int
    dependencies: frozenset[str]
    invalidated_at: int | None = None


class ProposalCache:
    """A provider-neutral proposal cache with explicit dependency invalidation."""

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    @staticmethod
    def key(job_type: str, schema_version: int, payload: Any, *, provider: str | None = None) -> str:
        """Return the canonical identity; ``provider`` is intentionally ignored."""
        del provider
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False)
        return sha256(f"{job_type}\n{schema_version}\n{payload_json}".encode("utf-8")).hexdigest()

    def put(self, job_type: str, schema_version: int, payload: Any, value: Any, *,
            now: int, ttl_seconds: int, dependencies: Iterable[str] = ()) -> str:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        key = self.key(job_type, schema_version, payload)
        self._entries[key] = _CacheEntry(value, now + ttl_seconds, frozenset(dependencies))
        return key

    def get(self, key: str, *, now: int) -> CacheRead:
        entry = self._entries.get(key)
        if entry is None:
            return CacheRead("miss", None, now)
        if entry.invalidated_at is not None:
            return CacheRead("invalidated", None, now)
        if now >= entry.expires_at:
            return CacheRead("expired", None, now)
        return CacheRead("hit", entry.value, now)

    def invalidate(self, dependency: str, *, now: int) -> int:
        """Invalidate each currently valid entry linked to an exact dependency."""
        changed = 0
        for entry in self._entries.values():
            if dependency in entry.dependencies and entry.invalidated_at is None:
                entry.invalidated_at = now
                changed += 1
        return changed


@dataclass(frozen=True)
class Lease:
    job_id: str
    token: str
    attempt: int
    owner: str
    leased_until: int


@dataclass(frozen=True)
class LeaseReceipt:
    kind: str
    attempt: int
    receipt_ref: str


@dataclass
class _Job:
    state: str = "queued"
    attempt: int = 0
    lease: Lease | None = None
    receipts: list[LeaseReceipt] = field(default_factory=list)


@dataclass(frozen=True)
class JobSnapshot:
    job_id: str
    state: str
    attempt: int
    receipts: tuple[LeaseReceipt, ...]


class LeaseLedger:
    """A deterministic in-memory lease state machine for recovery exercises."""

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}

    def enqueue(self, job_id: str) -> None:
        if not job_id or job_id in self._jobs:
            raise ValueError("job_id must be unique and non-empty")
        self._jobs[job_id] = _Job()

    def _reap(self, now: int) -> None:
        for job_id, job in self._jobs.items():
            lease = job.lease
            if job.state == "running" and lease is not None and now >= lease.leased_until:
                job.state = "queued"
                job.lease = None
                job.receipts.append(LeaseReceipt(
                    "lease_expired", lease.attempt, f"lease-expired:{job_id}:{lease.attempt}"))

    def claim(self, owner: str, *, now: int, lease_seconds: int) -> Lease:
        if not owner or lease_seconds <= 0:
            raise ValueError("owner and positive lease_seconds are required")
        self._reap(now)
        candidates = sorted(job_id for job_id, job in self._jobs.items() if job.state == "queued")
        if not candidates:
            raise LookupError("no claimable job")
        job_id = candidates[0]
        job = self._jobs[job_id]
        job.attempt += 1
        lease = Lease(job_id, f"lease:{job_id}:{job.attempt}", job.attempt, owner, now + lease_seconds)
        job.state = "running"
        job.lease = lease
        return lease

    def complete(self, job_id: str, token: str, *, now: int, receipt_ref: str) -> bool:
        self._reap(now)
        job = self._jobs.get(job_id)
        if job is None or job.state != "running" or job.lease is None or job.lease.token != token:
            return False
        job.receipts.append(LeaseReceipt("completion", job.lease.attempt, receipt_ref))
        job.state = "succeeded"
        job.lease = None
        return True

    def job(self, job_id: str) -> JobSnapshot:
        job = self._jobs[job_id]
        return JobSnapshot(job_id, job.state, job.attempt, tuple(job.receipts))


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    request_ref: str
    route_key: str
    estimated_cost_usd: float
    reservation_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BudgetReport:
    monthly_limit_usd: float
    spent_usd: float
    reserved_usd: float
    remaining_usd: float
    refused_count: int
    refused_cost_usd: float
    spent_by_route: tuple[tuple[str, float], ...]


class BudgetLedger:
    """Cost admission control with an evidence-bearing refusal register."""

    def __init__(self, *, monthly_limit_usd: float) -> None:
        if monthly_limit_usd < 0:
            raise ValueError("monthly_limit_usd must be non-negative")
        self.monthly_limit_usd = float(monthly_limit_usd)
        self._spent_usd = 0.0
        self._reservations: dict[str, BudgetDecision] = {}
        self._recorded: set[str] = set()
        self._refusals: list[BudgetDecision] = []
        self._spent_by_route: dict[str, float] = {}

    def authorize(self, route_key: str, *, estimated_cost_usd: float, request_ref: str) -> BudgetDecision:
        if estimated_cost_usd < 0 or not route_key or not request_ref:
            raise ValueError("route_key, request_ref, and a non-negative estimate are required")
        reserved = sum(d.estimated_cost_usd for d in self._reservations.values()
                       if d.reservation_id not in self._recorded)
        if self._spent_usd + reserved + estimated_cost_usd > self.monthly_limit_usd:
            refusal = BudgetDecision(False, request_ref, route_key, float(estimated_cost_usd),
                                     reason="monthly_budget_exceeded")
            self._refusals.append(refusal)
            return refusal
        reservation_id = f"reservation:{len(self._reservations) + 1}"
        decision = BudgetDecision(True, request_ref, route_key, float(estimated_cost_usd), reservation_id)
        self._reservations[reservation_id] = decision
        return decision

    def record(self, decision: BudgetDecision, *, actual_cost_usd: float) -> None:
        if (not decision.allowed or decision.reservation_id is None
                or decision.reservation_id not in self._reservations):
            raise ValueError("only an admitted reservation can record cost")
        if decision.reservation_id in self._recorded:
            raise ValueError("a reservation may record cost once")
        if actual_cost_usd < 0 or actual_cost_usd > decision.estimated_cost_usd:
            raise ValueError("actual cost must be non-negative and within the admitted reservation")
        self._recorded.add(decision.reservation_id)
        self._spent_usd += float(actual_cost_usd)
        self._spent_by_route[decision.route_key] = (
            self._spent_by_route.get(decision.route_key, 0.0) + float(actual_cost_usd))

    def report(self) -> BudgetReport:
        reserved = sum(d.estimated_cost_usd for d in self._reservations.values()
                       if d.reservation_id not in self._recorded)
        return BudgetReport(
            self.monthly_limit_usd,
            self._spent_usd,
            reserved,
            self.monthly_limit_usd - self._spent_usd - reserved,
            len(self._refusals),
            sum(d.estimated_cost_usd for d in self._refusals),
            tuple(sorted(self._spent_by_route.items())),
        )

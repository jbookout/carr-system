"""Model-neutral schedule and cognition execution primitives.

This module contains no provider SDK and no canonical record mutation. Provider
adapters return typed proposal envelopes; callers accept them only after schema
and budget checks. The job ledger remains the owner of state transitions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

from lib.control_plane import cache_key, validate_proposal


class BudgetExceeded(RuntimeError):
    pass


class ProviderExhausted(RuntimeError):
    def __init__(self, failures: list[dict[str, str]]):
        super().__init__("all registered provider routes failed")
        self.failures = failures


class ProviderAdapter(Protocol):
    def call(self, route: str, contract: dict[str, Any], payload: Any) -> dict[str, Any]: ...


class ResultCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, value: dict[str, Any], ttl_seconds: int) -> None: ...


def _guard_proposal(contract: dict[str, Any], proposal: dict[str, Any]) -> None:
    guard = contract.get("proposal_guard")
    if guard is None:
        return
    if guard == "weekly_social_no_quote_tweets":
        for index, draft in enumerate(proposal.get("drafts", [])):
            if not isinstance(draft, dict):
                raise ValueError(f"weekly social draft {index} must be an object")
            kind = str(draft.get("content_type") or "").strip().lower().replace("_", "-")
            if kind == "quote-tweet":
                raise ValueError(
                    f"weekly social draft {index} is a quote-tweet; route it to daily X replies")
        return
    raise ValueError(f"unregistered proposal guard: {guard}")


def _field_matches(field: str, value: int) -> bool:
    """Evaluate the finite cron grammar used by the tracked CARR schedules."""
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            return True
        if "/" in part:
            base, raw_step = part.split("/", 1)
            step = int(raw_step)
            lo, hi = (0, value) if base == "*" else (
                tuple(map(int, base.split("-", 1))) if "-" in base
                else (int(base), int(base)))
            if lo <= value <= hi and (value - lo) % step == 0:
                return True
        elif "-" in part:
            lo, hi = map(int, part.split("-", 1))
            if lo <= value <= hi:
                return True
        elif int(part) == value:
            return True
    return False


def cron_matches(expression: str, instant: datetime, timezone_name: str) -> bool:
    """True only at an exact scheduled minute in the workflow's own timezone."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"cron must have five fields: {expression}")
    local = instant.astimezone(ZoneInfo(timezone_name))
    cron_dow = (local.weekday() + 1) % 7  # cron: Sunday=0; datetime: Monday=0
    values = (local.minute, local.hour, local.day, local.month, cron_dow)
    return all(_field_matches(field, value) for field, value in zip(fields, values))


def due_workflows(manifest: dict[str, Any], instant: datetime) -> list[dict[str, Any]]:
    """Select due definitions; enqueue/idempotency remains a ledger operation."""
    due: list[dict[str, Any]] = []
    for workflow in manifest.get("workflows", []):
        recurrence = workflow.get("recurrence", {})
        cron = recurrence.get("cron")
        if workflow.get("enabled") is True and cron and cron_matches(
                cron, instant, recurrence["timezone"]):
            due.append(workflow)
    return due


class CognitionDispatcher:
    """Validate, budget, cache and fail over a finite cognition proposal job."""

    def __init__(self, provider: ProviderAdapter, cache: ResultCache,
                 before_route: Callable[[str], Any] | None = None,
                 after_accept: Callable[[str, Any, dict[str, Any]], None] | None = None,
                 after_failure: Callable[[str, Any, Exception], None] | None = None,
                 proposal_validator: Callable[[dict[str, Any]], None] | None = None):
        self.provider = provider
        self.cache = cache
        self.before_route = before_route
        self.after_accept = after_accept
        self.after_failure = after_failure
        self.proposal_validator = proposal_validator

    @staticmethod
    def _budget(contract: dict[str, Any], result: dict[str, Any]) -> None:
        usage = result.get("usage") or {}
        budget = contract["budget"]
        tokens = usage.get("total_tokens")
        cost = usage.get("cost_usd")
        if not isinstance(tokens, int) or tokens < 0:
            raise ValueError("provider result must report non-negative total_tokens")
        if not isinstance(cost, (int, float)) or cost < 0:
            raise ValueError("provider result must report non-negative cost_usd")
        if tokens > budget["max_tokens"] or float(cost) > float(budget["max_cost_usd"]):
            raise BudgetExceeded(
                f"usage tokens={tokens} cost={cost} exceeds registered cognition budget")

    def execute(self, contract: dict[str, Any], payload: Any) -> dict[str, Any]:
        if contract.get("canonical_write_authority") is not False:
            raise ValueError("cognition jobs are proposal-only")
        input_errors = validate_proposal(
            {"job_type": contract["key"],
             "schema_version": contract["input_schema_version"],
             "proposal": payload},
            contract["key"], contract["input_schema_version"], contract["input_schema"])
        if input_errors:
            raise ValueError("invalid cognition input: " + "; ".join(input_errors))
        key = cache_key(contract["key"], contract["output_schema_version"], payload)
        cached = self.cache.get(key) if contract.get("cache_ttl_seconds", 0) > 0 else None
        if cached is not None:
            try:
                cached_proposal = cached.get("proposal") if isinstance(cached, dict) else None
                if not isinstance(cached_proposal, dict):
                    raise ValueError("cached proposal is malformed")
                if self.proposal_validator is not None:
                    self.proposal_validator(cached_proposal)
            except Exception:
                # A contract can harden while an old provider-neutral entry is
                # still inside its TTL. Treat it as a miss; a new accepted
                # result overwrites the same key after full validation.
                cached = None
            else:
                return {**cached, "cache_hit": True}

        failures: list[dict[str, str]] = []
        for route in contract["provider_routes"]:
            reservation = None
            try:
                if self.before_route is not None:
                    reservation = self.before_route(route)
                result = self.provider.call(route, contract, payload)
                if not isinstance(result, dict):
                    raise ValueError("provider returned a non-object")
                proposal_envelope = {k: v for k, v in result.items() if k != "usage"}
                errors = validate_proposal(
                    proposal_envelope, contract["key"], contract["output_schema_version"],
                    contract["output_schema"])
                if errors:
                    raise ValueError("; ".join(errors))
                _guard_proposal(contract,result["proposal"])
                if self.proposal_validator is not None:
                    self.proposal_validator(result["proposal"])
                self._budget(contract, result)
                if self.after_accept is not None:
                    self.after_accept(route, reservation, result)
                accepted = {
                    "route": route, "proposal": result["proposal"],
                    "usage": result["usage"], "cache_key": key, "cache_hit": False,
                }
                if isinstance(result.get("provider_observation_recorded"), bool):
                    accepted["provider_observation_recorded"] = result[
                        "provider_observation_recorded"]
                ttl = int(contract.get("cache_ttl_seconds", 0))
                if ttl > 0:
                    self.cache.put(key, accepted, ttl)
                return accepted
            except BudgetExceeded as exc:
                if self.after_failure is not None:
                    self.after_failure(route, reservation, exc)
                raise
            except Exception as exc:
                if self.after_failure is not None:
                    self.after_failure(route, reservation, exc)
                failures.append({"route": route, "error_class": type(exc).__name__})
        raise ProviderExhausted(failures)

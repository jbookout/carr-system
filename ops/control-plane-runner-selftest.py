#!/usr/bin/env python3
"""Provider-free acceptance tests for the job runner and schedule owner."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def main() -> int:
    from lib.control_plane_runner import (
        BudgetExceeded, CognitionDispatcher, ProviderExhausted, cron_matches,
        due_workflows,
    )

    failures: list[str] = []
    total = 0

    def check(name: str, condition: bool) -> None:
        nonlocal total
        total += 1
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    monday = datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc)  # 08:30 Chicago
    check("cron matching is timezone-aware",
          cron_matches("30 8 * * 1-5", monday, "America/Chicago"))
    check("cron refuses a nonmatching minute",
          not cron_matches("0 8 * * 1-5", monday, "America/Chicago"))
    check("range and list fields are executable",
          cron_matches("30 8,13 15-21 * 1-5", monday, "America/Chicago"))

    manifest = {"workflows": [
        {"key": "on", "enabled": True,
         "recurrence": {"cron": "30 8 * * 1-5", "timezone": "America/Chicago"}},
        {"key": "off", "enabled": False,
         "recurrence": {"cron": "30 8 * * 1-5", "timezone": "America/Chicago"}},
    ]}
    check("the ledger scheduler selects enabled due work only",
          [w["key"] for w in due_workflows(manifest, monday)] == ["on"])

    class Cache:
        def __init__(self): self.value = None
        def get(self, _key): return self.value
        def put(self, _key, value, _ttl): self.value = value

    class Provider:
        def __init__(self): self.calls = []
        def call(self, route, _contract, _payload):
            self.calls.append(route)
            if route == "primary": raise RuntimeError("provider unavailable")
            return {"job_type": "fixture", "schema_version": 1,
                    "proposal": {"items": []},
                    "usage": {"total_tokens": 10, "cost_usd": 0.01}}

    contract = {"key": "fixture", "version": 1, "output_schema_version": 1,
                "input_schema_version": 1,
                "input_schema": {"type": "object", "required": ["x"],
                                 "properties": {"x": {"type": "integer"}}},
                "output_schema": {"type": "object", "required": ["items"],
                                  "properties": {"items": {"type": "array"}}},
                "provider_routes": ["primary", "secondary"],
                "budget": {"max_tokens": 20, "max_cost_usd": 0.10,
                           "timeout_seconds": 30},
                "cache_ttl_seconds": 60, "canonical_write_authority": False}
    provider, cache = Provider(), Cache()
    lifecycle: list[tuple[str, ...]] = []

    def reserve(route: str) -> str:
        lifecycle.append(("reserve", route))
        return f"r:{route}"

    def settle(route: str, reservation: str, _result) -> None:
        lifecycle.append(("settle", route, reservation))

    def release(route: str, reservation: str, _exc) -> None:
        lifecycle.append(("release", route, reservation))

    dispatcher = CognitionDispatcher(
        provider, cache,
        before_route=reserve,
        after_accept=settle,
        after_failure=release,
    )
    result = dispatcher.execute(contract, {"x": 1})
    check("provider failure falls through the registered route order",
          provider.calls == ["primary", "secondary"] and result["route"] == "secondary")
    check("cost is reserved before each attempt, released on failure, and settled on acceptance",
          lifecycle == [("reserve","primary"),("release","primary","r:primary"),
                        ("reserve","secondary"),("settle","secondary","r:secondary")])
    dispatcher.execute(contract, {"x": 1})
    check("validated results are served from provider-neutral cache",
          provider.calls == ["primary", "secondary"])

    class AlwaysFails:
        def call(self, route, *_args):
            raise ValueError(f"untrusted detail from {route}")
    try:
        CognitionDispatcher(AlwaysFails(), Cache()).execute(contract, {"x": 7})
        exhausted_detail = ""
    except ProviderExhausted as exc:
        exhausted_detail = str(exc)
    check("provider exhaustion exposes only bounded route and error classes",
          exhausted_detail ==
          "all registered provider routes failed: primary=ValueError, secondary=ValueError")

    class NeverDispatch:
        def __init__(self): self.calls = 0
        def call(self, *_args):
            self.calls += 1
            raise AssertionError("budget-refused route reached provider")
    refused_provider = NeverDispatch()
    refusal_events: list[tuple[object, ...]] = []
    def refuse_before_route(route: str) -> str:
        refusal_events.append(("admit", route))
        raise BudgetExceeded("monthly_budget_exceeded")
    def refusal_after_failure(route: str, reservation: object, exc: Exception) -> None:
        refusal_events.append(("failure", route, reservation, type(exc).__name__))
    try:
        CognitionDispatcher(refused_provider, Cache(), before_route=refuse_before_route,
                            after_failure=refusal_after_failure).execute(contract, {"x": 9})
        refused = False
    except BudgetExceeded:
        refused = True
    check("budget admission refusal stops before every provider call",
          refused and refused_provider.calls == 0
          and refusal_events == [("admit", "primary"),
                                 ("failure", "primary", None, "BudgetExceeded")])

    class Expensive:
        def call(self, *_args):
            return {"job_type": "fixture", "schema_version": 1,
                    "proposal": {"items": []},
                    "usage": {"total_tokens": 21, "cost_usd": 0.01}}
    try:
        CognitionDispatcher(Expensive(), Cache()).execute(contract, {"x": 2})
        refused = False
    except BudgetExceeded:
        refused = True
    check("over-budget model output is refused before acceptance", refused)

    try:
        CognitionDispatcher(Provider(), Cache()).execute(contract, {})
        refused = False
    except ValueError:
        refused = True
    check("untyped or incomplete cognition input is refused before dispatch", refused)

    unsafe = dict(contract, canonical_write_authority=True)
    try:
        CognitionDispatcher(Provider(), Cache()).execute(unsafe, {})
        refused = False
    except ValueError:
        refused = True
    check("a cognition contract can never claim canonical-write authority", refused)

    weekly = dict(contract, proposal_guard="weekly_social_no_quote_tweets")
    weekly["output_schema"] = {
        "type":"object","required":["drafts"],
        "properties":{"drafts":{"type":"array"}},
    }
    class QuoteTweetProvider:
        def call(self, *_args):
            return {"job_type":"fixture","schema_version":1,
                    "proposal":{"drafts":[{"content_type":"quote_tweet"}]},
                    "usage":{"total_tokens":1,"cost_usd":0.01}}
    try:
        CognitionDispatcher(QuoteTweetProvider(),Cache()).execute(weekly,{"x":3})
        refused = False
    except Exception:
        refused = True
    check("weekly social proposals deterministically reject quote tweets",refused)

    class ContractProvider:
        def __init__(self): self.calls: list[str] = []
        def call(self, route, *_args):
            self.calls.append(route)
            items = [] if route == "primary" else [{"ref": "input:1"}]
            return {"job_type":"fixture","schema_version":1,
                    "proposal":{"items":items},
                    "usage":{"total_tokens":1,"cost_usd":0.01}}
    contract_provider, contract_cache = ContractProvider(), Cache()
    contract_lifecycle: list[tuple[str, str]] = []
    def exact_proposal(proposal: dict) -> None:
        if proposal.get("items") != [{"ref":"input:1"}]:
            raise ValueError("proposal does not reconcile to typed input")
    guarded = CognitionDispatcher(
        contract_provider, contract_cache,
        before_route=lambda route: f"r:{route}",
        after_failure=lambda route, _reservation, _exc:
            contract_lifecycle.append(("release", route)),
        after_accept=lambda route, _reservation, _result:
            contract_lifecycle.append(("settle", route)),
        proposal_validator=exact_proposal,
    )
    guarded_result = guarded.execute(contract, {"x":4})
    check("workflow proposal contract runs before settlement and cache acceptance",
          contract_provider.calls == ["primary","secondary"]
          and contract_lifecycle == [("release","primary"),("settle","secondary")]
          and guarded_result["proposal"]["items"] == [{"ref":"input:1"}]
          and contract_cache.value is not None)
    stale_cache = Cache()
    stale_cache.value = {"route":"old","proposal":{"items":[]},
                         "usage":{"total_tokens":1,"cost_usd":0.01}}
    stale_provider = ContractProvider()
    refreshed = CognitionDispatcher(
        stale_provider, stale_cache, proposal_validator=exact_proposal).execute(
            contract, {"x":5})
    check("hardened contract revalidates and replaces a stale invalid cache entry",
          stale_provider.calls == ["primary","secondary"]
          and refreshed["proposal"]["items"] == [{"ref":"input:1"}]
          and stale_cache.value["proposal"]["items"] == [{"ref":"input:1"}])

    print(f"\ncontrol-plane-runner-selftest: {total-len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

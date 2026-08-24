#!/usr/bin/env python3
"""Contract tests for Slice 1 of the canonical room queue.

No Hermes task or room row is created here.  The adapter receives a recording
runner and the bridge receives in-memory room functions, which makes the two
important mutations (route-before-consume and removed idempotency) observable.
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import grammar  # noqa: E402
import kanban_adapter  # noqa: E402
import queue_grammar  # noqa: E402


FAILURES: list[str] = []


def check(label, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{label}: {exc}")
        print(f"  FAIL  {label}\n          {exc}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {exc!r}")
        print(f"  FAIL  {label}\n          unexpected {exc!r}")
    else:
        print(f"  ok    {label}")


CATALOG = {
    "v": 1,
    "targets": {
        "sol": {"enabled": True, "adapter": "desk", "assignee": "desk:codex-desk",
                "capabilities": ["read"], "effective_model": "gpt-5.6-sol"},
        "retired": {"enabled": False, "adapter": "hermes", "assignee": "builder",
                    "capabilities": ["read"], "unavailable_reason": "retired"},
    },
}


def turn(body, *, msg_id="11111111-1111-4111-8111-111111111111", seat="codex", seq=12):
    return {"body": body, "msg_id": msg_id, "seat": seat, "sponsor": "joe", "seq": seq}


def test_strict_enqueue_and_bounds():
    parsed = queue_grammar.parse(turn(
        "@queue enqueue target=sol cap=read priority=P1 runtime=45m key=attest finish=review :: Attest PR 514\n"
        "Run mutation checks."), CATALOG)
    assert parsed.kind == "enqueue"
    assert parsed.value["priority"] == 3
    assert parsed.value["runtime"] == "45m"
    assert parsed.value["finish"] == "review"
    assert parsed.value["idempotency_key"] == "room:partner-line:attest"

    for body, code in [
        ("@queue enqueue target=sol cap=read model=anything :: nope", "field_unknown"),
        ("@queue enqueue target=sol cap=read target=grok :: nope", "field_duplicate"),
        ("@queue enqueue target=nope cap=read :: nope", "target_unknown"),
        ("@queue enqueue target=sol cap=production :: nope", "capability_human_only"),
        ("@queue enqueue target=retired cap=read :: nope", "target_disabled"),
        ("@queue enqueue target=sol cap=read :: " + ("x" * 201), "title_invalid"),
    ]:
        outcome = queue_grammar.parse(turn(body), CATALOG)
        assert outcome.kind == "rejected", (body, outcome)
        assert outcome.code == code, (body, outcome.code)


def test_shared_key_converges_and_source_id_deduplicates():
    first = queue_grammar.parse(turn("@queue enqueue target=sol cap=read key=same :: One"), CATALOG)
    second = queue_grammar.parse(turn("@queue enqueue target=sol cap=read key=same :: Two", msg_id="22222222-2222-4222-8222-222222222222"), CATALOG)
    replay = queue_grammar.parse(turn("@queue enqueue target=sol cap=read :: One"), CATALOG)
    assert first.value["idempotency_key"] == second.value["idempotency_key"] == "room:partner-line:same"
    assert replay.value["idempotency_key"] == "room:partner-line:11111111-1111-4111-8111-111111111111"


def test_targets_and_status_parse_without_model():
    assert queue_grammar.parse(turn("@queue targets"), CATALOG).kind == "targets"
    by_id = queue_grammar.parse(turn("@queue status id=t_1234abcd"), CATALOG)
    assert by_id.kind == "status" and by_id.value == {"id": "t_1234abcd"}
    by_state = queue_grammar.parse(turn("@queue status state=review target=sol"), CATALOG)
    assert by_state.kind == "status" and by_state.value["state"] == "review"


def test_adapter_constructs_fixed_canonical_create_and_idempotency():
    seen = []

    def runner(argv):
        seen.append(argv)
        return {"id": "t_queue0001", "created": True}

    command = queue_grammar.parse(turn("@queue enqueue target=sol cap=read after=t_parent :: Verify queue"), CATALOG).value
    adapter = kanban_adapter.KanbanAdapter(runner=runner)
    result = adapter.create(command, turn("ignored"), CATALOG["targets"]["sol"])
    argv = seen[0]
    assert result["task_id"] == "t_queue0001"
    assert argv[:5] == ["hermes", "kanban", "--board", "carr-build", "create"]
    assert "--project" in argv and argv[argv.index("--project") + 1] == "carr"
    assert "--idempotency-key" in argv
    assert argv[argv.index("--idempotency-key") + 1] == command["idempotency_key"]
    assert "--parent" in argv and argv[argv.index("--parent") + 1] == "t_parent"
    body = argv[argv.index("--body") + 1]
    assert body.startswith("[CARR_QUEUE_META ") and '"cap":"read"' in body


def test_mutation_guard_idempotency_is_not_optional():
    """Removing the idempotency flag makes this test fail before a bridge retry can duplicate work."""
    source = inspect.getsource(kanban_adapter.KanbanAdapter.create)
    assert '"--idempotency-key"' in source


def test_service_creates_for_every_room_seat_and_keeps_status_bounded():
    class FakeAdapter:
        def __init__(self):
            self.creates = []

        def create(self, command, incoming, target):
            self.creates.append((command, incoming, target))
            return {"task_id": "t_queue0001", "created": False}

        def show(self, task_id):
            return {"task": {"id": task_id, "title": "x" * 1000, "status": "todo", "body": "never receipt this"}}

        def list(self, state, target, catalog):
            return {"tasks": [{"id": "t_queue0002", "title": "A", "status": state, "assignee": "desk:codex-desk"}]}

    adapter = FakeAdapter()
    service = kanban_adapter.QueueService(catalog=CATALOG, adapter=adapter)
    for seat in ("human", "claude", "hermes", "codex", "grok"):
        result = service.handle(turn("@queue enqueue target=sol cap=read key=shared :: Read only", seat=seat), room="partner-line")
        accepted = result["receipt"]["queue_accepted"]
        assert accepted["task_id"] == "t_queue0001" and accepted["status"] == "duplicate"
    assert len(adapter.creates) == 5
    status = service.handle(turn("@queue status id=t_queue0001"), room="partner-line")
    rendered = json.dumps(status["receipt"])
    assert "never receipt this" not in rendered and len(rendered) < 1000


def test_legacy_assignment_is_deprecated_without_a_local_log():
    posted = []
    outcome, parsed, _reason = grammar.classify(turn("@sol assign WR-9 Legacy title", seat="human"))
    assert outcome == "ok" and parsed is not None
    result = grammar.apply_assignment(turn("ignored", seat="human"), parsed,
                                      add_room_turn=lambda **kwargs: posted.append(kwargs) or {"seq": 13})
    assert result["record"]["status"] == "deprecated_use_queue"
    assert posted and "assignment_deprecated" in posted[0]["body"]
    assert "DEFAULT_ASSIGNMENTS_LOG" not in inspect.getsource(grammar)


def test_bridge_consumes_queue_before_routing_and_posts_receipt():
    class FakeQueue:
        def handle(self, incoming, *, room):
            assert room == "partner-line"
            return {"handled": True, "kind": "targets", "receipt": {"queue_targets": {"targets": []}}}

    posted = []
    def add_room_turn(**kwargs):
        posted.append(kwargs)
        return {"seq": 13}

    with tempfile.TemporaryDirectory() as root:
        state_path = Path(root) / "state.json"
        summary = bridge.run_once(
            state_path=state_path,
            read_room=lambda *_args, **_kwargs: {"turns": [turn("@queue targets")]},
            add_room_turn=add_room_turn,
            registry=type("Registry", (), {"entries": lambda self: {}, "path": Path(root) / "desks.json"})(),
            queue_service=FakeQueue(),
            read_profiles=lambda: [],
            log=lambda _msg: None,
        )
    assert summary["routed"]["11111111-1111-4111-8111-111111111111"] == []
    assert summary["queue"][0]["kind"] == "targets"
    assert posted and posted[0]["kind"] == "receipt"


def test_mutation_guard_queue_parse_precedes_route_turn():
    """Moving route_turn above queue classification reopens desk injection for malformed commands."""
    source = inspect.getsource(bridge.run_once)
    assert source.index("queue_service.handle") < source.index("state_mod.route_turn")


def main():
    check("strict enqueue grammar and bounds", test_strict_enqueue_and_bounds)
    check("shared key and delivery idempotency", test_shared_key_converges_and_source_id_deduplicates)
    check("targets and status are model-free reads", test_targets_and_status_parse_without_model)
    check("adapter fixed canonical create", test_adapter_constructs_fixed_canonical_create_and_idempotency)
    check("all model seats enqueue and status is bounded", test_service_creates_for_every_room_seat_and_keeps_status_bounded)
    check("legacy assignment is deprecation-only", test_legacy_assignment_is_deprecated_without_a_local_log)
    check("mutation guard: idempotency", test_mutation_guard_idempotency_is_not_optional)
    check("queue command is consumed before routing", test_bridge_consumes_queue_before_routing_and_posts_receipt)
    check("mutation guard: consumption order", test_mutation_guard_queue_parse_precedes_route_turn)
    if FAILURES:
        print(f"{len(FAILURES)} queue test(s) failed", file=sys.stderr)
        return 1
    print("all queue unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

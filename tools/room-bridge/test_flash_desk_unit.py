#!/usr/bin/env python3
"""The flash-local desk and target=auto routing (2026-09-24). No Flash server, no Jev, no Hermes: the wire gets a
fake opener, the service a fake router and a fake adapter, the registry a temporary file."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bridge  # noqa: E402
import desks  # noqa: E402
import flash_wire  # noqa: E402
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
        "flash": {"enabled": True, "adapter": "desk", "assignee": "desk:flash-model", "desk": "flash-model",
                  "capabilities": ["read"], "effective_model": "flash"},
        "claude-desktop": {"enabled": True, "adapter": "desk", "assignee": "desk:claude-desktop",
                           "desk": "claude-desktop", "capabilities": ["read", "repo-write", "record-write"],
                           "effective_model": "opus"},
        "joe": {"enabled": True, "adapter": "manual", "assignee": "human:joe",
                "capabilities": ["merge-approve", "production", "external-send", "destructive", "credential"]},
    },
}
POLICY = {"queue_targets": {"direct": "flash", "escalate": "claude-desktop", "code": "claude-desktop",
                            "script": "claude-desktop", "fallback": "claude-desktop"}}


def turn(body, *, msg_id="22222222-2222-4222-8222-222222222222"):
    return {"body": body, "msg_id": msg_id, "seat": "claude", "sponsor": "joe", "seq": 5,
            "origin_channel": "mcp", "origin_actor": "claude"}


class Reply:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode())
        self.status = 200

    def __enter__(self):
        return self.body

    def __exit__(self, *_):
        return False


def opener_returning(payload):
    def opener(req, timeout=None):
        opener.sent = json.loads(req.data)
        return Reply(payload)
    return opener


def test_wire_answers_with_thinking_off():
    opener = opener_returning({"choices": [{"message": {"content": " 42 "}, "finish_reason": "stop"}]})
    out = flash_wire.run_turn("6 x 7?", opener=opener)
    assert out == {"status": "completed", "result": "42", "finish": "stop"}, out
    assert opener.sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert opener.sent["messages"] == [{"role": "user", "content": "6 x 7?"}]


def test_wire_empty_reply_is_no_answer_not_completed():
    out = flash_wire.run_turn("q", opener=opener_returning(
        {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}))
    assert out["status"] == "failed" and out["detail"] == "no_answer" and out["finish"] == "length", out


def test_wire_unreachable_and_malformed_fail_cleanly():
    def down(req, timeout=None):
        raise urllib.error.URLError("refused")
    assert flash_wire.run_turn("q", opener=down)["status"] == "failed"
    assert flash_wire.run_turn("q", opener=opener_returning({"nope": 1}))["detail"] == "flash reply was malformed"


def test_registry_takes_a_flash_local_desk_naming_model_and_effort():
    with tempfile.TemporaryDirectory() as d:
        reg = desks.Registry(Path(d) / "desks.json")
        entry = reg.register("flash-model", "flash-local")
        assert entry["model"] == "flash" and entry["effort"], entry
        assert reg.resolve("flash-model")["kind"] == "flash-local"


def test_bridge_delivers_flash_synchronously_and_probes_its_server():
    rooms = []
    out = bridge.deliver("flash-model", {"kind": "flash-local"}, "flash",
                         {"body": "hi", "msg_id": "m", "seq": 1, "seat": "joe"}, state={}, registry=None,
                         results_path=Path("/dev/null"), add_room_turn=lambda **kw: rooms.append(kw),
                         dispatch_fn=lambda *a, **k: {"status": "completed", "result": "hello"})
    assert out["outcome"] == "replied_sync" and rooms[0]["body"] == "hello", (out, rooms)
    saved = flash_wire.is_up
    try:
        flash_wire.is_up = lambda *a, **k: False
        assert bridge.probe_live({"kind": "flash-local"}) is False
    finally:
        flash_wire.is_up = saved


def test_grammar_accepts_auto_and_keeps_human_only_on_joes_lane():
    ok = queue_grammar.parse(turn("@queue enqueue target=auto cap=read :: Shorten this"), CATALOG)
    assert ok.kind == "enqueue" and ok.value["target"] == "auto", ok
    refused = queue_grammar.parse(turn("@queue enqueue target=auto cap=production :: Deploy"), CATALOG)
    assert refused.kind == "rejected" and refused.code == "capability_human_lane_required", refused


class FakeRouter:
    def __init__(self, route, *, overflow=False):
        self.route, self.overflow, self.calls = route, overflow, []

    def load_policy(self):
        return POLICY

    def decide(self, task, context="", *, flash_free=True, policy=None):
        self.calls.append((task, context, flash_free))
        return {"route": self.route, "model": "flash" if self.route == "direct" else "opus", "effort": "x",
                "scores": {"direct": 0.9}, "overflow": self.overflow and not flash_free, "jev_error": None}


class FakeAdapter:
    def __init__(self):
        self.creates = []

    def create(self, command, incoming, target):
        self.creates.append((command, target))
        return {"task_id": "t_flash0001", "created": True}


def service(route, *, flash_up=True, overflow=False):
    adapter = FakeAdapter()
    router = FakeRouter(route, overflow=overflow)
    svc = kanban_adapter.QueueService(catalog=CATALOG, adapter=adapter, router=router, flash_up=lambda: flash_up)
    return svc, adapter, router


def test_auto_direct_task_goes_to_flash_with_the_route_on_the_receipt():
    svc, adapter, router = service("direct")
    out = svc.handle(turn("@queue enqueue target=auto cap=read :: Shorten this\nWe are reaching out."), room="p")
    accepted = out["receipt"]["queue_accepted"]
    assert accepted["target"] == "flash" and accepted["route"]["route"] == "direct", accepted
    assert adapter.creates[0][0]["target"] == "flash" and adapter.creates[0][1]["desk"] == "flash-model"
    assert router.calls[0][0] == "Shorten this" and "reaching out" in router.calls[0][1]


def test_auto_judgment_task_goes_to_opus():
    svc, adapter, _ = service("escalate")
    out = svc.handle(turn("@queue enqueue target=auto cap=read :: Can the tenant sublet?"), room="p")
    assert out["receipt"]["queue_accepted"]["target"] == "claude-desktop"


def test_auto_flash_route_falls_back_when_flash_is_down():
    svc, adapter, _ = service("direct", flash_up=False, overflow=True)
    out = svc.handle(turn("@queue enqueue target=auto cap=read :: Shorten this"), room="p")
    accepted = out["receipt"]["queue_accepted"]
    assert accepted["target"] == "claude-desktop", accepted
    assert accepted["route"]["fallback_reason"] == "flash_busy_or_down", accepted


def test_auto_flash_route_falls_back_when_flash_refuses_the_capability():
    svc, adapter, _ = service("direct")
    out = svc.handle(turn("@queue enqueue target=auto cap=repo-write :: Tidy the README"), room="p")
    accepted = out["receipt"]["queue_accepted"]
    assert accepted["target"] == "claude-desktop"
    assert accepted["route"]["fallback_reason"] == "capability_target_refused", accepted


def test_explicit_target_never_consults_the_router():
    svc, adapter, router = service("direct")
    svc.handle(turn("@queue enqueue target=flash cap=read :: Shorten this"), room="p")
    assert router.calls == [] and adapter.creates[0][0]["target"] == "flash"


class FakeAdapterDuplicate:
    """create() reports the SECOND call onward as a duplicate of the first, and show()
    answers with the first call's own body — the existing task's real, immutable target."""

    def __init__(self):
        self.creates = []
        self.created = False

    def create(self, command, incoming, target):
        self.creates.append((command, target))
        first = not self.created
        self.created = True
        return {"task_id": "t_flash0002", "created": first}

    def show(self, task_id):
        meta = {"v": 1, "target": "flash", "cap": "read", "source_seq": 5,
                "source_msg_id": "22222222-2222-4222-8222-222222222222", "finish": "done"}
        body = f"[CARR_QUEUE_META {json.dumps(meta, separators=(',', ':'))}]\nShorten this."
        return {"task": {"id": task_id, "body": body, "status": "todo"}}


class FlippingRouter:
    """First call: direct (flash). Second call: escalate (claude-desktop) — Flash's own
    liveness or Jev's decision can differ between an original target=auto send and a
    retry that reuses the SAME idempotency key, per PR #1249's review finding 4."""

    def __init__(self):
        self.calls = 0

    def load_policy(self):
        return POLICY

    def decide(self, task, context="", *, flash_free=True, policy=None):
        self.calls += 1
        route = "direct" if self.calls == 1 else "escalate"
        model = "flash" if route == "direct" else "opus"
        return {"route": route, "model": model, "effort": "x", "scores": {},
                "overflow": False, "jev_error": None}


def test_retried_auto_command_reports_the_existing_tasks_actual_target():
    adapter = FakeAdapterDuplicate()
    router = FlippingRouter()
    svc = kanban_adapter.QueueService(catalog=CATALOG, adapter=adapter, router=router,
                                      flash_up=lambda: True)
    incoming = turn("@queue enqueue target=auto cap=read key=retry-target :: Shorten this.")
    first = svc.handle(incoming, room="p")
    replay = svc.handle(incoming, room="p")
    assert first["receipt"]["queue_accepted"]["target"] == "flash", first
    assert replay["receipt"]["queue_accepted"]["status"] == "duplicate", replay
    # The retry's own route_auto call picked escalate -> claude-desktop, but the task
    # ALREADY EXISTS, on flash: the receipt must report where that existing task really
    # is, not this call's freshly recomputed (and here, different) route.
    assert replay["receipt"]["queue_accepted"]["target"] == "flash", replay


def test_real_catalog_and_policy_agree():
    catalog = kanban_adapter.load_catalog()
    policy = kanban_adapter._model_router().load_policy()
    for route, alias in policy["queue_targets"].items():
        if route.startswith("_"):
            continue
        assert alias in catalog["targets"] and catalog["targets"][alias]["enabled"], (route, alias)
    assert catalog["targets"]["flash"]["desk"] == "flash-model"


def main() -> int:
    check("wire answers with thinking off", test_wire_answers_with_thinking_off)
    check("wire empty reply is no_answer", test_wire_empty_reply_is_no_answer_not_completed)
    check("wire unreachable and malformed fail cleanly", test_wire_unreachable_and_malformed_fail_cleanly)
    check("registry takes a flash-local desk", test_registry_takes_a_flash_local_desk_naming_model_and_effort)
    check("bridge delivers flash synchronously and probes it", test_bridge_delivers_flash_synchronously_and_probes_its_server)
    check("grammar accepts auto, keeps human-only on Joe's lane", test_grammar_accepts_auto_and_keeps_human_only_on_joes_lane)
    check("auto direct task goes to flash", test_auto_direct_task_goes_to_flash_with_the_route_on_the_receipt)
    check("auto judgment task goes to opus", test_auto_judgment_task_goes_to_opus)
    check("auto flash route falls back when flash is down", test_auto_flash_route_falls_back_when_flash_is_down)
    check("auto flash route falls back on capability", test_auto_flash_route_falls_back_when_flash_refuses_the_capability)
    check("explicit target never consults the router", test_explicit_target_never_consults_the_router)
    check("retried auto command reports the existing task's actual target",
          test_retried_auto_command_reports_the_existing_tasks_actual_target)
    check("real catalog and policy agree", test_real_catalog_and_policy_agree)
    if FAILURES:
        print(f"{len(FAILURES)} flash desk test(s) failed", file=sys.stderr)
        return 1
    print("all flash desk unit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

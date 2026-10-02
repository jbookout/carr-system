#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verb_io  # noqa: E402


def fake_client(root: Path) -> Path:
    path = root / "fake-call-verb.py"
    path.write_text("""#!/usr/bin/env python3
import json, os, sys
args = json.loads(sys.argv[2])
profile = os.environ.get('CARR_MCP_CLIENT_PROFILE')
actor = 'hermes-pilot' if profile == 'hermes-projector' else 'joe-local'
print(json.dumps({'ok': True, 'room': 'partner-line', 'sponsor': 'joe',
  'seat': 'hermes', 'kind': 'receipt', 'origin_channel': 'mcp',
  'origin_actor': actor, 'msg_id': args['msg_id'],
  'idempotency_key': args['idempotency_key'], 'seq': 9}))
""", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_projector_uses_hermes_profile_and_accepts_exact_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = fake_client(Path(directory))
        out = verb_io.project_room_queue("{}", msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                         call_verb_path=path)
    assert out["origin_actor"] == "hermes-pilot"


def test_normal_room_turn_stays_joe_local() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = fake_client(Path(directory))
        out = verb_io.add_room_turn("hello", "hermes", kind="receipt",
                                    msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                    call_verb_path=path)
    assert out["origin_actor"] == "joe-local"


def test_room_turn_accepts_stable_callback_idempotency() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = fake_client(Path(directory))
        out = verb_io.add_room_turn(
            "{}", "claude", msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            idempotency_key="queue-completion:t_queue0001", call_verb_path=path,
        )
    assert out["idempotency_key"] == "queue-completion:t_queue0001"


def test_projector_rejects_reader_incompatible_append_response() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = fake_client(Path(directory))
        prior = os.environ.get("CARR_MCP_CLIENT_PROFILE")
        os.environ["CARR_MCP_CLIENT_PROFILE"] = "local"
        try:
            try:
                verb_io.project_room_queue("{}", msg_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                            call_verb_path=path,
                                            client_profile="local")
            except RuntimeError as exc:
                assert "rejected provenance" in str(exc)
            else:
                raise AssertionError("joe-local response was accepted as projector health")
        finally:
            if prior is None:
                os.environ.pop("CARR_MCP_CLIENT_PROFILE", None)
            else:
                os.environ["CARR_MCP_CLIENT_PROFILE"] = prior


def main() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_")]
    for test_case in tests:
        test_case()
    print(f"all {len(tests)} room-bridge verb identity tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

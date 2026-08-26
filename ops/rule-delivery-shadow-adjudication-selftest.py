#!/usr/bin/env python3
"""Static contract for the source-only legacy shadow adjudication package."""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from lib.rule_delivery_shadow import finding, observation_id  # noqa:E402
ARTIFACT = REPO / "audits/rule-delivery-shadow-adjudication-2026-08-26.v1.json"
HEX = set("0123456789abcdef")
EXPLAINED = {
    "a76928580de0fddc3231420e6663b3219831b80526d8cabb60b69e4cc85c2028",
    "46117889922249d1726395fdbe7f79e3ec8451e6ade050360b7a2b5262e83bf1",
    "95570890c5594f2196787adbbd0ab5096acc1dace9869f0b04ec7c5819666524",
    "c3f85ecea5509e543433d396eaead5ab4181d7dcfd3210d2c96e1727a76c6655",
    "c86bbe2ed62db23a747926008d29eb9ec12b25307e43aa100590f15cf8e48d96",
    "6717fc4071cf00837c4cb7ff3611d45f4f1de40e9af20d610e7d36150036a1e0",
    "d2364911e26f0efab8ea0b7ea68331fb32a1842cc7b0e936dbee2c311184d895",
    "df7a24c8d2113b609bbe04dd0b49fc96e52cff949503501d24497ca7bd6cd041",
}
REMEDIATED = {
    "ef796a5acc749ddaedf31c2fbe772046283d7a388c30a71299705ec2d5a2caba",
    "84b830dc596a702facf39ddd7fa43896a94493fa6943b8ab63f659fcb0ae5e9a",
    "4d2572bbfbf7942ee47c589a4dcc67542fe320539bc58502043327472bc5a98c",
    "55590cf93cb601888c34ea70e944561b171819dda59466a550205d25c38e8216",
    "04bcc76d7f557e4cf7680db48f1f0f7b6ef2b1f5cb359e4e39b043ca87617207",
    "f0c4df3eb2e3d4e8c9843ef62afc587bccd36043a1bbb1aa7a5af00851fef559",
}
EXPLAINED_CONTEXT = {
    "a76928580de0fddc3231420e6663b3219831b80526d8cabb60b69e4cc85c2028":
        ("Git/PR merge",),
    "46117889922249d1726395fdbe7f79e3ec8451e6ade050360b7a2b5262e83bf1":
        ("Git merge", "release schema ledger", "release key"),
    "95570890c5594f2196787adbbd0ab5096acc1dace9869f0b04ec7c5819666524":
        ("schema/release ledger", "design problem"),
    "c3f85ecea5509e543433d396eaead5ab4181d7dcfd3210d2c96e1727a76c6655":
        ("Git merge", "release ledger", "release key"),
    "c86bbe2ed62db23a747926008d29eb9ec12b25307e43aa100590f15cf8e48d96":
        ("Git merge", "migration/schema ledger", "post-apply"),
    "6717fc4071cf00837c4cb7ff3611d45f4f1de40e9af20d610e7d36150036a1e0":
        ("Git merge", "migration ledger", "release key"),
    "d2364911e26f0efab8ea0b7ea68331fb32a1842cc7b0e936dbee2c311184d895":
        ("Git merge", "schema/migration ledger", "post-apply"),
    "df7a24c8d2113b609bbe04dd0b49fc96e52cff949503501d24497ca7bd6cd041":
        ("Git merge", "release/schema ledger"),
}


def digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def pinned_ledger_prefix(raw: bytes, snapshot: dict) -> tuple[bytes, int]:
    """Return the immutable line-preserving snapshot prefix of an append-only log."""
    assert set(snapshot) == {"line_count", "sha256"}
    line_count = snapshot["line_count"]
    assert (isinstance(line_count, int) and not isinstance(line_count, bool)
            and line_count > 0)
    assert digest(snapshot["sha256"])
    lines = raw.splitlines(keepends=True)
    assert len(lines) >= line_count, "ledger is shorter than its immutable snapshot"
    prefix = b"".join(lines[:line_count])
    assert (hashlib.sha256(prefix).hexdigest() == snapshot["sha256"]), (
        "ledger snapshot prefix changed")
    return prefix, len(lines) - line_count


document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
assert document["schema"] == "rule-delivery-shadow-adjudication/v1"
assert document["status"] == "independently-reviewed"
assert document["review"] == {
    "reviewers": ["sol-orchestration", "independent-cutover-matrix"],
    "reviewed_at": "2026-08-26",
    "scope": "classification,evidence-bindings,source-remedies",
    "verdict": "clean",
}
assert document["ledger"] == {
    "path": "out/rule-delivery-shadow.jsonl",
    "snapshot": {
        "line_count": 30,
        "sha256": "6a7a06e2b032088013090aa97bc34cb668e3a8a3b4809a19cd6938849213853f",
    },
    "finding_count": 14,
    "mutation": "none",
}
assert "any recurrence invalidates eligibility" in document["disposition_boundary"]
assert "remain shadow" in document["source_remedy"]["validation"]

events = document["events"]
assert len(events) == 14
assert len({event["event_id"] for event in events}) == 14
assert {event["event_id"] for event in events
        if event["proposed_disposition"] == "explained"} == EXPLAINED
assert {event["event_id"] for event in events
        if event["proposed_disposition"] == "remediated"} == REMEDIATED
for event in events:
    assert set(event) == {"event_id", "observed_at", "session_id",
                          "proposed_disposition", "reason", "remedy_scope",
                          "transcript"}
    assert digest(event["event_id"]) and event["reason"]
    transcript = event["transcript"]
    assert transcript["path"].endswith(".jsonl") and digest(transcript["sha256"])
    excerpt = transcript["excerpt"]
    assert set(excerpt) == {"start_line", "end_line", "sha256"}
    assert 0 < excerpt["start_line"] <= excerpt["end_line"] and digest(excerpt["sha256"])
    if event["proposed_disposition"] == "explained":
        assert event["remedy_scope"] == "adjudication"
        assert "polysemy" in event["reason"]
        assert all(context in event["reason"]
                   for context in EXPLAINED_CONTEXT[event["event_id"]])
    else:
        assert "generic" in event["remedy_scope"]
    for scope in event["remedy_scope"].split("+"):
        refs = document["source_remedy"].get(scope)
        assert isinstance(refs, list) and refs
        assert all((REPO / ref).is_file() for ref in refs)

# On the owning machine, independently recompute the pinned append-only ledger,
# derive its finding identities, and rehash every transcript/excerpt. Hosted CI
# does not own these raw local files; that branch explicitly validates the full
# committed digest envelope above and reports which external proof was absent.
ledger_candidates = [
    REPO / document["ledger"]["path"],
    Path("/Users/booko/carr-system") / document["ledger"]["path"],
]
ledger_path = next((path for path in ledger_candidates if path.is_file()), None)
if ledger_path:
    ledger_prefix, appended_lines = pinned_ledger_prefix(
        ledger_path.read_bytes(), document["ledger"]["snapshot"])
    derived = {}
    for line in ledger_prefix.splitlines():
        row = json.loads(line)
        if finding(row):
            derived[observation_id(row)] = (row.get("session"), row.get("ts"))
    assert derived == {event["event_id"]: (event["session_id"], event["observed_at"])
                       for event in events}
    print(f"verified immutable ledger snapshot and {len(derived)} derived finding identities; "
          f"retained {appended_lines} later append-only line(s)")
else:
    print("external raw ledger unavailable; validated committed 14-event digest envelope")

# Snapshot validation deliberately accepts appended telemetry but never a short
# or rewritten historical prefix.
fixture_prefix = b'{"record_type":"observation","n":1}\n{"record_type":"observation","n":2}\n'
fixture_snapshot = {"line_count": 2,
                    "sha256": hashlib.sha256(fixture_prefix).hexdigest()}
fixture_appended = fixture_prefix + b'{"record_type":"disposition"}\n'
assert pinned_ledger_prefix(fixture_appended, fixture_snapshot)[1] == 1
fixture_lines = fixture_prefix.splitlines(keepends=True)
rewritten_fixture = b'{"record_type":"rewritten"}\n' + fixture_lines[1]
for invalid_fixture in (fixture_lines[0], rewritten_fixture):
    try:
        pinned_ledger_prefix(invalid_fixture, fixture_snapshot)
    except AssertionError:
        pass
    else:
        raise AssertionError("short or rewritten ledger snapshot prefix was accepted")

verified_transcripts = 0
missing_transcripts = []
for event in events:
    transcript = event["transcript"]
    path = Path(transcript["path"])
    if not path.is_file():
        missing_transcripts.append(transcript["path"])
        continue
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == transcript["sha256"]
    lines = raw.splitlines(keepends=True)
    excerpt = transcript["excerpt"]
    selected = b"".join(lines[excerpt["start_line"] - 1:excerpt["end_line"]])
    assert hashlib.sha256(selected).hexdigest() == excerpt["sha256"]
    verified_transcripts += 1
if missing_transcripts:
    assert verified_transcripts == 0, "partial transcript evidence availability is invalid"
    print(f"external transcripts unavailable ({len(missing_transcripts)}); "
          "validated every committed file/excerpt digest and line envelope")
else:
    print(f"verified {verified_transcripts} transcript files/excerpts against raw bytes")

print("rule-delivery-shadow-adjudication-selftest: 14/14 source candidates valid")

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const contract = JSON.parse(await readFile(new URL("../contracts/phase4-shared-continuity.v1.json", import.meta.url), "utf8"));
const gates = new Map(contract.gates.map(gate => [gate.id, gate]));

test("Phase 4 completion requires the exact six bidirectional continuity gates", () => {
  assert.deepEqual([...gates.keys()], [
    "PH4-MEMORY-FRESH-001",
    "PH4-MEMORY-TENTATIVE-001",
    "PH4-MEMORY-CONFLICT-001",
    "PH4-MEMORY-SCOPE-001",
    "PH4-DOCUMENT-RETRIEVAL-001",
    "PH4-SHARED-CONTINUITY-SOAK-001"
  ]);
  assert.deepEqual(contract.directions, ["joe_to_dell", "dell_to_joe"]);
  assert.match(contract.completion_rule, /fresh independent sessions/);
  assert.match(contract.completion_rule, /Static fixtures.*not passing evidence/);
});

test("shared memory acceptance distinguishes durable truth from conversational evidence", () => {
  assert.match(contract.shared_truth_boundary.canonical, /governed record-layer write/);
  assert.match(contract.shared_truth_boundary.tentative, /pending proposals|require confirmation/);
  assert.match(contract.shared_truth_boundary.personal, /never crosses partner scope/);
  assert.match(contract.shared_truth_boundary.raw_session, /not the canonical shared record/);
  assert.match(gates.get("PH4-MEMORY-FRESH-001").pass.join(" "), /without being told where it was stored/);
  assert.match(gates.get("PH4-MEMORY-TENTATIVE-001").refuse.join(" "), /silent promotion/);
});

test("conflict and privacy gates reject the two dangerous silent failures", () => {
  assert.match(gates.get("PH4-MEMORY-CONFLICT-001").pass.join(" "), /version conflict/);
  assert.match(gates.get("PH4-MEMORY-CONFLICT-001").refuse.join(" "), /last-write-wins/);
  assert.match(gates.get("PH4-MEMORY-SCOPE-001").pass.join(" "), /personal canary is absent/);
  assert.match(gates.get("PH4-MEMORY-SCOPE-001").refuse.join(" "), /prompt-only privacy enforcement/);
});

test("deliverable gate proves exact bytes on the other partner's computer", () => {
  const gate = gates.get("PH4-DOCUMENT-RETRIEVAL-001");
  assert.match(gate.pass.join(" "), /different supported computer/);
  assert.match(gate.pass.join(" "), /content hash/);
  assert.match(gate.pass.join(" "), /secure download bytes match/);
  assert.match(gate.refuse.join(" "), /metadata-only retrieval/);
  assert.match(gate.refuse.join(" "), /public permanent object URL/);
});

test("the soak gate forbids one-direction, hand-carried, or operator-repaired evidence", () => {
  const gate = gates.get("PH4-SHARED-CONTINUITY-SOAK-001");
  assert.match(gate.setup, /48 consecutive hours/);
  assert.match(gate.pass.join(" "), /both directions/);
  assert.match(gate.pass.join(" "), /no partner hand-carries/);
  assert.match(gate.refuse.join(" "), /one-direction evidence/);
  assert.match(gate.refuse.join(" "), /manually repairs hidden state/);
});

test("every runtime receipt can prove origin, retrieval, version, and direction", () => {
  for (const field of [
    "direction",
    "origin_session_id",
    "fresh_receiving_session_id",
    "canonical_record_or_document_id",
    "canonical_version",
    "write_audit_event_id",
    "retrieval_audit_event_id",
    "result",
    "failure_detail"
  ]) assert.ok(contract.required_receipt_fields.includes(field), field);
});

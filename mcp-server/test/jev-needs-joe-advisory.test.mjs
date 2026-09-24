import test from "node:test";
import assert from "node:assert/strict";
import { needsJoeAdvisory } from "../src/jev-needs-joe-advisory.js";

const queue = { items: [
  { human_ref: "WR-000123", title: "Needs Joe advice", state: "ready",
    source: { label: "runbook", freshness: "current" }, next_human_action: "Review the plan" },
  { human_ref: "WR-000124", title: "Browser route", state: "captured",
    source: { label: "runbook", freshness: "current" }, next_human_action: "Clarify the owner" },
] };

function fakeVendor(inspect, alter = answers => answers) {
  return async (_url, init) => {
    const payload = JSON.parse(init.body);
    inspect?.(payload, init);
    const answers = {};
    for (const key of Object.keys(payload.questions)) {
      if (key.endsWith("_class")) {
        const [, index, ref] = /^item_(\d+)_(WR-\d+)_class$/.exec(key);
        const selected = `${index}|${ref}|decision_ready`;
        const probabilities = Object.fromEntries(Object.keys(payload.questions[key].criteria)
          .map(option => [option, option === selected ? 1 : 0]));
        answers[key] = { type: "choice", choice: selected, confidence: 0.9, probabilities };
      } else answers[key] = { type: "noul", noul: 0.8 };
    }
    return { ok: true, json: async () => ({ model: payload.model, answers: alter(answers) }) };
  };
}

test("one bounded batch names the exact item in every model-visible question", async () => {
  const before = structuredClone(queue);
  const advisory = await needsJoeAdvisory(queue, { apiKey: "test-only", fetchImpl: fakeVendor((payload, init) => {
    assert.equal(payload.model, "jev-1.13.0");
    assert.equal(payload.state.items.length, 2);
    assert.equal(Object.keys(payload.questions).length, 8);
    assert.ok(init.body.length <= 52000);
    assert.equal(init.signal.aborted, false);
    for (const [index, item] of payload.state.items.entries()) {
      const matching = Object.entries(payload.questions).filter(([key]) => key.startsWith(`item_${index}_${item.human_ref}_`));
      assert.equal(matching.length, 4);
      for (const [, question] of matching) {
        assert.match(question.instructions, new RegExp(`state\\.items\\[${index}\\]`));
        assert.ok(question.instructions.includes(item.human_ref));
      }
    }
  }) });
  assert.equal(advisory.status, "available");
  assert.deepEqual(advisory.items.map(item => item.human_ref), queue.items.map(item => item.human_ref));
  assert.ok(advisory.items.every(item => item.calibration_status === "unverified_model_output"));
  assert.equal(advisory.judged_count, 2);
  assert.equal(advisory.abstained_count, 0);
  assert.ok(Number.isInteger(advisory.vendor_elapsed_ms));
  assert.match(advisory.snapshot_digest, /^sha256:[0-9a-f]{64}$/);
  assert.deepEqual(queue, before);
});

test("permuting the queue regenerates question paths and source digest", async () => {
  const original = await needsJoeAdvisory(queue, { apiKey: "test-only", fetchImpl: fakeVendor() });
  const reversed = await needsJoeAdvisory({ items: [...queue.items].reverse() }, { apiKey: "test-only", fetchImpl: fakeVendor() });
  assert.notEqual(original.snapshot_digest, reversed.snapshot_digest);
  assert.deepEqual(reversed.items.map(item => item.human_ref), ["WR-000124", "WR-000123"]);
});

test("a mismatched class echo abstains for that item", async () => {
  const advisory = await needsJoeAdvisory(queue, { apiKey: "test-only", fetchImpl: fakeVendor(null, answers => {
    answers["item_0_WR-000123_class"].choice = "1|WR-000124|decision_ready";
    return answers;
  }) });
  assert.equal(advisory.status, "partial");
  assert.equal(advisory.items[0].judged, false);
  assert.equal(advisory.items[1].judged, true);
});

test("malformed or contradictory Choice distributions abstain", async () => {
  for (const corrupt of [
    answer => { delete answer.probabilities; },
    answer => { answer.probabilities = { bogus: 1 }; },
    answer => { answer.probabilities[answer.choice] = 0.2; },
  ]) {
    const advisory = await needsJoeAdvisory(queue, { apiKey: "test-only", fetchImpl: fakeVendor(null, answers => {
      corrupt(answers["item_0_WR-000123_class"]);
      return answers;
    }) });
    assert.equal(advisory.status, "partial");
    assert.equal(advisory.items[0].judged, false);
  }
});

test("vendor outage and wrong answer set leave explicit unavailable advice", async () => {
  const down = await needsJoeAdvisory(queue, { apiKey: "test-only", fetchImpl: async () => { throw Error("vendor down"); } });
  assert.equal(down.status, "unavailable");
  const malformed = await needsJoeAdvisory(queue, { apiKey: "test-only", fetchImpl: fakeVendor(null, answers => {
    delete answers["item_0_WR-000123_priority"];
    return answers;
  }) });
  assert.equal(malformed.status, "unavailable");
  assert.equal(malformed.reason, "invalid_jev_answer");
});

test("thin item evidence abstains even when the vendor supplies confident values", async () => {
  const thin = { items: [{ ...queue.items[0], title: "Short", next_human_action: "Review" }] };
  const advisory = await needsJoeAdvisory(thin, { apiKey: "test-only", fetchImpl: fakeVendor() });
  assert.equal(advisory.status, "partial");
  assert.deepEqual(advisory.items[0], { human_ref: "WR-000123", index: 0,
    judged: false, reason_code: "insufficient_recorded_evidence" });
});

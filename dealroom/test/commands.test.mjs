import test from "node:test";
import assert from "node:assert/strict";
import { runCommand, listCommands } from "../js/commands.js";

function fakeClient(overrides = {}) {
  return {
    async setNextStep({ deal, text, next_date }) {
      return { status: "ok", event: { id: "evt1", deal, field: "next_step", new_value: text, next_date } };
    },
    async addDealNote({ deal, text }) {
      return { status: "ok", event: { id: "evt2", deal, field: "note", new_value: text } };
    },
    ...overrides,
  };
}

test("listCommands names the two governed commands", () => {
  const names = listCommands().map((c) => c.name).sort();
  assert.deepEqual(names, ["add_note", "set_next_step"]);
});

test("runCommand refuses with no deal in context, before ever touching the client", async () => {
  let called = false;
  const client = fakeClient({ setNextStep: async () => { called = true; return { status: "ok" }; } });
  const receipt = await runCommand(client, "set_next_step", "call the landlord", {});
  assert.equal(receipt.status, "refused");
  assert.equal(called, false);
});

test("runCommand refuses empty text before touching the client", async () => {
  const receipt = await runCommand(fakeClient(), "add_note", "   ", { dealId: "d1" });
  assert.equal(receipt.status, "refused");
  assert.match(receipt.detail.reason, /note text/);
});

test("runCommand returns ok with a normalized event on a successful write", async () => {
  const receipt = await runCommand(fakeClient(), "set_next_step", "send the LOI", { dealId: "d1", nextDate: "2026-10-01" });
  assert.equal(receipt.status, "ok");
  assert.equal(receipt.command, "set_next_step");
  assert.equal(receipt.deal, "d1");
  assert.equal(receipt.detail.event.new_value, "send the LOI");
});

test("runCommand surfaces a client conflict as status:conflict, not a false ok", async () => {
  const client = fakeClient({
    addDealNote: async () => ({ status: "conflict", conflict: { conflict_id: "c1", field: "note" } }),
  });
  const receipt = await runCommand(client, "add_note", "called the landlord", { dealId: "d1" });
  assert.equal(receipt.status, "conflict");
  assert.equal(receipt.detail.conflict.conflict_id, "c1");
});

test("runCommand reports unavailable, never a mock, when the client lacks the write", async () => {
  const client = {}; // no setNextStep / addDealNote at all — the honest V5-J102/F01-not-built case
  const receipt = await runCommand(client, "set_next_step", "send the LOI", { dealId: "d1" });
  assert.equal(receipt.status, "unavailable");
  assert.match(receipt.detail.reason, /V5-J102/);
});

test("runCommand catches a thrown client error and refuses instead of crashing the caller", async () => {
  const client = fakeClient({ addDealNote: async () => { throw new Error("network down"); } });
  const receipt = await runCommand(client, "add_note", "called the landlord", { dealId: "d1" });
  assert.equal(receipt.status, "refused");
  assert.match(receipt.detail.reason, /network down/);
});

test("an unknown command name refuses instead of throwing", async () => {
  const receipt = await runCommand(fakeClient(), "delete_everything", "x", { dealId: "d1" });
  assert.equal(receipt.status, "refused");
  assert.match(receipt.detail.reason, /Unknown command/);
});

// This is the parity guarantee itself: two "callers" (simulating the UI form
// and the Doc composer) that both go through runCommand with the SAME inputs
// get back structurally equal receipts, because it is the same function.
test("two independent callers issuing the identical command get an equivalent receipt", async () => {
  const client = fakeClient();
  const uiReceipt = await runCommand(client, "set_next_step", "send the LOI", { dealId: "d1", nextDate: "2026-10-01" });
  const docReceipt = await runCommand(client, "set_next_step", "send the LOI", { dealId: "d1", nextDate: "2026-10-01" });
  assert.equal(uiReceipt.status, docReceipt.status);
  assert.equal(uiReceipt.command, docReceipt.command);
  assert.equal(uiReceipt.deal, docReceipt.deal);
  assert.deepEqual(uiReceipt.detail.event, docReceipt.detail.event);
});

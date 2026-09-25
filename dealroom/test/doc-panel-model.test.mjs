import test from "node:test";
import assert from "node:assert/strict";
import {
  docPanelModality, escapeShouldClose, parseDocInput, formatReceiptLine,
} from "../js/doc-panel-model.js";

test("docPanelModality is closed unless open, then follows the same phone-width rule as the record panel", () => {
  assert.equal(docPanelModality({ open: false, phoneWidth: false }), "closed");
  assert.equal(docPanelModality({ open: false, phoneWidth: true }), "closed");
  assert.equal(docPanelModality({ open: true, phoneWidth: false }), "inline");
  assert.equal(docPanelModality({ open: true, phoneWidth: true }), "modal");
});

test("Escape closes an unpinned panel and never a pinned one", () => {
  assert.equal(escapeShouldClose({ pinned: false }), true);
  assert.equal(escapeShouldClose({ pinned: true }), false);
});

test("a leading /command splits into command + text; anything else is a plain note", () => {
  assert.deepEqual(parseDocInput("/set_next_step send the LOI tomorrow"), {
    command: "set_next_step", text: "send the LOI tomorrow",
  });
  assert.deepEqual(parseDocInput("/ADD_NOTE called the landlord"), {
    command: "add_note", text: "called the landlord",
  });
  assert.deepEqual(parseDocInput("called the landlord"), {
    command: "add_note", text: "called the landlord",
  });
  assert.deepEqual(parseDocInput("   "), { command: "add_note", text: "" });
});

test("formatReceiptLine gives every status its own distinct, honest copy", () => {
  const base = { command: "set_next_step", deal: "d1" };
  const ok = formatReceiptLine({ ...base, status: "ok", detail: {} });
  const conflict = formatReceiptLine({ ...base, status: "conflict", detail: {} });
  const unavailable = formatReceiptLine({ ...base, status: "unavailable", detail: { reason: "V5-J102 not deployed yet." } });
  const refused = formatReceiptLine({ ...base, status: "refused", detail: { reason: "Say what happens next." } });
  const lines = [ok, conflict, unavailable, refused].map((l) => l.text);
  assert.equal(new Set(lines).size, 4, "each status must render distinguishable text");
  assert.match(ok.text, /confirmed/);
  assert.match(conflict.text, /conflict/i);
  assert.match(unavailable.text, /V5-J102 not deployed yet\./);
  assert.match(refused.text, /Say what happens next\./);
  assert.equal(ok.tone, "ok");
  assert.equal(conflict.tone, "conflict");
  assert.equal(unavailable.tone, "unavailable");
  assert.equal(refused.tone, "refused");
});

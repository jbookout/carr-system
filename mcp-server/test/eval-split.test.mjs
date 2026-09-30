import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { withTuningAccess } from "../../evals/tuning-access.mjs";

test("Node tuning fails on a caught final read through an alias", async () => {
  const root = mkdtempSync(join(tmpdir(), "carr-eval-split-"));
  try {
    writeFileSync(join(root, "final.json"), "SECRET FINAL CASE");
    symlinkSync(join(root, "final.json"), join(root, "alias.json"));
    await assert.rejects(withTuningAccess([join(root, "final.json")], async () => {
      try { readFileSync(join(root, "alias.json")); } catch (e) { assert.match(e.message, /final_access/); }
    }), /final_access/);
  } finally { rmSync(root, { recursive: true }); }
});

test("Node tuning keeps ordinary development reads working", async () => {
  const root = mkdtempSync(join(tmpdir(), "carr-eval-split-"));
  try {
    writeFileSync(join(root, "development.json"), "development");
    assert.equal(await withTuningAccess([join(root, "final.json")], async () =>
      readFileSync(join(root, "development.json"), "utf8")), "development");
  } finally { rmSync(root, { recursive: true }); }
});

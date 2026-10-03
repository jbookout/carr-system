import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, readFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { withTuningAccess } from "../../evals/tuning-access.mjs";
import fs from "node:fs";
import promises from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { main } from "../../evals/retrieval/jev-rerank-eval.mjs";
const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

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

test("Node invalid setup restores the independent guard", async () => {
  await assert.rejects(withTuningAccess([new URL("https://example.invalid")], async () => {}));
  assert.equal(await withTuningAccess([], async () => "next"), "next");
});

test("Node inherited FileHandles and descriptors cannot read final", async () => {
  const root = mkdtempSync(join(tmpdir(), "carr-eval-handles-"));
  const final = join(root, "final.json");
  writeFileSync(final, "final text");
  try {
    const handle = await promises.open(final);
    try {
      await assert.rejects(withTuningAccess([final], () => handle.readFile("utf8")), /final_access/);
    } finally { await handle.close(); }
    const fd = fs.openSync(final, "r");
    try {
      await assert.rejects(withTuningAccess([final], () => fs.readSync(fd, Buffer.alloc(10), 0, 10, 0)), /final_access/);
    } finally { fs.closeSync(fd); }
    await assert.rejects(withTuningAccess([], () => { try { fs.readFileSync(123456); } catch {} }), /audit|descriptor/);
  } finally { rmSync(root, { recursive: true }); }
});

test("Node frozen rerank produces durable aggregate audits consumable by Python", async () => {
  const root = mkdtempSync(join(tmpdir(), "carr-eval-node-audit-"));
  const fixture = JSON.parse(readFileSync(resolve(REPO, "evals/retrieval/fixtures/jev-rerank-shortlists.2026-09-29.v1.json"), "utf8"));
  const cases = Array.from({ length: 6 }, (_, i) => ({ ...fixture.cases[0], id: `fresh-node-${i}`, group: `synthetic-${i}`,
    situation: `fresh synthetic situation ${i}` }));
  const source = join(root, "cases.json"), seen = join(root, "seen.json"), bundle = join(root, "bundle");
  writeFileSync(source, JSON.stringify(cases)); writeFileSync(seen, "[]");
  try {
    const freeze = spawnSync("python3", ["evals/rule-delivery/freeze_split.py", "freeze", source, bundle,
      "--seed", "test", "--source", "synthetic", "--seen", seen], { cwd: REPO, encoding: "utf8" });
    assert.equal(freeze.status, 0, freeze.stderr);
    const manifest = join(bundle, "manifest.json");
    let printed;
    await main(["--split-manifest", manifest], { stdout: () => {} });
    const report = await main(["--split-manifest", manifest], { stdout: text => { printed = JSON.parse(text); } });
    assert.equal(report.tuning_access.status, "passed");
    assert.equal(report.tuning_access.attempts.length, 2);
    assert.deepEqual(printed.tuning_access, report.tuning_access);
    assert.deepEqual(report.tuning_access, JSON.parse(readFileSync(join(bundle, "tuning-access.json"), "utf8")));
    const consume = spawnSync("python3", ["-c", `import sys,json;sys.path.insert(0,'ops');import eval_split as E
p=sys.argv[1]; a=json.load(open(sys.argv[2])); _,v=E.final_evaluation(p,dict(candidate_digest=E.digest('c'),baseline_digest=E.digest('b'),harness_digest=E.digest('h'),model='jev'),a);assert not E.provenance_errors(v)`,
      manifest, join(bundle, "tuning-access.json")], { cwd: REPO, encoding: "utf8" });
    assert.equal(consume.status, 0, consume.stderr);
  } finally { rmSync(root, { recursive: true }); }
});

test("Node failed frozen work leaves a failed durable audit", async () => {
  const root = mkdtempSync(join(tmpdir(), "carr-eval-node-failure-"));
  try {
    const cases = Array.from({ length: 6 }, (_, i) => ({ id: `node-bad-${i}`, group: `synthetic-${i}`, input: `bad-${i}` }));
    writeFileSync(join(root, "cases.json"), JSON.stringify(cases)); writeFileSync(join(root, "seen.json"), "[]");
    const frozen = spawnSync("python3", ["evals/rule-delivery/freeze_split.py", "freeze", join(root, "cases.json"), join(root, "bundle"),
      "--seed", "test", "--source", "synthetic", "--seen", join(root, "seen.json")], { cwd: REPO, encoding: "utf8" });
    assert.equal(frozen.status, 0, frozen.stderr);
    await assert.rejects(main(["--split-manifest", join(root, "bundle/manifest.json")], { stdout: () => {} }));
    const manifest = join(root, "bundle/manifest.json");
    await assert.rejects(withTuningAccess([join(root, "bundle/final.json")], () => {
      try { fs.readFileSync(123456); } catch {}
    }, [], manifest), /descriptor/);
    const handle = await promises.open(join(root, "bundle/final.json"));
    try {
      await assert.rejects(withTuningAccess([join(root, "bundle/final.json")], () => handle.readFile(), [], manifest), /final_access/);
    } finally { await handle.close(); }
    const audit = JSON.parse(readFileSync(join(root, "bundle/tuning-access.json"), "utf8"));
    assert.equal(audit.status, "failed");
    assert.equal(audit.attempts.length, 3);
    assert.deepEqual(audit.violations.map(v => v.reason), ["untracked_descriptor", "final_access"]);
  } finally { rmSync(root, { recursive: true }); }
});

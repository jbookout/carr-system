import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import { runInNewContext } from "node:vm";
import test from "node:test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const yaml = createRequire(import.meta.url)("js-yaml");
const files = ["ci.yml", "db-acceptance.yml"];
const read = name => readFileSync(new URL("../../.github/workflows/" + name, import.meta.url), "utf8");
// Evaluate only the scalar policy expressions used by these YAML workflows.
// Unknown expressions fail closed. GitHub's scheduler owns actual cancellation;
// this replay executes the policy expressions read from the deployed workflows.
function expression(value, github, success = true) {
  if (value === undefined) return success;
  const raw = String(value).replace(/^\$\{\{\s*|\s*\}\}$/g, "");
  assert.match(raw, /^(?:github\.(?:workflow|ref|run_id|event_name|event\.action|event\.pull_request\.number)|always\(\)|'[^']*'|[\s()=!&|]|true|false)+$/, `unsupported expression: ${raw}`);
  const result = runInNewContext(raw, { github, always: () => true }, { timeout: 1000 });
  // Actions implicitly adds success() unless a status function is present.
  return raw.includes("always()") || success ? result : false;
}
function context(event_name = "pull_request", action = "synchronize", number = 9, run_id = 1) {
  return { event_name, run_id, ref: event_name === "pull_request" ? `refs/pull/${number}/merge` : "refs/heads/main",
    event: { action, pull_request: event_name === "pull_request" ? { number } : {} } };
}
function policy(source, event, success = true) {
  const workflow = yaml.load(source);
  const types = workflow.on.pull_request?.types ?? ["opened", "synchronize", "reopened"];
  const triggers = event.event_name !== "pull_request" || types.includes(event.event.action);
  const concurrency = workflow.concurrency;
  const group = concurrency?.group.replace(/\$\{\{(.*?)\}\}/g,
    (_, expr) => expression(expr.trim(), { ...event, workflow: workflow.name }));
  const cancel = concurrency ? Boolean(expression(concurrency["cancel-in-progress"], event)) : false;
  const jobs = Object.entries(workflow.jobs);
  assert.ok(jobs.length);
  const runnable = triggers ? jobs.filter(([, job]) => expression(job.if, event, success)).map(([name]) => name) : [];
  return { triggers, group, cancel, runnable, jobs: jobs.map(([name]) => name) };
}
function replay(source, events) {
  const runs = [];
  for (const [tick, event] of events.entries()) {
    const current = policy(source, event);
    if (!current.triggers) continue;
    // Five seconds per synthetic suite; this is work accounting, not fleet savings.
    for (const run of runs) if (run.status === "running" && tick >= run.start + 5) run.status = "success";
    if (current.cancel && current.group) for (const run of runs) {
      if (run.status === "running" && run.group === current.group) {
        run.status = "superseded"; run.seconds = tick - run.start;
      }
    }
    runs.push({ ...current, start: tick, status: current.runnable.length ? "running" : "skipped",
      seconds: current.runnable.length ? 5 : 0 });
  }
  for (const run of runs) if (run.status === "running") run.status = "success";
  return runs;
}
for (const file of files) {
  const source = read(file);
  test(`${file}: rapid A/B/C supersedes A/B and C runs every job`, () => {
    const runs = replay(source, [context("pull_request", "opened", 9, 1), context("pull_request", "synchronize", 9, 2), context("pull_request", "synchronize", 9, 3)]);
    assert.deepEqual(runs.map(run => run.status), ["superseded", "superseded", "success"]);
    assert.deepEqual(runs[2].runnable, runs[2].jobs);
    const uncancelled = replay(source.replace(/^concurrency:\n(?:  .+\n)+/m, ""), [context("pull_request", "opened", 9, 1), context("pull_request", "synchronize", 9, 2), context("pull_request", "synchronize", 9, 3)]);
    assert.equal(runs.reduce((sum, r) => sum + r.seconds, 0), 7);
    assert.equal(uncancelled.reduce((sum, r) => sum + r.seconds, 0), 15);
    assert.equal(runs.filter(r => r.runnable.length).length, 3, "supersession saves tail work, not started suites");
  });
  test(`${file}: closed event cancels prior work with zero runnable jobs; reopen recovers`, () => {
    for (const action of ["opened", "synchronize", "edited", "ready_for_review"]) {
      const runs = replay(source, [context("pull_request", action, 9, 1), context("pull_request", "closed", 9, 2)]);
      assert.deepEqual(runs.map(run => run.status), ["superseded", "skipped"]);
      assert.deepEqual(runs[1].runnable, [], "closed must not checkout/install/test/aggregate");
    }
    const reopened = policy(source, context("pull_request", "reopened"));
    assert.deepEqual(reopened.runnable, reopened.jobs);
    const removedGuards = source.replace(/^    if:.*\n/gm, "");
    assert.notDeepEqual(policy(removedGuards, context("pull_request", "closed")).runnable, [], "control removing only the close guard is rejected");
  });
  test(`${file}: workflow/PR identity is stable on close and isolated from other PR/main/manual`, () => {
    const current = policy(source, context());
    assert.ok(current.group);
    assert.equal(policy(source, context("pull_request", "closed")).group, current.group);
    assert.notEqual(policy(source, context("pull_request", "opened", 10)).group, current.group);
    assert.notEqual(policy(source.replace(/^name: .+$/m, "name: Another workflow"), context()).group, current.group);
    for (const event of ["push", "schedule", "workflow_dispatch"]) {
      const one = policy(source, context(event, "", 9, 1)), two = policy(source, context(event, "", 9, 2));
      assert.equal(one.cancel, false, `${event} must retain its completed verdict`);
      assert.notEqual(one.group, current.group);
      assert.notEqual(one.group, two.group, "pending non-PR verdicts must not replace each other");
      assert.deepEqual(one.runnable, one.jobs);
    }
  });
  test(`${file}: edits and draft-to-ready retain full validation; completed verdict survives close`, () => {
    for (const action of ["opened", "synchronize", "reopened", "edited", "ready_for_review"]) {
      assert.deepEqual(policy(source, context("pull_request", action)).runnable, policy(source, context()).jobs);
    }
    assert.doesNotMatch(source, /github\.event\.pull_request\.draft|continue-on-error:/);
    const events = [context("pull_request", "opened"), ...Array.from({ length: 5 }, (_, i) => context("push", "", 9, 100 + i)), context("pull_request", "closed", 9, 7)];
    assert.equal(replay(source, events)[0].status, "success", "a close event must not erase completed green evidence");
  });
}

test("strict aggregate reports failure/cancelled/unknown on open PR and never runs on close", () => {
  const ci = read("ci.yml"), gate = yaml.load(ci).jobs.checks;
  assert.equal(gate.name, "ops/ci.sh --strict");
  assert.equal(gate.needs, "classes");
  assert.ok(policy(ci, context(), false).runnable.includes("checks"), "always() must override implicit success()");
  assert.deepEqual(policy(ci, context("pull_request", "closed"), false).runnable, []);
  for (const result of ["success", "failure", "cancelled", "skipped", "unknown", ""]) {
    const output = spawnSync("bash", ["-e", "-c", gate.steps[0].run], {
      env: { ...process.env, CLASSES_RESULT: result }, encoding: "utf8", timeout: 5000 });
    assert.ifError(output.error);
    assert.equal(output.status === 0, result === "success");
  }
});
test("running Worker canary remains independent and cannot be cancelled by validation", () => {
  const canary = yaml.load(read("main-canary.yml"));
  assert.equal(canary.concurrency["cancel-in-progress"], false);
  assert.equal(canary.on.pull_request, undefined);
  for (const file of files) for (const event of [context(), context("push"), context("schedule"), context("workflow_dispatch")]) {
    assert.notEqual(policy(read(file), event).group, canary.concurrency.group);
  }
});

test("edited PR event retains gates and invalid no-eval edits fail the actual receipt oracle", t => {
  const source = read("ci.yml"), ci = yaml.load(source);
  assert.ok(policy(source, context("pull_request", "edited")).runnable.includes("classes"));
  assert.ok(ci.jobs.classes.strategy.matrix.classes.includes("gates"));
  assert.match(readFileSync(new URL("../../ops/ci.sh", import.meta.url), "utf8"), /check-eval-receipt/);
  const directory = mkdtempSync(join(tmpdir(), "pr-eval-edit-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  // Isolate the synthetic Git fixture from pre-push's exported Git environment.
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")));
  const git = (...args) => {
    const out = spawnSync("git", ["-C", directory, ...args], { env, encoding: "utf8", timeout: 5000 });
    assert.ifError(out.error); assert.equal(out.status, 0, out.stderr);
  };
  git("init", "-q"); git("config", "user.name", "Synthetic"); git("config", "user.email", "synthetic@example.invalid");
  git("config", "core.hooksPath", "/dev/null");
  mkdirSync(join(directory, "evals"));
  writeFileSync(join(directory, "evals/surfaces.json"), readFileSync(new URL("../../evals/surfaces.json", import.meta.url)));
  writeFileSync(join(directory, "AGENTS.md"), "Synthetic instruction baseline.\n");
  git("add", "evals/surfaces.json", "AGENTS.md"); git("commit", "-qm", "Synthetic baseline"); git("tag", "base");
  writeFileSync(join(directory, "AGENTS.md"), "Synthetic changed instruction.\n");
  git("add", "AGENTS.md"); git("commit", "-qm", "Synthetic candidate");
  const eventPath = join(directory, "event.json");
  const oracle = fileURLToPath(new URL("../../ops/check-eval-receipt.py", import.meta.url));
  for (const [body, expected] of [
    ["no-eval: session-instructions: The synthetic adapter cannot measure model execution because this fixture has no model provider or evaluation harness.", 0],
    ["no-eval: session-instructions:", 1],
    ["no-eval: session-instructions: tiny tweak", 1],
    ["", 1],
  ]) {
    writeFileSync(eventPath, JSON.stringify({ action: "edited", pull_request: { body } }));
    const out = spawnSync("python3", [oracle, "--root", directory, "--base", "base"], {
      cwd: directory, env: { ...env, GITHUB_EVENT_NAME: "pull_request", GITHUB_EVENT_PATH: eventPath }, encoding: "utf8", timeout: 15000 });
    assert.ifError(out.error); assert.equal(out.status, expected, out.stdout + out.stderr);
  }
});

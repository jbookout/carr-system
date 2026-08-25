import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const migration = fs.readFileSync(new URL("../../migrations/0310_engineering_execution_fabric.sql", import.meta.url), "utf8");
const runtime = fs.readFileSync(new URL("../src/engineering-runtime.js", import.meta.url), "utf8");
const mcp = fs.readFileSync(new URL("../src/mcp.js", import.meta.url), "utf8");
const registry = JSON.parse(fs.readFileSync(new URL("../../ops/config/control-plane-workflows.v1.json", import.meta.url), "utf8"));

test("0310 binds the typed fabric to canonical ledgers and keeps evidence append-only", () => {
  for (const table of ["engineering_slice_plan", "engineering_execution_envelope", "engineering_slice_receipt", "engineering_reviewer_fact"])
    assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`));
  assert.match(migration, /references ops\.job\(id\)/);
  assert.match(migration, /references ops\.sourced_work_request_plan\(id\)/);
  assert.match(migration, /references ops\.capability_agent_session\(id\)/);
  assert.match(migration, /create or replace function ops\.engineering_admission_source/);
  assert.match(migration, /create or replace function ops\.engineering_passport_facts/);
  assert.match(migration, /create or replace function ops\.engineering_enqueue_slice_job/);
  assert.match(migration, /create or replace function ops\.engineering_claim_slice/);
  assert.match(migration, /j\.definition_key='engineering-slice'/);
  assert.match(migration, /before update or delete on ops\.engineering_slice_receipt/);
  assert.match(migration, /attempt_id text not null check \(attempt_id ~ '\^attempt:\[1-9\]\[0-9\]\*\$'\)/);
  assert.match(migration, /\(key,version,enabled,risk,owner_actor,execution_kind,execution_contract,\s*inventory_contract,state_contract,routing_contract,filtering_contract,\s*recurrence,/s);
  assert.match(migration, /'\{"kind":"on_demand","schedule":null,"cron":null,"timezone":"America\/Chicago","source":"MCP admit-engineering-slice only"\}'::jsonb/);
  assert.match(migration, /unique \(envelope_id, job_attempt_id\)/);
  assert.match(migration, /unique \(slice_plan_id, slice_ref\)/);
  assert.match(migration, /engineering slice is not registered for the exact plan/);
  assert.doesNotMatch(migration, /envelope_id uuid not null unique/);
  assert.match(migration, /grant execute on function ops\.engineering_admission_source/);
  assert.match(migration, /grant insert on ops\.engineering_execution_envelope to carr_writer/);
  assert.match(migration, /grant execute on function ops\.engineering_enqueue_slice_job\(text,text,text,text\)\s+to carr_writer/);
  assert.match(migration, /revoke update, delete on ops\.engineering_slice_plan, ops\.engineering_execution_envelope/);
  assert.doesNotMatch(migration, /create table if not exists (?!ops\.)/);
});

test("0310's receipt insertion requires the claimed lease and concrete attempt", () => {
  assert.match(migration, /create or replace function ops\.engineering_record_slice_receipt/);
  assert.match(migration, /attempt_row\.lease_token=p_lease_token and attempt_row\.state='running'/);
  assert.match(migration, /job_attempt_id uuid not null unique references ops\.job_attempt\(id\)/);
  assert.match(runtime, /engineering_record_slice_receipt/);
  assert.match(runtime, /engineering_claim_slice/);
  assert.match(runtime, /ops\.complete_job\(/, "the runtime uses the existing completion seam");
});

test("0310 admits dependent slices from the reviewer fact's bound attempt", () => {
  assert.match(migration, /v->'fact'->>'attempt_id' = r->>'attempt_id'/);
  assert.match(migration, /v->>'slice_ref' = dep/);
  assert.match(migration, /v->>'state' = 'passed'/);
});

test("the reviewed control-plane inventory carries the exact engineering job contract", () => {
  const workflow = registry.workflows.find(item => item.key === "engineering-slice" && item.version === 1);
  assert.ok(workflow);
  assert.equal(workflow.enabled, true);
  assert.equal(workflow.risk, "yellow");
  assert.deepEqual(workflow.execution, { kind: "deterministic", entrypoint: "mcp-server/src/engineering-runtime.js", export: "runCodexSlice", args: [], shadow_args: [], canary: { enabled: false, reason: "fresh native Codex execution has no isolated canary adapter" } });
  assert.equal(workflow.inventory.trigger, "MCP admission only; no scheduler");
  assert.equal(workflow.inventory.current_completion_signal, "lease-bound typed receipt plus independent reviewer fact");
  assert.deepEqual(workflow.recurrence, { kind: "on_demand", schedule: null, cron: null, timezone: "America/Chicago", source: "MCP admit-engineering-slice only" });
  assert.deepEqual(workflow.routing, { key: "facts.all_true", spec: { all_of: ["capability.candidate_admitted", "runner.identity_bound"] }, description: "an accepted capability candidate and bound runner identity admit the slice" });
  assert.deepEqual(workflow.filtering, { key: "facts.all_true", spec: { all_of: ["command.registered_args_selected"] }, description: "only the registered fresh Codex adapter is selected" });
  assert.deepEqual(workflow.validation, { key: "facts.all_true", spec: { all_of: ["command.exit_zero", "command.workflow_marker_valid"] }, description: "the bounded adapter succeeds and returns its typed workflow marker" });
  assert.deepEqual(workflow.retry, { max_attempts: 2, backoff: "constant", base_seconds: 30, cap_seconds: 300, timeout_seconds: 1800 });
  assert.deepEqual(workflow.deduplication, { key_template: "engineering-slice:{plan_digest}:{work_request}:{slice_ref}" });
  assert.deepEqual(workflow.completion, { key: "facts.all_true", spec: { all_of: ["command.receipt_persisted", "command.execution_evidence_reconciles"] }, description: "lease-bound typed receipt persists and reconciles to the issued envelope", receipt_kind: "engineering_slice" });
  assert.deepEqual(workflow.legacy_schedule, { provider: "none", status: "disabled", disable_requires: "no scheduler exists; on-demand MCP admission only" });
});

test("Hermes keeps execution controller-only while retaining its existing capture allowlist", () => {
  const match = mcp.match(/hermes: new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(match, "Hermes write allowlist must remain explicit");
  for (const verb of ["register-engineering-slice-plan", "admit-engineering-slice", "review-engineering-slice"]) assert.doesNotMatch(match[1], new RegExp(`['\"]${verb}['\"]`));
});

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const migration = fs.readFileSync(new URL("../../migrations/0310_engineering_execution_fabric.sql", import.meta.url), "utf8");
const authorityRepair = fs.readFileSync(new URL("../../migrations/0311_sponsored_engineering_executor_authority.sql", import.meta.url), "utf8");
const controllerMigration = fs.readFileSync(new URL("../../migrations/0312_engineering_dispatch_controller.sql", import.meta.url), "utf8");
const successorPermissionRepair = fs.readFileSync(new URL("../../migrations/0319_engineering_envelope_writer_successor.sql", import.meta.url), "utf8");
const claimOutputRepair = fs.readFileSync(new URL("../../migrations/0323_engineering_claim_output_qualification.sql", import.meta.url), "utf8");
const claimEligibilityRepair = fs.readFileSync(new URL("../../migrations/0325_engineering_claim_envelope_eligibility.sql", import.meta.url), "utf8");
const currentnessRepair = fs.readFileSync(new URL("../../migrations/0326_engineering_controller_currentness.sql", import.meta.url), "utf8");
const engineeringMigrationCorpus = fs.readdirSync(new URL("../../migrations/", import.meta.url))
  .filter(name => /^\d+_.*engineering.*\.sql$/i.test(name))
  .map(name => fs.readFileSync(new URL(`../../migrations/${name}`, import.meta.url), "utf8"))
  .join("\n");
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
  const submit = runtime.slice(runtime.indexOf("export async function submitEngineeringReceipt"), runtime.indexOf("function controllerActor"));
  assert.doesNotMatch(submit, /ops\.(?:complete_job|fail_job)\(/, "the runtime must not finalize a job after receipt persistence");
});

test("0310 admits dependent slices from the reviewer fact's bound attempt", () => {
  assert.match(migration, /v->'fact'->>'attempt_id' = r->>'attempt_id'/);
  assert.match(migration, /v->>'slice_ref' = dep/);
  assert.match(migration, /v->>'state' = 'passed'/);
});

test("0311 replaces stale envelopes immutably and admits a new job generation", () => {
  assert.match(authorityRepair, /add column if not exists supersedes_envelope_id/);
  assert.match(authorityRepair, /engineering_envelope_one_successor/);
  assert.match(authorityRepair, /engineering_envelope_supersession_guard/);
  assert.match(authorityRepair, /current executable engineering envelope cannot be superseded/);
  assert.match(authorityRepair, /engineering_enqueue_slice_job\(\s*p_work_request text, p_slice_ref text, p_plan_digest text,\s*p_idempotency_key text, p_generation integer/s);
  assert.match(authorityRepair, /generation:' \|\| p_generation/);
  assert.match(authorityRepair, /revoke all on function ops\.engineering_enqueue_slice_job\(text,text,text,text\)\s+from public,carr_reader,carr_writer,carr_jobs,carr_authority/);
  assert.match(runtime, /supersedes_envelope_id/);
  assert.match(runtime, /capability:engineering-repository-write/);
  for (const action of ["repository:create-worktree", "repository:write-declared-scope", "repository:run-checks", "repository:commit", "repository:push-branch", "repository:open-pr"])
    assert.match(runtime, new RegExp(action));
  for (const forbidden of ["repository:merge", "repository:deploy", "repository:migrate-production", "repository:independent-review"])
    assert.doesNotMatch(runtime, new RegExp(forbidden));
});

test("0319 keeps the successor guard on the writer's append-only authority", () => {
  assert.match(successorPermissionRepair, /create or replace function ops\.guard_engineering_envelope_supersession/);
  assert.match(successorPermissionRepair, /engineering-envelope:' \|\| new\.slice_plan_id/);
  const guardStart = successorPermissionRepair.indexOf("create or replace function ops.guard_engineering_envelope_supersession()");
  const guardEnd = successorPermissionRepair.indexOf("-- The safe repair", guardStart);
  assert.ok(guardStart >= 0 && guardEnd > guardStart, "0319 must contain the replacement guard body");
  assert.doesNotMatch(successorPermissionRepair.slice(guardStart, guardEnd), /for key share/i);
  assert.match(successorPermissionRepair, /has_table_privilege\('carr_writer', 'ops\.engineering_execution_envelope', 'update'\)/);
  assert.match(successorPermissionRepair, /carr_writer cannot create or read engineering envelopes/);
  assert.doesNotMatch(successorPermissionRepair, /grant\s+update\s+on\s+ops\.engineering_execution_envelope/i);
});

test("0323 qualifies the claim attempt output without widening its authority", () => {
  assert.match(claimOutputRepair, /create or replace function ops\.engineering_claim_slice/);
  assert.match(claimOutputRepair, /insert into ops\.job_attempt as claimed_attempt\(job_id,attempt,lease_owner,lease_token,state\)/);
  assert.match(claimOutputRepair, /returning claimed_attempt\.job_id/);
  assert.match(claimOutputRepair, /grant execute on function ops\.engineering_claim_slice\(text,integer,integer\) to carr_jobs/);
  assert.match(claimOutputRepair, /acl\.grantee=0 and acl\.privilege_type='EXECUTE'/);
  assert.doesNotMatch(claimOutputRepair, /grant\s+(?:all|insert|update|delete)\s+on\s+ops\.(?:job|job_attempt)/i);
});

test("0325 refuses stale Engineering envelopes before a lease is created", () => {
  assert.match(claimEligibilityRepair, /engineering_admission_source\(w\.ref\)/);
  assert.match(claimEligibilityRepair, /p_minimum_remaining_seconds integer default 60/);
  assert.match(claimEligibilityRepair, /e\.expires_at>statement_timestamp\(\)\+make_interval\(secs=>p_minimum_remaining_seconds\)/);
  assert.match(claimEligibilityRepair, /ops\.engineering_envelope_is_executable\(e\.id,j\.id,p_lease_seconds\+60\)/);
  assert.match(claimEligibilityRepair, /read_only'='false'/);
  assert.match(claimEligibilityRepair, /successor\.supersedes_envelope_id=e\.id/);
  assert.match(claimEligibilityRepair, /sp\.work_request_version=e\.state_version/);
  assert.match(claimEligibilityRepair, /j\.payload->>'generation'/);
  assert.match(claimEligibilityRepair, /s\.state not in \('completed','cancelled'\)/);
  assert.match(claimEligibilityRepair, /a\.active and a\.kind='automation' and a\.slug='codex'/);
  assert.match(claimEligibilityRepair, /pg_input_is_valid\(e\.envelope->>'expires_at','timestamp with time zone'\)/);
  assert.match(claimEligibilityRepair, /\(e\.envelope->>'expires_at'\)::timestamptz=e\.expires_at/);
  assert.match(claimEligibilityRepair, /j\.lease_token is not null/);
  assert.match(claimEligibilityRepair, /j\.leased_until>statement_timestamp\(\)/);
  assert.match(claimEligibilityRepair, /p_limit is distinct from 1/);
  assert.match(claimEligibilityRepair, /p_lease_seconds is null or p_lease_seconds<1/);
  assert.match(claimEligibilityRepair, /order by e\.slice_plan_id,e\.slice_ref/);
  assert.match(claimEligibilityRepair, /j\.lease_token=p_lease_token/);
  assert.match(claimEligibilityRepair, /engineering_controller_binding\(uuid,uuid,uuid\)/);
  assert.match(claimEligibilityRepair, /j\.lease_token=p_lease_token and j\.leased_until>statement_timestamp\(\)/);
  assert.match(claimEligibilityRepair, /j\.mode='shadow'/);
  assert.equal((claimEligibilityRepair.match(/engineering_envelope_is_executable\(/g) || []).length >= 4, true);
  assert.match(claimEligibilityRepair, /leased engineering envelope cannot be superseded/);
  assert.match(claimEligibilityRepair, /engineering session terminalization deferred while its dispatch lease is live/);
  assert.match(claimEligibilityRepair, /engineering_session_terminalization_guard/);
  assert.match(claimEligibilityRepair, /has_table_privilege\('carr_jobs','ops\.work_request','SELECT'\)/);
  assert.match(claimEligibilityRepair, /capability:engineering-repository-write/);
  assert.match(claimEligibilityRepair, /codex_desktop/);
  assert.match(claimEligibilityRepair, /returning claimed_attempt\.job_id/);
  assert.match(claimEligibilityRepair, /engineering_finalize_slice_receipt/);
  assert.match(claimEligibilityRepair, /grant execute on function ops\.engineering_claim_slice\(text,integer,integer\),[\s\S]*engineering_finalize_slice_receipt\(uuid,uuid,jsonb,text,uuid\) to carr_jobs/);
  assert.doesNotMatch(claimEligibilityRepair, /grant\s+(?:all|select|insert|update|delete)\s+on\s+/i);
});

test("0326 makes controller eligibility current, safe to parse, and receipt-race safe", () => {
  assert.match(currentnessRepair, /add column if not exists lease_expires_at/);
  assert.match(currentnessRepair, /engineering_safe_timestamptz/);
  assert.match(currentnessRepair, /exception when others/);
  assert.match(currentnessRepair, /capability_agent_session_lease_immutable/);
  assert.match(currentnessRepair, /envelope->>'envelope_id'='env:'/);
  assert.match(currentnessRepair, /envelope->'request'->>'job_ref'='job:'/);
  assert.match(currentnessRepair, /envelope->'plan_revision'->>'digest'/);
  assert.match(currentnessRepair, /envelope->'phase_binding'->>'phase_id'/);
  assert.match(currentnessRepair, /p_generation\s+is\s+null/);
  assert.match(currentnessRepair, /jsonb_typeof\(p_receipt->'deviations'\)\s+is\s+distinct\s+from\s+'array'/);
  assert.match(currentnessRepair, /group\s+by\s+deviation->>'deviation_ref'\s+having\s+count\(\*\)>1/i);
  assert.match(currentnessRepair, /envelope->>'envelope_id'='env:'/);
  assert.match(currentnessRepair, /envelope->'request'->>'job_ref'='job:'/);
  assert.match(currentnessRepair, /expires_at<=issued_at\+interval '30 minutes'/);
  assert.match(currentnessRepair, /agent_session_lease_expired_or_mismatched/);
  assert.match(currentnessRepair, /malformed_envelope_schema/);
  assert.match(currentnessRepair, /superseded_envelope/);
  assert.match(currentnessRepair, /already_receipted/);
  assert.match(currentnessRepair, /for update/);
  assert.match(currentnessRepair, /engineering_retire_permanently_ineligible_jobs/);
  assert.match(currentnessRepair, /'dead_letter'/);
  assert.match(currentnessRepair, /p_limit is distinct from 1/);
  assert.match(currentnessRepair, /drop function(?: if exists)? ops\.engineering_controller_binding\(uuid,uuid,uuid\)/i);
  assert.match(currentnessRepair, /drop function(?: if exists)? ops\.engineering_finalize_slice_receipt\(uuid,uuid,jsonb,text,uuid\)/i);
  assert.match(currentnessRepair, /drop function(?: if exists)? ops\.engineering_envelope_is_executable\(uuid,uuid,integer\)/i);
  assert.match(currentnessRepair, /revoke all on function ops\.engineering_claim_slice\(text,integer,integer\)/i);
  assert.doesNotMatch(currentnessRepair, /grant execute on function ops\.engineering_controller_binding\(uuid,uuid,uuid\)/i);
  assert.doesNotMatch(currentnessRepair, /grant execute on function ops\.engineering_finalize_slice_receipt\(uuid,uuid,jsonb,text,uuid\)/i);
  assert.doesNotMatch(currentnessRepair, /grant execute on function ops\.engineering_envelope_is_executable\(uuid,uuid,integer\)/i);
  assert.match(currentnessRepair, /ops\.engineering_envelope_is_executable\(e\.id,j\.id\)/);
  assert.match(currentnessRepair, /insert into ops\.engineering_slice_receipt[\s\S]*perform ops\.complete_job/);
  assert.match(currentnessRepair, /insert into ops\.engineering_slice_receipt[\s\S]*perform ops\.fail_job/);
  assert.match(currentnessRepair, /perform ops\.complete_job[\s\S]*perform ops\.fail_job[\s\S]*update ops\.capability_agent_session\s+set state='cancelled'[\s\S]*where id=s\.id and work_request_id=e\.work_request_id/);
  assert.doesNotMatch(currentnessRepair, /grant\s+(?:all|update|insert)\s+on\s+ops\.(?:job|job_attempt)/i);
});

test("reviewer lineage is pinned to the immutable receipt and latest-generation projection", () => {
  assert.match(engineeringMigrationCorpus, /(?:v\.receipt_id\s*=\s*r\.id|review\.receipt_id\s*=\s*receipt\.id)/i,
    "dependent admission must bind the reviewer row to the exact receipt row");
  assert.match(engineeringMigrationCorpus, /(?:order\s+by\s+r\.created_at\s+desc\s*,\s*r\.id\s+desc[\s\S]{0,160}limit\s+1|newer_receipt\.created_at\s*,\s*newer_receipt\.id\)\s*>\s*\(receipt\.created_at\s*,\s*receipt\.id\))/i,
    "database projection must select the latest receipt generation deterministically");
  assert.match(engineeringMigrationCorpus, /create\s+(?:or\s+replace\s+)?function\s+ops\.[A-Za-z0-9_]*(?:review|reviewer|fact)[A-Za-z0-9_]*|create\s+trigger\s+[A-Za-z0-9_]*(?:review|reviewer|fact)[A-Za-z0-9_]*/i,
    "reviewer facts need a database guard/trigger, not only caller validation");
  assert.match(runtime, /latestReceiptForSlice[\s\S]*\.sort\(compareCanonicalGeneration\)\.at\(-1\)/,
    "runtime projection/admission must use latest immutable receipt semantics");
  assert.match(runtime, /row\.receipt_id\s*===\s*receiptRow\.id/,
    "runtime reviewer admission must use the exact receipt identifier");
});

test("the reviewed control-plane inventory carries the exact engineering job contract", () => {
  const workflow = registry.workflows.find(item => item.key === "engineering-slice" && item.version === 1);
  assert.ok(workflow);
  assert.equal(workflow.enabled, true);
  assert.equal(workflow.risk, "yellow");
  assert.deepEqual(workflow.execution, { kind: "deterministic", entrypoint: "mcp-server/src/engineering-runtime.js", export: "runEngineeringWorker", args: ["room-bridge-engineering-controller"], shadow_args: [], canary: { enabled: false, reason: "fresh native Codex execution has no isolated canary adapter" } });
  assert.equal(workflow.inventory.trigger, "MCP admission only; no scheduler");
  assert.equal(workflow.inventory.current_completion_signal, "lease-bound typed receipt plus independent reviewer fact");
  assert.deepEqual(workflow.recurrence, { kind: "on_demand", schedule: null, cron: null, timezone: "America/Chicago", source: "MCP admit-engineering-slice only" });
  assert.deepEqual(workflow.routing, { key: "facts.all_true", spec: { all_of: ["capability.candidate_admitted", "runner.identity_bound"] }, description: "an accepted capability candidate and bound runner identity admit the slice" });
  assert.deepEqual(workflow.filtering, { key: "facts.all_true", spec: { all_of: ["command.registered_args_selected"] }, description: "only the registered fresh Codex adapter is selected" });
  assert.deepEqual(workflow.validation, { key: "facts.all_true", spec: { all_of: ["command.exit_zero", "command.workflow_marker_valid"] }, description: "the bounded adapter succeeds and returns its typed workflow marker" });
  assert.deepEqual(workflow.retry, { max_attempts: 2, backoff: "constant", base_seconds: 30, cap_seconds: 300, timeout_seconds: 1800 });
  assert.equal(workflow.inventory.authority, "server-derived sponsored Codex execution with a closed repository action allowlist; no caller-selected identity, authority, model, action, or native session");
  assert.deepEqual(workflow.inventory.external_dependencies, ["room-bridge lease-bound controller", "Codex Desktop fresh-native-session adapter"]);
  assert.deepEqual(workflow.deduplication, { key_template: "engineering-slice:{plan_digest}:{work_request}:{slice_ref}:generation:{generation}" });
  assert.deepEqual(workflow.completion, { key: "facts.all_true", spec: { all_of: ["command.receipt_persisted", "command.execution_evidence_reconciles"] }, description: "lease-bound typed receipt persists and reconciles to the issued envelope", receipt_kind: "engineering_slice" });
  assert.deepEqual(workflow.legacy_schedule, { provider: "none", status: "disabled", disable_requires: "no scheduler exists; on-demand MCP admission only" });
});

test("0312 binds the controller to the issued session and closes PUBLIC execution", () => {
  assert.match(controllerMigration, /create or replace function ops\.engineering_controller_binding/);
  assert.match(controllerMigration, /join ops\.capability_agent_session s on s\.id=e\.agent_session_id/);
  assert.match(controllerMigration, /p_executor_actor_id is distinct from session_executor/);
  assert.match(controllerMigration, /revoke all on function ops\.engineering_controller_binding\(uuid,uuid\) from public/);
  assert.match(controllerMigration, /revoke all on function ops\.engineering_record_slice_receipt\(uuid,uuid,jsonb,text,uuid\) from public/);
  assert.match(controllerMigration, /aclexplode/);
  assert.doesNotMatch(controllerMigration, /ops\.claim_job\(/);
});

test("Hermes keeps execution controller-only while retaining its existing capture allowlist", () => {
  const match = mcp.match(/hermes: new Set\(\[([\s\S]*?)\]\)/);
  assert.ok(match, "Hermes write allowlist must remain explicit");
  for (const verb of ["register-engineering-slice-plan", "admit-engineering-slice", "review-engineering-slice"]) assert.doesNotMatch(match[1], new RegExp(`['\"]${verb}['\"]`));
});

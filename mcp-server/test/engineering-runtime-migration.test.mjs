import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";

const migration = fs.readFileSync(new URL("../../migrations/0301_engineering_execution_fabric.sql", import.meta.url), "utf8");
const runtime = fs.readFileSync(new URL("../src/engineering-runtime.js", import.meta.url), "utf8");

test("0301 binds the typed fabric to canonical ledgers and keeps evidence append-only", () => {
  for (const table of ["engineering_slice_plan", "engineering_execution_envelope", "engineering_slice_receipt", "engineering_reviewer_fact"])
    assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`));
  assert.match(migration, /references ops\.job\(id\)/);
  assert.match(migration, /references ops\.sourced_work_request_plan\(id\)/);
  assert.match(migration, /references ops\.capability_agent_session\(id\)/);
  assert.match(migration, /create or replace function ops\.engineering_admission_source/);
  assert.match(migration, /create or replace function ops\.engineering_passport_facts/);
  assert.match(migration, /create or replace function ops\.engineering_enqueue_slice_job/);
  assert.match(migration, /before update or delete on ops\.engineering_slice_receipt/);
  assert.match(migration, /attempt_id text not null unique check \(attempt_id ~ '\^attempt:\[1-9\]\[0-9\]\*\$'\)/);
  assert.match(migration, /grant execute on function ops\.engineering_admission_source/);
  assert.match(migration, /grant insert on ops\.engineering_execution_envelope to carr_writer/);
  assert.match(migration, /grant execute on function ops\.engineering_enqueue_slice_job\(text,text,text,text\)\s+to carr_writer/);
  assert.match(migration, /revoke update, delete on ops\.engineering_slice_plan, ops\.engineering_execution_envelope/);
  assert.doesNotMatch(migration, /create table if not exists (?!ops\.)/);
});

test("0301's receipt insertion requires the claimed lease and concrete attempt", () => {
  assert.match(migration, /create or replace function ops\.engineering_record_slice_receipt/);
  assert.match(migration, /attempt_row\.lease_token=p_lease_token and attempt_row\.state='running'/);
  assert.match(migration, /job_attempt_id uuid not null unique references ops\.job_attempt\(id\)/);
  assert.match(runtime, /engineering_record_slice_receipt/);
  assert.match(runtime, /ops\.complete_job\(/, "the runtime uses the existing completion seam");
});

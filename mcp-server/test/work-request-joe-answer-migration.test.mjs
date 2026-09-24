import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0574_answer_needs_joe_work_request.sql"), "utf8");

test("0574 records Joe's answer and makes only the needs_joe-to-triaged transition", () => {
  assert.doesNotMatch(migration, /^(begin|commit);/m, "0339+ migrations run in the runner's single transaction");
  assert.match(migration, /add column if not exists joe_answer_text text/);
  assert.match(migration, /add column if not exists joe_answered_by_actor_id uuid references public\.actor\(id\)/);
  assert.match(migration, /add column if not exists joe_answered_at timestamptz/);
  assert.match(migration, /create table if not exists ops\.work_request_joe_answer_receipt/);
  assert.match(migration, /create or replace function ops\.answer_work_request_for_joe\(/);
  // The CAS guard: state must be exactly needs_joe at exactly the caller's base_version.
  assert.match(migration, /v_work_request\.state is distinct from 'needs_joe'/);
  assert.match(migration, /only the exact current needs_joe Work Request may be answered/);
  // The only state this function ever sets.
  assert.match(migration, /set state = 'triaged'/);
  assert.doesNotMatch(migration, /set state = '(?!triaged)/);
  // The actor is server-derived, never caller-supplied.
  assert.match(migration, /ops\.authority_actor_slug\(\)/);
  assert.match(migration, /authority session user is not an active human actor/);
  // Idempotent replay is receipt-backed, matching the sibling triage/decline/supersede verbs.
  assert.match(migration, /idempotency key already names a different answer to Joe/);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table|alter\s+table\s+ops\.work_request\s+drop/i);
});

test("0574 grants the door to authority only", () => {
  assert.match(migration, /revoke all on function ops\.answer_work_request_for_joe\(text,integer,text,uuid\) from public, carr_reader, carr_writer, carr_jobs;/);
  assert.match(migration, /grant execute on function ops\.answer_work_request_for_joe\(text,integer,text,uuid\) to carr_authority;/);
  assert.doesNotMatch(migration, /grant execute on function ops\.answer_work_request_for_joe\(text,integer,text,uuid\) to carr_writer/);
  assert.match(migration, /revoke all on table ops\.work_request_joe_answer_receipt from public, carr_reader, carr_writer, carr_jobs, carr_authority;/);
  assert.match(migration, /grant select on table ops\.work_request_joe_answer_receipt to carr_reader;/);
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const tools = await readFile(new URL("../src/tools.js", import.meta.url), "utf8");
const mcp = await readFile(new URL("../src/mcp.js", import.meta.url), "utf8");
const migration = await readFile(
  new URL("../../migrations/0117_append_only_audit_chain.sql", import.meta.url), "utf8");

test("decision corrections append projection events instead of rewriting audit history", () => {
  assert.doesNotMatch(tools, /update\s+event\s+set/i);
  assert.match(tools, /pg_advisory_xact_lock\(hashtextextended\('carr:decision:'/);
  assert.match(tools, /verb='amend-decision' and a\.new_value \? 'current_new_value'/);
  assert.match(tools, /new: \{ current_new_value: next/);
  assert.match(tools, /pointer_event_id: ptr\.id/);
  assert.match(tools, /not exists \([\s\S]*detached\.verb='detach-decision'/);
});

test("every write and read receipt carries the active operational profile", () => {
  assert.match(tools, /insert into tool_call[\s\S]*operational_profile/);
  assert.match(tools, /insert into event[\s\S]*operational_profile/);
  assert.match(mcp, /insert into tool_read_call[\s\S]*operational_profile/);
  assert.match(mcp, /actor\.operational_profile \|\| "full"/);
});

test("locked reviewer identity has an explicit read membership", () => {
  assert.match(mcp, /PROFILE_READS = \{[\s\S]*reviewer: new Set\(\["standing-context", "list-verbs"\]\)/);
  assert.match(mcp, /const memberships = PROFILE_READS\[profile\]/);
});

test("migration chains inserts and makes event plus ledger rows append-only", () => {
  assert.match(migration, /after insert on event[\s\S]*append_event_to_audit_chain/);
  assert.match(migration, /before update or delete on event[\s\S]*refuse_append_only_mutation/);
  assert.match(migration, /before update or delete on audit_ledger[\s\S]*refuse_append_only_mutation/);
  assert.match(migration, /revoke update, delete on event from carr_writer/);
  assert.match(migration, /audit_event_payload_digest\(e\)/);
  assert.match(migration, /previous_hash = expected_previous_hash as predecessor_ok/);
  assert.match(migration, /l\.actor_id is not distinct from e\.actor_id/);
  assert.match(migration, /l\.organization_tenant_id is not distinct from coalesce\(e\.organization_tenant_id/);
  assert.match(migration, /metadata_breaks/);
  assert.match(migration, /revoke all on audit_ledger from public, carr_reader, carr_writer, carr_jobs, carr_exporter/);
  assert.match(migration, /revoke all on v_audit_chain_entry from public, carr_reader, carr_writer, carr_jobs, carr_exporter/);
  assert.doesNotMatch(migration, /'head_hash', head_hash/);
  assert.match(migration, /select 'audit_chain'::text[\s\S]*from v_audit_chain_status/);
});

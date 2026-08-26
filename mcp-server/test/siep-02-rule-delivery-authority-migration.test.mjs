import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const migration = await readFile(new URL("../../migrations/0336_siep02_rule_delivery_authority.sql", import.meta.url), "utf8");
const runtime = await readFile(new URL("../../ops/rule-delivery-cutover.py", import.meta.url), "utf8");
const wrapper = await readFile(new URL("../../bin/rule-delivery-cutover-prod.sh", import.meta.url), "utf8");

test("cutover attribution is derived from the exact Joe authority login", () => {
  assert.match(migration, /session_user\s*<>\s*'carr_authority_joe'/i);
  assert.match(migration, /ops\.authority_actor_slug\(\)\s*<>\s*'joe'/i);
  assert.match(migration, /'joe',btrim\(p_reason\),p_expected_map_digest/i);
  assert.doesNotMatch(migration.match(/create or replace function ops\.set_rule_delivery_mode\([\s\S]*?end \$\$/i)?.[0] || "", /p_changed_by/i);
});

test("legacy and routine execution paths are revoked", () => {
  assert.match(migration, /revoke all on function ops\.set_rule_delivery_mode\(text,text,text,text\)[\s\S]*?carr_authority/i);
  assert.match(migration, /revoke all on function ops\.set_rule_delivery_mode\(text,text,text\)[\s\S]*?carr_writer,carr_jobs,carr_authority/i);
  assert.match(migration, /grant execute on function ops\.set_rule_delivery_mode\(text,text,text\)\s+to carr_authority/i);
  assert.match(migration, /rule-delivery policy mutation requires the Joe authority login/i);
  assert.match(migration, /create or replace function ops\.rule_delivery_cutover_preflight/i);
  assert.match(migration, /preflight requires the Joe authority login/i);
  assert.match(migration, /cardinality\(p_curation_proposal_ids\)\s*<>\s*38/i);
  assert.match(migration, /revoke all on function ops\.rule_delivery_cutover_preflight\(uuid\[\]\)[\s\S]*?carr_writer,carr_jobs,carr_authority/i);
});

test("the Production caller has no owner credential or caller attribution input", () => {
  assert.match(wrapper, /CARR_DB_AUTHORITY_JOE_URL/);
  assert.doesNotMatch(wrapper, /neondb_owner|--changed-by|NEONCTL/);
  assert.match(runtime, /select session_user,current_user/);
  assert.match(runtime, /\("carr_authority_joe", "carr_authority_joe"\)/);
  assert.doesNotMatch(runtime, /--changed-by|args\.changed_by/);
  assert.match(runtime, /set_rule_delivery_mode\(%s,%s,%s\)/);
  assert.match(runtime, /ops\.rule_delivery_cutover_preflight\(%s::uuid\[\]\)/);
  assert.doesNotMatch(runtime, /from retrieval_proposal|from ops\.rule_delivery_activation_target/i);
  assert.match(runtime, /from ops\.rule_delivery_policy where singleton/);
});

test("SIEP-02 does not activate or deploy rule delivery", () => {
  assert.doesNotMatch(migration, /values\s*\([^)]*'enforced'/i);
  assert.doesNotMatch(runtime, /default\s*=\s*["']enforced["']/i);
  assert.match(wrapper, /APPLY=0/);
});

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

const migration = await readFile(new URL("../../migrations/0452_siep02_rule_delivery_authority.sql", import.meta.url), "utf8");
const runtime = await readFile(new URL("../../ops/rule-delivery-cutover.py", import.meta.url), "utf8");
const wrapper = await readFile(new URL("../../bin/rule-delivery-cutover-prod.sh", import.meta.url), "utf8");
const controllerSuccessor = await readFile(new URL("../../migrations/0363_rule_delivery_activation_digest_repin.sql", import.meta.url), "utf8");
const sourceMergeSuccessor = await readFile(new URL("../../migrations/0471_source_merge_catalog_registry_successor.sql", import.meta.url), "utf8");
const mapBytes = await readFile(new URL("../../ops/config/rule-enforcement-map.json", import.meta.url));
const overlay = JSON.parse(await readFile(new URL("../../ops/config/rule-delivery-activation-overlay.v1.json", import.meta.url), "utf8"));
const currentMapDigest = createHash("sha256").update(mapBytes).digest("hex");

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
  assert.doesNotMatch(runtime, /from retrieval_proposal/i);
  assert.match(runtime, /array_agg\(short_id order by short_id\)/i);
  assert.match(runtime, /from ops\.rule_delivery_policy where singleton/);
});

test("SIEP-02 does not activate or deploy rule delivery", () => {
  assert.doesNotMatch(migration, /values\s*\([^)]*'enforced'/i);
  assert.doesNotMatch(runtime, /default\s*=\s*["']enforced["']/i);
  assert.match(wrapper, /APPLY=0/);
});

test("the controller successor owns the exact nine-to-eight shadow transition", () => {
  assert.equal(overlay.base_map_sha256, currentMapDigest);
  assert.match(sourceMergeSuccessor, new RegExp(currentMapDigest));
  assert.match(controllerSuccessor, /v_prior constant text := '4038e097f571f73499aee79b8c9e7b5bd3cea4ca0ba0f3847873e2f720106218'/);
  assert.match(controllerSuccessor, /cardinality\(v_prior_ids\)/);
  assert.match(controllerSuccessor, /cardinality\(v_ids\)/);
  assert.match(controllerSuccessor, /mode[^;]+is distinct from 'shadow'/s);
  assert.match(controllerSuccessor, /short_id\s*=\s*'581cb3fe'/);
  assert.match(controllerSuccessor, /return query select p_mode,8::bigint,v_receipt/);
  assert.doesNotMatch(controllerSuccessor,
    /update\s+ops\.rule_delivery_policy[\s\S]*?set\s+mode\s*=\s*'enforced'/i);
});

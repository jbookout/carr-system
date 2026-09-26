// V5-F05 durable rule-context seam against disposable loopback PostgreSQL.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_F05_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

async function database(t) {
  if (!DSN) {
    if (REQUIRED) assert.fail("CARR_F05_DB_REQUIRED requires DATABASE_URL");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN), "REFUSED: F05 SQL fixtures require disposable loopback PostgreSQL");
  return (await import("pg")).default ?? (await import("pg"));
}

test("typed contracts are append-only, actor-scoped, and missing rules stay explicit", async t => {
  const pg = await database(t);
  if (!pg) return;
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  try {
    const joe = (await client.query("select id from public.actor where slug='joe' and active")).rows[0];
    assert.ok(joe?.id, "Joe fixture actor must exist");
    await client.query("select set_config('carr.organization_tenant_id','carr-internal',true)");
    await client.query("select set_config('carr.acting_actor_slug','joe',true)");
    await client.query("select set_config('carr.sponsoring_human_slug','joe',true)");
    await client.query("select set_config('carr.verified_human_actor_slug','joe',true)");

    const inserted = await client.query(
      `insert into public.rule(statement,human_quote,taught_by,status)
       values ('Use the F05 SQL fixture control.', 'Joe approved the F05 fixture.', $1, 'proposed')
       returning id, version`, [joe.id]);
    const ruleId = inserted.rows[0].id;
    const controlKey = `f05-fixture-${randomUUID()}`;
    await client.query(
      `insert into ops.enforcement_control_catalog
         (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
       values ($1,'migrations/0724_f05_live_rule_context.sql',
         'mcp-server/test/rule-context-runtime-postgres.test.mjs',
         'transactional_schema',true,now())`, [controlKey]);
    await client.query(
      `insert into ops.rule_load_layer
         (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
       values ($1::uuid,left(($1::uuid)::text,8),'control','{}'::text[],'shared',
         'V5-F05 disposable PostgreSQL fixture control',
         'mcp-server/test/rule-context-runtime-postgres.test.mjs',repeat('0',64))`,
      [ruleId]);
    await client.query(
      `insert into ops.rule_control_binding
         (rule_id,control_key,statement_hash,binding_contract)
       select id,$1,encode(digest(statement,'sha256'),'hex'),
         '{"fixture":"V5-F05 PostgreSQL integration"}'::jsonb
       from public.rule where id=$2`, [controlKey, ruleId]);
    await client.query("set session authorization carr_authority_joe");
    const approved = (await client.query(
      "select ops.approve_rule($1,'machine_enforceable',array[$2],$3,$4) result",
      [ruleId, controlKey, `f05-approve-${randomUUID()}`,
        "Joe local V5-F05 integration acceptance"])).rows[0].result;
    assert.equal(approved.policy_status, "active");
    assert.equal(approved.replayed, false);
    await client.query("reset session authorization");
    const activeVersion = (await client.query(
      "select version from public.rule where id=$1", [ruleId])).rows[0].version;
    assert.ok(activeVersion > inserted.rows[0].version,
      "atomic approval must advance the source rule version");

    const before = (await client.query(
      "select ops.f05_rule_universe('fixture.act','fixture-resource') result")).rows[0].result;
    assert.ok(before.missing_rule_ids.includes(ruleId));
    assert.equal(before.active_rule_count - before.projected_rule_count,
      before.missing_rule_ids.length);

    const input = {
      rule_class: "workflow", mandatory: true,
      trigger: { action: ["fixture.act"], resource_class: ["fixture-resource"] },
      control_effect: { control_key: "fixture-control", effect: "require" },
      tests: ["check:f05-fixture"],
      retirement: { behavior: "permanent_until_superseded" },
    };
    const key = randomUUID();
    await client.query("set session authorization carr_authority_joe");
    const bound = (await client.query(
      "select ops.bind_f05_rule_contract($1::uuid,$2::jsonb,$3::uuid) result",
      [ruleId, input, key])).rows[0].result;
    assert.equal(bound.ok, true);
    assert.equal(bound.replayed, false);
    assert.equal(bound.contract.rule_id, ruleId);
    assert.equal(bound.contract.version, activeVersion);
    assert.equal(bound.contract.binding_text, "Use the F05 SQL fixture control.");
    assert.equal(bound.contract.owner, "joe");
    assert.equal(bound.contract.provenance.source_record_id, `rule:${ruleId}`);

    const replay = (await client.query(
      "select ops.bind_f05_rule_contract($1::uuid,$2::jsonb,$3::uuid) result",
      [ruleId, input, key])).rows[0].result;
    assert.equal(replay.replayed, true);
    await client.query("reset session authorization");

    const after = (await client.query(
      "select ops.f05_rule_universe('fixture.act','fixture-resource') result")).rows[0].result;
    assert.equal(after.missing_rule_ids.includes(ruleId), false);
    assert.ok(after.policy.rules.some(rule => rule.rule_id === ruleId));
    assert.ok(after.policy.declared_actions.includes("fixture.act"));
    assert.ok(after.policy.declared_resource_classes.includes("fixture-resource"));

    await assert.rejects(
      client.query("update ops.rule_f05_contract set contract=contract where rule_id=$1", [ruleId]),
      /append-only/,
    );
  } finally {
    await client.query("rollback");
    await client.end();
  }
});

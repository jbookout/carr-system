import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { executeRegisteredTool, ToolError } from '../src/tools.js';

const dsn = process.env.CARR_RULE_TEST_DATABASE_URL;
if (dsn) assert.match(dsn, /^postgres(?:ql)?:\/\/[^@/]*@(?:127\.0\.0\.1|localhost):/);

async function fixture(run) {
  const { Client } = (await import('pg')).default;
  const c = new Client({ connectionString: dsn });
  await c.connect();
  try {
    await c.query('begin');
    await c.query(`do $$ begin
      if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
        create role carr_authority_joe login;
      end if;
    end $$`);
    await c.query('grant carr_authority to carr_authority_joe');
    // The reconstructed schema has no production rows; seed only this
    // transaction's named pack fixtures before exercising Joe's role.
    await c.query(`insert into ops.rule_pack(pack,title,description,triggers,source)
      values ('engineering-git','Synthetic engineering','synthetic test pack',array['fixture'],'one-step test'),
             ('governance-rules','Synthetic governance','synthetic test pack',array['fixture'],'one-step test')
      on conflict (pack) do nothing`);
    const actor = { ...(await c.query("select id,slug from actor where slug='joe' and kind='human' and active")).rows[0], human: true };
    assert.ok(actor.id);
    // Exercise the production Joe authority identity, not owner privileges.
    await c.query('set session authorization carr_authority_joe');
    await c.query("select set_config('carr.acting_actor_slug','joe',true)");
    await run(c, actor);
  } finally {
    await c.query('rollback');
    await c.end();
  }
}

async function teach(c, actor, extra = {}) {
  return executeRegisteredTool(c, actor, 'teach', {
    idempotency_key: randomUUID(), statement: 'Synthetic one-step approval rule',
    human_quote: 'Synthetic approval request', enforcement_home: 'core', ...extra,
  });
}
const approveArgs = rule_id => ({ rule_id, idempotency_key: randomUUID(), reason: 'Joe approves this rule' });

test('approve-rule translates SQL refusals into ToolError with a readable reason', async () => {
  const c = { query: async sql => {
    if (sql.includes('ops.approve_rule')) throw Object.assign(new Error('Build control fixture-control-to-build before approving this rule.'), {
      code: 'P0001', detail: JSON.stringify({ error: 'rule_control_not_installed', control: 'fixture-control-to-build' }),
    });
    return { rows: [] };
  } };
  await assert.rejects(() => executeRegisteredTool(c, { id: randomUUID(), slug: 'joe', human: true }, 'approve-rule', {
    ...approveArgs(randomUUID()), policy_kind: 'machine_enforceable', control_keys: ['fixture-control-to-build'],
  }), e => e instanceof ToolError && e.payload.error === 'rule_control_not_installed' && /fixture-control-to-build/.test(e.payload.message));
});

for (const verb of ['approve-rule', 'admit-rule']) {
  test(`${verb} input refusal has a readable ToolError before database access`, async () => {
    const c = { query: async () => assert.fail('invalid input reached the database') };
    await assert.rejects(() => executeRegisteredTool(c, { id: randomUUID(), slug: 'joe', human: true }, verb, {}),
      e => e instanceof ToolError && e.payload.error === 'missing_required' &&
        typeof e.payload.message === 'string' && /rule_id/.test(e.payload.message));
  });
}

for (const [name, extra, layer, packs] of [
  ['core', {}, 'layer0', []],
  ['jit with scope pack', { enforcement_home: 'jit', scope: { packs: ['engineering-git'] } }, 'pack', ['engineering-git']],
  ['jit with teach pack', { enforcement_home: 'jit', packs: ['governance-rules'] }, 'pack', ['governance-rules']],
  ['judgment advisory', { enforcement_home: 'judgment_advisory', why_no_machine: 'Requires contextual judgment' }, 'layer0', []],
  ['judgment advisory with pack', { enforcement_home: 'judgment_advisory', why_no_machine: 'Requires contextual judgment', scope: { packs: ['governance-rules'] } }, 'pack', ['governance-rules']],
]) {
  test(`real PostgreSQL: teach ${name} then approve makes the rule active without admit-rule`, { skip: !dsn }, async () => fixture(async (c, actor) => {
    const captured = await teach(c, actor, extra);
    const args = approveArgs(captured.rule_id);
    const approved = await executeRegisteredTool(c, actor, 'approve-rule', args);
    assert.equal(approved.policy_status, 'active');
    assert.equal(approved.enforcement_status, 'delivered_advisory');
    assert.deepEqual(approved.installed_controls, []);
    await c.query('reset session authorization');
    const rule = (await c.query('select status,enforcement from rule where id=$1', [captured.rule_id])).rows[0];
    assert.equal(rule.status, 'active');
    assert.notEqual(rule.enforcement, 'gate');
    const delivery = (await c.query('select load_layer,packs from ops.rule_load_layer where rule_id=$1', [captured.rule_id])).rows[0];
    assert.deepEqual(delivery, { load_layer: layer, packs });
    assert.equal((await c.query("select count(*)::int n from ops.authority_receipt where subject_id=$1 and kind in ('admission','activation')", [captured.rule_id])).rows[0].n, 2);
    await c.query('set session authorization carr_authority_joe');
    const replay = await executeRegisteredTool(c, actor, 'approve-rule', args);
    assert.equal(replay.replayed, true);
    const sqlReplay = (await c.query('select ops.approve_rule($1,null,null,$2,$3) result',
      [args.rule_id, args.idempotency_key, args.reason])).rows[0].result;
    assert.equal(sqlReplay.replayed, true);
  }));
}

for (const [name, extra, error, message] of [
  ['jit without pack', { enforcement_home: 'jit' }, 'rule_pack_required', /pack.*missing|name.*pack/i],
  ['unknown pack', { enforcement_home: 'jit', scope: { packs: ['missing-fixture-pack'] } }, 'rule_pack_unknown', /missing-fixture-pack/],
  ['gate without installed control', { enforcement_home: 'gate', carrying_control: 'fixture-control-to-build' }, 'rule_control_not_installed', /fixture-control-to-build/],
]) {
  test(`real PostgreSQL: ${name} refuses atomically with a typed explanation`, { skip: !dsn }, async () => fixture(async (c, actor) => {
    const captured = await teach(c, actor, extra);
    await c.query('savepoint refusal');
    await assert.rejects(() => executeRegisteredTool(c, actor, 'approve-rule', approveArgs(captured.rule_id)),
      e => e instanceof ToolError && e.payload.error === error && message.test(e.payload.message));
    await c.query('rollback to savepoint refusal');
    await c.query('reset session authorization');
    assert.equal((await c.query('select status from rule where id=$1', [captured.rule_id])).rows[0].status, 'proposed');
    assert.equal((await c.query('select count(*)::int n from ops.rule_admission where rule_id=$1', [captured.rule_id])).rows[0].n, 0);
    assert.equal((await c.query('select count(*)::int n from ops.authority_receipt where subject_id=$1', [captured.rule_id])).rows[0].n, 0);
  }));
}

test('real PostgreSQL: admit-rule refuses a malformed delivery before writing admission', { skip: !dsn }, async () => fixture(async (c, actor) => {
  const captured = await teach(c, actor);
  for (const delivery of ['core', { load_layer: 'pack', packs: [] }, { load_layer: 'layer0', packs: [], why: '' }]) {
    await c.query('savepoint refusal');
    await assert.rejects(() => executeRegisteredTool(c, actor, 'admit-rule', {
      ...approveArgs(captured.rule_id), enforcement_class: 'judgment_advisory', binding_moment: 'Every session',
      applicability: {}, projection: { delivery }, reachability: {}, input_contract: {},
      fixture_refs: [], enforcement_points: [],
    }), e => e instanceof ToolError && e.payload.error === 'rule_delivery_invalid' && !!e.payload.message);
    await c.query('rollback to savepoint refusal');
  }
  await c.query('reset session authorization');
  assert.equal((await c.query('select count(*)::int n from ops.rule_admission where rule_id=$1', [captured.rule_id])).rows[0].n, 0);
}));

test('real PostgreSQL: a gate activates only with its named installed control and cannot downgrade to advice', { skip: !dsn }, async () => fixture(async (c, actor) => {
  const control = `fixture-installed-${randomUUID()}`;
  await c.query('reset session authorization');
  await c.query(`insert into ops.enforcement_control_catalog
    (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
    values($1,'synthetic installed control','one-step-rule-approval.test.mjs','transactional_schema',true,now())`, [control]);
  await c.query('set session authorization carr_authority_joe');
  const captured = await teach(c, actor, { enforcement_home: 'gate', carrying_control: control });
  await c.query('savepoint refusal');
  await assert.rejects(() => executeRegisteredTool(c, actor, 'approve-rule', {
    ...approveArgs(captured.rule_id), policy_kind: 'judgment_advisory', control_keys: [],
  }), e => e instanceof ToolError && e.payload.error === 'rule_gate_policy_mismatch' && e.payload.message.includes(control));
  await c.query('rollback to savepoint refusal');
  const approved = await executeRegisteredTool(c, actor, 'approve-rule', approveArgs(captured.rule_id));
  assert.equal(approved.enforcement_status, 'hard_enforced');
  assert.deepEqual(approved.installed_controls, [control]);
  await c.query('reset session authorization');
  assert.equal((await c.query('select enforcement from rule where id=$1', [captured.rule_id])).rows[0].enforcement, 'gate');
}));

test('real PostgreSQL: an existing explicit mechanical admission retains guarded approval', { skip: !dsn }, async () => fixture(async (c, actor) => {
  const control = `fixture-explicit-${randomUUID()}`;
  await c.query('reset session authorization');
  await c.query(`insert into ops.enforcement_control_catalog
    (control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
    values($1,'synthetic explicit control','one-step-rule-approval.test.mjs','transactional_schema',true,now())`, [control]);
  await c.query('set session authorization carr_authority_joe');
  const captured = await teach(c, actor);
  // admit-rule uses the writer connection; approve-rule uses Joe authority.
  await c.query('set session authorization carr_writer');
  await executeRegisteredTool(c, actor, 'admit-rule', {
    ...approveArgs(captured.rule_id), enforcement_class: 'machine_enforceable', binding_moment: 'On the fixture action',
    applicability: {}, projection: { delivery: { load_layer: 'control', packs: [], why: 'Explicit installed fixture control' } },
    reachability: {}, input_contract: {}, fixture_refs: ['one-step-rule-approval.test.mjs'],
    enforcement_points: [{ control_key: control, implementation_ref: 'synthetic explicit control',
      test_ref: 'one-step-rule-approval.test.mjs', enforcement_class: 'transactional_schema', installed: true }],
  });
  await c.query('set session authorization carr_authority_joe');
  await c.query('savepoint refusal');
  await assert.rejects(() => executeRegisteredTool(c, actor, 'approve-rule', {
    ...approveArgs(captured.rule_id), policy_kind: 'judgment_advisory', control_keys: [],
  }), e => e instanceof ToolError && e.payload.error === 'rule_gate_policy_mismatch');
  await c.query('rollback to savepoint refusal');
  const approved = await executeRegisteredTool(c, actor, 'approve-rule', approveArgs(captured.rule_id));
  assert.equal(approved.enforcement_status, 'hard_enforced');
  assert.deepEqual(approved.installed_controls, [control]);
}));

import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, mkdtempSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import { TOOLS } from '../src/tools.js';
import { readCommandCenterSummary } from '../src/workspace-command-center.js';

const root = fileURLToPath(new URL('../../', import.meta.url));
const schema = readFileSync(path.join(root, 'db/schema.sql'), 'utf8');
let bin;
for (const config of ['pg_config', '/opt/homebrew/opt/postgresql@17/bin/pg_config', '/usr/lib/postgresql/17/bin/pg_config']) {
  try {
    const candidate = execFileSync(config, ['--bindir'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    if (existsSync(path.join(candidate, 'postgres'))) { bin = candidate; break; }
  } catch { /* Try the other supported PostgreSQL installations. */ }
}
const id = n => `aa000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const actor = { id: id(1), slug: 'joe', human: true, via: 'dealroom-cookie', client_id: 'dealroom-pwa' };

test('Local Deals PostgreSQL caller and evidence regressions', { skip: !bin && 'PostgreSQL unavailable' }, async t => {
  const dir = mkdtempSync('/tmp/local-deals-');
  let running = false;
  let c;
  try {
    execFileSync(path.join(bin, 'initdb'), ['-D', dir, '-U', 'fixture', '--auth=trust', '--no-locale'], { stdio: 'pipe' });
    execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-l', path.join(dir, 'server.log'), '-o', `-k ${dir} -h ''`, '-w', 'start'], { stdio: 'pipe' });
    running = true;
    c = new pg.Client({ host: dir, user: 'fixture', database: 'postgres',
      // Preserve PostgreSQL microseconds, as the production HTTP driver does.
      types: { getTypeParser: (oid, format) => oid === 1184 ? value => value : pg.types.getTypeParser(oid, format) },
    });
    await c.connect();
    await c.query('create role carr_reader; create role carr_writer;');
    // Use the committed table definitions and caller views, without production data.
    for (const name of ['actor', 'party', 'client', 'deal', 'deal_phase', 'deal_participant', 'next_action', 'deal_note', 'national_account_owner', 'deal_market_assignment', 'deal_review_item', 'deal_review_session', 'event', 'tool_call', 'deal_conflict']) {
      const table = schema.match(new RegExp(`CREATE TABLE public\\.${name} \\([\\s\\S]*?\\n\\);`))?.[0];
      assert.ok(table, name);
      await c.query(table);
    }
    await c.query('alter table tool_call add primary key(idempotency_key);');
    await c.query("create view v_last_touch as select null::text subject_type, null::uuid subject_id, null::date last_touch where false;");
    for (const name of ['v_client_account', 'v_deal_board', 'v_deal_room_account', 'v_deal_room_board', 'v_deal_room_event', 'v_deal_room_session']) {
      const view = schema.match(new RegExp(`CREATE VIEW public\\.${name} AS[\\s\\S]*?;`))?.[0];
      assert.ok(view, name);
      await c.query(view);
      await c.query(`grant select on ${name} to carr_reader,carr_writer`);
    }
    await c.query("create view v_ref_index as select 'deal'::text subject_type, id subject_id from deal;");
    await c.query("insert into actor(id,slug,display_name,kind,active) values($1,'joe','Synthetic Partner','human',true)", [actor.id]);
    for (const [slug, sort] of [['research', 1], ['negotiation', 2], ['legal', 3], ['closed', 4]]) {
      await c.query('insert into deal_phase(slug,label,sort) values($1,$1,$2)', [slug, sort]);
    }
    await c.query(readFileSync(path.join(root, 'migrations/0771_local_deal_board_evidence.sql'), 'utf8'));
    const invoiceMigration = readFileSync(path.join(root, 'migrations/0772_invoice_tracker.sql'), 'utf8');
    await c.query(invoiceMigration.slice(invoiceMigration.indexOf('create or replace view v_deal_room_board'), invoiceMigration.indexOf('create or replace view v_deal_reconciliation_read')));
    const fixture = async national => {
      await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Practice',$2,$2)", [id(2), actor.id]);
      await c.query('insert into client(id,party_id,created_by,updated_by) values($1,$2,$3,$3)', [id(3), id(2), actor.id]);
      if (national) {
        await c.query("insert into party(id,kind,name,created_by,updated_by) values($1,'org','Synthetic Account',$2,$2)", [id(5), actor.id]);
        await c.query('update party set org_id=$1 where id=$2', [id(5), id(2)]);
        await c.query("insert into client(id,party_id,client_type,created_by,updated_by) values($1,$2,'national_account',$3,$3)", [id(6), id(5), actor.id]);
      }
      await c.query("insert into deal(id,client_id,name,deal_type,phase,owner,attention,next_date,created_by,updated_by) values($1,$2,'Synthetic Assignment','renewal','research','joe',true,current_date-1,$3,$3)", [id(4), id(3), actor.id]);
    };
    const transaction = async fn => {
      await c.query('begin');
      try { await fn(); } finally { await c.query('rollback'); }
    };

    await t.test('actual legacy handler works as carr_reader, including an empty board', async () => transaction(async () => {
      assert.equal((await c.query("select has_table_privilege('carr_reader','deal','select') as allowed")).rows[0].allowed, false);
      await c.query('set local role carr_reader');
      assert.deepEqual(await TOOLS['deal-board'].handler(c), { deals: [] });
      await c.query('reset role');
      await fixture(false);
      await c.query("update deal set outcome='won',phase='closed',operating_state='parked',parking_reason='other',parking_note='Synthetic pause',parked_at=now(),parked_by=$1 where id=$2", [actor.id, id(4)]);
      await c.query('set local role carr_reader');
      const row = (await TOOLS['deal-board'].handler(c)).deals[0];
      assert.equal(row.id, id(4));
      assert.equal(row.client_name, 'Synthetic Practice');
      assert.equal(row.phase_sort, 4);
      assert.equal(row.operating_state, 'parked');
      assert.equal(row.parking_note, 'Synthetic pause');
      assert.equal(row.invoiced_on, null);
      await c.query('reset role');
      await c.query('update deal set invoiced_on=current_date where id=$1', [id(4)]);
      await c.query('set local role carr_reader');
      assert.deepEqual(await TOOLS['deal-board'].handler(c), { deals: [] });
    }));

    await t.test('human conflict picker replaces an automatic badge through existing write handlers', async () => transaction(async () => {
      await fixture(false);
      const automatic = await TOOLS['patch-deal-field'].handler(c, { ...actor, via: 'mcp', client_id: 'agent' }, {
        idempotency_key: id(10), deal: id(4), field: 'phase', value: 'negotiation', base_event_id: null,
      });
      assert.equal((await c.query('select automatic from v_deal_room_phase_change where deal_id=$1', [id(4)])).rows[0].automatic, true);
      const conflict = await TOOLS['patch-deal-field'].handler(c, actor, {
        idempotency_key: id(11), deal: id(4), field: 'phase', value: 'legal', base_event_id: null,
      });
      assert.equal(conflict.ok, false);
      assert.equal(conflict.conflict.event_a, automatic.event_id);
      const resolved = await TOOLS['resolve-conflict'].handler(c, actor, { idempotency_key: id(12), conflict_id: conflict.conflict.id, winner: 'b' });
      assert.equal(resolved.ok, true);
      const event = (await c.query('select verb,cause,via,client_id from event where id=$1', [resolved.event_id])).rows[0];
      assert.deepEqual(event, { verb: 'resolve-conflict', cause: 'automation_job', via: 'dealroom-cookie', client_id: 'dealroom-pwa' });
      const evidence = (await c.query('select * from v_deal_room_phase_change where deal_id=$1', [id(4)])).rows[0];
      assert.equal(evidence.event_id, resolved.event_id);
      assert.equal(evidence.phase, 'legal');
      assert.equal(evidence.automatic, false);
    }));

    await t.test('account counters agree with the invoice-eligible board across outcome and parking states', async () => transaction(async () => {
      await fixture(true);
      for (const outcome of [null, 'won', 'lost', 'paused']) {
        for (const parked of [false, true]) {
          for (const invoiced of [false, true]) {
            await c.query(`update deal set outcome=$1,invoiced_on=case when $2 then current_date else null end,
              operating_state=case when $3 then 'parked' else 'active' end,
              parking_reason=case when $3 then 'other' else null end,
              parked_at=case when $3 then now() else null end,parked_by=case when $3 then $4::uuid else null end where id=$5`, [outcome, invoiced, parked, actor.id, id(4)]);
            await c.query('set local role carr_reader');
            const board = await TOOLS['deal-room-board'].handler(c, actor, { workspace: 'national_account', account_client_id: id(6) });
            assert.equal(board.deals.length, invoiced ? 0 : 1);
            const account = board.accounts.find(row => row.account_client_id === id(6));
            const active = !invoiced && !parked ? 1 : 0;
            for (const field of ['open_deals', 'attention_deals', 'overdue_deals', 'stale_deals']) {
              assert.equal(Number(account[field]), active, `${field}: ${outcome}/${parked}/${invoiced}`);
            }
            assert.equal(Number(account.parked_deals), !invoiced && parked ? 1 : 0);
            await c.query('reset role');
          }
        }
      }
    }));

    await t.test('Command Center counts the same active team population as its destination board', async () => transaction(async () => {
      await fixture(false);
      for (const outcome of [null, 'won', 'lost', 'paused']) {
        await c.query('update deal set outcome=$1 where id=$2', [outcome, id(4)]);
        await c.query('set local role carr_reader');
        const client = { query: (sql, params) => sql.includes('from ops.work_request')
          ? { rows: [{ needs_viewer: 0, doc_at_work: 0, changed_count: 0, legacy_unscoped_held: 0, legacy_unscoped_recent: 0 }] }
          : c.query(sql, params) };
        const summary = await readCommandCenterSummary({ client, actor, correlationId: 'synthetic' });
        const board = await TOOLS['deal-room-board'].handler(c, actor, { workspace: 'team' });
        assert.equal(summary.metrics[0].active_deals, board.deals.filter(row => row.operating_state === 'active').length);
        assert.equal(summary.metrics[0].flagged_deals, board.deals.filter(row => row.operating_state === 'active' && row.attention).length);
        await c.query('reset role');
      }
    }));

    await t.test('canonical SQL proof rejects missing evidence and wrong undo identity/value', async () => {
      const definition = (await c.query("select pg_get_viewdef('v_deal_room_phase_change'::regclass,true) as sql")).rows[0].sql.replace(/;\s*$/, '');
      const runProof = () => execFileSync(path.join(bin, 'psql'), ['-X', '-h', dir, '-U', 'fixture', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-f', path.join(root, 'mcp-server/test/local-deals-postgres.sql')], { stdio: 'pipe' });
      runProof();
      const columns = ['deal_id', 'event_id', 'prior_phase', 'phase', 'automatic', 'reason', 'evidence_date', 'recorded_at'];
      const mutations = [
        { select: '*', where: 'false', error: /Phase identity, evidence date or reason lost/ },
        ...[['event_id', 'uuid'], ['prior_phase', 'text'], ['phase', 'text'], ['automatic', 'boolean'], ['reason', 'text'], ['evidence_date', 'text'], ['recorded_at', 'text']]
          .map(([field, type]) => ({ select: columns.map(column => column === field ? `null::${type} as ${column}` : column).join(','), error: /Phase identity, evidence date or reason lost/ })),
        { select: '*', where: "recorded_at::date <> '2026-10-05'::date", error: /Manual correction retained automatic badge/ },
        { select: '*', where: "recorded_at::date <> '2026-10-07'::date", error: /Undo identity, value or classification lost/ },
        ...[['event_id', `'${id(99)}'::uuid`], ['phase', "'research'::text"], ['automatic', 'true']]
          .map(([field, value]) => ({
            select: columns.map(column => column === field ? `case when recorded_at::date='2026-10-07'::date then ${value} else ${column} end as ${column}` : column).join(','),
            error: /Undo identity, value or classification lost/,
          })),
      ];
      try {
        for (const mutation of mutations) {
          await c.query(`create or replace view v_deal_room_phase_change as select ${mutation.select} from (${definition}) evidence where ${mutation.where || 'true'}`);
          assert.throws(runProof, mutation.error);
        }
      } finally {
        await c.query(`create or replace view v_deal_room_phase_change as ${definition}`);
      }
    });
  } finally {
    if (c) await c.end();
    if (running) execFileSync(path.join(bin, 'pg_ctl'), ['-D', dir, '-m', 'immediate', '-w', 'stop'], { stdio: 'pipe' });
  }
});

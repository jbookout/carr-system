import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TOOLS } from '../src/tools.js';

const actor = { id: '10000000-0000-0000-0000-000000000002', slug: 'joe', display: 'Joe', human: true, via: 'mcp', client_id: 'codex' };
const args = { idempotency_key: 'routine-score-fixture', party_id: '20000000-0000-0000-0000-000000000001', stage: 'new', source_detail: 'NPPES weekly evidence' };
function client() {
  return { insert: null, async query(sql, params = []) {
    if (sql.startsWith('select request_hash')) return { rows: [] };
    if (sql.startsWith('select 1 from lead_stage')) return { rows: [{ exists: 1 }] };
    if (sql.includes('nextval')) return { rows: [{ r: 'L-123' }] };
    if (sql.includes('insert into lead ')) {
      this.insert = { sql, params };
      return { rows: [{ id: '30000000-0000-0000-0000-000000000001' }] };
    }
    if (sql.includes('insert into tool_call') || sql.includes('insert into event')) return { rows: [] };
    throw new Error('Unexpected SQL: ' + sql.slice(0, 100));
  } };
}

test('new-lead persists estimated score with basis and retains unscored callers', async () => {
  const c = client();
  await TOOLS['new-lead'].handler(c, actor, { ...args, score: 2, score_basis: 'Estimated: weak new individual signal; Joe qualifies.' });
  assert.match(c.insert.sql, /source_detail,\s*score/);
  assert.equal(c.insert.params[7], 2);
  assert.match(c.insert.params[6], /weak new individual signal/);
  const legacy = client();
  await TOOLS['new-lead'].handler(legacy, actor, args);
  assert.equal(legacy.insert.params[7], null);
  assert.equal(legacy.insert.params[6], args.source_detail);
});

test('new-lead refuses invalid or unexplained scores before any lead insert', async () => {
  for (const extra of [{ score: -1, score_basis: 'estimate' }, { score: 11, score_basis: 'estimate' }, { score: NaN, score_basis: 'estimate' }, { score: 2 }, { score_basis: 'orphan basis' }]) {
    const c = client();
    await assert.rejects(TOOLS['new-lead'].handler(c, actor, { ...args, ...extra }), /estimated_score/);
    assert.equal(c.insert, null);
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { registeredOperation } from '../src/mutation-registry.js';
import { frozenInventory } from '../../ops/scac-mutation-inventory.mjs';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('unfinished work follows delivered Doc activity and preserves all predecessor operations', () => {
  for (const verb of ['unfinished-work', 'read-doc-activity', 'read-room-latest',
    'advance-leads', 'lead-approval-queue'])
    assert.ok(registeredOperation(verb), `${verb} remains reachable`);
  const predecessor = frozenInventory('scac-mutation-registry.v107');
  const successor = frozenInventory('scac-mutation-registry.v108');
  assert.deepEqual(successor.filter(row => row.ingress_key !== 'mcp-tool:unfinished-work'),
    predecessor);
});

test('unfinished-work migration follows the exact delivered predecessor in one atomic group', () => {
  const names = readdirSync(new URL('../../migrations', import.meta.url)).filter(name => name.endsWith('.sql'));
  const scope = '0826_system_work_census_read_scope.sql';
  const successor = '0827_system_work_scac_successor.sql';
  assert.deepEqual(names.filter(name => name.startsWith('0826_')), [scope]);
  assert.deepEqual(names.filter(name => name.startsWith('0827_')), [successor]);
  assert.ok(!names.includes('0788_system_work_census_read_scope.sql'));
  assert.ok(!names.includes('0789_system_work_scac_successor.sql'));
  const sql = read(`migrations/${successor}`);
  for (const path of ['migrations/0825_doc_activity_scac_successor.sql', `migrations/${scope}`])
    assert.ok(sql.includes(createHash('sha256').update(read(path)).digest('hex')), `${path} is pinned`);
  const migrationRunner = read('tools/migrate.py');
  const group = /\(\s*"0826_system_work_census_read_scope\.sql",\s*"0827_system_work_scac_successor\.sql",\s*\)/;
  assert.match(migrationRunner.split('STRICT_ATOMIC_MIGRATION_GROUPS:')[0], group);
  assert.match(migrationRunner.split('STRICT_ATOMIC_MIGRATION_GROUPS:')[1], group);
});

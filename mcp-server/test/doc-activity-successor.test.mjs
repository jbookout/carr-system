import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { SCAC_MUTATION_REGISTRY_VERSION, registeredOperation } from '../src/mutation-registry.js';
import { CURRENT_REGISTRY_VERSION, frozenInventory } from '../../ops/scac-mutation-inventory.mjs';

const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('Doc activity follows delivered Observatory and lead automation without rewriting their contracts', () => {
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, CURRENT_REGISTRY_VERSION);
  const predecessor = frozenInventory('scac-mutation-registry.v106');
  const successor = frozenInventory('scac-mutation-registry.v107');
  assert.deepEqual(successor.filter(row => row.ingress_key !== 'mcp-tool:read-doc-activity'), predecessor);
  for (const verb of ['read-doc-activity', 'read-room-latest', 'advance-leads', 'lead-approval-queue'])
    assert.ok(registeredOperation(verb), `${verb} remains reachable`);
});

test('Doc activity migration has a unique number and binds the exact delivered predecessor', () => {
  const name = '0825_doc_activity_scac_successor.sql';
  const names = readdirSync(new URL('../../migrations', import.meta.url)).filter(name => name.endsWith('.sql'));
  assert.deepEqual(names.filter(name => name.startsWith('0825_')), [name]);
  assert.ok(!names.includes('0788_doc_activity_scac_successor.sql'));
  const sql = read(`migrations/${name}`);
  const predecessor = read('migrations/0812_lead_automation_scac_successor.sql');
  assert.ok(sql.includes(createHash('sha256').update(predecessor).digest('hex')));
  assert.match(sql, /scac-mutation-registry\.v106/);
  assert.match(sql, /scac-mutation-registry\.v107/);
});

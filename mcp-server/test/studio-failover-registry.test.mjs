import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { CURRENT_REGISTRY_VERSION, frozenInventory, boundInventoryRows,
  assertCurrentSourceInventoryMatchesFixture } from '../../ops/scac-mutation-inventory.mjs';
import { TOOLS } from '../src/tools.js';
import { SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('failover successor preserves existing verb contracts and the delivered predecessor', () => {
  assert.ok(Number(CURRENT_REGISTRY_VERSION.split('.v').at(-1)) >= 113);
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, CURRENT_REGISTRY_VERSION);
  assert.deepEqual(boundInventoryRows(frozenInventory('scac-mutation-registry.v113')),
    boundInventoryRows(frozenInventory('scac-mutation-registry.v112')));
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS), true);
  const sql = readFileSync(new URL('../../migrations/0848_studio_failover_scac_successor.sql', import.meta.url), 'utf8');
  const domain = readFileSync(new URL('../../migrations/0847_studio_failover_leader.sql', import.meta.url));
  assert.ok(sql.includes(createHash('sha256').update(domain).digest('hex')));
  const runner = readFileSync(new URL('../../tools/migrate.py', import.meta.url), 'utf8');
  assert.equal((runner.match(/"0847_studio_failover_leader.sql",\s*"0848_studio_failover_scac_successor.sql"/g) || []).length, 2);
});

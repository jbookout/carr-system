import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { frozenInventory, boundInventoryRows } from '../../ops/scac-mutation-inventory.mjs';
const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('census follows delivered Jev and Observatory contracts without replacing their history', () => {
  const predecessor = boundInventoryRows(frozenInventory('scac-mutation-registry.v105'));
  const current = boundInventoryRows(frozenInventory('scac-mutation-registry.v106'));
  const authorityContract = row => Object.fromEntries(Object.entries(row)
    .filter(([key]) => !['source_digest', 'schema_digest', 'handler_digest'].includes(key)));
  assert.deepEqual(current.filter(row => !row.ingress_key.includes('workflow-census')).map(authorityContract),
    predecessor.map(authorityContract));
  for (const verb of ['record-workflow-census', 'read-workflow-census', 'record-workflow-census-reanchor'])
    assert.ok(current.some(row => row.ingress_key === `mcp-tool:${verb}`));
  const migration = read('migrations/0809_workflow_census_store_scac_successor.sql');
  assert.match(migration, /0807_observatory_room_read_scac_successor[.]sql/);
  assert.match(migration, /scac-mutation-registry[.]v105/);
  assert.match(migration, /scac-mutation-registry[.]v106/);
});

test('census store runs after the delivered catalog and retains both staging state bindings', () => {
  const names = readdirSync(fileURLToPath(new URL('../../migrations', import.meta.url))).sort();
  assert.ok(names.includes('0808_workflow_census_store.sql'));
  assert.ok(!names.includes('0774_workflow_census_store.sql'));
  const staging = read('mcp-server/wrangler.toml').split('\n[env.staging]\n')[1];
  for (const binding of ['OAUTH_CONSENT_STATE', 'WORKFLOW_CENSUS_ANCHOR'])
    assert.match(staging, new RegExp(`name = "${binding}"`));
});

test('census freezes the immediately preceding catalog and epoch without repeating historical renames', () => {
  const migration = read('migrations/0809_workflow_census_store_scac_successor.sql');
  assert.match(migration, /alter function ops[.]scac_mutation_catalog_v105_current\(\) rename to scac_mutation_catalog_v105_live_at_seal;/);
  assert.match(migration, /alter function ops[.]scac_policy_epoch_snapshot\(\) rename to scac_policy_epoch_snapshot_v105;/);
  assert.doesNotMatch(migration, /alter function ops[.]scac_mutation_catalog_v104_current\(\)/);
});

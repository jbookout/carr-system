import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { assertRegisteredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';
import { CURRENT_REGISTRY_VERSION, frozenInventory, boundInventoryRows, assertCurrentSourceInventoryMatchesFixture } from '../../ops/scac-mutation-inventory.mjs';

test('lead successor preserves delivered Observatory v105 and admits both contracts', async () => {
  const v105 = readFileSync(new URL('../src/scac-mutation-registry.v105.generated.js', import.meta.url));
  assert.equal(createHash('sha256').update(v105).digest('hex'), 'b9f4d0cf0a92e8ac1ab32409d5e5aaad767dd2f20d2a40fbf24e5eb6d60fd9d8');
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, CURRENT_REGISTRY_VERSION);
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS, CURRENT_REGISTRY_VERSION), true);
  const predecessor = frozenInventory('scac-mutation-registry.v105');
  const successor = frozenInventory('scac-mutation-registry.v106');
  for (const operation of ['ask-jev', 'read-room-latest']) {
    const before = predecessor.find(row => row.operation === operation);
    const after = successor.find(row => row.operation === operation);
    assert.deepEqual(boundInventoryRows([after]), boundInventoryRows([before]));
  }
  for (const name of ['ask-jev', 'read-room-latest', 'record-lead-contact', 'advance-leads', 'approve-lead-draft', 'approve-lead-move']) {
    const admitted = await assertRegisteredOperation(name, TOOLS[name], {});
    assert.equal(admitted.ingress_key, `mcp-tool:${name}`);
  }
});

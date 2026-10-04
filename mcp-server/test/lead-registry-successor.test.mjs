import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { assertRegisteredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';
import { frozenInventory, boundInventoryRows, assertCurrentSourceInventoryMatchesFixture } from '../../ops/scac-mutation-inventory.mjs';

test('lead successor preserves delivered Jev cap v104 and admits both contracts', async () => {
  const v104 = readFileSync(new URL('../src/scac-mutation-registry.v104.generated.js', import.meta.url));
  assert.equal(createHash('sha256').update(v104).digest('hex'), '4250f3b38513998fb41ea0217fc0ab186b61990762b24e0f003bb612527c8bea');
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v105');
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS, 'scac-mutation-registry.v105'), true);
  const oldJev = frozenInventory('scac-mutation-registry.v104').find(row => row.operation === 'ask-jev');
  const newJev = frozenInventory('scac-mutation-registry.v105').find(row => row.operation === 'ask-jev');
  assert.deepEqual(boundInventoryRows([newJev]), boundInventoryRows([oldJev]));
  for (const name of ['ask-jev', 'record-lead-contact', 'advance-leads', 'approve-lead-draft', 'approve-lead-move']) {
    const admitted = await assertRegisteredOperation(name, TOOLS[name], {});
    assert.equal(admitted.ingress_key, `mcp-tool:${name}`);
  }
});

import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { liveRegistryPins } from '../../ops/live-registry-test-contract.mjs';

test('live registry equality pins are refused in either operand', () => {
  const source = `
assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v7');
assert.strictEqual("scac-mutation-registry.v8", inventory.CURRENT_REGISTRY_VERSION);
assert.equal(inventory.REGISTRY_V9_VERSION, SCAC_MUTATION_REGISTRY_VERSION);
assert.equal(SCAC_MUTATION_REGISTRY_VERSION, inventory.REGISTRY_V10_VERSION);
`;
  assert.deepEqual(liveRegistryPins(source), [2, 3, 4, 5]);
});

test('historical seals and relationships remain testable', () => {
  const source = `
assert.equal(SCAC_MUTATION_REGISTRY_VERSION, inventory.CURRENT_REGISTRY_VERSION);
assert.equal(frozenInventory('scac-mutation-registry.v7').length, 3);
assert.ok(Number(SCAC_MUTATION_REGISTRY_VERSION.split('.v')[1]) >= 7);
assert.equal(SEALED_PREDECESSOR_VERSION, 'scac-mutation-registry.v7');
// assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v7');
/* assert.equal(SCAC_MUTATION_REGISTRY_VERSION, inventory.REGISTRY_V7_VERSION); */
const example = "assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v7');";
`;
  assert.deepEqual(liveRegistryPins(source), []);
});

test('import aliases cannot hide live selector pins', () => {
  const source = `
import { SCAC_MUTATION_REGISTRY_VERSION as live } from '../src/mutation-registry.js';
import { CURRENT_REGISTRY_VERSION as current } from '../../ops/scac-mutation-inventory.mjs';
assert.equal(live, 'scac-mutation-registry.v7');
assert.equal('scac-mutation-registry.v7', current);
`;
  assert.deepEqual(liveRegistryPins(source), [4, 5]);
});

test('repository tests compare the live selector to the current contract', () => {
  const directory = new URL('./', import.meta.url);
  const violations = readdirSync(directory)
    .filter(name => /\.test\.(?:mjs|js)$/.test(name))
    .flatMap(name => liveRegistryPins(readFileSync(new URL(name, directory), 'utf8'))
      .map(line => `${name}:${line}`));
  assert.deepEqual(violations, [],
    'Compare live selectors to CURRENT_REGISTRY_VERSION; use frozenInventory(version) for sealed history.');
});

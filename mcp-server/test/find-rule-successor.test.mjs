import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import * as inventory from '../../ops/scac-mutation-inventory.mjs';
import { registeredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('find-rule follows the shipped human-only merge registry without rewriting it', () => {
  const predecessor = inventory.frozenInventory('scac-mutation-registry.v102');
  const successor = inventory.frozenInventory('scac-mutation-registry.v103');
  assert.equal(predecessor.some(row => row.ingress_key === 'mcp-tool:find-rule'), false);
  assert.equal(predecessor.find(row => row.ingress_key === 'mcp-tool:confirm-merge').human_only, true);
  assert.deepEqual(successor.find(row => row.ingress_key === 'mcp-tool:confirm-merge'),
    predecessor.find(row => row.ingress_key === 'mcp-tool:confirm-merge'));
  assert.ok(successor.some(row => row.ingress_key === 'mcp-tool:find-rule'));
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, inventory.CURRENT_REGISTRY_VERSION);
  const current = inventory.frozenInventory(SCAC_MUTATION_REGISTRY_VERSION);
  // Teach has a separately sealed successor input contract for one-step approval.
  for (const verb of ['find-rule','confirm-merge']) {
    const key = `mcp-tool:${verb}`;
    assert.deepEqual(inventory.boundInventoryRows(current).find(row => row.ingress_key === key),
      inventory.boundInventoryRows(successor).find(row => row.ingress_key === key));
  }
  assert.equal(registeredOperation('confirm-merge').human_only, true);
  assert.ok(registeredOperation('find-rule'));
  assert.ok(registeredOperation('teach'));
  const sql = inventory.renderFindRuleRegistrySql(successor);
  assert.equal(readFileSync(new URL('../../migrations/0786_find_rule_scac_successor.sql', import.meta.url), 'utf8'), sql);
  assert.match(sql, /0768_confirm_merge_human_only_scac_successor.sql/);
  assert.match(sql, /0785_rule_teach_supersession.sql/);
  assert.match(sql, /scac_mutation_registry_v102_seal_available\(\)/);
  assert.match(sql, /scac_mutation_registry_v103_seal_available\(\)/);
});

test('the Jev successor applies after its exact find-rule predecessors', () => {
  const root = new URL('../../migrations/', import.meta.url);
  const names = readdirSync(root).sort();
  const successorName = names.find(name => name.endsWith('_jev_cap_scac_successor.sql'));
  const sql = readFileSync(new URL(successorName, root), 'utf8');
  const pins = [...sql.matchAll(/filename='([^']+)' and sha256='([0-9a-f]{64})'/g)];
  assert.equal(pins.length, 2);
  for (const [, predecessorName, digest] of pins) {
    assert.ok(names.includes(predecessorName), `missing predecessor ${predecessorName}`);
    assert.ok(names.indexOf(predecessorName) < names.indexOf(successorName),
      `${successorName} must apply after ${predecessorName}`);
    assert.equal(createHash('sha256').update(readFileSync(new URL(predecessorName, root))).digest('hex'), digest);
  }
  assert.equal(sql, inventory.renderJevCapRegistrySql(
    inventory.frozenInventory('scac-mutation-registry.v104')));
});

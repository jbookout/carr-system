import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v103');
  assert.equal(registeredOperation('confirm-merge').human_only, true);
  assert.ok(registeredOperation('find-rule'));
  const sql = inventory.renderFindRuleRegistrySql(successor);
  assert.equal(readFileSync(new URL('../../migrations/0786_find_rule_scac_successor.sql', import.meta.url), 'utf8'), sql);
  assert.match(sql, /0768_confirm_merge_human_only_scac_successor.sql/);
  assert.match(sql, /0785_rule_teach_supersession.sql/);
  assert.match(sql, /scac_mutation_registry_v102_seal_available\(\)/);
  assert.match(sql, /scac_mutation_registry_v103_seal_available\(\)/);
});

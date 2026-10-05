import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { frozenInventory, boundInventoryRows, renderRuleApprovalRegistrySql } from '../../ops/scac-mutation-inventory.mjs';
import { registeredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('one-step approval changes only teach and approve input contracts after delivered v108', () => {
  const before = boundInventoryRows(frozenInventory('scac-mutation-registry.v108'));
  const after = boundInventoryRows(frozenInventory('scac-mutation-registry.v109'));
  const changed = after.filter((row, index) => JSON.stringify(row) !== JSON.stringify(before[index]));
  assert.deepEqual(changed.map(row => row.ingress_key).sort(), ['mcp-tool:approve-rule', 'mcp-tool:teach']);
  for (const row of changed) {
    const old = before.find(other => other.ingress_key === row.ingress_key);
    const { schema_digest: oldSchema, ...oldAuthority } = old;
    const { schema_digest: newSchema, ...newAuthority } = row;
    assert.deepEqual(newAuthority, oldAuthority, `${row.ingress_key} keeps its authority`);
    assert.notDeepEqual(newSchema, oldSchema);
  }
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v109');
  assert.equal(registeredOperation('approve-rule').authority_only, true);
  assert.ok(registeredOperation('unfinished-work'));
});

test('one-step approval generated successor preserves delivered registry history', () => {
  const sql = renderRuleApprovalRegistrySql(frozenInventory('scac-mutation-registry.v109'));
  assert.equal(readFileSync(new URL('../../migrations/0838_one_step_rule_approval_scac_successor.sql', import.meta.url), 'utf8'), sql);
  const domain = readFileSync(new URL('../../migrations/0836_one_step_rule_approval.sql', import.meta.url));
  assert.ok(sql.includes(createHash('sha256').update(domain).digest('hex')), 'exact approval implementation is pinned');
  assert.match(sql, /scac_mutation_registry_v108_seal_available\(\)/);
  assert.match(sql, /scac_mutation_registry_v109_seal_available\(\)/);
});

test('approval domain and registry changes require one complete atomic migration group', () => {
  const runner = readFileSync(new URL('../../tools/migrate.py', import.meta.url), 'utf8');
  const pair = /\(\s*"0836_one_step_rule_approval\.sql",\s*"0838_one_step_rule_approval_scac_successor\.sql",\s*\)/;
  assert.match(runner.split('STRICT_ATOMIC_MIGRATION_GROUPS:')[0], pair);
  assert.match(runner.split('STRICT_ATOMIC_MIGRATION_GROUPS:')[1], pair);
});

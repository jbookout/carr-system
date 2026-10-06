import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { frozenInventory, boundInventoryRows, renderRuleApprovalRegistrySql } from '../../ops/scac-mutation-inventory.mjs';
import { registryChain } from '../../ops/registry-chain.mjs';
import { registeredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('one-step approval and the consolidated migration runner preserve v112 authority', () => {
  const before = boundInventoryRows(frozenInventory('scac-mutation-registry.v112'));
  const after = boundInventoryRows(frozenInventory('scac-mutation-registry.v113'));
  const changed = after.filter((row, index) => JSON.stringify(row) !== JSON.stringify(before[index]));
  assert.deepEqual(changed.map(row => row.ingress_key).sort(), ['external-admin:tools/migrate.py', 'mcp-tool:approve-rule', 'mcp-tool:teach']);
  for (const row of changed) {
    const old = before.find(other => other.ingress_key === row.ingress_key);
    const { schema_digest: oldSchema, handler_digest: oldHandler, ...oldAuthority } = old;
    const { schema_digest: newSchema, handler_digest: newHandler, ...newAuthority } = row;
    assert.deepEqual(newAuthority, oldAuthority, `${row.ingress_key} keeps its authority`);
    assert.notDeepEqual(newSchema, oldSchema);
    if (row.ingress_key === 'external-admin:tools/migrate.py') assert.notEqual(newHandler, oldHandler);
  }
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v113');
  assert.equal(registeredOperation('approve-rule').authority_only, true);
  assert.ok(registeredOperation('unfinished-work'));
});

test('one-step approval generated successor preserves delivered registry history', () => {
  const sql = renderRuleApprovalRegistrySql(frozenInventory('scac-mutation-registry.v113'));
  assert.equal(readFileSync(new URL('../../migrations/0854_one_step_rule_approval_scac_successor.sql', import.meta.url), 'utf8'), sql);
  const domain = readFileSync(new URL('../../migrations/0853_one_step_rule_approval.sql', import.meta.url));
  assert.ok(sql.includes(createHash('sha256').update(domain).digest('hex')), 'exact approval implementation is pinned');
  assert.match(sql, /scac_mutation_registry_v112_seal_available\(\)/);
  assert.match(sql, /scac_mutation_registry_v113_seal_available\(\)/);
});

test('approval domain and registry changes require one complete atomic migration group', () => {
  const pair = ['0853_one_step_rule_approval.sql', '0854_one_step_rule_approval_scac_successor.sql'];
  assert.ok(registryChain.atomic_groups.some(group => JSON.stringify(group) === JSON.stringify(pair)));
  assert.ok(registryChain.strict_atomic_groups.some(group => JSON.stringify(group) === JSON.stringify(pair)));
  const migrations = readdirSync(new URL('../../migrations/', import.meta.url)).filter(name => name.endsWith('.sql')).sort();
  const domainIndex = migrations.indexOf('0853_one_step_rule_approval.sql');
  assert.equal(migrations[domainIndex + 1], '0854_one_step_rule_approval_scac_successor.sql',
    'the runner must encounter the complete authority group without an intervening migration');
});

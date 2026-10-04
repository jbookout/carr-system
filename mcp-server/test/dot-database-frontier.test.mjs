import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('database privilege repairs bind a new frontier without rewriting applied history', () => {
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v105');
  const sql = fs.readFileSync(new URL('../../migrations/0791_dot_database_design_scac_successor.sql',import.meta.url),'utf8');
  assert.match(sql,/scac_mutation_registry_v104_seal_available/);
  assert.match(sql,/scac_mutation_catalog_v105_current/);
  assert.match(sql,/exact applied 0790/);
  assert.match(sql,/scac-mutation-registry\.v100/);
});

test('local registry gates pin v105 to its released v104 predecessor', () => {
  const gate = fs.readFileSync(new URL('../../ops/siep18-reference-monitor-local-pg-gate.py', import.meta.url), 'utf8');
  assert.match(gate, /LIVE_REGISTRY_VERSION = "scac-mutation-registry.v105"/);
  assert.match(gate, /SEALED_PREDECESSOR_ORDINAL = LIVE_REGISTRY_ORDINAL - 1/);
  assert.match(gate, /SEALED_PREDECESSOR_VERSION = "scac-mutation-registry.v104"/);
});

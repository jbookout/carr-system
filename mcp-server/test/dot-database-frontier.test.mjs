import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('database privilege repairs bind a new frontier without rewriting applied history', () => {
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v101');
  const sql = fs.readFileSync(new URL('../../migrations/0758_dot_database_design_scac_successor.sql',import.meta.url),'utf8');
  assert.match(sql,/scac_mutation_registry_v99_seal_available/);
  assert.match(sql,/scac_mutation_catalog_v101_current/);
  assert.match(sql,/exact applied 0757/);
  assert.doesNotMatch(sql,/scac-mutation-registry\.v100/);
});

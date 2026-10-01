import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as inventory from '../../ops/scac-mutation-inventory.mjs';
import { SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('hardening registry is selected and delivered atomically with metadata', () => {
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v100');
  const runner = readFileSync(new URL('../../tools/migrate.py', import.meta.url), 'utf8');
  assert.match(runner, /\(\s*"0760_dot_security_definer_hardening.sql",\s*"0761_completion_tenant_security_barriers.sql",\s*"0762_dot_hardening_scac_successor.sql",\s*\)/);
});

test('definer hardening successor preserves its predecessor and seals hardened metadata', () => {
  const rows = inventory.frozenInventory('scac-mutation-registry.v100');
  const sql = inventory.renderDefinerHardeningRegistrySql(rows);
  assert.equal(sql, readFileSync(new URL('../../migrations/0762_dot_hardening_scac_successor.sql', import.meta.url), 'utf8'));
  assert.match(sql, /scac_mutation_catalog_v99_current\(\) rename to scac_mutation_catalog_v99_live_at_seal/);
  assert.match(sql, /ops\.scac_mutation_registry_v99_seal_available\(\) and ops\.scac_mutation_registry_v100_seal_available\(\)/);
  assert.match(sql, /when 'scac-mutation-registry\.v99' then 'sha256:b8c963310dae7cf990e046f6b022b01c76b3ac0b487994d5bea0eff679a32ec9'/);
  assert.match(sql, /scac_mutation_catalog_v100_current\(\)/);
  assert.match(sql, /filename='0760_dot_security_definer_hardening\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /filename='0761_completion_tenant_security_barriers\.sql' and sha256='[0-9a-f]{64}'/);
  assert.doesNotMatch(sql, /security definer set search_path=[^\n]+(?<!pg_temp) as \$fn\$/);
  assert.throws(() => inventory.renderDefinerHardeningRegistrySql(rows, '-- changed predecessor'), /predecessor.*pin drifted/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import * as inventory from '../../ops/scac-mutation-inventory.mjs';
import { SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

test('hardening registry is selected and delivered atomically with metadata', () => {
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v105');
  const runner = readFileSync(new URL('../../tools/migrate.py', import.meta.url), 'utf8');
  assert.match(runner, /\(\s*"0783_dot_security_definer_hardening.sql",\s*"0784_completion_tenant_security_barriers.sql",\s*"0785_dot_hardening_scac_successor.sql",\s*\)/);
});

test('definer hardening successor preserves its predecessor and seals hardened metadata', () => {
  const rows = inventory.frozenInventory('scac-mutation-registry.v105');
  const sql = inventory.renderDefinerHardeningRegistrySql(rows);
  assert.equal(sql, readFileSync(new URL('../../migrations/0785_dot_hardening_scac_successor.sql', import.meta.url), 'utf8'));
  for (const filename of ['0773_jev_cap_scac_successor.sql',
    '0783_dot_security_definer_hardening.sql', '0784_completion_tenant_security_barriers.sql']) {
    const bytes = readFileSync(new URL(`../../migrations/${filename}`, import.meta.url));
    const digest = createHash('sha256').update(bytes).digest('hex');
    assert.ok(sql.includes(`filename='${filename}' and sha256='${digest}'`), filename);
  }
  assert.match(sql, /scac_mutation_catalog_v104_current\(\) rename to scac_mutation_catalog_v104_live_at_seal/);
  assert.match(sql, /ops\.scac_mutation_registry_v104_seal_available\(\) and ops\.scac_mutation_registry_v105_seal_available\(\)/);
  assert.ok(sql.includes(inventory.registrySeal(inventory.REGISTRY_V104_VERSION, inventory.frozenInventory(inventory.REGISTRY_V104_VERSION), inventory.JEV_CAP_V104_DB_CATALOG_BASELINE).digest));
  assert.match(sql, /scac_mutation_catalog_v105_current\(\)/);
  assert.match(sql, /filename='0783_dot_security_definer_hardening\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /filename='0784_completion_tenant_security_barriers\.sql' and sha256='[0-9a-f]{64}'/);
  assert.doesNotMatch(sql, /security definer set search_path=[^\n]+(?<!pg_temp) as \$fn\$/);
  assert.throws(() => inventory.renderDefinerHardeningRegistrySql(rows, '-- changed predecessor'), /predecessor.*pin drifted/);
});

test('snapshot absorbs the ordered hardening batch and leaves qualification pending', () => {
  const snapshot = readFileSync(new URL('../../db/schema.sql', import.meta.url), 'utf8');
  const ledger = snapshot.slice(snapshot.indexOf('COPY public.schema_migrations'));
  for (const filename of ['0773_jev_cap_scac_successor.sql',
    '0783_dot_security_definer_hardening.sql', '0784_completion_tenant_security_barriers.sql',
    '0785_dot_hardening_scac_successor.sql']) {
    assert.ok(ledger.includes(filename), filename);
  }
  assert.ok(!ledger.includes('0786_qualify_security_definer_dependencies.sql'));
});

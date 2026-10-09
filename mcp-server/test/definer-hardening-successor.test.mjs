import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { registryChain, preservesRegistryChainHistory } from '../../ops/registry-chain.mjs';
import { execFileSync } from 'node:child_process';
import { SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';

const hardening = registryChain.versions.find(row => row.migration.endsWith('_dot_hardening_scac_successor.sql'));
const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('hardening extends published history and delivers metadata atomically', () => {
  assert.ok(hardening);
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, registryChain.versions.at(-1).version);
  const before = JSON.parse(execFileSync('git', ['show', 'origin/main:ops/config/scac-registry-chain.json'], { encoding: 'utf8' }));
  assert.ok(preservesRegistryChainHistory(before, registryChain));
  assert.deepEqual(hardening.atomic_pair, [
    '0854_dot_security_definer_hardening.sql', '0855_completion_tenant_security_barriers.sql',
    '0856_qualify_security_definer_dependencies.sql', '0857_dot_hardening_scac_successor.sql',
  ]);
  assert.ok(registryChain.strict_atomic_groups.some(group => JSON.stringify(group) === JSON.stringify(hardening.atomic_pair)));
});

test('hardening successor binds its predecessor and preserves temporary-schema pinning', () => {
  assert.ok(hardening);
  const sql = read(hardening.migration);
  const predecessor = registryChain.versions.find(row => row.version === hardening.predecessor);
  for (const filename of [predecessor.migration.split('/').at(-1), ...hardening.atomic_pair.slice(0, -1)]) {
    const hash = createHash('sha256').update(read(`migrations/${filename}`)).digest('hex');
    assert.ok(sql.includes(`filename='${filename}' and sha256='${hash}'`), filename);
  }
  assert.ok(sql.includes(predecessor.digest));
  assert.ok(sql.includes(`ops.scac_mutation_registry_v${predecessor.number}_seal_available()`));
  assert.ok(sql.includes(`ops.scac_mutation_catalog_v${hardening.number}_current()`));
  const paths = [...sql.matchAll(/security definer set search_path=([^\n]+?) as \$fn\$/g)];
  assert.ok(paths.length);
  assert.ok(paths.every(match => match[1].endsWith(',pg_temp')));
});

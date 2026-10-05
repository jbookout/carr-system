import { registryChain } from '../../ops/registry-chain.mjs';
const migrationPairs = registryChain.atomic_groups.map(group => '(' + group.map(name => '\n    '+JSON.stringify(name)+',').join('')+'\n)').join('\n');
import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync } from 'node:fs';
import { readRegistryArtifact as readFileSync } from '../../ops/registry-history.mjs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { registeredOperation } from '../src/mutation-registry.js';
import { CURRENT_REGISTRY_VERSION, frozenInventory } from '../../ops/scac-mutation-inventory.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const read = name => readFileSync(resolve(root, name), 'utf8');

test('property evidence does not reuse the shipped feedback migration numbers or registry version', () => {
  const files = readdirSync(resolve(root, 'migrations')).filter(name => /^\d{4}_.*\.sql$/.test(name) && Number(name.slice(0, 4)) >= 749);
  const duplicateNumbers = files.filter((name, index) => files.some((other, otherIndex) =>
    otherIndex < index && other.slice(0, 4) === name.slice(0, 4)));
  assert.deepEqual(duplicateNumbers, [], 'migration numbers must be unique');
  const sql = read('migrations/0755_property_evidence_scac_successor.sql');
  assert.match(sql, /filename='0750_tour_client_feedback_scac_successor\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /scac_mutation_registration_v98/);
  assert.match(sql, /scac-mutation-registry\.v99/);
  assert.ok(registeredOperation('read-tour-feedback'));
  assert.equal(registryChain.versions.at(-1).version, CURRENT_REGISTRY_VERSION);
  assert.match(read('mcp-server/src/scac-mutation-registry.v98.generated.js'), /mcp-tool:read-tour-feedback/);
});

test('property evidence follows the current SCAC seal with a distinct version', () => {
  const sql = read('migrations/0755_property_evidence_scac_successor.sql');
  assert.match(sql, /filename='0754_tour_property_evidence\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /scac-mutation-registry\.v98/);
  assert.match(sql, /scac-mutation-registry\.v99/);
  assert.match(read('mcp-server/src/scac-mutation-registry.v99.generated.js'),
    /SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry\.v99"/);
  assert.equal(registeredOperation('append-tour-source-evidence').schema_digest,
    frozenInventory('scac-mutation-registry.v99')
      .find(row => row.ingress_key === 'mcp-tool:append-tour-source-evidence').schema_digest);
  assert.equal(registryChain.versions.at(-1).version, CURRENT_REGISTRY_VERSION);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const read = name => readFileSync(resolve(root, name), 'utf8');

test('property evidence follows the current SCAC seal with a distinct version', () => {
  const sql = read('migrations/0750_property_evidence_scac_successor.sql');
  assert.match(sql, /filename='0749_tour_property_evidence\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /scac-mutation-registry\.v97/);
  assert.match(sql, /scac-mutation-registry\.v98/);
  assert.match(read('mcp-server/src/scac-mutation-registry.v98.generated.js'),
    /SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry\.v98"/);
  assert.match(read('mcp-server/src/mutation-registry.js'),
    /scac-mutation-registry\.v98\.generated\.js/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('Leads extends the delivered Observatory seal without rewriting its immutable source', () => {
  const delivered = {
    'migrations/0807_observatory_room_read_scac_successor.sql': 'c127f3251004318c7470594533290441595f7087c85ef16cbb00b9779dff6304',
    'mcp-server/src/scac-mutation-registry.v105.generated.js': 'b9f4d0cf0a92e8ac1ab32409d5e5aaad767dd2f20d2a40fbf24e5eb6d60fd9d8',
  };
  for (const [path, expected] of Object.entries(delivered)) {
    assert.equal(createHash('sha256').update(read(path)).digest('hex'), expected,
      `${path} must retain the delivered Observatory bytes`);
  }
  const leads = read('migrations/0809_leads_scac_successor.sql');
  assert.match(leads, /filename='0807_observatory_room_read_scac_successor\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(leads, /scac_mutation_registration_v105\('sha256:[0-9a-f]{64}','mcp-tool:update-lead'\)/);
  assert.match(leads, /scac-mutation-registry\.v106/);
  assert.match(read('mcp-server/src/mutation-registry.js'), /scac-mutation-registry\.v106\.generated\.js/);
});

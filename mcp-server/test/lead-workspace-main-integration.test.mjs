import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('Leads extends the delivered lead automation seal without rewriting its immutable source', () => {
  const delivered = {
    'migrations/0812_lead_automation_scac_successor.sql': '2013b4ec0a6cfbb1f9fc0c2e29307e95ad3b7c49960435cf21a49382425ec507',
    'mcp-server/src/scac-mutation-registry.v106.generated.js': '7553d82d4f4b6cb889b4d4b50fea3ea6c4b76af74b074824430d51842ef0d1b1',
  };
  for (const [path, expected] of Object.entries(delivered)) {
    assert.equal(createHash('sha256').update(read(path)).digest('hex'), expected,
      `${path} must retain the delivered lead automation bytes`);
  }
  const leads = read('migrations/0814_leads_scac_successor.sql');
  assert.match(leads, /filename='0812_lead_automation_scac_successor\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(leads, /scac_mutation_registration_v106\('sha256:[0-9a-f]{64}','mcp-tool:update-lead'\)/);
  assert.match(leads, /scac-mutation-registry\.v107/);
  assert.match(read('mcp-server/src/mutation-registry.js'), /scac-mutation-registry\.v107\.generated\.js/);
});

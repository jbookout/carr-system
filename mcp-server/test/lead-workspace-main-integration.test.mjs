import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const read = path => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('Leads extends the delivered system-work seal without rewriting its immutable source', () => {
  const delivered = {
    'mcp-server/src/scac-mutation-registry.v108.generated.js': 'b71eed9557ae3087e68a422a2226c5a27433b3e8b3b5f61d73c1fd20c882b456',
    'migrations/0827_system_work_scac_successor.sql': '5fdf0a6e85a1eebbb8fc8eecc7a516a550bb8c675b713000340b79f5f2a57f58',
    'migrations/0812_lead_automation_scac_successor.sql': '2013b4ec0a6cfbb1f9fc0c2e29307e95ad3b7c49960435cf21a49382425ec507',
    'mcp-server/src/scac-mutation-registry.v106.generated.js': '7553d82d4f4b6cb889b4d4b50fea3ea6c4b76af74b074824430d51842ef0d1b1',
    'migrations/0825_doc_activity_scac_successor.sql': 'a7b87f0dd5c3e88cf9756017ac4fb8abcda41ddb460ec5a24c9282a2e73793dd',
    'mcp-server/src/scac-mutation-registry.v107.generated.js': 'a6ecad8a02d80d5885906ca41f17ceff35637708ccc8ce83b6f12fc5859b5610',
  };
  for (const [path, expected] of Object.entries(delivered)) {
    assert.equal(createHash('sha256').update(read(path)).digest('hex'), expected,
      `${path} must retain the delivered predecessor bytes`);
  }
  const leads = read('migrations/0844_leads_scac_successor.sql');
  assert.match(leads, /filename='0827_system_work_scac_successor\.sql' and sha256='5fdf0a6e85a1eebbb8fc8eecc7a516a550bb8c675b713000340b79f5f2a57f58'/);
  assert.match(leads, /scac_mutation_registration_v108\('sha256:[0-9a-f]{64}','mcp-tool:update-lead'\)/);
  assert.match(leads, /scac-mutation-registry\.v109/);
  assert.match(read('mcp-server/src/mutation-registry.js'), /scac-mutation-registry\.v109\.generated\.js/);
});

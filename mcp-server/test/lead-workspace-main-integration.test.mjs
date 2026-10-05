import test from 'node:test';
import assert from 'node:assert/strict';
import { readRegistryArtifact as readFileSync } from '../../ops/registry-history.mjs';
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
  const leads = read('migrations/0846_leads_scac_successor.sql');
  assert.match(leads, /scac_mutation_registration_v111\('sha256:[0-9a-f]{64}','mcp-tool:codex-read-recovery'\)/);
  assert.match(leads, /scac-mutation-registry\.v112/);
  assert.ok(Number(read('mcp-server/src/mutation-registry.js').match(/scac-mutation-registry[.]v(\d+)[.]generated[.]js/)[1]) >= 112);
});

test('Leads extends the delivered relationship v109 frontier without rewriting it', () => {
  const delivered = {
    "migrations/0839_relationship_deal_links.sql": "6f683da138f82d72421b79edfdc575502a882acedfeeebd44c4c4258210fe1f3",
    "migrations/0840_relationship_scac_successor.sql": "50c556fa9090c6158b7d23a9c02ae1873d219bb82454941ec484b1c73d1d56c0",
    "mcp-server/src/scac-mutation-registry.v109.generated.js": "da727eeeed5c11c37b80051808d76082c7b536fa7d719abec25c4475c71c6efc"
  };
  for (const [path, expected] of Object.entries(delivered))
    assert.equal(createHash('sha256').update(read(path)).digest('hex'), expected, path);
  assert.ok(Number(read('mcp-server/src/mutation-registry.js').match(/scac-mutation-registry[.]v(\d+)[.]generated[.]js/)[1]) >= 112);
  const leads = read('migrations/0846_leads_scac_successor.sql');
  assert.match(leads, /0844_automation_undo_scac_successor\.sql/);
  assert.match(leads, /scac_mutation_registration_v111\(/);
  assert.match(leads, /scac-mutation-registry\.v112/);
});

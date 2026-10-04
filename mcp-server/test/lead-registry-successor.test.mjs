import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { assertRegisteredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';
import { frozenInventory, boundInventoryRows, assertCurrentSourceInventoryMatchesFixture } from '../../ops/scac-mutation-inventory.mjs';

test('lead successor preserves delivered Observatory v105 and admits both contracts', async () => {
  const v105 = readFileSync(new URL('../src/scac-mutation-registry.v105.generated.js', import.meta.url));
  assert.equal(createHash('sha256').update(v105).digest('hex'), 'b9f4d0cf0a92e8ac1ab32409d5e5aaad767dd2f20d2a40fbf24e5eb6d60fd9d8');
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v107');
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS, 'scac-mutation-registry.v107'), true);
  const predecessor = frozenInventory('scac-mutation-registry.v105');
  const successor = frozenInventory('scac-mutation-registry.v106');
  for (const operation of ['ask-jev', 'read-room-latest']) {
    const before = predecessor.find(row => row.operation === operation);
    const after = successor.find(row => row.operation === operation);
    assert.deepEqual(boundInventoryRows([after]), boundInventoryRows([before]));
  }
  for (const name of ['ask-jev', 'read-room-latest', 'record-lead-contact', 'advance-leads', 'approve-lead-draft', 'approve-lead-move', 'undo-lead-move', 'record-deal-invoice', 'invoice-close-queue', 'undo-invoice-close']) {
    const admitted = await assertRegisteredOperation(name, TOOLS[name], {});
    assert.equal(admitted.ingress_key, `mcp-tool:${name}`);
  }
});

// W3c extends the seal rather than rewriting W3b's delivered authority.
test('audited automation preserves the exact v106 delivered seal', () => {
  const runtime=readFileSync(new URL('../src/scac-mutation-registry.v106.generated.js',import.meta.url));
  const migration=readFileSync(new URL('../../migrations/0812_lead_automation_scac_successor.sql',import.meta.url));
  assert.equal(createHash('sha256').update(runtime).digest('hex'),'7553d82d4f4b6cb889b4d4b50fea3ea6c4b76af74b074824430d51842ef0d1b1');
  assert.equal(createHash('sha256').update(migration).digest('hex'),'2013b4ec0a6cfbb1f9fc0c2e29307e95ad3b7c49960435cf21a49382425ec507');
  const runner=readFileSync(new URL('../../tools/migrate.py',import.meta.url),'utf8');
  assert.equal((runner.match(/"0813_automation_reason_undo_archive_invoice.sql",\s*"0814_automation_undo_scac_successor.sql"/g)||[]).length,2);
});

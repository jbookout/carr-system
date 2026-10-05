import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { assertRegisteredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';
import { frozenInventory, boundInventoryRows, assertCurrentSourceInventoryMatchesFixture, CURRENT_REGISTRY_VERSION } from '../../ops/scac-mutation-inventory.mjs';

// Reads only the checked-out tree: a shallow CI checkout has no origin/main ref.
test('audited automation migrations follow their sealed predecessor in atomic order', () => {
  const root = new URL('../../', import.meta.url);
  const names = readdirSync(new URL('migrations/', root)).filter(name => /^\d{4}_.*\.sql$/.test(name));
  const pair = ['automation_reason_undo_archive_invoice.sql', 'automation_undo_scac_successor.sql']
    .map(suffix => {
      const matches = names.filter(name => name.endsWith('_' + suffix));
      assert.equal(matches.length, 1, suffix + ' must have one current migration');
      return matches[0];
    });
  for (const name of pair) {
    const number = name.slice(0, 4);
    assert.deepEqual(names.filter(other => other.startsWith(number + '_')), [name], number + ' must not collide');
  }
  assert.ok('0825_doc_activity_scac_successor.sql' < pair[0], 'the v107 predecessor must apply first');
  assert.ok(pair[0] < pair[1], 'domain changes must precede their registry seal');
  const runner = readFileSync(new URL('tools/migrate.py', root), 'utf8');
  const atomic = new RegExp('"' + pair[0].replaceAll('.', '\\.') + '",\\s*"' + pair[1].replaceAll('.', '\\.') + '"', 'g');
  assert.equal((runner.match(atomic) || []).length, 2, 'dry-run and apply must retain the atomic pair');
});

test('lead successor preserves delivered Observatory v105 and admits both contracts', async () => {
  const v105 = readFileSync(new URL('../src/scac-mutation-registry.v105.generated.js', import.meta.url));
  assert.equal(createHash('sha256').update(v105).digest('hex'), 'b9f4d0cf0a92e8ac1ab32409d5e5aaad767dd2f20d2a40fbf24e5eb6d60fd9d8');
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, CURRENT_REGISTRY_VERSION);
  assert.equal(assertCurrentSourceInventoryMatchesFixture(TOOLS, CURRENT_REGISTRY_VERSION), true);
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

// W3c extends the seal rather than rewriting Doc activity's delivered authority.
test('audited automation preserves the exact v107 delivered seal', () => {
  const runtime = readFileSync(new URL('../src/scac-mutation-registry.v107.generated.js', import.meta.url));
  const migration = readFileSync(new URL('../../migrations/0825_doc_activity_scac_successor.sql', import.meta.url));
  assert.equal(createHash('sha256').update(runtime).digest('hex'), 'a6ecad8a02d80d5885906ca41f17ceff35637708ccc8ce83b6f12fc5859b5610');
  assert.equal(createHash('sha256').update(migration).digest('hex'), 'a7b87f0dd5c3e88cf9756017ac4fb8abcda41ddb460ec5a24c9282a2e73793dd');
});

// Disposable PostgreSQL acceptance observed this full-entry seal for the v108
// source seed. Pin both halves so resealing cannot retain a digest measured
// before the seed changed.
test('audited automation source binds its measured PostgreSQL full-entry seal', () => {
  const migration = readFileSync(new URL('../../migrations/0832_automation_undo_scac_successor.sql', import.meta.url), 'utf8');
  const seed = migration.split('$automation_undo_v108_source$')[1];
  assert.equal(createHash('sha256').update(seed).digest('hex'), '68b76c3782e5a13c71d52b21ff8562b511f9de31a087b8ed57dea5f88419315a');
  const seals = JSON.parse(readFileSync(new URL('../../ops/config/scac-registry-full-entry-set-seals.json', import.meta.url), 'utf8'));
  const measured = 'sha256:7793ec592ba286e306924f7ef00c8f14e935183243e96e0dcb25a3f997efa34e';
  assert.equal(seals['scac-mutation-registry.v108'], measured);
  assert.ok(migration.includes(`v.entry_set_digest is distinct from '${measured}'`));
});

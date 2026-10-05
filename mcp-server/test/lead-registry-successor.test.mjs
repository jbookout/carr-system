import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { TOOLS } from '../src/tools.js';
import { assertRegisteredOperation, SCAC_MUTATION_REGISTRY_VERSION } from '../src/mutation-registry.js';
import { frozenInventory, boundInventoryRows, assertCurrentSourceInventoryMatchesFixture } from '../../ops/scac-mutation-inventory.mjs';

test('pending automation migrations extend the current main ledger in atomic order', () => {
  const root = new URL('../../', import.meta.url);
  const base = execFileSync('git', ['ls-tree', '--name-only', 'origin/main', 'migrations/'], { cwd: root, encoding: 'utf8' })
    .trim().split('\n').map(path => path.split('/').at(-1));
  const ceiling = Math.max(...base.map(name => Number(name.match(/^\d+/)?.[0] || 0)));
  const names = readdirSync(new URL('migrations/', root));
  const pair = ['automation_reason_undo_archive_invoice.sql', 'automation_undo_scac_successor.sql']
    .map(suffix => {
      const matches = names.filter(name => name.endsWith('_' + suffix));
      assert.equal(matches.length, 1, suffix + ' must have one current migration');
      return matches[0];
    });
  const pending = pair.filter(name => !base.includes(name));
  for (const name of pending) assert.ok(Number(name.slice(0, 4)) > ceiling, name + ' must follow main');
  assert.ok(pair[0] < pair[1], 'domain changes must precede their registry seal');
  const runner = readFileSync(new URL('tools/migrate.py', root), 'utf8');
  const atomic = new RegExp('"' + pair[0].replaceAll('.', '\\.') + '",\\s*"' + pair[1].replaceAll('.', '\\.') + '"', 'g');
  assert.equal((runner.match(atomic) || []).length, 2, 'dry-run and apply must retain the atomic pair');
});

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
});

// Independent PostgreSQL acceptance observed this full-entry seal for the
// renumbered migration runner. Pin both halves so source resealing cannot
// accidentally retain a digest measured before the source seed changed.
test('renumbered automation source binds its measured PostgreSQL full-entry seal', () => {
  const migration = readFileSync(new URL('../../migrations/0832_automation_undo_scac_successor.sql', import.meta.url), 'utf8');
  const seed = migration.split('$automation_undo_v107_source$')[1];
  assert.equal(createHash('sha256').update(seed).digest('hex'), '2f34b1c698f5328b7fb61446e9392af0265a3b09e61a70fd9cf56a2412224a14');
  const seals = JSON.parse(readFileSync(new URL('../../ops/config/scac-registry-full-entry-set-seals.json', import.meta.url), 'utf8'));
  const measured = 'sha256:d4ad724f0f1f648d82e517e81b4be2e34252cd48c3f6d91316bb8fe68a003fe3';
  assert.equal(seals['scac-mutation-registry.v107'], measured);
  assert.ok(migration.includes(`v.entry_set_digest is distinct from '${measured}'`));
});

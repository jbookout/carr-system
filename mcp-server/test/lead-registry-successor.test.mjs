import { registryChain } from '../../ops/registry-chain.mjs';
const migrationPairs = registryChain.atomic_groups.map(group => '(' + group.map(name => '\n    '+JSON.stringify(name)+',').join('')+'\n)').join('\n');
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
  assert.ok('0842_invoice_tracker_scac_successor.sql' < pair[0], 'the v110 predecessor must apply first');
  assert.ok(pair[0] < pair[1], 'domain changes must precede their registry seal');
  const runner = migrationPairs;
  const atomic = new RegExp('"' + pair[0].replaceAll('.', '\\.') + '",\\s*"' + pair[1].replaceAll('.', '\\.') + '"', 'g');
  assert.equal((runner.match(atomic) || []).length, 1, 'dry-run and apply must retain the atomic pair');
});

test('lead successor preserves delivered Observatory v105 and admits both contracts', async () => {
  const v105 = readFileSync(new URL('../src/scac-mutation-registry.v105.generated.js', import.meta.url));
  assert.equal(createHash('sha256').update(v105).digest('hex'), 'b9f4d0cf0a92e8ac1ab32409d5e5aaad767dd2f20d2a40fbf24e5eb6d60fd9d8');
  assert.ok(Number(SCAC_MUTATION_REGISTRY_VERSION.split('.v').at(-1)) >= 111);
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

// W3c extends the seal rather than rewriting unfinished-work's delivered authority.
test('audited automation preserves the exact v108 delivered seal', () => {
  const runtime = readFileSync(new URL('../src/scac-mutation-registry.v108.generated.js', import.meta.url));
  const migration = readFileSync(new URL('../../migrations/0827_system_work_scac_successor.sql', import.meta.url));
  assert.equal(createHash('sha256').update(runtime).digest('hex'), 'b71eed9557ae3087e68a422a2226c5a27433b3e8b3b5f61d73c1fd20c882b456');
  assert.equal(createHash('sha256').update(migration).digest('hex'), '5fdf0a6e85a1eebbb8fc8eecc7a516a550bb8c675b713000340b79f5f2a57f58');
});

// Bind the regenerated source seed and projected full-entry seal together.
test('audited automation source binds its projected full-entry seal', () => {
  const migration = readFileSync(new URL('../../migrations/0844_automation_undo_scac_successor.sql', import.meta.url), 'utf8');
  const seed = migration.split('$automation_undo_v111_source$')[1];
  assert.equal(createHash('sha256').update(seed).digest('hex'), '5671455328a38c66b9b39ccd2976012d22232fb492fa7c6f3b103dc91aac7285');
  const seals = JSON.parse(readFileSync(new URL('../../ops/config/scac-registry-full-entry-set-seals.json', import.meta.url), 'utf8'));
  const measured = 'sha256:4276c228821c5ae51b445dffaa6180943ffe8bcc6d4126b3040626c74797a800';
  assert.equal(seals['scac-mutation-registry.v111'], measured);
  assert.ok(migration.includes(`v.entry_set_digest is distinct from '${measured}'`));
});

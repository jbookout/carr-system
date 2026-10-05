import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { registeredOperation } from '../src/mutation-registry.js';
import { CURRENT_REGISTRY_VERSION, frozenInventory } from '../../ops/scac-mutation-inventory.mjs';

const root = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const read = name => readFileSync(resolve(root, name), 'utf8');

test('Doc suggestions follow the schedule registry without reusing its migration or version', () => {
  const migrationNames = readdirSync(resolve(root, 'migrations'));
  const successor = '0745_doc_suggestions_scac_successor.sql';
  assert.ok(migrationNames.includes('0743_schedule_board_scac_successor.sql'));
  assert.ok(migrationNames.includes('0744_doc_suggestions.sql'));
  assert.ok(migrationNames.includes(successor));
  assert.equal(migrationNames.filter(name => name.startsWith('0743_')).length, 1);
  assert.equal(migrationNames.filter(name => name.startsWith('0744_')).length, 1);
  assert.equal(migrationNames.filter(name => name.startsWith('0745_')).length, 1);

  const sql = read(`migrations/${successor}`);
  assert.match(sql, /filename='0743_schedule_board_scac_successor\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /filename='0744_doc_suggestions\.sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /scac-mutation-registry\.v95/);
  assert.match(sql, /scac-mutation-registry\.v96/);

  const runtime = read('mcp-server/src/scac-mutation-registry.v96.generated.js');
  assert.match(runtime, /SCAC_MUTATION_REGISTRY_VERSION = "scac-mutation-registry\.v96"/);
  assert.match(runtime, /mcp-tool:suggest-doc-work/);
  assert.match(runtime, /mcp-tool:schedule-board/);
});

test('Codex session read has its own sealed successor', () => {
  const migrationNames = readdirSync(resolve(root, 'migrations'));
  assert.equal(migrationNames.filter(name => name.startsWith('0748_')).length, 1);
  const sql = read('migrations/0748_codex_session_read_scac_successor.sql');
  assert.match(sql, /scac-mutation-registry\.v96/);
  assert.match(sql, /scac-mutation-registry\.v97/);
  const runtime = read('mcp-server/src/scac-mutation-registry.v97.generated.js');
  assert.match(runtime, /mcp-tool:list-my-codex-sessions/);
  assert.equal(registeredOperation('list-my-codex-sessions').schema_digest,
    frozenInventory('scac-mutation-registry.v97')
      .find(row => row.ingress_key === 'mcp-tool:list-my-codex-sessions').schema_digest);
  assert.match(read('mcp-server/src/mutation-registry.js'), new RegExp(CURRENT_REGISTRY_VERSION.replaceAll(".", "\\.") + "\\.generated\\.js"));
});

// Both branches advanced the registry: Observatory must follow the delivered Jev cap seal.
test('Observatory read preserves the Jev cap predecessor and has a forward seal', () => {
  const sql = read('migrations/0807_observatory_room_read_scac_successor.sql');
  assert.match(sql, /0787_jev_cap_scac_successor[.]sql/);
  assert.match(sql, /scac-mutation-registry\.v104/);
  assert.match(sql, /scac-mutation-registry\.v105/);
  const runtime = read('mcp-server/src/scac-mutation-registry.v105.generated.js');
  assert.match(runtime, /mcp-tool:read-room-latest/);
  const predecessor = frozenInventory('scac-mutation-registry.v104');
  const successor = frozenInventory('scac-mutation-registry.v105');
  assert.deepEqual(successor.filter(row => row.ingress_key !== 'mcp-tool:read-room-latest'), predecessor);
  const added = successor.find(row => row.ingress_key === 'mcp-tool:read-room-latest');
  assert.equal(added.write, false);
  assert.equal(added.authority_only, false);
});

test('Observatory successor has an unshared migration number at the end of main', () => {
  const names = readdirSync(resolve(root, 'migrations')).filter(name => name.endsWith('.sql')).sort();
  const successors = names.filter(name => /_observatory_room_read_scac_successor[.]sql$/.test(name));
  assert.deepEqual(successors, ['0807_observatory_room_read_scac_successor.sql']);
  assert.deepEqual(names.filter(name => name.startsWith('0807_')), successors);
  assert.ok(successors[0] > '0800_deal_timeline_lease_read.sql');
});


test('relationship attribution follows the delivered system-work contract without rewriting its sealed contracts', () => {
  const predecessor = frozenInventory('scac-mutation-registry.v108');
  const successor = frozenInventory('scac-mutation-registry.v109');
  for (const key of ['mcp-tool:read-room-latest', 'mcp-tool:advance-leads', 'mcp-tool:approve-lead-draft', 'mcp-tool:read-doc-activity', 'mcp-tool:unfinished-work']) {
    const previous = predecessor.find(row => row.ingress_key === key);
    assert.ok(previous, `${key} must exist in the delivered predecessor`);
    assert.deepEqual(successor.find(row => row.ingress_key === key), previous);
  }
  assert.notEqual(successor.find(row => row.ingress_key === 'mcp-tool:link-parties').schema_digest,
    predecessor.find(row => row.ingress_key === 'mcp-tool:link-parties').schema_digest);
  const names = readdirSync(resolve(root, 'migrations')).sort();
  const successorName = '0840_relationship_scac_successor.sql';
  assert.deepEqual(names.filter(name => /_relationship_scac_successor[.]sql$/.test(name)), [successorName]);
  assert.ok('0839_relationship_deal_links.sql' > '0827_system_work_scac_successor.sql');
  assert.ok(successorName > '0839_relationship_deal_links.sql');
  const sql = read(`migrations/${successorName}`);
  assert.match(sql, /filename='0827_system_work_scac_successor[.]sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /filename='0839_relationship_deal_links[.]sql' and sha256='[0-9a-f]{64}'/);
  assert.match(sql, /scac_mutation_registration_v108/);
  // v109 stays sealed history; a later successor may own the live selector (Joe 2026-10-05: no version pins on main).
  const live = Number(read('mcp-server/src/mutation-registry.js').match(/scac-mutation-registry[.]v(\d+)[.]generated[.]js/)[1]);
  assert.ok(live >= 109, `live registry selector v${live} must not precede v109`);
  assert.ok(readdirSync(resolve(root, 'mcp-server/src')).includes('scac-mutation-registry.v109.generated.js'));
  assert.match(read('tools/migrate.py'),
    /"0839_relationship_deal_links[.]sql",\n\s+"0840_relationship_scac_successor[.]sql"/);
});

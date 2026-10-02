import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

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
});

test('Observatory read has a forward seal after the current main history', () => {
  const sql = read('migrations/0772_observatory_room_read_scac_successor.sql');
  assert.match(sql, /scac-mutation-registry\.v103/);
  assert.match(sql, /scac-mutation-registry\.v104/);
  assert.match(read('mcp-server/src/scac-mutation-registry.v104.generated.js'), /mcp-tool:read-room-latest/);
  assert.match(read('mcp-server/src/mutation-registry.js'), /scac-mutation-registry\.v104\.generated\.js/);
});

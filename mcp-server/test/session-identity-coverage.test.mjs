import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('session identity acceptance suite registers its handler and refusal proofs', () => {
  const env = { ...process.env, DATABASE_URL: '', CARR_SESSION_IDENTITY_DB_REQUIRED: '0' };
  delete env.NODE_TEST_CONTEXT;
  const output = execFileSync(process.execPath, ['--test', '--test-reporter=tap',
    fileURLToPath(new URL('./session-identity.test.mjs', import.meta.url))], { env, encoding: 'utf8' });
  assert.match(output, /ok \d+ - the shapers refuse/);
  assert.match(output, /ok \d+ - AC-SI-DISPATCH/);
  for (const behavior of ['a Claude leaf resolves', 'a Codex checkpoint resolves',
    'a harvested worktree row resolves', 'two actors, one query shape',
    'rows the acting actor may not see', 'an actor who may see nothing',
    'sent and acted carry the row', 'acknowledged and received are null',
    'a superseded instruction', 'the cursor is opaque']) {
    assert.ok(output.includes(behavior), `missing acceptance proof: ${behavior}`);
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { acquirePostgresFixtureGroup } from './helpers/disposable-postgres.mjs';

test('Node PostgreSQL fixtures hold the shared budget through teardown', async () => {
  const first = await acquirePostgresFixtureGroup();
  let entered = false, second;
  const waiting = acquirePostgresFixtureGroup().then(release => { entered = true; second = release; });
  try {
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.equal(entered, false, 'another fixture must wait while the first cluster exists');
    await first();
    await waiting;
    assert.equal(entered, true);
  } finally {
    await first();
    await waiting;
    await second?.();
  }
});

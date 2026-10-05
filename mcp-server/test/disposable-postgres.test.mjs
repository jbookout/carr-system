import test from 'node:test';
import assert from 'node:assert/strict';
import { withPostgresFixture } from './helpers/disposable-postgres.mjs';

test('verb fixture owns isolated connections and rolls back refused transactions', async () => {
  await withPostgresFixture({}, async fixture => {
    const first = await fixture.connect();
    const second = await fixture.connect();
    assert.equal((await first.query('show server_encoding')).rows[0].server_encoding, 'UTF8');
    await first.query('create table synthetic(value text)');
    await assert.rejects(() => fixture.transaction(first, async c => {
      await c.query("insert into synthetic values ('rolled back')");
      throw new Error('synthetic refusal');
    }), /synthetic refusal/);
    assert.deepEqual((await second.query('select * from synthetic')).rows, []);
    await fixture.transaction(first, c => c.query("insert into synthetic values ('committed')"));
    assert.deepEqual((await second.query('select * from synthetic')).rows, [{ value: 'committed' }]);
  });
  await withPostgresFixture({}, async fixture => {
    const c = await fixture.connect();
    assert.equal((await c.query("select to_regclass('synthetic') as table_name")).rows[0].table_name, null);
  });
});

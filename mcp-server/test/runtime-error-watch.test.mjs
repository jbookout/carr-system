import test from 'node:test';
import assert from 'node:assert/strict';
import { consumeRuntimeErrors } from '../../ops/runtime-error-watch.mjs';
import { RuntimeErrorStore } from '../src/runtime-errors.js';
import * as watcher from '../../ops/runtime-error-watch.mjs';
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('CLI stderr version conflicts refresh the persisted pending operation', async () => {
  const root = mkdtempSync(join(tmpdir(), 'runtime-conflict-'));
  try {
    writeFileSync(join(root, 'run.sh'), '#!/bin/sh\nif [ "$2" = read-loop ]; then echo \'{"loop":{"loop_id":"one-loop","version":1}}\'; else echo \'TOOL ERROR {"error":"version_conflict"}\' >&2; exit 1; fi\n');
    chmodSync(join(root, 'run.sh'), 0o755);
    let refreshed = false;
    const control = async path => {
      if (path.endsWith('/plan')) return { ok: true, operations: [{ verb: 'update-loop', fingerprint: 'fp', key: 'key', args: { loop_id: 'one-loop' } }], health: 'WARN' };
      if (path.endsWith('/prepare')) return { ok: true, operation: { args: { loop_id: 'one-loop', base_version: 1 } } };
      if (path.endsWith('/refresh')) refreshed = true;
      return { ok: true };
    };
    await assert.rejects(consumeRuntimeErrors(control, (verb, args) => watcher.callRuntimeVerb(verb, args, root)), /version_conflict_replan/);
    assert.equal(refreshed, true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('a lost update acknowledgement replays identical versioned arguments against persisted storage', async () => {
  const data = new Map(), writes = new Map();
  const store = new RuntimeErrorStore({ blockConcurrencyWhile: fn => fn(), storage: {
    get: async key => structuredClone(data.get(key)), put: async (key, value) => data.set(key, structuredClone(value)),
    list: async () => structuredClone(data),
  } });
  let version = 1, interrupt = false;
  const control = async (path, body) => {
    if (interrupt && path.endsWith('/ack')) { interrupt = false; throw new Error('lost ack'); }
    return (await store.fetch(new Request('https://store' + path.replace('/_runtime-errors', ''), { method: 'POST', body: JSON.stringify(body) }))).json();
  };
  const call = async (verb, args) => {
    if (verb === 'read-loop') return { loop: { loop_id: args.loop_id, version, status: 'open' }, amended: false, amendments: [] };
    if (writes.has(args.idempotency_key)) { assert.deepEqual(args, writes.get(args.idempotency_key)); return { ok: true, loop_id: '12345678-1234-1234-1234-123456789abc' }; }
    writes.set(args.idempotency_key, structuredClone(args)); version++;
    return { ok: true, loop_id: '12345678-1234-1234-1234-123456789abc' };
  };
  await control('/_runtime-errors/capture', {});
  await consumeRuntimeErrors(control, call);
  for (let n = 0; n < 9; n++) await control('/_runtime-errors/capture', {});
  interrupt = true;
  await assert.rejects(consumeRuntimeErrors(control, call), /lost ack/);
  await consumeRuntimeErrors(control, call);
  assert.equal(writes.size, 2, 'one creation and one update despite replay');
  assert.equal(version, 3);
  assert.equal([...data.values()][0].pending, null);
});

test('lost acknowledgement replays the same add-loop operation instead of filing a second loop', async () => {
  const operation = { verb: 'add-loop', key: 'stable-idempotency-key', fingerprint: 'fingerprint', args: { body: 'redacted error', kind: 'open_loop', owner: 'Orchestrator' } };
  const writes = new Map(); let attempts = 0, acknowledgements = 0;
  const control = async path => {
    if (path.endsWith('/plan')) return { ok: true, operations: [operation], health: 'WARN runtime errors · on breach: dedup loop' };
    if (++acknowledgements === 1) throw new Error('interrupted acknowledgement');
    return { ok: true };
  };
  const call = async (verb, args) => { attempts++; writes.set(args.idempotency_key, { verb, args }); return { ok: true, loop_id: 'one-loop' }; };
  await assert.rejects(consumeRuntimeErrors(control, call), /interrupted/);
  await consumeRuntimeErrors(control, call);
  assert.equal(attempts, 2);
  assert.equal(writes.size, 1);
});

test('updates and closures read the loop version before a write and retain one loop id', async () => {
  const calls = [];
  for (const verb of ['update-loop', 'close-loop']) {
    const control = async path => path.endsWith('/plan') ? { ok: true, operations: [{ verb, key: verb, fingerprint: 'fp', args: { loop_id: 'one-loop' } }], health: 'OK' } : path.endsWith('/prepare') ? { ok: true, operation: { args: { loop_id: 'one-loop', base_version: 7 } } } : { ok: true };
    await consumeRuntimeErrors(control, async (name, args) => { calls.push({ name, args }); return name === 'read-loop' ? { loop: { loop_id: args.loop_id, version: 7, status: 'open' }, amended: false, amendments: [] } : { ok: true, loop_id: 'one-loop' }; });
  }
  assert.deepEqual(calls.map(call => call.name), ['read-loop', 'update-loop', 'read-loop', 'close-loop']);
  assert.equal(calls[1].args.base_version, 7);
  assert.equal(calls[3].args.base_version, 7);
});

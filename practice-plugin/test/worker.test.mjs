import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import worker from '../src/worker.mjs';

const endpoint = 'https://practice.synthetic.invalid/mcp';
const headers = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' };
const post = (body, extra = {}) => worker.fetch(new Request(endpoint, {
  method: 'POST', headers: { ...headers, ...extra }, body: JSON.stringify(body),
}));

test('SDK client initializes, lists, pings and calls without authentication or sessions', async () => {
  const transport = new StreamableHTTPClientTransport(new URL(endpoint), {
    fetch: (url, init) => worker.fetch(new Request(url, init)),
  });
  const client = new Client({ name: 'synthetic-review-client', version: '0.1.0' });
  try {
    await client.connect(transport);
    assert.equal(transport.sessionId, undefined);
    assert.equal((await client.listTools()).tools.length, 5);
    await client.ping();
    const r = await client.callTool({ name: 'plan_practice_space', arguments: {
      practice_type: 'medical', providers: 1, operatories: 0, exam_rooms: 3,
    } });
    assert.ok(!r.isError);
    assert.match(r.structuredContent.notice, /not legal/i);
  } finally { await client.close(); }
});

test('transport refuses foreign origins and invalid protocol/header/body requests', async () => {
  const ping = { jsonrpc: '2.0', id: 1, method: 'ping' };
  assert.equal((await post(ping, { Origin: 'https://foreign.synthetic.invalid' })).status, 403);
  assert.equal((await post(ping, { Origin: 'null' })).status, 403);
  assert.equal((await post(ping, { 'MCP-Protocol-Version': 'unsupported' })).status, 400);
  assert.equal((await post(ping, { Accept: 'text/plain' })).status, 406);
  assert.equal((await post(ping, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await post({ ...ping, padding: 'x'.repeat(17000) })).status, 413);
  assert.equal((await worker.fetch(new Request(endpoint, { method: 'POST', headers, body: '{' }))).status, 400);
  assert.equal((await worker.fetch(new Request(endpoint, { method: 'GET' }))).status, 405);
  assert.equal((await worker.fetch(new Request(endpoint, { method: 'DELETE' }))).status, 405);
  assert.equal((await worker.fetch(new Request('https://practice.synthetic.invalid/'))).status, 404);
});

test('Worker has no binding, secrets, OAuth, logs or outbound request capability in source', () => {
  const config = JSON.parse(fs.readFileSync(new URL('../wrangler.json', import.meta.url)));
  const allowed = ['name', 'main', 'compatibility_date', 'workers_dev', 'preview_urls', 'observability'];
  assert.ok(Object.keys(config).every(k => allowed.includes(k)));
  assert.deepEqual(config.observability, { enabled: false });
  assert.equal(config.workers_dev, false);
  assert.equal(config.preview_urls, false);
  assert.equal(config.main, 'src/worker.mjs');
  for (const file of fs.readdirSync(new URL('../src/', import.meta.url))) {
    const code = fs.readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(code, /process\.env|env\.|console\.|\bfetch\s*\(|DATABASE|OAuth|Authorization|\.dev\.vars/);
  }
});

test('calls do not make network requests, log data or echo rejected input', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('runtime egress forbidden'); };
  try {
    const response = await post({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: {
      name: 'get_broker_search_help', arguments: { request: 'actual_search_help', contact: 'synthetic-rejected' },
    } });
    const body = await response.json();
    assert.ok(body.result.isError);
    assert.doesNotMatch(JSON.stringify(body), /synthetic-rejected/);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  } finally { globalThis.fetch = oldFetch; }
});

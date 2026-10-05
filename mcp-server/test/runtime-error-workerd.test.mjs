import test from 'node:test';
import assert from 'node:assert/strict';
import { builtinModules } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('real Worker authenticates browser capture and local control and persists one scrubbed fingerprint', async () => {
  const bundle = await build({ absWorkingDir: fileURLToPath(new URL('../../', import.meta.url)), entryPoints: ['mcp-server/src/index.js'], bundle: true, write: false, format: 'esm', platform: 'neutral', logLevel: 'silent', mainFields: ['browser', 'module', 'main'], conditions: ['workerd', 'worker', 'browser'], external: ['cloudflare:*', 'node:*'], loader: { '.ttf': 'binary' }, banner: { js: 'import { createRequire } from "node:module"; const require = createRequire("/index.js");' }, plugins: [{ name: 'node-compat', setup(builder) { builder.onResolve({ filter: /^[a-z]/ }, args => builtinModules.includes(args.path) ? { path: 'node:' + args.path, external: true } : undefined); } }] });
  const worker = new Miniflare({ ...convertV4MiniflareOptions({ workers: [{ name: 'carr', modules: true, script: bundle.outputFiles[0].text,
    compatibilityDate: '2026-09-01', compatibilityFlags: ['nodejs_compat', 'global_fetch_strictly_public'], kvNamespaces: ['OAUTH_KV'],
    bindings: { GOOGLE_CLIENT_ID: 'synthetic', GOOGLE_CLIENT_SECRET: 'synthetic', APP_HOST: 'app.example', CARR_ENV: 'staging', GIT_SHA: 'a'.repeat(40), LOCAL_TOKENS: JSON.stringify({ 'joe-local': 'synthetic-local' }) },
    durableObjects: { RUNTIME_ERRORS: { className: 'RuntimeErrorStore', useSQLite: true }, OAUTH_CONSENT_STATE: { className: 'OAuthConsentState', useSQLite: true } },
    outboundService: async () => Response.json({ service: 'doctorcre-app', environment: 'staging', source_commit: 'b'.repeat(40) }),
  }, { name: 'rpc-fixture', modules: true, compatibilityDate: '2026-09-01', serviceBindings: { CARR_ERRORS: { name: 'carr', entrypoint: 'RuntimeErrorSink' } }, script: `export default { async fetch(request, env) { return Response.json(await env.CARR_ERRORS.capture(await request.json())); } };` }] }), resourcePersistencePath: fileURLToPath(new URL(`../../out/_to_delete/runtime-error-fixture/${crypto.randomUUID()}/`, import.meta.url)), unsafeEnableSharedStorage: false });
  try {
    const origin = 'https://app.example';
    const input = { type: 'TypeError', message: 'Cannot read properties of Alice alice@example.test', route: '/clients/Alice', stack: 'at Alice (https://app/js/private.js:12:3)', release_sha: 'b'.repeat(40) };
    const post = headers => worker.dispatchFetch(origin + '/api/v1/runtime-errors', { method: 'POST', redirect: 'manual', headers: { 'content-type': 'application/json', origin, ...headers }, body: JSON.stringify(input) });
    assert.equal((await post({})).status, 401);
    assert.equal((await worker.dispatchFetch(origin + '/_runtime-errors/plan', { method: 'POST', body: '{}' })).status, 401);
    const cookie = 'synthetic-session';
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(cookie)))].map(byte => byte.toString(16).padStart(2, '0')).join('');
    const kv = await worker.getKVNamespace('OAUTH_KV');
    await kv.put('dealroom_session:' + hash, JSON.stringify({ props: { slug: 'joe', human: true }, createdAt: Date.now(), expiresAt: Date.now() + 3600000, csrfToken: 'synthetic-csrf', origin }));
    assert.equal((await post({ cookie: '__Host-dealroom_session=' + cookie, origin: 'https://other.example' })).status, 403);
    for (let i = 0; i < 2; i++) assert.equal((await post({ cookie: '__Host-dealroom_session=' + cookie })).status, 202);
    const response = await worker.dispatchFetch(origin + '/_runtime-errors/plan', { method: 'POST', headers: { authorization: 'Bearer synthetic-local' }, body: '{}' });
    assert.equal(response.status, 200);
    const plan = await response.json();
    assert.equal(plan.operations.length, 1);
    assert.equal(plan.operations[0].count, 2);
    assert.equal(plan.operations[0].verb, 'add-loop');
    assert.doesNotMatch(JSON.stringify(plan), /Alice|example.test|private/);
    assert.match(plan.health, /owner orchestrator.*verify.*auto-clear/);
    const rpc = await worker.getWorker('rpc-fixture');
    assert.equal((await rpc.fetch(origin + '/capture', { method: 'POST', body: JSON.stringify(input) })).status, 200);
    const rpcPlan = await (await worker.dispatchFetch(origin + '/_runtime-errors/plan', { method: 'POST', headers: { authorization: 'Bearer synthetic-local' }, body: '{}' })).json();
    assert.equal(rpcPlan.operations.length, 2);
    assert.ok(rpcPlan.operations.some(op => op.args.body.includes('app-worker')));
    assert.doesNotMatch(JSON.stringify(rpcPlan), /Alice|example.test|private/);
  } finally { await worker.dispose(); }
});

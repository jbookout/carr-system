import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { handleAuthorize, s256 } from "../src/google-oidc.js";
import * as policy from "../src/oauth-policy.js";

// Substitute only the deployment identity allow-list with synthetic accounts.
// Signature verification, callback and approval code execute unchanged.
const oidcSource = await readFile(new URL("../src/google-oidc.js", import.meta.url), "utf8");
const syntheticOidc = await import(`data:text/javascript;base64,${Buffer.from(oidcSource
  .replace(/import \{ slugForEmail[^\n]+/, `const slugForEmail = email => email === "partner@example.invalid" ? "synthetic-partner" : null;
const propsForSlug = (slug, extra) => ({ slug, ...extra });
const agentSlugForClient = () => null;
const verifiedAgentSlugForClient = () => null;`)
  .replace('"./oauth-policy.js"', JSON.stringify(new URL("../src/oauth-policy.js", import.meta.url).href))
).toString("base64")}`);

// Only the Workers host class is stubbed. The pinned provider's parsing,
// encryption, KV storage, audience checks and exchanges execute unchanged.
const providerSource = await readFile(new URL("../node_modules/@cloudflare/workers-oauth-provider/dist/oauth-provider.js", import.meta.url), "utf8");
assert.ok(providerSource.startsWith('import { WorkerEntrypoint } from "cloudflare:workers";'));
const { OAuthProvider, getOAuthApi } = await import(`data:text/javascript;base64,${Buffer.from(
  providerSource.replace('import { WorkerEntrypoint } from "cloudflare:workers";', "class WorkerEntrypoint {}")
).toString("base64")}`);

const ORIGIN = "https://oauth.example";
const REDIRECT = "https://client.example/callback";
const VERIFIER = "a".repeat(43);
const ctx = () => ({ waitUntil() {} });
class MemoryKV {
  values = new Map();
  async put(key, value, options = {}) {
    this.values.set(key, { value, expires: options.expiration ??
      (options.expirationTtl ? Date.now() / 1000 + options.expirationTtl : Infinity) });
  }
  async get(key, options) {
    const item = this.values.get(key);
    if (!item || item.expires <= Date.now() / 1000) return null;
    return (options === "json" || options?.type === "json") ? JSON.parse(item.value) : item.value;
  }
  async delete(key) { this.values.delete(key); }
  async list({ prefix = "" } = {}) {
    return { keys: [...this.values.keys()].filter(k => k.startsWith(prefix)).map(name => ({ name })), list_complete: true };
  }
}
async function fixture(extraOptions = {}) {
  const env = { OAUTH_KV: new MemoryKV(), GOOGLE_CLIENT_ID: "synthetic-config", GOOGLE_CLIENT_SECRET: "synthetic-config" };
  const options = { apiRoute: ["/mcp", "/doc/mcp", "/pipeline/changes"],
    apiHandler: { fetch: async () => new Response("accepted") },
    defaultHandler: { fetch: handleAuthorize }, authorizeEndpoint: "/authorize",
    tokenEndpoint: "/token", clientRegistrationEndpoint: "/register", allowPlainPKCE: false,
    accessTokenTTL: 3600, refreshTokenTTL: 7776000, clientIdMetadataDocumentEnabled: false, ...extraOptions };
  env.OAUTH_PROVIDER = getOAuthApi(options, env);
  const client = await env.OAUTH_PROVIDER.createClient({ clientName: "Synthetic Client", redirectUris: [REDIRECT], tokenEndpointAuthMethod: "none" });
  const rawProvider = new OAuthProvider(options);
  const provider = { fetch: (request, env, ctx) => policy.sharedOAuthFetch
    ? policy.sharedOAuthFetch(rawProvider, request, env, ctx) : rawProvider.fetch(request, env, ctx) };
  return { env, client, provider, rawProvider };
}
async function authorization(client, changes = {}) {
  const params = new URLSearchParams({ response_type: "code", client_id: client.clientId,
    redirect_uri: REDIRECT, code_challenge_method: "S256", code_challenge: await s256(VERIFIER),
    resource: `${ORIGIN}/mcp`, ...changes });
  for (const [k, v] of Object.entries(changes)) if (v === null) params.delete(k);
  return new Request(`${ORIGIN}/authorize?${params}`);
}

test("authorization refuses missing S256 challenge before starting Google sign-in", async () => {
  const { env, client, provider } = await fixture();
  const response = await provider.fetch(await authorization(client, { code_challenge: null }), env, ctx());
  assert.equal(response.status, 400);
  assert.equal(response.headers.has("location"), false);
});

async function signedIdentity(email = "partner@example.invalid") {
  const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const encode = data => Buffer.from(JSON.stringify(data)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const message = `${encode({ alg: "RS256", kid: "synthetic" })}.${encode({ iss: "https://accounts.google.com",
    aud: "synthetic-config", exp: now + 3600, iat: now, email, email_verified: true, sub: "synthetic-subject" })}`;
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, new TextEncoder().encode(message));
  return { id_token: `${message}.${Buffer.from(signature).toString("base64url")}`,
    jwks: { keys: [{ ...await crypto.subtle.exportKey("jwk", pair.publicKey), kid: "synthetic" }] } };
}
async function consentFixture(email, clientName = "Synthetic Client") {
  const f = await fixture();
  await f.env.OAUTH_PROVIDER.updateClient(f.client.clientId, { clientName });
  const start = await f.provider.fetch(await authorization(f.client), f.env, ctx());
  const state = new URL(start.headers.get("location")).searchParams.get("state");
  const cookie = start.headers.get("set-cookie")?.split(";")[0] || "";
  const identity = await signedIdentity(email);
  await f.env.OAUTH_KV.put("google_jwks_cache", JSON.stringify(identity.jwks));
  const original = globalThis.fetch;
  globalThis.fetch = async url => {
    assert.equal(url, "https://oauth2.googleapis.com/token");
    return Response.json({ id_token: identity.id_token });
  };
  let response;
  try { response = await syntheticOidc.handleCallback(new Request(`${ORIGIN}/callback?state=${state}&code=synthetic-code`, { headers: { cookie } }), f.env); }
  finally { globalThis.fetch = original; }
  const html = await response.text();
  const form = new URLSearchParams();
  for (const match of html.matchAll(/name="([^"]+)" value="([^"]*)"/g)) form.set(match[1], match[2]);
  return { ...f, response, html, form, cookie };
}
async function approve(f, { cookie = f.cookie, origin = ORIGIN, form = f.form, method = "POST", decision = "approve" } = {}) {
  return syntheticOidc.handleConsent(new Request(`${ORIGIN}/consent`, { method,
    headers: { cookie, origin, "content-type": "application/x-www-form-urlencoded" },
    ...(method === "POST" ? { body: new URLSearchParams({ ...Object.fromEntries(form), decision }) } : {}) }), f.env);
}
test("verified identity reaches client-specific consent before any grant and approval issues a bound code", async () => {
  const f = await consentFixture();
  assert.equal(f.response.status, 200);
  for (const value of ["Synthetic Client", REDIRECT, `${ORIGIN}/mcp`, "Read and write"]) assert.ok(f.html.includes(value));
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  const accepted = await approve(f);
  assert.equal(accepted.status, 302);
  const code = new URL(accepted.headers.get("location")).searchParams.get("code");
  const tokens = await (await exchange(f, { grant_type: "authorization_code", code, code_verifier: VERIFIER })).json();
  assert.equal(tokens.resource, `${ORIGIN}/mcp`);
  assert.equal((await access(f, tokens.access_token, "/mcp")).status, 200);
  assert.equal((await approve(f)).status, 400);
});
test("consent rejects absent/wrong browser cookie, cross-site POST, tampered nonce, expiry and GET", async () => {
  const f = await consentFixture();
  assert.equal(f.response.status, 200);
  for (const options of [{ cookie: "" }, { cookie: "wrong=browser" }, { origin: "https://hostile.example" },
    { form: new URLSearchParams({ ...Object.fromEntries(f.form), csrf: "tampered" }) }, { method: "GET" }]) {
    assert.ok([400, 403, 405].includes((await approve(f, options)).status));
    assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  }
  for (const [key] of f.env.OAUTH_KV.values) if (key.startsWith("pending_consent:")) await f.env.OAUTH_KV.delete(key);
  assert.equal((await approve(f)).status, 400);
});
test("wrong Google identity is denied and configured approved-client IDs restrict authorizations", async () => {
  const denied = await consentFixture("outsider@example.invalid");
  assert.equal(denied.response.status, 403);
  assert.equal((await denied.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  const f = await fixture();
  for (const config of ["[]", "invalid-json", '["other-client"]']) {
    f.env.CARR_OAUTH_APPROVED_CLIENT_IDS = config;
    assert.equal((await f.provider.fetch(await authorization(f.client), f.env, ctx())).status, 400);
  }
  f.env.CARR_OAUTH_APPROVED_CLIENT_IDS = JSON.stringify([f.client.clientId]);
  assert.equal((await f.provider.fetch(await authorization(f.client), f.env, ctx())).status, 302);
});

test("denying consent never issues a grant and untrusted client labels are escaped", async () => {
  const f = await consentFixture(undefined, '<script>synthetic()</script>');
  assert.ok(f.html.includes("&lt;script&gt;synthetic()&lt;/script&gt;"));
  assert.equal(f.html.includes("<script>"), false);
  assert.equal(f.response.headers.get("x-frame-options"), "DENY");
  assert.match(f.response.headers.get("content-security-policy"), /frame-ancestors 'none'/);
  assert.equal((await approve(f, { decision: "deny" })).status, 403);
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
  assert.equal((await approve(f)).status, 400);
});

test("consent cannot replace the approved redirect or bypass a changed server client policy", async () => {
  const f = await consentFixture();
  const form = new URLSearchParams({ ...Object.fromEntries(f.form), redirect_uri: "https://hostile.example/callback", resource: `${ORIGIN}/doc/mcp` });
  const accepted = await approve(f, { form });
  assert.equal(new URL(accepted.headers.get("location")).origin, "https://client.example");
  const code = new URL(accepted.headers.get("location")).searchParams.get("code");
  assert.equal((await (await exchange(f, { grant_type: "authorization_code", code, code_verifier: VERIFIER })).json()).resource, `${ORIGIN}/mcp`);
  const changed = await consentFixture();
  changed.env.CARR_OAUTH_APPROVED_CLIENT_IDS = "[]";
  assert.equal((await approve(changed)).status, 403);
  assert.equal((await changed.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
});

test("approval revalidates a client's registration and fails closed if its redirect changed", async () => {
  const f = await consentFixture();
  await f.env.OAUTH_PROVIDER.updateClient(f.client.clientId, { redirectUris: ["https://replacement.example/callback"] });
  const refused = await approve(f);
  assert.equal(refused.status, 400);
  assert.equal(refused.headers.has("location"), false);
  assert.equal((await f.env.OAUTH_PROVIDER.listUserGrants("synthetic-partner")).items.length, 0);
});

test("Google callback refuses a stolen state from another browser before exchanging anything", async () => {
  const f = await fixture();
  const start = await f.provider.fetch(await authorization(f.client), f.env, ctx());
  const state = new URL(start.headers.get("location")).searchParams.get("state");
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("No network may be called"); };
  try {
    assert.equal((await syntheticOidc.handleCallback(new Request(`${ORIGIN}/callback?state=${state}&code=synthetic-code`), f.env)).status, 403);
  } finally { globalThis.fetch = original; }
});

test("pinned baseline accepts missing challenge, broad/no audience and reflects hostile Origin", async () => {
  const f = await fixture();
  const parsed = await f.env.OAUTH_PROVIDER.parseAuthRequest(await authorization(f.client, { code_challenge: null }));
  assert.equal(parsed.codeChallenge, undefined);
  for (const resource of [null, ORIGIN]) {
    const code = await codeGrant(f, resource);
    const raw = await f.rawProvider.fetch(new Request(`${ORIGIN}/token`, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "authorization_code", client_id: f.client.clientId, code, code_verifier: VERIFIER }) }), f.env, ctx());
    const tokens = await raw.json();
    for (const path of ["/mcp", "/doc/mcp", "/pipeline/changes"]) {
      const response = await f.rawProvider.fetch(new Request(ORIGIN + path, { headers: { authorization: `Bearer ${tokens.access_token}`, origin: "https://hostile.example" } }), f.env, ctx());
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("access-control-allow-origin"), "https://hostile.example");
    }
  }
});

test("authorization accepts canonical S256 and refuses malformed challenges and methods", async () => {
  const { env, client, provider } = await fixture();
  assert.equal((await provider.fetch(await authorization(client), env, ctx())).status, 302);
  for (const change of [ { code_challenge: "x" }, { code_challenge: "!".repeat(43) },
    { code_challenge: "a".repeat(43) }, { code_challenge_method: "plain" },
    { code_challenge_method: "bogus" }, { response_type: "token" } ]) {
    assert.equal((await provider.fetch(await authorization(client, change), env, ctx())).status, 400);
  }
});

async function codeGrant(f, resource = `${ORIGIN}/mcp`, props = {}) {
  const request = await f.env.OAUTH_PROVIDER.parseAuthRequest(await authorization(f.client, { resource }));
  const { redirectTo } = await f.env.OAUTH_PROVIDER.completeAuthorization({ request,
    userId: "synthetic-partner", scope: [], props, revokeExistingGrants: false });
  return new URL(redirectTo).searchParams.get("code");
}
async function exchange(f, params) {
  return f.provider.fetch(new Request(`${ORIGIN}/token`, { method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: f.client.clientId, ...params }) }), f.env, ctx());
}
test("pinned provider requires the matching verifier for a stored S256 challenge", async () => {
  const f = await fixture();
  const code = await codeGrant(f);
  for (const change of [{}, { code_verifier: "b".repeat(43) }]) {
    const response = await exchange(f, { grant_type: "authorization_code", code, ...change });
    assert.equal(response.status, 400);
  }
  assert.equal((await exchange(f, { grant_type: "authorization_code", code, code_verifier: VERIFIER })).status, 200);
});

async function access(f, token, path) {
  return f.provider.fetch(new Request(`${ORIGIN}${path}`, { headers: { authorization: `Bearer ${token}` } }), f.env, ctx());
}
test("legacy missing or origin-wide audiences work only on mcp and migrate silently at refresh", async () => {
  for (const legacyResource of [null, ORIGIN, `${ORIGIN}/`]) {
    const f = await fixture();
    const code = await codeGrant(f, legacyResource);
    // Mint a pre-policy token with the real provider, then cross the new seam.
    const legacyResponse = await f.rawProvider.fetch(new Request(`${ORIGIN}/token`, { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ client_id: f.client.clientId, grant_type: "authorization_code", code, code_verifier: VERIFIER }) }), f.env, ctx());
    const legacy = await legacyResponse.json();
    assert.equal((await access(f, legacy.access_token, "/mcp")).status, 200);
    assert.equal((await access(f, legacy.access_token, "/doc/mcp")).status, 401);
    assert.equal((await access(f, legacy.access_token, "/pipeline/changes")).status, 401);
    const before = [...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]);
    const refused = await exchange(f, { grant_type: "refresh_token", refresh_token: legacy.refresh_token, resource: `${ORIGIN}/doc/mcp` });
    assert.equal(refused.status, 400);
    assert.deepEqual([...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]), before);
    const refreshed = await (await exchange(f, { grant_type: "refresh_token", refresh_token: legacy.refresh_token,
      ...(legacyResource ? { resource: legacyResource } : {}) })).json();
    assert.equal(refreshed.resource, `${ORIGIN}/mcp`);
    assert.equal((await access(f, refreshed.access_token, "/mcp")).status, 200);
    for (const path of ["/doc/mcp", "/pipeline/changes"]) assert.equal((await access(f, refreshed.access_token, path)).status, 401);
    const again = await (await exchange(f, { grant_type: "refresh_token", refresh_token: refreshed.refresh_token })).json();
    assert.equal(again.resource, `${ORIGIN}/mcp`);
  }
});

test("each exact resource token is refused on both other routes and cannot expand at refresh", async () => {
  const f = await fixture();
  const paths = ["/mcp", "/doc/mcp", "/pipeline/changes"];
  for (const path of paths) {
    const tokens = await (await exchange(f, { grant_type: "authorization_code", code: await codeGrant(f, `${ORIGIN}${path}`), code_verifier: VERIFIER })).json();
    for (const target of paths) {
      assert.equal((await access(f, tokens.access_token, target)).status, target === path ? 200 : 401);
      if (target !== path) {
        const before = [...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]);
        assert.equal((await exchange(f, { grant_type: "refresh_token", refresh_token: tokens.refresh_token, resource: `${ORIGIN}${target}` })).status, 400);
        assert.deepEqual([...f.env.OAUTH_KV.values].map(([k, v]) => [k, v.value]), before);
      }
    }
    const refreshed = await (await exchange(f, { grant_type: "refresh_token", refresh_token: tokens.refresh_token })).json();
    assert.equal(refreshed.resource, `${ORIGIN}${path}`);
    assert.equal((await access(f, refreshed.access_token, path)).status, 200);
  }
});

test("authorization binds omitted resource to legacy mcp and rejects broad, foreign and multiple resources", async () => {
  const f = await fixture();
  for (const resource of [ORIGIN, `${ORIGIN}/`, `${ORIGIN}/doc`, "https://elsewhere.example/mcp", `${ORIGIN}/mcp#fragment`]) {
    assert.equal((await f.provider.fetch(await authorization(f.client, { resource }), f.env, ctx())).status, 400);
  }
  const missing = await f.provider.fetch(await authorization(f.client, { resource: null }), f.env, ctx());
  assert.equal(missing.status, 302);
  const state = new URL(missing.headers.get("location")).searchParams.get("state");
  assert.equal((await f.env.OAUTH_KV.get(`pending_auth:${state}`, "json")).req.resource, `${ORIGIN}/mcp`);
  const duplicate = new URL((await authorization(f.client)).url);
  duplicate.searchParams.append("resource", `${ORIGIN}/doc/mcp`);
  assert.equal((await f.provider.fetch(new Request(duplicate), f.env, ctx())).status, 400);
});

test("Origin policy refuses hostile, null and malformed origins before every protected-route method", async () => {
  const f = await fixture();
  for (const path of ["/mcp", "/doc/mcp", "/pipeline/changes", "/mcp/nested"]) {
    for (const method of ["GET", "POST", "OPTIONS", "DELETE"]) {
      for (const origin of ["https://hostile.example", "null", "https://chatgpt.com.evil.example", "https://claude.ai/path", "https://user@claude.ai"]) {
        const response = await f.provider.fetch(new Request(ORIGIN + path, { method, headers: { origin } }), f.env, ctx());
        assert.equal(response.status, 403);
        assert.equal(response.headers.get("access-control-allow-origin"), null);
      }
    }
  }
});

test("absent Origin and exact ChatGPT/Claude/configured origins reach OAuth authentication", async () => {
  const f = await fixture();
  f.env.CARR_MCP_ALLOWED_ORIGINS = JSON.stringify(["https://browser.example"]);
  for (const origin of [null, "https://chatgpt.com", "https://chat.openai.com", "https://claude.ai", "https://browser.example"]) {
    const headers = origin ? { origin } : {};
    assert.equal((await f.provider.fetch(new Request(`${ORIGIN}/mcp`, { headers }), f.env, ctx())).status, 401);
  }
  f.env.CARR_MCP_ALLOWED_ORIGINS = '["*"]';
  assert.equal((await f.provider.fetch(new Request(`${ORIGIN}/mcp`, { headers: { origin: "https://chatgpt.com" } }), f.env, ctx())).status, 403);
});

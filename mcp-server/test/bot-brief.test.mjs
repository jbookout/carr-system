import test from "node:test";
import assert from "node:assert/strict";

import { botBriefTools } from "../src/bot-brief.js";
import { ToolError } from "../src/tool-error.js";
import { assertNoCallerAuthorityFields } from "../src/tools.js";

const profile = (overrides = {}) => ({
  profile_key: "doc", display_name: "Doc", charter: ["routine operations"],
  current_model: null, current_desk: null, sponsor_scope: "shared",
  status: "parked", version: "1", ...overrides,
});

function clientFor({ rows = profile(), generation = "42", mode = "shadow", packs = [] } = {}) {
  const calls = [];
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (/from agent_profile/.test(sql)) return { rows: rows ? [rows] : [] };
      if (/from doctrine_meta/.test(sql)) return { rows: [{ generation }] };
      if (/rule_delivery_policy/.test(sql)) return { rows: [{ mode }] };
      if (/rule_pack_index/.test(sql)) return { rows: packs };
      throw new Error(`unexpected query: ${sql}`);
    },
  };
}

const joe = { id: "joe-runtime", slug: "joe-local", human: false,
  sponsoring_human_slug: "joe", human_slug: "joe", authorization_class: "sponsored_agent",
  operational_profile: "hermes", via: "hermes-token" };
const dell = { id: "dell-runtime", slug: "dell-local", human: false,
  sponsoring_human_slug: "dell", human_slug: "dell", authorization_class: "sponsored_agent",
  operational_profile: "hermes", via: "hermes-token" };

const TOOLS = botBriefTools({ ToolError, assertNoCallerAuthorityFields });

test("bot-brief is read-only, typed, and rejects caller authority fields", () => {
  assert.ok(TOOLS["bot-brief"]);
  assert.notEqual(TOOLS["bot-brief"].write, true);
  assert.equal(TOOLS["bot-brief"].inputSchema.additionalProperties, false);
  for (const key of ["sponsor", "partner", "tenant", "runtime", "device", "capability", "authority"])
    assert.equal(Object.hasOwn(TOOLS["bot-brief"].inputSchema.properties, key), false);
});

test("packs are canonicalized before recognition and definition hashing", async () => {
  const a = await TOOLS["bot-brief"].handler(clientFor({
    packs: [{ pack: "system-watch", title: "System", triggers: ["x"], rule_count: "1" }],
  }), joe, { profile_key: "doc", packs: ["system-watch"] });
  const b = await TOOLS["bot-brief"].handler(clientFor({
    packs: [{ pack: "system-watch", title: "System", triggers: ["x"], rule_count: "1" }],
  }), joe, { profile_key: "doc", packs: ["SYSTEM-WATCH", "system-watch", " "] });
  assert.deepEqual(b.requested_packs, ["system-watch"]);
  assert.deepEqual(b.unknown_packs, []);
  assert.equal(a.definition_digest, b.definition_digest);
});

test("same definition digest, different instance digest for Joe and Dell", async () => {
  const args = { profile_key: "doc", packs: ["system-watch"] };
  const a = await TOOLS["bot-brief"].handler(clientFor(), joe, args);
  const b = await TOOLS["bot-brief"].handler(clientFor(), dell, args);
  assert.equal(a.definition_digest, b.definition_digest);
  assert.notEqual(a.instance_digest, b.instance_digest);
  assert.equal(a.identity.sponsoring_human_slug, "joe");
  assert.equal(b.identity.sponsoring_human_slug, "dell");
});

test("profile and brief never grant authority; local files and memory are non-authoritative", async () => {
  const out = await TOOLS["bot-brief"].handler(clientFor(), joe, { profile_key: "doc" });
  assert.equal(out.profile_grants_authority, false);
  assert.equal(out.brief_grants_authority, false);
  assert.equal(out.local_markdown_authoritative, false);
  assert.equal(out.local_memory_authoritative, false);
  assert.deepEqual(out.runtime_requirements, {
    skip_context_files: true, skip_memory: true, ephemeral_system_prompt: true,
  });
  assert.equal(out.tool_allowlist_source, "mcp tools/list");
  assert.equal(out.boot_sources.standing_context, "standing-context");
  assert.equal(out.boot_sources.read_profiles, "read-profiles");
});

test("caller fields cannot cross brains or widen identity", async () => {
  const client = clientFor();
  await assert.rejects(TOOLS["bot-brief"].handler(client, joe, {
    profile_key: "doc", sponsor: "dell", partner: "dell", tenant: "other",
    runtime: "other", device: "other", capability: "full", authority: "human",
  }), error => error instanceof ToolError);
  const out = await TOOLS["bot-brief"].handler(clientFor(), joe, { profile_key: "doc" });
  assert.equal(out.identity.sponsoring_human_slug, "joe");
  assert.equal(out.identity.organization_tenant_id, "carr-internal");
  assert.equal(out.identity.runtime_principal, "joe-local");
  assert.equal(out.identity.human_only_authority, false);
});

test("canonical authority aliases are rejected by the direct handler too", async () => {
  for (const field of ["identity", "actor", "audience", "capabilities", "action", "actions",
    "action_authority", "action_authorities", "allowed_actions", "authorization", "profile",
    "write", "writes_records", "calls_models", "call_models"]) {
    const client = clientFor();
    await assert.rejects(
      TOOLS["bot-brief"].handler(client, joe, { profile_key: "doc", [field]: "caller" }),
      error => error instanceof ToolError,
      `direct handler must refuse ${field}`,
    );
    assert.equal(client.calls.length, 0, `${field} must be refused before any DB query`);
  }
});

test("Bot-Brief-specific authority aliases are rejected before any DB query", async () => {
  for (const field of ["partner", "runtime", "device", "authority", "personal_brain_scope",
    "operational_profile", "session_capability_profile", "human_only_authority", "device_id",
    "profile_grants_authority", "brief_grants_authority"]) {
    const client = clientFor();
    await assert.rejects(
      TOOLS["bot-brief"].handler(client, joe, { profile_key: "doc", [field]: "caller" }),
      error => error instanceof ToolError,
      `direct handler must refuse ${field}`,
    );
    assert.equal(client.calls.length, 0, `${field} must be refused before any DB query`);
  }
});

test("an ambiguous sponsored OAuth actor fails closed instead of becoming shared", async () => {
  const actor = { ...joe, via: "oauth-google", sponsor_required: true,
    sponsoring_human_slug: null, human_slug: null };
  await assert.rejects(
    TOOLS["bot-brief"].handler(clientFor(), actor, { profile_key: "doc" }),
    error => error instanceof ToolError &&
      error.payload.error === "missing_or_ambiguous_sponsor" &&
      /reconnect|partner/i.test(error.payload.hint),
  );
});

test("unknown profile is a typed error and does not read other sources", async () => {
  const client = clientFor({ rows: null });
  await assert.rejects(
    TOOLS["bot-brief"].handler(client, joe, { profile_key: "missing" }),
    error => error instanceof ToolError && error.payload.error === "profile_not_found",
  );
  assert.equal(client.calls.length, 1);
});

test("parked profile preserves null model and status, and reports unknown packs", async () => {
  const out = await TOOLS["bot-brief"].handler(clientFor({
    rows: profile({ current_model: null, current_desk: null, status: "parked", version: "7" }),
    packs: [{ pack: "known", title: "Known", triggers: ["x"], rule_count: "2" }],
  }), joe, { profile_key: "doc", packs: ["known", "missing"] });
  assert.equal(out.profile.status, "parked");
  assert.equal(out.profile.model, null);
  assert.equal(out.profile.desk, null);
  assert.deepEqual(out.requested_packs, ["known", "missing"]);
  assert.deepEqual(out.unknown_packs, ["missing"]);
});

test("bot-brief performs no database write", async () => {
  const client = clientFor();
  await TOOLS["bot-brief"].handler(client, joe, { profile_key: "doc" });
  assert.ok(client.calls.every(({ sql }) => /^\s*select/i.test(sql)),
    "all database interaction must be read-only SELECT");
});

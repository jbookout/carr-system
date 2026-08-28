import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import {
  canonicalExactEffectContract,
  deriveTrustedPrincipalBinding,
  ExactEffectRefusal,
  immutableExactEffectContracts,
  resolveExactEffects,
  SCAC_EXACT_EFFECT_CONTRACTS,
  SCAC_TRUSTED_PRINCIPAL_READBACK_SQL,
} from "../src/scac-exact-effects.js";
const migration = fs.readFileSync(new URL(
  "../../migrations/0392_siep18_exact_effects_trusted_principal.sql", import.meta.url), "utf8");

const contract = (ingress_key, direct_effects = [], delegates_to = [], extra = {}) => ({
  schema_version: "scac-exact-effect-contract.v1", ingress_key, direct_effects, delegates_to,
  sql_state: "static_reviewed", integration_state: "reviewed_source_test", ...extra,
});
const insert = (relation, columns) => ({ kind: "insert", relation, columns });
const update = (relation, columns) => ({ kind: "update", relation, columns });
const execute = function_signature => ({ kind: "execute", function_signature });

test("exact DML and EXECUTE contracts are finite, canonical, closed, and immutable", () => {
  const reviewed = canonicalExactEffectContract(contract("mcp-tool:leaf", [
    execute("ops.record_event(uuid,text)"),
    insert("public.event", ["actor_id", "kind", "subject_id"]),
    update("public.loop_item", ["status", "updated_at", "updated_by"]),
  ]));
  assert.equal(Object.isFrozen(reviewed), true);
  assert.equal(Object.isFrozen(reviewed.direct_effects), true);
  assert.equal(Object.isFrozen(reviewed.direct_effects[1].columns), true);
  assert.throws(() => canonicalExactEffectContract({ ...reviewed, inferred_from_write: true }),
    /open_or_incomplete/);
  assert.throws(() => canonicalExactEffectContract(contract("mcp-tool:leaf", [
    insert("public.event", ["kind", "actor_id"]),
  ])), /not_canonical/);
  assert.throws(() => canonicalExactEffectContract(contract("mcp-tool:leaf", [
    { kind: "execute", function_signature: "ops.anything(text); drop table actor" },
  ])), /target_malformed/);
});

test("recursive delegation returns the exact transitive union once", () => {
  const contracts = immutableExactEffectContracts([
    contract("mcp-tool:outer", [update("public.loop_item", ["status"])],
      ["mcp-tool:left", "mcp-tool:right"]),
    contract("mcp-tool:left", [insert("public.event", ["actor_id", "kind"])]),
    contract("mcp-tool:right", [execute("ops.audit_event(uuid)"),
      insert("public.event", ["actor_id", "kind"])]),
  ]);
  assert.deepEqual(resolveExactEffects("mcp-tool:outer", contracts), [
    { kind: "execute", function_signature: "ops.audit_event(uuid)" },
    { kind: "insert", relation: "public.event", columns: ["actor_id", "kind"] },
    { kind: "update", relation: "public.loop_item", columns: ["status"] },
  ]);
});

test("missing, wildcard, cycle, dynamic SQL, opaque, and unintegrated paths default deny", () => {
  assert.deepEqual(SCAC_EXACT_EFFECT_CONTRACTS, {});
  assert.throws(() => resolveExactEffects("mcp-tool:unknown"),
    error => error instanceof ExactEffectRefusal && error.error === "effect_contract_missing");
  assert.throws(() => immutableExactEffectContracts([
    contract("mcp-tool:any", [], ["mcp-tool:*registered"]),
  ]), /wildcard_delegate_refused/);
  const cyclic = immutableExactEffectContracts([
    contract("mcp-tool:a", [], ["mcp-tool:b"]),
    contract("mcp-tool:b", [], ["mcp-tool:a"]),
  ]);
  assert.throws(() => resolveExactEffects("mcp-tool:a", cyclic),
    error => error instanceof ExactEffectRefusal && error.error === "effect_delegate_cycle");
  for (const [sql_state, expected] of [
    ["dynamic_refused", "effect_sql_not_static"], ["opaque_refused", "effect_sql_not_static"],
  ]) {
    const refused = immutableExactEffectContracts([contract("mcp-tool:x", [], [],
      { sql_state, integration_state: "unintegrated_default_deny" })]);
    assert.throws(() => resolveExactEffects("mcp-tool:x", refused),
      error => error instanceof ExactEffectRefusal && error.error === expected);
  }
  const unintegrated = immutableExactEffectContracts([contract("mcp-tool:x", [], [],
    { integration_state: "unintegrated_default_deny" })]);
  assert.throws(() => resolveExactEffects("mcp-tool:x", unintegrated),
    error => error instanceof ExactEffectRefusal && error.error === "effect_path_unintegrated");
});

test("trusted principal binds server actor fields to the actual DB session readback", async () => {
  assert.match(SCAC_TRUSTED_PRINCIPAL_READBACK_SQL.text, /session_user::text/);
  assert.match(SCAC_TRUSTED_PRINCIPAL_READBACK_SQL.text, /current_user::text/);
  assert.match(SCAC_TRUSTED_PRINCIPAL_READBACK_SQL.text, /pg_backend_pid\(\)/);
  const actor = {
    id: "10000000-0000-4000-8000-000000000001", slug: "joe", display: "Joe Bookout",
    human: true, via: "oauth-google", client_id: "trusted-client", sponsoring_human_slug: null,
    human_slug: null, sponsor_required: false,
  };
  const readback = { session_principal: "carr_writer", current_principal: "carr_writer",
    privilege_bundle: "carr_writer", backend_pid: 4123 };
  const bound = await deriveTrustedPrincipalBinding(actor, readback, "carr_writer");
  assert.deepEqual({ ...bound, principal_digest: undefined }, {
    schema_version: "scac-trusted-principal.v1", organization_tenant_id: "carr-internal",
    actor_id: actor.id, actor_slug: "joe", human: true, via: "oauth-google",
    client_id: "trusted-client", sponsoring_human_slug: "joe",
    authorization_class: "verified_partner", session_principal: "carr_writer",
    privilege_bundle: "carr_writer", backend_pid: 4123, principal_digest: undefined,
    source: "server_authenticated_actor_plus_database_readback",
    production_enforcement_active: false,
  });
  assert.match(bound.principal_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(Object.isFrozen(bound), true);
});

test("caller labels and mismatched or elevated database principals cannot create a binding", async () => {
  const actor = { id: "10000000-0000-4000-8000-000000000001", slug: "joe", human: true };
  const writer = { session_principal: "carr_writer", current_principal: "carr_writer",
    privilege_bundle: "carr_writer", backend_pid: 9 };
  await assert.rejects(deriveTrustedPrincipalBinding(actor, { ...writer, claimed_actor: "dell" },
    "carr_writer"), /open_or_incomplete/);
  await assert.rejects(deriveTrustedPrincipalBinding(actor,
    { ...writer, session_principal: "carr_authority_joe", current_principal: "carr_authority_joe",
      privilege_bundle: "carr_authority" }, "carr_writer"),
    error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
  await assert.rejects(deriveTrustedPrincipalBinding(actor,
    { ...writer, current_principal: "neondb_owner" }, "carr_writer"),
    error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
  const joeAuthority = { session_principal: "carr_authority_joe",
    current_principal: "carr_authority_joe", privilege_bundle: "carr_authority", backend_pid: 10 };
  const joe = await deriveTrustedPrincipalBinding(actor, joeAuthority, "carr_authority");
  assert.equal(joe.session_principal, "carr_authority_joe");
  await assert.rejects(deriveTrustedPrincipalBinding(actor, {
    ...joeAuthority,
    session_principal: "carr_authority_dell",
    current_principal: "carr_authority_dell",
  }, "carr_authority"),
  error => error instanceof ExactEffectRefusal && error.error === "trusted_database_principal_mismatch");
});

test("0392 persists exact effects and trusted principals without activating enforcement", () => {
  for (const fragment of [
    /create table ops\.scac_exact_effect_contract/i,
    /create table ops\.scac_trusted_principal_binding/i,
    /create or replace function ops\.scac_register_exact_effect_contract/i,
    /create or replace function ops\.scac_exact_effect_union/i,
    /with recursive walk/i,
    /create or replace function ops\.scac_bind_trusted_principal/i,
    /p_server_principal->>'session_principal'<>session_user/i,
    /p_server_principal->>'privilege_bundle'<>expected_bundle/i,
    /p_server_principal->>'backend_pid'\)::integer<>pg_backend_pid\(\)/i,
    /p_server_principal->>'sponsoring_human_slug' not in \('joe','dell'\)/i,
    /session_user<>'carr_authority_'\|\|/i,
    /where id=\(p_server_principal->>'actor_id'\)::uuid/i,
    /and slug=p_server_principal->>'actor_slug' and active/i,
    /recorded_by text not null check \(recorded_by='joe'\)/i,
    /production_enforcement_active boolean not null default false/i,
    /check \(not production_enforcement_active\)/i,
  ]) assert.match(migration, fragment);
  assert.doesNotMatch(migration, /insert into ops\.scac_exact_effect_contract[\s\S]+values\s*\('scac-mutation-registry/i);
  assert.doesNotMatch(migration, /mode\s*=\s*'enforced_source_test'/i);
  assert.doesNotMatch(migration, /^\s*(?:begin|commit)\s*;/im);
});

test("0392 refuses inference, wildcard delegation, dynamic SQL, and caller identity", () => {
  assert.match(migration, /write labels, prose, grants, opaque functions, and dynamic SQL are never effect authority/i);
  assert.match(migration, /p_contract->>'sql_state'<>'static_reviewed'/i);
  assert.match(migration, /p_contract->>'integration_state'<>'reviewed_source_test'/i);
  assert.match(migration, /d~E'\[\\n\\r\\t\*\]'/i);
  assert.match(migration, /de\.contract->>'operation'=raw\.d/i);
  assert.match(migration, /coalesce\(bool_and\(cardinality\(keys\)=1\),true\)/i);
  assert.match(migration, /resolved_registry_delegates<>p_contract->'delegates_to'/i);
  assert.doesNotMatch(migration, /contract->>'write'/i);
  assert.doesNotMatch(migration, /description/i);
});

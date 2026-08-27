import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import fs from "node:fs";
import test from "node:test";

import { digest } from "../src/artifact-trust.js";
import * as scacToken from "../src/scac-token.js";

const migration = fs.readFileSync(
  new URL("../../migrations/0355_siep17_token_challenge_authority.sql", import.meta.url), "utf8");
const clone = value => structuredClone(value);

function fixture() {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const raw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  const issuer = {
    schema_version: "scac-token-issuer.v1", algorithm: "ed25519",
    issuer_key_digest: digest(raw), issuer_public_key: raw.toString("base64"),
    issuer_root_event_digest: digest("root-event"),
  };
  const token = {
    schema_version: "scac-capability-token.v1",
    token_id: "0198ed30-1900-7000-8000-000000000017",
    tenant_scope: "carr-internal", environment: "source-test",
    principal_digest: digest("joe-session"), device_ref: "joe-studio-pending",
    device_key_digest: digest("device-key"), facts_digest: digest("device-facts"),
    workload_digest: null, registry_version: "scac-mutation-registry.v7",
    registry_digest: digest("registry-v7"), ingress_key: "mcp-tool:standing-context",
    mutation_kind: "scac.mutation.business_record", target_surface: "scac.surface.database",
    policy_epoch: 7, policy_epoch_digest: digest("epoch-7"),
    operation_manifest_digest: digest("closed-operation-manifest"),
    request_digest: digest("closed-operation-manifest"), idempotency_digest: digest("request-idempotency"),
    challenge_digest: digest("challenge"), issuer_key_digest: issuer.issuer_key_digest,
    issuer_root_event_digest: issuer.issuer_root_event_digest,
    issued_at: "2026-08-27T12:00:00Z", expires_at: "2026-08-27T12:01:00Z",
    signed_payload_digest: null, signature: null, signature_digest: null,
  };
  token.signed_payload_digest = digest(scacToken.tokenPayload(token));
  const signature = sign(null, Buffer.from(token.signed_payload_digest), privateKey);
  token.signature = signature.toString("base64");
  token.signature_digest = digest(signature);
  return { token, issuer };
}

test("signed exact-scope token verifies but remains non-authorizing pending SIEP-18", () => {
  const { token, issuer } = fixture();
  const result = scacToken.verifyCapabilityToken({ token, issuer });
  assert.equal(result.token_state, "valid_cryptography_unchecked_database_state");
  assert.equal(result.admission_state, "ineligible_pending_siep18_atomic_database_reference_monitor");
  assert.equal(result.privilege_state, "none");
  assert.equal(result.routing_eligible, false);
  assert.equal(result.privileges_active, false);
  assert.equal(result.production_enforcement_active, false);
  assert.equal(Object.isFrozen(result), true);
  const serialized = JSON.stringify(result);
  for (const hidden of [token.token_id, token.signature, issuer.issuer_public_key])
    assert.equal(serialized.includes(hidden), false);
});

test("closed schemas refuse caller clocks, authority claims, and extra privilege fields", () => {
  const { token, issuer } = fixture();
  assert.throws(() => scacToken.verifyCapabilityToken({ token, issuer, now: token.issued_at }),
    /scac_token_verification_request_open_or_incomplete/);
  for (const field of ["authorized", "privileges", "routing_eligible"]) {
    const changed = clone(token); changed[field] = true;
    assert.throws(() => scacToken.verifyCapabilityToken({ token: changed, issuer }),
      /scac_token_open_or_incomplete/);
  }
  const changedIssuer = clone(issuer); changedIssuer.root_private_key = "forbidden";
  assert.throws(() => scacToken.verifyCapabilityToken({ token, issuer: changedIssuer }),
    /scac_token_issuer_open_or_incomplete/);
});

test("every exact request and authority binding is signed", () => {
  const { token, issuer } = fixture();
  for (const field of ["principal_digest", "device_key_digest", "facts_digest", "registry_digest",
    "policy_epoch_digest", "operation_manifest_digest", "request_digest", "idempotency_digest",
    "challenge_digest", "issuer_root_event_digest"]) {
    const changed = clone(token); changed[field] = digest(`substitute:${field}`);
    assert.throws(() => scacToken.verifyCapabilityToken({ token: changed, issuer }),
      /binding_mismatch|payload_digest_mismatch/);
  }
  for (const field of ["device_ref", "registry_version", "ingress_key", "mutation_kind",
    "target_surface", "policy_epoch"]) {
    const changed = clone(token);
    changed[field] = field === "policy_epoch" ? 8 : `${token[field]}-substitute`;
    assert.throws(() => scacToken.verifyCapabilityToken({ token: changed, issuer }),
      /malformed|payload_digest_mismatch/);
  }
});

test("issuer substitution, signature tampering, malformed encodings, and excessive TTL fail closed", () => {
  const { token, issuer } = fixture();
  const changedIssuer = clone(issuer);
  changedIssuer.issuer_public_key = Buffer.alloc(32, 9).toString("base64");
  assert.throws(() => scacToken.verifyCapabilityToken({ token, issuer: changedIssuer }),
    /issuer_binding_mismatch/);
  const changedSignature = clone(token);
  changedSignature.signature = Buffer.alloc(64).toString("base64");
  changedSignature.signature_digest = digest(Buffer.alloc(64));
  assert.throws(() => scacToken.verifyCapabilityToken({ token: changedSignature, issuer }),
    /signature_invalid/);
  const bad = clone(token); bad.signature = "AA==";
  assert.throws(() => scacToken.verifyCapabilityToken({ token: bad, issuer }), /signature_malformed/);
  const long = clone(token); long.expires_at = "2026-08-27T12:05:01Z";
  assert.throws(() => scacToken.verifyCapabilityToken({ token: long, issuer }), /ttl_invalid/);
});

test("migration exposes only typed non-authorizing verbs and preserves Joe-only global control", () => {
  assert.match(migration, /create table ops\.scac_pop_challenge/i);
  assert.match(migration, /create table ops\.scac_pop_challenge_consumption/i);
  assert.match(migration, /create table ops\.scac_capability_token_receipt/i);
  assert.match(migration, /create table ops\.scac_token_revocation_event/i);
  assert.match(migration, /create table ops\.scac_token_kill_switch_event/i);
  assert.match(migration, /session_user<>'carr_authority_joe'/i);
  assert.match(migration, /expires_at<=v_now/i);
  assert.match(migration, /on conflict \(challenge_id\) do nothing/i);
  assert.match(migration, /admission_state','ineligible_pending_siep18'/i);
  assert.match(migration, /production_enforcement_active',false/i);
  assert.doesNotMatch(migration, /grant\s+(?:insert|update|delete|truncate).*scac_/i);
  assert.doesNotMatch(migration, /authorized['"]?\s*[:,=]\s*(?:true|1)/i);
  assert.doesNotMatch(migration, /^\s*(?:begin|commit)\s*;/im);
});

test("SIEP-17 exports no signing, write-admission, grant, routing, or Production activation verb", () => {
  const exported = Object.keys(scacToken);
  for (const forbidden of ["sign", "admit", "authorize", "grant", "route", "activate"])
    assert.equal(exported.some(name => name.toLowerCase().includes(forbidden)), false, forbidden);
  assert.equal(scacToken.SCAC_TOKEN_CONTRACT.admission_authority,
    "siep18-atomic-database-reference-monitor");
});

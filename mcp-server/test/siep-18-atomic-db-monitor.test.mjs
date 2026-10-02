import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import { digest } from "../src/artifact-trust.js";
import {
  SCAC_REFERENCE_MONITOR_CONTRACT,
  buildOperationManifest,
  monitorAdmissionQuery,
} from "../src/scac-reference-monitor.js";

const migration = fs.readFileSync(
  new URL("../../migrations/0467_siep18_atomic_db_monitor_grants.sql", import.meta.url), "utf8");

function fixture() {
  const base = {
    schema_version: "scac-token-verification.v1",
    token_ref_digest: digest("token"), signed_payload_digest: digest("payload"),
    challenge_digest: digest("challenge"), principal_digest: digest("principal"),
    device_ref: "joe-studio-pending", device_key_digest: digest("device-key"),
    facts_digest: digest("facts"), workload_digest: null,
    registry_version: "scac-mutation-registry.v9", registry_digest: digest("registry-v9"),
    ingress_key: "mcp-tool:add-loop", mutation_kind: "scac.mutation.business_record",
    target_surface: "scac.surface.database", policy_epoch: 9,
    policy_epoch_digest: digest("epoch-9"), operation_manifest_digest: null,
    request_digest: null, idempotency_digest: digest("idempotency"),
    token_state: "valid_cryptography_unchecked_database_state",
    kill_switch_state: "unverified_pending_database_readback",
    revocation_state: "unverified_pending_database_readback",
    issuer_trust_state: "unverified_pending_siep18_transaction_bridge",
    admission_state: "ineligible_pending_siep18_atomic_database_reference_monitor",
    privilege_state: "none", routing_eligible: false, privileges_active: false,
    production_enforcement_active: false,
  };
  const requestPayloadDigest = digest({ loop: "bounded" });
  const effectKeys = [
    "db-relation-acl:public.event:carr_writer:insert",
    "db-relation-acl:public.loop_item:carr_writer:insert",
  ];
  const manifest = {
    domain: "CARR-SCAC-OPERATION-V1", schema_version: "scac-operation-manifest.v1",
    tenant_scope: "carr-internal", environment: "source-test",
    principal_digest: base.principal_digest, device_ref: base.device_ref,
    device_key_digest: base.device_key_digest, facts_digest: base.facts_digest,
    workload_digest: null, registry_version: base.registry_version,
    registry_digest: base.registry_digest, ingress_key: base.ingress_key,
    mutation_kind: base.mutation_kind, target_surface: base.target_surface,
    policy_epoch: base.policy_epoch, policy_epoch_digest: base.policy_epoch_digest,
    request_payload_digest: requestPayloadDigest, idempotency_digest: base.idempotency_digest,
    effect_keys: [...effectKeys].sort(),
  };
  base.operation_manifest_digest = digest(manifest);
  base.request_digest = base.operation_manifest_digest;
  return { verification: base, requestPayloadDigest, effectKeys, manifest };
}

test("closed operation manifest binds the verified token and sorted exact effects", () => {
  const value = fixture();
  const result = buildOperationManifest(value);
  assert.deepEqual(result.manifest, value.manifest);
  assert.equal(result.operation_manifest_digest, digest(value.manifest));
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.manifest), true);
  assert.equal(JSON.stringify(result).includes("bounded"), false);
});

test("caller authority, duplicate effects, scope substitution, and token reuse fail closed", () => {
  const value = fixture();
  assert.throws(() => buildOperationManifest({ ...value,
    verification: { ...value.verification, authorized: true } }), /open_or_incomplete/);
  assert.throws(() => buildOperationManifest({ ...value,
    effectKeys: [value.effectKeys[0], value.effectKeys[0]] }), /duplicate/);
  assert.throws(() => buildOperationManifest({ ...value,
    verification: { ...value.verification, ingress_key: "mcp-tool:other" } }), /binding_mismatch/);
  assert.throws(() => buildOperationManifest({ ...value,
    requestPayloadDigest: digest("different-request") }), /binding_mismatch/);
  assert.throws(() => buildOperationManifest({ ...value,
    verification: { ...value.verification, kill_switch_state: "inactive" } }),
    /not_non_authorizing/);
  assert.throws(() => buildOperationManifest({ ...value,
    verification: { ...value.verification, device_ref: "../unbound-device" } }),
    /scope_malformed/);
  assert.throws(() => buildOperationManifest({ ...value,
    verification: { ...value.verification, registry_version: "latest" } }),
    /scope_malformed/);
  assert.throws(() => buildOperationManifest({ ...value,
    effectKeys: ["db-column-acl:public.loop_item:carr_writer:insert"] }),
    /effect_keys_malformed/);
});

test("typed admission query carries only token, closed manifest, and one idempotency key", () => {
  const value = fixture();
  const built = buildOperationManifest(value);
  const query = monitorAdmissionQuery({ tokenRefDigest: value.verification.token_ref_digest,
    manifest: built.manifest,
    admissionIdempotencyKey: "0198ed30-1800-7000-8000-000000000018" });
  assert.equal(query.text, "select ops.scac_admit_mutation($1,$2::jsonb,$3::uuid) as admission");
  assert.equal(query.values.length, 3);
  assert.deepEqual(JSON.parse(query.values[1]), built.manifest);
  assert.equal(JSON.stringify(query).includes("signature"), false);
});

test("0467 owns same-transaction DB enforcement and never activates Production", () => {
  for (const fragment of [
    /create table ops\.scac_token_issuer_binding/i,
    /create table ops\.scac_token_verification_binding/i,
    /create table ops\.scac_operation_effect_binding/i,
    /create table ops\.scac_reference_monitor_receipt/i,
    /create or replace function ops\.scac_admit_mutation/i,
    /pg_backend_pid\(\)/i, /txid_current\(\)/i,
    /set_config\('carr\.scac_admission_id'/i,
    /create trigger scac_reference_monitor_guard_row/i,
    /t\.tgfoid='ops\.scac_reference_monitor_guard\(\)'::regprocedure/i,
    /db-column-acl:/i, /db-relation-acl:/i,
    /studio_benchmark_count<>9/i,
    /scac\.refusal\.device_benchmark_incomplete/i,
    /scac\.refusal\.scope_violation: SIEP-18 exact operation effect set mismatch/i,
    /SIEP-18 shadow is non-authorizing/i,
    /SIEP-18 unknown DML principal/i,
    /session_user<>'carr_authority_joe'/i,
    /mode in \('shadow','enforced_source_test'\)/i,
  ]) assert.match(migration, fragment);
  assert.doesNotMatch(migration, /mode in \([^)]*production/i);
  assert.doesNotMatch(migration, /production_enforcement_active\s+boolean[^;]+check\s*\(production_enforcement_active\)/is);
  assert.doesNotMatch(migration, /^\s*(?:begin|commit)\s*;/im);
  assert.match(migration,
    /if not exists\([\s\S]+create trigger scac_reference_monitor_guard_row/i);
  assert.equal(SCAC_REFERENCE_MONITOR_CONTRACT.production_enforcement_active, false);
});

test("raw control ledgers stay private and role verbs remain least privilege", () => {
  assert.match(migration, /revoke all on table ops\.scac_token_issuer_binding[\s\S]+from public,carr_reader,carr_writer,carr_jobs,carr_authority/i);
  assert.match(migration, /grant execute on function ops\.scac_record_token_verification_binding\(text,jsonb,uuid\)\s+to carr_jobs/i);
  assert.match(migration, /grant execute on function ops\.scac_register_token_issuer_binding[\s\S]+to carr_authority/i);
  assert.match(migration, /offline root key cannot be the online token issuer/i);
  assert.doesNotMatch(migration, /private_key|secret_key|raw_payload|request_payload\s+jsonb/i);
});

test("SIEP-17 remains cryptographic-only while exposing the device binding to SIEP-18", async () => {
  const source = await fs.promises.readFile(new URL("../src/scac-token.js", import.meta.url), "utf8");
  assert.match(source, /device_ref:\s*token\.device_ref/);
  assert.match(source, /admission_state:\s*"ineligible_pending_siep18_atomic_database_reference_monitor"/);
  assert.match(source, /privileges_active:\s*false/);
  assert.doesNotMatch(source, /function\s+(?:admit|authorize|grant|activate)/i);
});

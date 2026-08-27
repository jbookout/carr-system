import { createPublicKey, verify } from "node:crypto";

import { digest } from "./artifact-trust.js";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const RFC3339_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const REGISTRY_VERSION = /^scac-mutation-registry\.v[1-9]\d*$/;
const IDENTIFIER = /^[a-z][a-z0-9._:-]{0,255}$/;
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

export const SCAC_TOKEN_CONTRACT = Object.freeze({
  domain: "CARR-SCAC-TOKEN-V1",
  token_schema: "scac-capability-token.v1",
  issuer_schema: "scac-token-issuer.v1",
  verification_schema: "scac-token-verification.v1",
  algorithm: "ed25519",
  maximum_ttl_seconds: 300,
  database_status_contract: "siep17-token-control-snapshot.v1",
  admission_authority: "siep18-atomic-database-reference-monitor",
  authority_state: "cryptographic_verifier_only_non_authorizing",
});

function exact(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new TypeError(`${label}_malformed`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new TypeError(`${label}_open_or_incomplete`);
}

function bytes(value, length, label) {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4)
    throw new TypeError(`${label}_malformed`);
  const result = Buffer.from(value, "base64");
  if (result.length !== length || result.toString("base64") !== value)
    throw new TypeError(`${label}_malformed`);
  return result;
}

function timestamp(value, label) {
  if (typeof value !== "string" || !RFC3339_UTC.test(value) || !Number.isFinite(Date.parse(value)))
    throw new TypeError(`${label}_malformed`);
  const normalized = new Date(value).toISOString();
  if (normalized !== value && normalized.replace(".000Z", "Z") !== value)
    throw new TypeError(`${label}_malformed`);
  return Date.parse(value);
}

function assertDigest(value, label) {
  if (!SHA256.test(value || "")) throw new TypeError(`${label}_malformed`);
}

function assertIdentifier(value, label) {
  if (!IDENTIFIER.test(value || "")) throw new TypeError(`${label}_malformed`);
}

const TOKEN_KEYS = [
  "schema_version", "token_id", "tenant_scope", "environment", "principal_digest",
  "device_ref", "device_key_digest", "facts_digest", "workload_digest", "registry_version",
  "registry_digest", "ingress_key", "mutation_kind", "target_surface", "policy_epoch",
  "policy_epoch_digest", "operation_manifest_digest", "request_digest", "idempotency_digest",
  "challenge_digest", "issuer_key_digest", "issuer_root_event_digest", "issued_at", "expires_at",
  "signed_payload_digest", "signature", "signature_digest",
];

export function tokenPayload(token) {
  return {
    domain: SCAC_TOKEN_CONTRACT.domain,
    schema_version: token.schema_version,
    token_id: token.token_id,
    tenant_scope: token.tenant_scope,
    environment: token.environment,
    principal_digest: token.principal_digest,
    device_ref: token.device_ref,
    device_key_digest: token.device_key_digest,
    facts_digest: token.facts_digest,
    workload_digest: token.workload_digest,
    registry_version: token.registry_version,
    registry_digest: token.registry_digest,
    ingress_key: token.ingress_key,
    mutation_kind: token.mutation_kind,
    target_surface: token.target_surface,
    policy_epoch: token.policy_epoch,
    policy_epoch_digest: token.policy_epoch_digest,
    operation_manifest_digest: token.operation_manifest_digest,
    request_digest: token.request_digest,
    idempotency_digest: token.idempotency_digest,
    challenge_digest: token.challenge_digest,
    issuer_key_digest: token.issuer_key_digest,
    issuer_root_event_digest: token.issuer_root_event_digest,
    issued_at: token.issued_at,
    expires_at: token.expires_at,
  };
}

export function tokenPayloadDigest(token) {
  exact(token, TOKEN_KEYS, "scac_token");
  if (token.schema_version !== SCAC_TOKEN_CONTRACT.token_schema ||
      !UUID.test(token.token_id || "") || token.tenant_scope !== "carr-internal" ||
      token.environment !== "source-test" || !REGISTRY_VERSION.test(token.registry_version || "") ||
      !Number.isSafeInteger(token.policy_epoch) || token.policy_epoch <= 0)
    throw new TypeError("scac_token_value_malformed");
  assertIdentifier(token.device_ref, "scac_token_device_ref");
  if (typeof token.ingress_key !== "string" || token.ingress_key.length < 3 ||
      token.ingress_key.length > 1000 || /[\n\r\t]/.test(token.ingress_key))
    throw new TypeError("scac_token_ingress_key_malformed");
  if (!/^scac\.mutation\.[a-z_]+$/.test(token.mutation_kind || "") ||
      !/^scac\.surface\.[a-z_]+$/.test(token.target_surface || ""))
    throw new TypeError("scac_token_scope_malformed");
  for (const [value, label] of [
    [token.principal_digest, "principal_digest"], [token.device_key_digest, "device_key_digest"],
    [token.facts_digest, "facts_digest"], [token.registry_digest, "registry_digest"],
    [token.policy_epoch_digest, "policy_epoch_digest"],
    [token.operation_manifest_digest, "operation_manifest_digest"],
    [token.request_digest, "request_digest"], [token.idempotency_digest, "idempotency_digest"],
    [token.challenge_digest, "challenge_digest"], [token.issuer_key_digest, "issuer_key_digest"],
    [token.issuer_root_event_digest, "issuer_root_event_digest"],
    [token.signed_payload_digest, "signed_payload_digest"],
    [token.signature_digest, "signature_digest"],
  ]) assertDigest(value, label);
  if (token.workload_digest !== null) assertDigest(token.workload_digest, "workload_digest");
  if (token.request_digest !== token.operation_manifest_digest)
    throw new Error("scac_token_request_manifest_binding_mismatch");
  const issuedAt = timestamp(token.issued_at, "scac_token_issued_at");
  const expiresAt = timestamp(token.expires_at, "scac_token_expires_at");
  const ttl = (expiresAt - issuedAt) / 1000;
  if (ttl <= 0 || ttl > SCAC_TOKEN_CONTRACT.maximum_ttl_seconds)
    throw new Error("scac_token_ttl_invalid");
  return digest(tokenPayload(token));
}

export function verifyCapabilityToken(input) {
  exact(input, ["token", "issuer"], "scac_token_verification_request");
  const { token, issuer } = input;
  exact(issuer, ["schema_version", "algorithm", "issuer_key_digest", "issuer_public_key",
    "issuer_root_event_digest"], "scac_token_issuer");
  if (issuer.schema_version !== SCAC_TOKEN_CONTRACT.issuer_schema ||
      issuer.algorithm !== SCAC_TOKEN_CONTRACT.algorithm)
    throw new TypeError("scac_token_issuer_value_malformed");
  assertDigest(issuer.issuer_key_digest, "issuer_key_digest");
  assertDigest(issuer.issuer_root_event_digest, "issuer_root_event_digest");
  const publicKey = bytes(issuer.issuer_public_key, 32, "scac_token_issuer_public_key");
  if (digest(publicKey) !== issuer.issuer_key_digest ||
      issuer.issuer_key_digest !== token.issuer_key_digest ||
      issuer.issuer_root_event_digest !== token.issuer_root_event_digest)
    throw new Error("scac_token_issuer_binding_mismatch");
  const payloadDigest = tokenPayloadDigest(token);
  if (payloadDigest !== token.signed_payload_digest)
    throw new Error("scac_token_payload_digest_mismatch");
  const signature = bytes(token.signature, 64, "scac_token_signature");
  if (digest(signature) !== token.signature_digest)
    throw new Error("scac_token_signature_digest_mismatch");
  const key = createPublicKey({
    key: Buffer.concat([ED25519_SPKI_PREFIX, publicKey]), format: "der", type: "spki",
  });
  if (!verify(null, Buffer.from(payloadDigest, "utf8"), key, signature))
    throw new Error("scac_token_signature_invalid");
  const tokenRefDigest = digest({
    schema_version: token.schema_version,
    signed_payload_digest: payloadDigest,
    signature_digest: token.signature_digest,
  });
  return Object.freeze({
    schema_version: SCAC_TOKEN_CONTRACT.verification_schema,
    token_ref_digest: tokenRefDigest,
    signed_payload_digest: payloadDigest,
    challenge_digest: token.challenge_digest,
    principal_digest: token.principal_digest,
    device_key_digest: token.device_key_digest,
    facts_digest: token.facts_digest,
    workload_digest: token.workload_digest,
    registry_version: token.registry_version,
    registry_digest: token.registry_digest,
    ingress_key: token.ingress_key,
    mutation_kind: token.mutation_kind,
    target_surface: token.target_surface,
    policy_epoch: token.policy_epoch,
    policy_epoch_digest: token.policy_epoch_digest,
    operation_manifest_digest: token.operation_manifest_digest,
    request_digest: token.request_digest,
    idempotency_digest: token.idempotency_digest,
    token_state: "valid_cryptography_unchecked_database_state",
    kill_switch_state: "unverified_pending_database_readback",
    revocation_state: "unverified_pending_database_readback",
    issuer_trust_state: "unverified_pending_siep18_transaction_bridge",
    admission_state: "ineligible_pending_siep18_atomic_database_reference_monitor",
    privilege_state: "none",
    routing_eligible: false,
    privileges_active: false,
    production_enforcement_active: false,
  });
}

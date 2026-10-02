import { digest } from "./artifact-trust.js";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const INGRESS = /^[a-z][a-z0-9_-]+:[^\n\r\t]{1,980}$/;
const MUTATION_KIND = /^scac\.mutation\.[a-z_]+$/;
const TARGET_SURFACE = /^scac\.surface\.[a-z_]+$/;
const DEVICE_REF = /^[a-z0-9][a-z0-9._-]{2,127}$/;
const REGISTRY_VERSION = /^scac-mutation-registry\.v[1-9]\d*$/;
const EFFECT = /^(?:db-relation-acl:[^:]+:(?:carr_writer|carr_jobs|carr_authority):(?:insert|update|delete|truncate)|db-column-acl:[^:]+:(?:carr_writer|carr_jobs|carr_authority):update)$/;

export const SCAC_REFERENCE_MONITOR_CONTRACT = Object.freeze({
  domain: "CARR-SCAC-OPERATION-V1",
  manifest_schema: "scac-operation-manifest.v1",
  database_function: "ops.scac_admit_mutation(text,jsonb,uuid)",
  admission_sql: "select ops.scac_admit_mutation($1,$2::jsonb,$3::uuid) as admission",
  enforcement_modes: Object.freeze(["shadow", "enforced_source_test"]),
  production_enforcement_active: false,
});

function exact(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new TypeError(`${label}_malformed`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new TypeError(`${label}_open_or_incomplete`);
}

function exactDigest(value, label) {
  if (!SHA256.test(value || "")) throw new TypeError(`${label}_malformed`);
  return value;
}

function nullableDigest(value, label) {
  if (value === null) return null;
  return exactDigest(value, label);
}

const VERIFICATION_KEYS = [
  "schema_version", "token_ref_digest", "signed_payload_digest", "challenge_digest",
  "principal_digest", "device_ref", "device_key_digest", "facts_digest", "workload_digest",
  "registry_version", "registry_digest", "ingress_key", "mutation_kind", "target_surface",
  "policy_epoch", "policy_epoch_digest", "operation_manifest_digest", "request_digest",
  "idempotency_digest", "token_state", "kill_switch_state", "revocation_state",
  "issuer_trust_state", "admission_state", "privilege_state", "routing_eligible",
  "privileges_active", "production_enforcement_active",
];

export function buildOperationManifest({ verification, requestPayloadDigest, effectKeys }) {
  exact(verification, VERIFICATION_KEYS, "scac_verified_token");
  if (verification.schema_version !== "scac-token-verification.v1" ||
      verification.token_state !== "valid_cryptography_unchecked_database_state" ||
      verification.kill_switch_state !== "unverified_pending_database_readback" ||
      verification.revocation_state !== "unverified_pending_database_readback" ||
      verification.issuer_trust_state !== "unverified_pending_siep18_transaction_bridge" ||
      verification.admission_state !== "ineligible_pending_siep18_atomic_database_reference_monitor" ||
      verification.privilege_state !== "none" || verification.routing_eligible !== false ||
      verification.privileges_active !== false || verification.production_enforcement_active !== false)
    throw new Error("scac_verified_token_not_non_authorizing");
  if (!Number.isSafeInteger(verification.policy_epoch) || verification.policy_epoch <= 0 ||
      !DEVICE_REF.test(verification.device_ref || "") ||
      !REGISTRY_VERSION.test(verification.registry_version || "") ||
      !INGRESS.test(verification.ingress_key || "") ||
      !MUTATION_KIND.test(verification.mutation_kind || "") ||
      !TARGET_SURFACE.test(verification.target_surface || ""))
    throw new TypeError("scac_verified_token_scope_malformed");
  for (const [value, label] of [
    [verification.token_ref_digest, "token_ref_digest"],
    [verification.signed_payload_digest, "signed_payload_digest"],
    [verification.challenge_digest, "challenge_digest"],
    [verification.principal_digest, "principal_digest"],
    [verification.device_key_digest, "device_key_digest"],
    [verification.facts_digest, "facts_digest"],
    [verification.registry_digest, "registry_digest"],
    [verification.policy_epoch_digest, "policy_epoch_digest"],
    [verification.operation_manifest_digest, "operation_manifest_digest"],
    [verification.request_digest, "request_digest"],
    [verification.idempotency_digest, "idempotency_digest"],
    [requestPayloadDigest, "request_payload_digest"],
  ]) exactDigest(value, label);
  nullableDigest(verification.workload_digest, "workload_digest");
  if (!Array.isArray(effectKeys) || effectKeys.length === 0 ||
      effectKeys.some(value => typeof value !== "string" || !EFFECT.test(value)))
    throw new TypeError("scac_effect_keys_malformed");
  const effects = [...effectKeys].sort();
  if (new Set(effects).size !== effects.length) throw new TypeError("scac_effect_keys_duplicate");
  const manifest = {
    domain: SCAC_REFERENCE_MONITOR_CONTRACT.domain,
    schema_version: SCAC_REFERENCE_MONITOR_CONTRACT.manifest_schema,
    tenant_scope: "carr-internal",
    environment: "source-test",
    principal_digest: verification.principal_digest,
    device_ref: verification.device_ref,
    device_key_digest: verification.device_key_digest,
    facts_digest: verification.facts_digest,
    workload_digest: verification.workload_digest,
    registry_version: verification.registry_version,
    registry_digest: verification.registry_digest,
    ingress_key: verification.ingress_key,
    mutation_kind: verification.mutation_kind,
    target_surface: verification.target_surface,
    policy_epoch: verification.policy_epoch,
    policy_epoch_digest: verification.policy_epoch_digest,
    request_payload_digest: requestPayloadDigest,
    idempotency_digest: verification.idempotency_digest,
    effect_keys: effects,
  };
  const operationManifestDigest = digest(manifest);
  if (operationManifestDigest !== verification.operation_manifest_digest ||
      operationManifestDigest !== verification.request_digest)
    throw new Error("scac_operation_manifest_token_binding_mismatch");
  return Object.freeze({ manifest: Object.freeze(manifest), operation_manifest_digest: operationManifestDigest });
}

export function monitorAdmissionQuery({ tokenRefDigest, manifest, admissionIdempotencyKey }) {
  exactDigest(tokenRefDigest, "token_ref_digest");
  if (typeof admissionIdempotencyKey !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(admissionIdempotencyKey))
    throw new TypeError("admission_idempotency_key_malformed");
  exact(manifest, ["domain", "schema_version", "tenant_scope", "environment",
    "principal_digest", "device_ref", "device_key_digest", "facts_digest", "workload_digest",
    "registry_version", "registry_digest", "ingress_key", "mutation_kind", "target_surface",
    "policy_epoch", "policy_epoch_digest", "request_payload_digest", "idempotency_digest",
    "effect_keys"], "scac_operation_manifest");
  return Object.freeze({
    text: SCAC_REFERENCE_MONITOR_CONTRACT.admission_sql,
    values: Object.freeze([tokenRefDigest, JSON.stringify(manifest), admissionIdempotencyKey]),
  });
}

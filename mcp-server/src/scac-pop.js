import { createPublicKey, verify } from "node:crypto";

import { digest } from "./artifact-trust.js";
import { verifyDeviceFactEnvelope } from "./device-enrollment.js";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const RFC3339_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

export const SCAC_POP_CONTRACT = Object.freeze({
  domain: "CARR-SCAC-POP-V1",
  challenge_schema: "scac-pop-challenge.v1",
  proof_schema: "scac-pop-proof.v1",
  verification_schema: "scac-pop-verification.v1",
  algorithm: "ed25519",
  maximum_ttl_seconds: 300,
  freshness_authority_contract: "siep17-atomic-challenge-readback.v1",
  expiry_semantics: "exclusive_at_siep17_atomic_consumption",
  authority_state: "verifier_only_pending_siep17_atomic_consumption",
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

export function popChallengePayload(challenge) {
  return {
    domain: SCAC_POP_CONTRACT.domain,
    schema_version: SCAC_POP_CONTRACT.challenge_schema,
    challenge_id: challenge.challenge_id,
    device_ref: challenge.device_ref,
    device_key_digest: challenge.device_key_digest,
    facts_digest: challenge.facts_digest,
    policy_epoch: challenge.policy_epoch,
    policy_epoch_digest: challenge.policy_epoch_digest,
    operation_manifest_digest: challenge.operation_manifest_digest,
    nonce: challenge.nonce,
    issued_at: challenge.issued_at,
    expires_at: challenge.expires_at,
  };
}

export function popChallengeDigest(challenge) {
  exact(challenge, ["schema_version", "challenge_id", "device_ref", "device_key_digest",
    "facts_digest", "policy_epoch", "policy_epoch_digest", "operation_manifest_digest", "nonce",
    "issued_at", "expires_at"], "pop_challenge");
  if (challenge.schema_version !== SCAC_POP_CONTRACT.challenge_schema ||
      !UUID.test(challenge.challenge_id || "") ||
      !Number.isSafeInteger(challenge.policy_epoch) || challenge.policy_epoch <= 0)
    throw new TypeError("pop_challenge_value_malformed");
  for (const [value, label] of [[challenge.device_key_digest, "device_key_digest"],
    [challenge.facts_digest, "facts_digest"], [challenge.policy_epoch_digest, "policy_epoch_digest"],
    [challenge.operation_manifest_digest, "operation_manifest_digest"]]) assertDigest(value, label);
  bytes(challenge.nonce, 32, "pop_challenge_nonce");
  const issuedAt = timestamp(challenge.issued_at, "pop_challenge_issued_at");
  const expiresAt = timestamp(challenge.expires_at, "pop_challenge_expires_at");
  const ttl = (expiresAt - issuedAt) / 1000;
  if (ttl <= 0 || ttl > SCAC_POP_CONTRACT.maximum_ttl_seconds)
    throw new Error("pop_challenge_ttl_invalid");
  return digest(popChallengePayload(challenge));
}

export function verifyProofOfPossession(input) {
  exact(input, ["challenge", "proof", "enrollment"], "pop_verification_request");
  const { challenge, proof, enrollment } = input;
  const enrollmentState = verifyDeviceFactEnvelope(enrollment);
  exact(proof, ["schema_version", "challenge_id", "signed_payload_digest", "signature",
    "signature_digest"], "pop_proof");
  const challengeDigest = popChallengeDigest(challenge);
  for (const key of ["device_ref", "device_key_digest", "facts_digest", "policy_epoch",
    "policy_epoch_digest"]) {
    if (challenge[key] !== enrollment[key]) throw new Error("pop_enrollment_binding_mismatch");
  }
  if (proof.schema_version !== SCAC_POP_CONTRACT.proof_schema ||
      proof.challenge_id !== challenge.challenge_id ||
      proof.signed_payload_digest !== challengeDigest)
    throw new Error("pop_proof_contract_mismatch");
  const publicKey = bytes(enrollment.device_public_key, 32, "pop_device_public_key");
  const signature = bytes(proof.signature, 64, "pop_signature");
  if (digest(signature) !== proof.signature_digest) throw new Error("pop_signature_digest_mismatch");
  const key = createPublicKey({
    key: Buffer.concat([ED25519_SPKI_PREFIX, publicKey]), format: "der", type: "spki",
  });
  if (!verify(null, Buffer.from(challengeDigest, "utf8"), key, signature))
    throw new Error("pop_signature_invalid");
  return Object.freeze({
    schema_version: SCAC_POP_CONTRACT.verification_schema,
    challenge_digest: challengeDigest,
    request_digest: challenge.operation_manifest_digest,
    operation_manifest_digest: challenge.operation_manifest_digest,
    device_ref: enrollmentState.device_ref,
    device_key_digest: enrollment.device_key_digest,
    facts_digest: enrollmentState.facts_digest,
    policy_epoch: enrollment.policy_epoch,
    policy_epoch_digest: enrollment.policy_epoch_digest,
    proof_state: "valid",
    verification_authority_state: "cryptographically_valid_non_authorizing",
    freshness_state: "unverified_pending_siep17_atomic_consumption",
    atomic_consumption_state: "required_pending_siep17",
    token_state: "none_pending_siep17",
    privilege_state: "none",
    routing_eligible: false,
    privileges_active: false,
    production_enforcement_active: false,
  });
}

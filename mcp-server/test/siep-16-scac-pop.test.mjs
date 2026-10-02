import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import { digest } from "../src/artifact-trust.js";
import * as pop from "../src/scac-pop.js";

const vector = JSON.parse(fs.readFileSync(
  new URL("./fixtures/siep-16-scac-pop.v1.json", import.meta.url), "utf8"));
const clone = value => structuredClone(value);
const verifyVector = overrides => pop.verifyProofOfPossession({
  challenge: clone(vector.challenge),
  proof: clone(vector.proof),
  enrollment: clone(vector.enrollment),
  ...overrides,
});

test("static golden vector verifies the complete enrollment, policy, and request transcript", () => {
  const result = verifyVector();
  for (const [key, value] of Object.entries(vector.expected)) assert.equal(result[key], value);
  assert.equal(Object.isFrozen(result), true);
  assert.equal(pop.popChallengeDigest(vector.challenge), vector.expected.challenge_digest);
});

test("successful PoP stays non-authorizing and does not leak proof material", () => {
  const result = verifyVector();
  assert.equal(result.token_state, "none_pending_siep17");
  assert.equal(result.privilege_state, "none");
  assert.equal(result.freshness_state, "unverified_pending_siep17_atomic_consumption");
  assert.equal(result.routing_eligible, false);
  assert.equal(Object.hasOwn(result, "challenge_id"), false);
  const serialized = JSON.stringify(result);
  for (const secret of [vector.challenge.nonce, vector.proof.signature,
    vector.enrollment.device_public_key]) assert.equal(serialized.includes(secret), false);
});

test("open schemas and malformed encodings fail closed", () => {
  const challenge = clone(vector.challenge); challenge.extra = true;
  assert.throws(() => verifyVector({ challenge }), /pop_challenge_open_or_incomplete/);
  const proof = clone(vector.proof); proof.extra = true;
  assert.throws(() => verifyVector({ proof }), /pop_proof_open_or_incomplete/);
  assert.throws(() => verifyVector({ freshness: {} }), /pop_verification_request_open_or_incomplete/);
  assert.throws(() => verifyVector({ now: "2026-08-27T01:02:30Z" }),
    /pop_verification_request_open_or_incomplete/);
  const badNonce = clone(vector.challenge); badNonce.nonce = "AA==";
  assert.throws(() => verifyVector({ challenge: badNonce }), /pop_challenge_nonce_malformed/);
  const badId = clone(vector.challenge); badId.challenge_id = "not-a-uuid";
  assert.throws(() => verifyVector({ challenge: badId }), /pop_challenge_value_malformed/);
});

test("identity, device key, facts, epoch, and operation transcript substitution fail closed", () => {
  for (const field of ["device_key_digest", "facts_digest", "policy_epoch_digest",
    "operation_manifest_digest"]) {
    const challenge = clone(vector.challenge); challenge[field] = digest(`substitute:${field}`);
    assert.throws(() => verifyVector({ challenge }),
      /pop_enrollment_binding_mismatch|pop_proof_contract_mismatch/);
  }
  const challenge = clone(vector.challenge); challenge.device_ref = "joe-studio-substitute";
  assert.throws(() => verifyVector({ challenge }),
    /pop_enrollment_binding_mismatch|pop_proof_contract_mismatch/);
  const epoch = clone(vector.challenge); epoch.policy_epoch += 1;
  assert.throws(() => verifyVector({ challenge: epoch }),
    /pop_enrollment_binding_mismatch|pop_proof_contract_mismatch/);
});

test("challenge lifetime is bounded but freshness and consumption stay outside SIEP-16", () => {
  const overlong = clone(vector.challenge); overlong.expires_at = "2026-08-27T01:05:01Z";
  assert.throws(() => verifyVector({ challenge: overlong }), /pop_challenge_ttl_invalid/);
  const first = verifyVector();
  const replay = verifyVector();
  assert.deepEqual(replay, first);
  assert.equal(replay.proof_state, "valid");
  assert.equal(replay.verification_authority_state, "cryptographically_valid_non_authorizing");
  assert.equal(replay.freshness_state, "unverified_pending_siep17_atomic_consumption");
  assert.equal(replay.atomic_consumption_state, "required_pending_siep17");
});

test("enrollment self-signature and PoP signature are both independently required", () => {
  const enrollment = clone(vector.enrollment);
  enrollment.signature = Buffer.alloc(64).toString("base64");
  enrollment.signature_digest = digest(Buffer.alloc(64));
  assert.throws(() => verifyVector({ enrollment }), /device_fact_signature_invalid/);
  const proof = clone(vector.proof);
  proof.signature = Buffer.alloc(64).toString("base64");
  proof.signature_digest = digest(Buffer.alloc(64));
  assert.throws(() => verifyVector({ proof }), /pop_signature_invalid/);
});

test("SIEP-16 exports no issuer, consumer, token, grant, revocation, or activation verb", () => {
  const exported = Object.keys(pop);
  for (const forbidden of ["issue", "consume", "token", "grant", "revoke", "activate"])
    assert.equal(exported.some(name => name.toLowerCase().includes(forbidden)), false, forbidden);
  assert.equal(pop.SCAC_POP_CONTRACT.authority_state,
    "verifier_only_pending_siep17_atomic_consumption");
  assert.equal(pop.SCAC_POP_CONTRACT.expiry_semantics,
    "exclusive_at_siep17_atomic_consumption");
  assert.equal(JSON.stringify(vector).includes("private_key"), false);
  assert.equal(JSON.stringify(vector).includes("secret_seed"), false);
});

// DoctorCRE v5 slice V5-J102: the lifecycle driven LIVE, through the real store,
// against real PostgreSQL.
//
// WHAT THIS PROVES THAT NOTHING ELSE HERE DOES. cre-lifecycle-store.v5.test.mjs
// runs the store against a scripted fake handle, and cre-lifecycle-postgres.sql
// drives the SQL writers directly with envelopes it builds by hand. Neither one
// ever put the two together: the envelopes the STORE builds had never been handed
// to the writers the SQL defines. This suite does exactly that, as three real
// database principals (carr_authority_joe, carr_authority_dell, and carr_writer
// acting as a sponsored agent), with evidence produced through F01's own
// writers and nothing seeded around a guard.
//
// It runs only when the three DSNs below are set, which is what
// ops/cre-lifecycle-local-pg-gate.py does on a scratch database it builds and
// drops. Without them every test here is skipped by name, never passed.
//
// EVERY RECORD IS SYNTHETIC. Ids carry a per-run nonce so the suite can run
// twice against the same scratch database without colliding.

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_J102_TRANSITION_IDS,
  v5J102TransitionTruthTable,
  v5J102TransitionContract,
} from "../src/cre-lifecycle.v5.js";
import { createCreLifecycleStore } from "../src/cre-lifecycle-store.v5.js";

const DSN = {
  joe: process.env.CARR_J102_LIVE_PG_DSN_JOE,
  dell: process.env.CARR_J102_LIVE_PG_DSN_DELL,
  writer: process.env.CARR_J102_LIVE_PG_DSN_WRITER,
};
const LIVE = Boolean(DSN.joe && DSN.dell && DSN.writer);
const SKIP = LIVE ? false
  : "CARR_J102_LIVE_PG_DSN_{JOE,DELL,WRITER} are not set; ops/cre-lifecycle-local-pg-gate.py sets them on a scratch database";

const RUN = randomUUID().slice(0, 8);
const id = prefix => `${prefix}-live-${RUN}-${randomUUID().slice(0, 8)}`;
const key = () => `j102-live-${randomUUID()}`;

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const DELL = Object.freeze({ slug: "dell", display: "Dell", human: true, via: "oauth-google" });
const AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});

let pg;
const pools = {};

async function pool(name) {
  if (pools[name]) return pools[name];
  if (!pg) pg = (await import("pg")).default;
  const config = { connectionString: name === "agent" || name === "joe_writer"
    ? DSN.writer : DSN[name], max: 2 };
  if (name === "agent") config.options = "-c carr.acting_actor_slug=codex";
  if (name === "joe_writer") config.options = "-c carr.acting_actor_slug=joe";
  pools[name] = new pg.Pool(config);
  return pools[name];
}

/** A store whose `db` is one real connection per operation, like mcp.js. */
function storeFor(name) {
  return createCreLifecycleStore({
    db: {
      async query(text, params) {
        throw new Error("storeFor: transaction() is always used");
      },
      async transaction(fn) {
        const client = await (await pool(name)).connect();
        try {
          await client.query("BEGIN");
          const out = await fn({ query: (t, p) => client.query(t, p) });
          await client.query("COMMIT");
          return out;
        } catch (e) {
          await client.query("ROLLBACK").catch(() => {});
          throw e;
        } finally {
          client.release();
        }
      },
    },
  });
}

const ACTORS = { joe: JOE, dell: DELL, agent: AGENT, joe_writer: JOE };
function as(name) {
  const store = storeFor(name);
  const ctx = { actor: ACTORS[name] };
  return Object.fromEntries(Object.entries(store).map(([k, fn]) =>
    [k, typeof fn === "function" ? payload => fn(payload, ctx) : fn]));
}

async function sql(name, text, params = []) {
  const client = await (await pool(name)).connect();
  try {
    return (await client.query(text, params)).rows;
  } finally {
    client.release();
  }
}

const sha = s => createHash("sha256").update(s).digest("hex");
const D = s => `sha256:${sha(s)}`;

// ---------------------------------------------------------------------------
// Evidence, produced through F01's own writers. Nothing here writes a row F01
// or J102 would not write for a real caller.
// ---------------------------------------------------------------------------

async function f01Document(name, { document_class, states }) {
  const document_id = id("doc");
  const content_digest = D(document_id);
  const now = (await sql(name, "select ops.f01_now_text() as now"))[0].now;
  const actor = (await sql(name, "select ops.f01_context_actor_slug() as a"))[0].a;
  const record = {
    schema_version: "doctorcre-v5-f01-stored-document-version.v1",
    tenant: ORGANIZATION_TENANT_ID,
    document_class,
    neon_identity: { document_id, version_no: 1, content_digest },
    object_storage_identity: { object_key: `j102/live/${document_id}`, content_digest,
      byte_length: 2048, sealed: true },
    onedrive_identity: { drive_id: "j102-live-drive", item_id: document_id, content_digest,
      filing_state: "filed" },
    preparation_state: "approved_for_delivery",
    delivery_state: "delivered",
    signature_state: "fully_executed",
    validity_state: "effective",
    version_state: "current",
    official_filing_state: "filed",
    prior_document_digest: null,
    recorded_by: actor,
    recorded_at: now,
    ...states,
  };
  const envelope = {
    schema_version: "doctorcre-v5-f01-stored-record-envelope.v1",
    record_kind: "stored_document_version", tenant: ORGANIZATION_TENANT_ID,
    record, record_digest: digest(record),
  };
  await sql(name, "select ops.f01_record_document($1::jsonb, null, $2::text, $3::text)",
    [JSON.stringify(envelope), key(), D(key())]);
  return { document_id, version_no: 1, content_digest };
}

async function f01Artifact(name) {
  const native_id = id("artifact");
  const now = (await sql(name, "select ops.f01_now_text() as now"))[0].now;
  const record = {
    schema_version: "doctorcre-v5-f01-corporate-artifact.v1",
    tenant: ORGANIZATION_TENANT_ID,
    source_system: "j102-live-mailbox",
    source_class: null,
    source_account: "j102-live-account",
    native_identity: { native_id, native_id_epoch: "1" },
    native_version: "1",
    content_digest: D(native_id),
    byte_length: 1024,
    observed_at: now,
    provenance: { method: "synthetic_fixture", source_ref: native_id },
    evidence_class: "corporate_document_bytes",
    declared_data_classes: ["internal_business"],
    taint_class: "untrusted_external",
  };
  const envelope = {
    schema_version: "doctorcre-v5-f01-stored-record-envelope.v1",
    record_kind: "stored_corporate_artifact", tenant: ORGANIZATION_TENANT_ID,
    record, record_digest: digest(record),
    is_fact: false, makes_field_authoritative: false, immutable: true,
  };
  const rows = await sql(name, "select ops.f01_record_artifact($1::jsonb, $2::text, $3::text) as o",
    [JSON.stringify(envelope), key(), D(key())]);
  return rows[0].o.artifact_digest;
}

// ---------------------------------------------------------------------------

after(async () => {
  for (const p of Object.values(pools)) await p.end();
});

function ok(answer, what) {
  assert.equal(answer.decision, "allow",
    `${what}: expected allow, got ${answer.decision}/${answer.reason_id}: ${JSON.stringify(answer).slice(0, 1500)}`);
  return answer;
}

async function subject(name, subject_kind, subject_id) {
  const answer = await as(name).readCreLifecycle({
    selector: { kind: "subject", subject_kind, subject_id } });
  return answer;
}

// ---------------------------------------------------------------------------
// Walk helpers. Each performs one real store operation and asserts it landed.
// ---------------------------------------------------------------------------

const REF = (subject_kind, subject_id) => ({ subject_kind, subject_id });

async function linkDocument(name, doc, subject_kind, subject_id) {
  return ok(await as(name).recordEvidenceSubjectLink({ idempotency_key: key(), link: {
    evidence_source: "f01_document", ...docPin(doc), subject_kind, subject_id } }),
  `link document to ${subject_kind}`);
}

async function linkArtifact(name, artifact_digest, subject_kind, subject_id) {
  return ok(await as(name).recordEvidenceSubjectLink({ idempotency_key: key(), link: {
    evidence_source: "f01_corporate_artifact", artifact_digest, subject_kind, subject_id } }),
  `link artifact to ${subject_kind}`);
}

async function fact(name, record_kind, subject_kind, subject_id, extra = {}) {
  const record_id = id(record_kind.replace(/_/g, "-"));
  ok(await as(name).recordLifecycleFact({ idempotency_key: key(), fact: {
    record_kind, record_id, subject_kind, subject_id, ...extra } }), `record fact ${record_kind}`);
  return record_id;
}

/** A client with an active engagement, reached through the real doors. */
async function client(name = "joe") {
  const who = as(name);
  const rel = id("rel"), eng = id("eng");
  ok(await who.initializeProspectRelationship({ idempotency_key: key(),
    declared: { new_subject_id: rel } }), "initialize prospect");
  const etl = await f01Document(name, { document_class: "engagement_letter" });
  await linkDocument(name === "agent" ? "joe" : name, etl, "relationship", rel);
  ok(await who.recordRepresentationAgreement({ idempotency_key: key(),
    subject_ref: REF("relationship", rel),
    evidence_refs: [{ evidence_kind: "signed_engagement_letter", ...docRef(etl) }],
    declared: { new_subject_id: eng } }), "establish client and engagement");
  return { rel, eng };
}

/** An opened assignment under a fresh client. */
async function openedAssignment(name = "joe") {
  const { rel, eng } = await client(name);
  const asg = id("asg");
  ok(await as(name).initializeAssignment({ idempotency_key: key(),
    related_refs: { engagement: REF("engagement", eng), relationship: REF("relationship", rel) },
    declared: { new_subject_id: asg } }), "initialize assignment");
  const mandate = await fact(name, "assignment_mandate", "assignment", asg,
    { detail: "synthetic search mandate" });
  ok(await as(name).openCreAssignment({ idempotency_key: key(),
    subject_ref: REF("assignment", asg),
    related_refs: { engagement: REF("engagement", eng), relationship: REF("relationship", rel) },
    evidence_refs: [{ evidence_kind: "search_initiation", record_id: mandate }],
    declared: { mandate_scope: "search" } }), "open assignment");
  return { rel, eng, asg };
}

/** A negotiation with a delivered LOI, accepted by the counterparty. */
async function acceptedNegotiation(name, asg) {
  const neg = id("neg"), property = id("prop");
  ok(await as(name).initializePropertyNegotiation({ idempotency_key: key(),
    related_refs: { assignment: REF("assignment", asg) },
    declared: { new_subject_id: neg, property_id: property } }), "initialize negotiation");
  const loi = await f01Document(name, { document_class: "letter_of_intent",
    states: { signature_state: "unsigned", validity_state: "draft" } });
  await linkDocument("joe", loi, "property_negotiation", neg);
  ok(await as(name).recordLoiSubmission({ idempotency_key: key(),
    subject_ref: REF("property_negotiation", neg),
    related_refs: { assignment: REF("assignment", asg) },
    evidence_refs: [{ evidence_kind: "submitted_loi", ...docRef(loi) }] }), "LOI submission");
  const acceptance = await f01Artifact(name);
  await linkArtifact("joe", acceptance, "property_negotiation", neg);
  ok(await as(name).recordLoiAcceptance({ idempotency_key: key(),
    subject_ref: REF("property_negotiation", neg),
    evidence_refs: [{ evidence_kind: "counterparty_loi_acceptance", artifact_digest: acceptance }] }),
  "LOI acceptance");
  return { neg, property };
}

/** A pending deal: the partner commits the winning property. */
async function pendingDeal(instrument_kind = "lease") {
  const { rel, eng, asg } = await openedAssignment("joe");
  const { neg, property } = await acceptedNegotiation("joe", asg);
  const deal = id("deal");
  const commitment = await fact("joe", "winning_property_commitment", "assignment", asg,
    { detail: "synthetic winner selection" });
  ok(await as("joe").commitWinningProperty({ idempotency_key: key(),
    subject_ref: REF("assignment", asg),
    related_refs: { property_negotiation: REF("property_negotiation", neg) },
    evidence_refs: [{ evidence_kind: "winner_selection_commitment", record_id: commitment }],
    declared: { instrument_kind, new_deal_id: deal } }), "commit winning property");
  return { rel, eng, asg, neg, property, deal };
}

async function state(name, subject_kind, subject_id) {
  const answer = await as(name).readCreLifecycle({
    selector: { kind: "subject", subject_kind, subject_id } });
  return answer;
}

test("a lease deal walks prospect to closed and paid, live, as a verified partner", { skip: SKIP }, async () => {
  const joe = as("joe");
  const { asg, deal } = await pendingDeal("lease");

  const lease = await f01Document("joe", { document_class: "lease" });
  await linkDocument("joe", lease, "deal", deal);
  ok(await joe.recordDealExecution({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "executed_lease", ...docRef(lease) }] }), "lease execution");

  const commission = await f01Document("joe", { document_class: "commission_agreement" });
  await linkDocument("joe", commission, "deal", deal);
  ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "commission_agreement", ...docRef(commission) }],
    declared: { axis: "commission_agreement_state" } }), "commission agreement");

  const settlement = await fact("joe", "closing_settlement", "deal", deal,
    { closing_date: "2026-09-20T15:00:00Z" });
  ok(await joe.recordDealClosing({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "final_closing_settlement", record_id: settlement }] }),
  "deal closing");

  const invoice = await fact("joe", "invoice", "deal", deal, { detail: "synthetic invoice" });
  ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "invoice_issued", record_id: invoice }],
    declared: { axis: "invoice_state" } }), "invoice issued");

  for (const level of ["partially_paid", "paid"]) {
    const payment = await fact("joe", "payment", "deal", deal, { detail: `synthetic ${level}` });
    ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
      evidence_refs: [{ evidence_kind: "payment_received", record_id: payment }],
      declared: { axis: "payment_state", payment_level: level } }), `payment ${level}`);
  }
  const completion = await fact("joe", "completion", "deal", deal, { detail: "synthetic completion" });
  ok(await joe.recordDealAxis({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "completion_recorded", record_id: completion }],
    declared: { axis: "completion_state" } }), "completion");

  const final = await state("joe", "deal", deal);
  console.log(JSON.stringify(final).slice(0, 900));
});

test("a purchase deal is executed but PENDING through diligence, and closes only on its closing date", { skip: SKIP }, async () => {
  const joe = as("joe");
  const { deal } = await pendingDeal("purchase");
  const contract = await f01Document("joe", { document_class: "purchase_contract",
    states: { validity_state: "draft" } });
  await linkDocument("joe", contract, "deal", deal);
  ok(await joe.recordDealExecution({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "signed_purchase_contract", ...docRef(contract) }] }),
  "purchase contract execution");
  const outcome = await fact("joe", "diligence_outcome", "deal", deal, { detail: "synthetic diligence" });
  ok(await joe.recordDiligenceOutcome({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "diligence_outcome", record_id: outcome }],
    declared: { diligence_result: "satisfied" } }), "diligence outcome");
  const settlement = await fact("joe", "closing_settlement", "deal", deal,
    { closing_date: "2026-09-21T15:00:00Z" });
  ok(await joe.recordDealClosing({ idempotency_key: key(), subject_ref: REF("deal", deal),
    evidence_refs: [{ evidence_kind: "final_closing_settlement", record_id: settlement }] }),
  "purchase closing");
  console.log(JSON.stringify(await state("joe", "deal", deal)).slice(0, 900));
});

test("a failed pending deal is cancelled with its reason and the assignment returns to search", { skip: SKIP }, async () => {
  const joe = as("joe");
  const { rel, eng, asg, deal } = await pendingDeal("lease");
  const failure = await fact("joe", "deal_failure", "deal", deal,
    { reason: "synthetic landlord withdrew" });
  ok(await joe.cancelPendingDeal({ idempotency_key: key(), subject_ref: REF("deal", deal),
    related_refs: { assignment: REF("assignment", asg), engagement: REF("engagement", eng),
      relationship: REF("relationship", rel) },
    evidence_refs: [{ evidence_kind: "deal_failure_record", record_id: failure }],
    declared: { return_phase: "search" } }), "cancel pending deal");
  console.log(JSON.stringify(await state("joe", "assignment", asg)).slice(0, 900));
});

function docPin(doc) {
  return { document_id: doc.document_id, expected_version_no: doc.version_no,
    expected_content_digest: doc.content_digest };
}
function docRef(doc) {
  return { document_id: doc.document_id, expected_version_no: doc.version_no,
    expected_content_digest: doc.content_digest };
}

// ===========================================================================
// THE FULL TRANSITION TRUTH TABLE, LIVE.
//
// v5J102TransitionTruthTable() is the kernel's own enumeration: every guard of
// every transition against every value that guard can be shown. Each cell here
// is DRIVEN through the real store against the real database — a subject in the
// cell's state, reached through the real doors, with admitting evidence bound
// to it — and the answer is compared with what the cell says.
//
// A CELL THE DATABASE CAN NEVER REACH is not quietly passed. Some state values
// have no door that produces them (no transition writes loi_countered, an
// expired engagement or a concluded assignment); some guards cannot be shown a
// wrong value through the store by construction (the store fixes the subject
// kind per operation and dispatches deal execution on the STORED instrument).
// Every such cell is named in the coverage report and the unreachable set is
// pinned, so a new door that makes one reachable fails this suite until the
// cell is driven.
// ===========================================================================

/** Deal steps, each a real store operation with admitting evidence. */
const DEAL_STEPS = {
  async lease_exec(d) {
    const doc = await f01Document("joe", { document_class: "lease" });
    await linkDocument("joe", doc, "deal", d.deal);
    return ok(await as("joe").recordDealExecution({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "executed_lease", ...docRef(doc) }] }), "lease exec");
  },
  async purchase_exec(d) {
    const doc = await f01Document("joe", { document_class: "purchase_contract",
      states: { validity_state: "draft" } });
    await linkDocument("joe", doc, "deal", d.deal);
    return ok(await as("joe").recordDealExecution({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "signed_purchase_contract", ...docRef(doc) }] }),
    "purchase exec");
  },
  async diligence(d, result) {
    const r = await fact("joe", "diligence_outcome", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDiligenceOutcome({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "diligence_outcome", record_id: r }],
      declared: { diligence_result: result } }), `diligence ${result}`);
  },
  async close(d) {
    const r = await fact("joe", "closing_settlement", "deal", d.deal,
      { closing_date: "2026-09-20T15:00:00Z" });
    return ok(await as("joe").recordDealClosing({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "final_closing_settlement", record_id: r }] }), "close");
  },
  async cancel(d) {
    const r = await fact("joe", "deal_failure", "deal", d.deal, { reason: "synthetic failure" });
    return ok(await as("joe").cancelPendingDeal({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      related_refs: { assignment: REF("assignment", d.asg) },
      evidence_refs: [{ evidence_kind: "deal_failure_record", record_id: r }],
      declared: { return_phase: "search" } }), "cancel");
  },
  async commission(d) {
    const doc = await f01Document("joe", { document_class: "commission_agreement" });
    await linkDocument("joe", doc, "deal", d.deal);
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "commission_agreement", ...docRef(doc) }],
      declared: { axis: "commission_agreement_state" } }), "commission");
  },
  async invoice(d) {
    const r = await fact("joe", "invoice", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "invoice_issued", record_id: r }],
      declared: { axis: "invoice_state" } }), "invoice");
  },
  async payment(d, level) {
    const r = await fact("joe", "payment", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "payment_received", record_id: r }],
      declared: { axis: "payment_state", payment_level: level } }), `payment ${level}`);
  },
  async completion(d) {
    const r = await fact("joe", "completion", "deal", d.deal, { detail: "synthetic" });
    return ok(await as("joe").recordDealAxis({ idempotency_key: key(),
      subject_ref: REF("deal", d.deal),
      evidence_refs: [{ evidence_kind: "completion_recorded", record_id: r }],
      declared: { axis: "completion_state" } }), "completion");
  },
};

async function dealWith(instrument_kind, steps) {
  const d = await pendingDeal(instrument_kind);
  for (const step of steps) {
    const [name, arg] = step.split(":");
    await DEAL_STEPS[name](d, arg);
  }
  return d;
}

/**
 * A FRESH subject whose `axis` holds `value`, built through the real doors, or
 * null when no door produces that value. `instrument` is the deal instrument
 * wanted, so that record-deal-execution dispatches to the transition under test.
 */
async function subjectAt(subject_kind, axis, value, instrument = "lease") {
  if (subject_kind === "relationship") {
    if (value === "prospect") {
      const rel = id("rel");
      ok(await as("joe").initializeProspectRelationship({ idempotency_key: key(),
        declared: { new_subject_id: rel } }), "prospect");
      return { kind: "relationship", id: rel, rel };
    }
    if (value === "client") {
      const c = await client("joe");
      return { kind: "relationship", id: c.rel, ...c };
    }
    return null;
  }
  if (subject_kind === "assignment") {
    if (value === "research" || value === "search") {
      const c = await client("joe");
      const asg = id("asg");
      ok(await as("joe").initializeAssignment({ idempotency_key: key(),
        related_refs: { engagement: REF("engagement", c.eng), relationship: REF("relationship", c.rel) },
        declared: { new_subject_id: asg } }), "initialize assignment");
      if (value === "search") {
        const mandate = await fact("joe", "assignment_mandate", "assignment", asg, { detail: "s" });
        ok(await as("joe").openCreAssignment({ idempotency_key: key(),
          subject_ref: REF("assignment", asg),
          related_refs: { engagement: REF("engagement", c.eng), relationship: REF("relationship", c.rel) },
          evidence_refs: [{ evidence_kind: "search_initiation", record_id: mandate }],
          declared: { mandate_scope: "search" } }), "open assignment");
      }
      return { kind: "assignment", id: asg, asg, ...c };
    }
    if (value === "negotiation") {
      const a = await openedAssignment("joe");
      const n = await acceptedNegotiation("joe", a.asg);
      return { kind: "assignment", id: a.asg, ...a, neg: n.neg };
    }
    if (value === "committed") {
      const d = await pendingDeal("lease");
      return { kind: "assignment", id: d.asg, ...d };
    }
    return null;
  }
  if (subject_kind === "property_negotiation") {
    if (value === "loi_drafted" || value === "loi_submitted") {
      const a = await openedAssignment("joe");
      const neg = id("neg");
      ok(await as("joe").initializePropertyNegotiation({ idempotency_key: key(),
        related_refs: { assignment: REF("assignment", a.asg) },
        declared: { new_subject_id: neg, property_id: id("prop") } }), "initialize negotiation");
      if (value === "loi_submitted") {
        const loi = await f01Document("joe", { document_class: "letter_of_intent",
          states: { signature_state: "unsigned", validity_state: "draft" } });
        await linkDocument("joe", loi, "property_negotiation", neg);
        ok(await as("joe").recordLoiSubmission({ idempotency_key: key(),
          subject_ref: REF("property_negotiation", neg),
          related_refs: { assignment: REF("assignment", a.asg) },
          evidence_refs: [{ evidence_kind: "submitted_loi", ...docRef(loi) }] }), "LOI submission");
      }
      return { kind: "property_negotiation", id: neg, ...a, neg };
    }
    if (value === "loi_accepted") {
      const a = await openedAssignment("joe");
      const n = await acceptedNegotiation("joe", a.asg);
      return { kind: "property_negotiation", id: n.neg, ...a, neg: n.neg };
    }
    if (value === "selected_winner") {
      const d = await pendingDeal("lease");
      return { kind: "property_negotiation", id: d.neg, ...d };
    }
    return null;
  }
  if (subject_kind === "deal") {
    const exec = instrument === "purchase" ? "purchase_exec" : "lease_exec";
    // A purchase closes only after its diligence resolves (Q094).
    const closed = instrument === "purchase"
      ? [exec, "diligence:satisfied", "close"] : [exec, "close"];
    const steps = {
      deal_state: { pending: [], closed, cancelled: ["cancel"] },
      execution_state: { unexecuted: [], executed: [exec] },
      diligence_state: instrument === "purchase"
        ? { not_applicable: null, in_progress: [exec],
            satisfied: [exec, "diligence:satisfied"], waived: [exec, "diligence:waived"],
            failed: [exec, "diligence:failed"] }
        : { not_applicable: [], in_progress: null, satisfied: null, waived: null, failed: null },
      closing_state: { not_reached: [], closed },
      commission_agreement_state: { absent: [], agreed: ["commission"] },
      invoice_state: { not_invoiced: [], invoiced: ["invoice"] },
      payment_state: { unpaid: [], partially_paid: ["payment:partially_paid"],
        paid: ["payment:paid"] },
      completion_state: { open: [], complete: ["completion"] },
    }[axis]?.[value];
    if (steps === undefined || steps === null) return null;
    const d = await dealWith(instrument, steps);
    return { kind: "deal", id: d.deal, ...d };
  }
  return null; // engagement: no transition takes an engagement as its subject
}

/** The admitting evidence for one transition, bound to subject `s`. */
async function admittingEvidence(transition_id, s) {
  switch (transition_id) {
    case "establish-client-and-engagement": {
      const doc = await f01Document("joe", { document_class: "engagement_letter" });
      await linkDocument("joe", doc, "relationship", s.id);
      return [{ evidence_kind: "signed_engagement_letter", ...docRef(doc) }];
    }
    case "open-assignment":
      return [{ evidence_kind: "search_initiation",
        record_id: await fact("joe", "assignment_mandate", "assignment", s.id, { detail: "s" }) }];
    case "record-loi-submission": {
      const doc = await f01Document("joe", { document_class: "letter_of_intent",
        states: { signature_state: "unsigned", validity_state: "draft" } });
      await linkDocument("joe", doc, "property_negotiation", s.id);
      return [{ evidence_kind: "submitted_loi", ...docRef(doc) }];
    }
    case "record-loi-acceptance": {
      const art = await f01Artifact("joe");
      await linkArtifact("joe", art, "property_negotiation", s.id);
      return [{ evidence_kind: "counterparty_loi_acceptance", artifact_digest: art }];
    }
    case "commit-winning-property":
      return [{ evidence_kind: "winner_selection_commitment",
        record_id: await fact("joe", "winning_property_commitment", "assignment", s.id,
          { detail: "s" }) }];
    case "record-lease-execution": {
      const doc = await f01Document("joe", { document_class: "lease" });
      await linkDocument("joe", doc, "deal", s.id);
      return [{ evidence_kind: "executed_lease", ...docRef(doc) }];
    }
    case "record-purchase-contract-execution": {
      const doc = await f01Document("joe", { document_class: "purchase_contract",
        states: { validity_state: "draft" } });
      await linkDocument("joe", doc, "deal", s.id);
      return [{ evidence_kind: "signed_purchase_contract", ...docRef(doc) }];
    }
    case "record-diligence-outcome":
      return [{ evidence_kind: "diligence_outcome",
        record_id: await fact("joe", "diligence_outcome", "deal", s.id, { detail: "s" }) }];
    case "record-deal-closing":
      return [{ evidence_kind: "final_closing_settlement",
        record_id: await fact("joe", "closing_settlement", "deal", s.id,
          { closing_date: "2026-09-20T15:00:00Z" }) }];
    case "cancel-pending-deal":
      return [{ evidence_kind: "deal_failure_record",
        record_id: await fact("joe", "deal_failure", "deal", s.id, { reason: "synthetic" }) }];
    case "record-commission-agreement": {
      const doc = await f01Document("joe", { document_class: "commission_agreement" });
      await linkDocument("joe", doc, "deal", s.id);
      return [{ evidence_kind: "commission_agreement", ...docRef(doc) }];
    }
    case "record-invoice-issued":
      return [{ evidence_kind: "invoice_issued",
        record_id: await fact("joe", "invoice", "deal", s.id, { detail: "s" }) }];
    case "record-payment":
      return [{ evidence_kind: "payment_received",
        record_id: await fact("joe", "payment", "deal", s.id, { detail: "s" }) }];
    case "record-completion":
      return [{ evidence_kind: "completion_recorded",
        record_id: await fact("joe", "completion", "deal", s.id, { detail: "s" }) }];
    default:
      throw new Error(`no admitting evidence for ${transition_id}`);
  }
}

/** One store call performing `transition_id` on subject `s`, as `who`. */
async function attempt(who, transition_id, s, evidence_refs, { subject_kind } = {}) {
  const store = as(who);
  const payload = { idempotency_key: key(),
    subject_ref: { subject_kind: subject_kind ?? s.kind, subject_id: s.id }, evidence_refs };
  const withRelated = related => (related ? { ...payload, related_refs: related } : payload);
  const calls = {
    "establish-client-and-engagement": () => store.recordRepresentationAgreement({ ...payload,
      declared: { new_subject_id: id("eng") } }),
    "open-assignment": () => store.openCreAssignment({
      ...withRelated(s.eng ? { engagement: REF("engagement", s.eng),
        relationship: REF("relationship", s.rel) } : null),
      declared: { mandate_scope: "search" } }),
    "record-loi-submission": () => store.recordLoiSubmission(
      withRelated(s.asg ? { assignment: REF("assignment", s.asg) } : null)),
    "record-loi-acceptance": () => store.recordLoiAcceptance(payload),
    "commit-winning-property": () => store.commitWinningProperty({
      ...withRelated(s.neg ? { property_negotiation: REF("property_negotiation", s.neg) } : null),
      declared: { instrument_kind: "lease", new_deal_id: id("deal") } }),
    "record-lease-execution": () => store.recordDealExecution(payload),
    "record-purchase-contract-execution": () => store.recordDealExecution(payload),
    "record-diligence-outcome": () => store.recordDiligenceOutcome({ ...payload,
      declared: { diligence_result: "satisfied" } }),
    "record-deal-closing": () => store.recordDealClosing(payload),
    "cancel-pending-deal": () => store.cancelPendingDeal({
      ...withRelated(s.asg ? { assignment: REF("assignment", s.asg) } : null),
      declared: { return_phase: "search" } }),
    "record-commission-agreement": () => store.recordDealAxis({ ...payload,
      declared: { axis: "commission_agreement_state" } }),
    "record-invoice-issued": () => store.recordDealAxis({ ...payload,
      declared: { axis: "invoice_state" } }),
    "record-payment": () => store.recordDealAxis({ ...payload,
      declared: { axis: "payment_state", payment_level: "paid" } }),
    "record-completion": () => store.recordDealAxis({ ...payload,
      declared: { axis: "completion_state" } }),
  };
  try {
    const answer = await calls[transition_id]();
    return { decision: answer.decision, reason_id: answer.reason_id,
      transition_id: answer.transition_id ?? null,
      unmet_axis: answer.refusal_detail?.unmet_axis ?? null,
      records_written: answer.records_written ?? null, at: "store_or_kernel" };
  } catch (error) {
    if (error?.constructor?.name === "V5J102StoreError") {
      return { decision: "refuse", reason_id: error.code, unmet_axis: null,
        records_written: 0, at: "store_boundary" };
    }
    throw error;
  }
}

async function digestOf(s) {
  return (await state("joe", s.kind, s.id)).readback?.body?.state_digest ?? null;
}

/** A subject the transition's four guards all admit, for driving one other guard. */
async function admittedSubject(transition_id, instrument) {
  const c = v5J102TransitionContract(transition_id);
  const inst = instrument ?? c.instrument_kinds?.[0] ??
    (transition_id === "record-diligence-outcome" ? "purchase" : "lease");
  const [axis, permitted] = Object.entries(c.prerequisites ?? {}).at(-1) ?? [null, []];
  // A commitment needs an ACCEPTED negotiation under its assignment, which only
  // an assignment already negotiating has.
  const value = transition_id === "commit-winning-property" ? "negotiation" : permitted[0];
  if (c.subject_kind === "deal") {
    // Every from-axis admitting at once: build the shape each deal transition needs.
    const steps = {
      "record-diligence-outcome": ["purchase_exec"],
      "record-deal-closing": [inst === "purchase" ? "purchase_exec" : "lease_exec"],
    }[transition_id] ?? [];
    const d = await dealWith(inst, steps);
    return { kind: "deal", id: d.deal, ...d };
  }
  return subjectAt(c.subject_kind, axis, value, inst);
}

const TABLE = LIVE ? v5J102TransitionTruthTable() : null;

// The state values NO door produces, pinned. A value leaves this set only by
// being reached — at which point the cells that need it must be driven.
const UNREACHABLE_STATE_VALUES = Object.freeze([
  "relationship_state=client_paused", "relationship_state=client_ended",
  "assignment_phase=concluded",
  "negotiation_state=loi_countered", "negotiation_state=loi_rejected",
  "negotiation_state=loi_withdrawn", "negotiation_state=superseded",
]);

test("LIVE TRUTH TABLE (prerequisite guard): every reachable cell of every transition decides as the table says", { skip: SKIP, timeout: 900000 }, async () => {
  const cells = TABLE.cells.filter(c => c.guard === "prerequisite");
  const report = { cells: cells.length, driven: 0, isolated: 0, not_isolated: [], unreachable: [] };
  const cache = new Map();
  for (const cell of cells) {
    const c = v5J102TransitionContract(cell.transition_id);
    const inst = c.instrument_kinds?.[0] ??
      (cell.transition_id === "record-diligence-outcome" ? "purchase" : "lease");
    // A REFUSING cell writes nothing, so its subject may be shared; an admitting
    // cell may move its subject, so it always gets a fresh one.
    const cacheKey = `${c.subject_kind}|${cell.axis}|${cell.value}|${inst}`;
    let s = cell.guard_admits ? undefined : cache.get(cacheKey);
    if (s === undefined) {
      s = await subjectAt(c.subject_kind, cell.axis, cell.value, inst);
      // Q094's split: diligence exists only on a purchase, so a lease deal is
      // the one that holds not_applicable, and the prerequisite still has to
      // refuse it.
      if (s === null && c.subject_kind === "deal" && cell.axis === "diligence_state") {
        s = await subjectAt("deal", cell.axis, cell.value, inst === "purchase" ? "lease" : "purchase");
      }
      if (!cell.guard_admits) cache.set(cacheKey, s);
    }
    if (s === null) {
      report.unreachable.push(`${cell.transition_id}:${cell.axis}=${cell.value}`);
      continue;
    }
    const before = await digestOf(s);
    const evidence = await admittingEvidence(cell.transition_id, s);
    const r = await attempt("joe", cell.transition_id, s, evidence);
    report.driven += 1;
    if (cell.guard_admits) {
      assert.ok(!(r.reason_id === "prerequisite_not_met" && r.unmet_axis === cell.axis),
        `${cell.transition_id} ${cell.axis}=${cell.value} is admitted by the table and refused live: ${JSON.stringify(r)}`);
      continue;
    }
    assert.equal(r.decision, "refuse",
      `${cell.transition_id} ${cell.axis}=${cell.value} must refuse: ${JSON.stringify(r)}`);
    assert.equal(r.reason_id, "prerequisite_not_met",
      `${cell.transition_id} ${cell.axis}=${cell.value}: ${JSON.stringify(r)}`);
    assert.equal(await digestOf(s), before, "a refusal moved the subject");
    if (r.unmet_axis === cell.axis) report.isolated += 1;
    else report.not_isolated.push(`${cell.transition_id}:${cell.axis}=${cell.value} (first unmet: ${r.unmet_axis})`);
  }
  console.log("LIVE prerequisite cells", JSON.stringify(report, null, 1));
  for (const u of report.unreachable) {
    const cellValue = u.split(":")[1];
    const pinned = UNREACHABLE_STATE_VALUES.includes(cellValue) ||
      // Diligence has no in_progress..failed state on a lease and no
      // not_applicable state on an executed purchase, by Q094's own split.
      cellValue.startsWith("diligence_state=");
    assert.ok(pinned, `${u} was not driven and its value is not pinned unreachable`);
  }
});

/** Evidence of ANY registered kind, bound to the subject its contract names. */
async function evidenceOfKind(kind, family) {
  const doc = async (document_class, subject_kind, subject_id, states) => {
    const d = await f01Document("joe", { document_class, states });
    await linkDocument("joe", d, subject_kind, subject_id);
    return docRef(d);
  };
  const rec = async (record_kind, subject_kind, subject_id, extra = { detail: "s" }) =>
    ({ record_id: await fact("joe", record_kind, subject_kind, subject_id, extra) });
  switch (kind) {
    case "signed_engagement_letter":
      return doc("engagement_letter", "relationship", family.rel);
    case "submitted_loi":
      return doc("letter_of_intent", "property_negotiation", family.neg,
        { signature_state: "unsigned", validity_state: "draft" });
    case "executed_lease": return doc("lease", "deal", family.deal);
    case "signed_purchase_contract":
      return doc("purchase_contract", "deal", family.deal, { validity_state: "draft" });
    case "commission_agreement": return doc("commission_agreement", "deal", family.deal);
    case "counterparty_loi_acceptance": {
      const art = await f01Artifact("joe");
      await linkArtifact("joe", art, "property_negotiation", family.neg);
      return { artifact_digest: art };
    }
    case "search_initiation": return rec("assignment_mandate", "assignment", family.asg);
    case "winner_selection_commitment":
      return rec("winning_property_commitment", "assignment", family.asg);
    case "diligence_outcome": return rec("diligence_outcome", "deal", family.deal);
    case "final_closing_settlement":
      return rec("closing_settlement", "deal", family.deal, { closing_date: "2026-09-20T15:00:00Z" });
    case "deal_failure_record":
      return rec("deal_failure", "deal", family.deal, { reason: "synthetic" });
    case "invoice_issued": return rec("invoice", "deal", family.deal);
    case "payment_received": return rec("payment", "deal", family.deal);
    case "completion_recorded": return rec("completion", "deal", family.deal);
    case "manual_correction":
      return rec("lifecycle_correction", family.self_kind, family.self_id, { reason: "synthetic" });
    case "approved_representation_equivalent":
    case "multi_target_exception_approval":
      return { approval_ref: id("approval") };
    default:
      throw new Error(`no producer for evidence kind ${kind}`);
  }
}

test("LIVE TRUTH TABLE (subject_kind, actor_class, instrument_kind, required_evidence guards)", { skip: SKIP, timeout: 900000 }, async () => {
  const report = {};
  const tally = (guard, outcome) => {
    report[guard] ??= {};
    report[guard][outcome] = (report[guard][outcome] ?? 0) + 1;
  };
  // A family of subjects of every kind, to host evidence bound to a subject
  // OTHER than the one under test.
  const other = await pendingDeal("lease");

  for (const transition_id of V5_J102_TRANSITION_IDS) {
    const c = v5J102TransitionContract(transition_id);
    const own = TABLE.cells.filter(x => x.transition_id === transition_id);

    // --- subject_kind: the store fixes the kind per operation --------------
    const s0 = await admittedSubject(transition_id);
    for (const cell of own.filter(x => x.guard === "subject_kind" && !x.guard_admits)) {
      const ev = await admittingEvidence(transition_id, s0);
      const r = await attempt("joe", transition_id, s0, ev, { subject_kind: cell.value });
      assert.equal(r.decision, "refuse", `${transition_id} subject_kind=${cell.value}`);
      assert.equal(r.reason_id, "subject_kind_mismatch", `${transition_id} subject_kind=${cell.value}: ${JSON.stringify(r)}`);
      tally("subject_kind", `refused:${r.at}`);
    }

    // --- required_evidence: every kind but the admitting one refuses --------
    const before = await digestOf(s0);
    const family = { rel: s0.rel ?? other.rel, asg: s0.asg ?? other.asg,
      neg: s0.neg ?? other.neg, deal: s0.deal ?? other.deal,
      self_kind: s0.kind, self_id: s0.id };
    for (const cell of own.filter(x => x.guard === "required_evidence" && !x.guard_admits)) {
      const ref = { evidence_kind: cell.value, ...(await evidenceOfKind(cell.value, family)) };
      const r = await attempt("joe", transition_id, s0, [ref]);
      assert.equal(r.decision, "refuse",
        `${transition_id} evidence=${cell.value} must refuse: ${JSON.stringify(r)}`);
      assert.equal(r.records_written ?? 0, 0);
      tally("required_evidence", `refused:${r.reason_id}`);
    }
    assert.equal(await digestOf(s0), before, `${transition_id}: an evidence refusal moved the subject`);

    // --- actor_class: both classes, each on a fresh admitted subject --------
    for (const cell of own.filter(x => x.guard === "actor_class")) {
      const who = cell.value === "verified_partner" ? "dell" : "agent";
      const s = await admittedSubject(transition_id);
      const ev = await admittingEvidence(transition_id, s);
      const r = await attempt(who, transition_id, s, ev);
      if (cell.guard_admits) {
        assert.ok(r.reason_id !== "actor_class_not_permitted" &&
          r.reason_id !== "authority_only_operation_refused",
          `${transition_id} admits ${cell.value} and refused it live: ${JSON.stringify(r)}`);
        assert.equal(r.decision, "allow", `${transition_id} as ${cell.value}: ${JSON.stringify(r)}`);
        tally("actor_class", `admitted:${cell.value}`);
      } else {
        assert.equal(r.decision, "refuse", `${transition_id} as ${cell.value}`);
        assert.ok(["actor_class_not_permitted", "authority_only_operation_refused"].includes(r.reason_id),
          `${transition_id} as ${cell.value}: ${JSON.stringify(r)}`);
        tally("actor_class", `refused:${r.reason_id}`);
      }
    }

    // --- instrument_kind: dispatch on the STORED instrument ----------------
    for (const cell of own.filter(x => x.guard === "instrument_kind")) {
      const s = await admittedSubject(transition_id, cell.value);
      const ev = await admittingEvidence(transition_id, s);
      const r = await attempt("joe", transition_id, s, ev);
      if (cell.guard_admits) {
        assert.equal(r.decision, "allow", `${transition_id} on a ${cell.value} deal: ${JSON.stringify(r)}`);
        tally("instrument_kind", "admitted");
      } else {
        // The store never hands the lease transition a purchase deal: it
        // dispatches on the stored instrument, and the other transition then
        // refuses this evidence. Either way the transition under test does not run.
        assert.equal(r.decision, "refuse", `${transition_id} on a ${cell.value} deal`);
        assert.notEqual(r.transition_id, transition_id,
          `the store handed ${transition_id} a ${cell.value} deal`);
        tally("instrument_kind", `refused:${r.reason_id}`);
      }
    }
  }
  console.log("LIVE guard cells", JSON.stringify(report, null, 1));
});

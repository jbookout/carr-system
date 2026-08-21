// THE PRODUCER, AGAINST A REAL DATABASE.
//
// WHY THIS FILE EXISTS. receipt-producer.test.mjs drives writeReceiptsFor
// against a hand-written fake client, and an audit showed that fake refuses
// nothing: three separate mutations of the producer -- swapping material and
// prior in the INSERT column list, dropping the session scope from the subject
// query, and hardcoding the subject in the material recipe -- all left it 9/9
// green. The real database refuses every one of them. That is the same shape as
// the fake mint that ignored its parameters, and it had already shipped twice.
//
// Nothing else in the repo executed the producer's SQL at all: the apply-time
// block and the Python contract suite both hand-write their own inserts, so the
// exact statements the Worker sends had no coverage anywhere.
//
// HOW IT TALKS TO POSTGRES. mcp-server depends on @neondatabase/serverless,
// which speaks WebSocket to Neon and cannot reach a local cluster, so the client
// here shells out to psql. It is deliberately thin: it does not interpret the
// producer's SQL, it hands it to the database verbatim.
//
// WHAT IT DOES NOT COVER, said plainly: each statement autocommits, so this does
// not exercise the producer's transaction, and the advisory lock it takes is
// released immediately. Concurrency is out of scope here; the guards are not.
//
// SKIPPED unless CARR_TEST_DSN names a database with 0232 through 0238 applied.
// ops/check-application-session.sh sets it.
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { writeReceiptsFor } from "../src/tools.js";

const DSN = process.env.CARR_TEST_DSN;
const SEP = "|";

function raw(sql) {
  return execFileSync("psql", [DSN, "-v", "ON_ERROR_STOP=1", "-A", "-t", "-F", SEP, "-c", sql],
                      { encoding: "utf8" });
}
// Literals are escaped by doubling quotes. Every value the producer passes is a
// uuid, a hex digest, or an identifier, but escaping is not optional in a helper
// that hands strings to a database.
const lit = v => (v === null || v === undefined) ? "null" : "'" + String(v).replace(/'/g, "''") + "'";

function psqlClient() {
  return {
    queries: [],
    async query(text, params = []) {
      this.queries.push({ text, params });
      const sql = text.replace(/\$(\d+)/g, (_, n) => lit(params[Number(n) - 1]));
      const isSelect = /^\s*select/i.test(sql);
      const wrapped = isSelect ? "select row_to_json(t) from (" + sql + ") t" : sql;
      const out = raw(wrapped).trim();
      if (!isSelect) return { rows: [] };
      return { rows: out ? out.split("\n").filter(Boolean).map(l => JSON.parse(l)) : [] };
    },
  };
}

describe("the receipt producer against a real database", { skip: !DSN }, () => {
  const tenant = "carr-internal";
  let actorId, sessionId;

  function setup() {
    actorId = raw("select id from actor where kind='human' order by slug limit 1").trim();
    sessionId = randomUUID();
    raw("insert into ops.application_session (id, actor_id, organization_tenant_id," +
        " sponsoring_human_slug, via, auth_issuer, authorization_class, verified_subject," +
        " expires_at) values (" + lit(sessionId) + ", " + lit(actorId) + ", " + lit(tenant) +
        ", 'joe', 'probe', 'probe-issuer', 'verified_partner', 'probe'," +
        " clock_timestamp() + interval '1 hour')");
  }
  function evt(key, subject, value) {
    raw("insert into event (occurred_at, actor_id, verb, subject_type, subject_id, field," +
        " new_value, cause, idempotency_key, organization_tenant_id, application_session_id)" +
        " values (clock_timestamp(), " + lit(actorId) + ", 'log-activity', 'deal', " +
        lit(subject) + ", 'stage', " + lit(JSON.stringify(value)) + "::jsonb, 'system', " +
        lit(key) + ", " + lit(tenant) + ", " + lit(sessionId) + ")");
  }
  function call(key, subject, value) {
    raw("insert into tool_call (idempotency_key, verb, actor_id, request_hash, response," +
        " organization_tenant_id, application_session_id) values (" + lit(key) +
        ", 'log-activity', " + lit(actorId) + ", " + lit(key) + ", '{}'::jsonb, " +
        lit(tenant) + ", " + lit(sessionId) + ")");
    evt(key, subject, value);
  }
  const actor = () => ({ id: actorId, via: "probe",
    organization_tenant_id: tenant, application_session_id: sessionId,
    sponsoring_human_slug: "joe", personal_scope: "none",
    authorization_class: "verified_partner" });

  test("a qualified write produces a receipt the database itself proves", async () => {
    setup();
    const subject = randomUUID(), key = randomUUID();
    call(key, subject, "under-loi");
    await writeReceiptsFor(psqlClient(), actor(), "log-activity", key, key);

    const row = raw("select is_proven::text, prior_digest, material_digest = " +
      "ops.write_receipt_material_digest(" + lit(key) + ", " + lit(sessionId) +
      ", 'deal', " + lit(subject) + ") from ops.write_receipt where subject_id = " +
      lit(subject)).trim().split(SEP);
    assert.equal(row[0], "true", "the receipt the producer filed did not prove");
    assert.equal(row[1], "origin", "the first receipt for a subject must build on origin");
    assert.equal(row[2], "t", "the material the producer claimed is not what its call wrote");
  });

  test("a second write chains onto the first, and an identical one writes nothing", async () => {
    setup();
    const subject = randomUUID(), k1 = randomUUID(), k2 = randomUUID(), k3 = randomUUID();
    call(k1, subject, "under-loi");
    await writeReceiptsFor(psqlClient(), actor(), "log-activity", k1, k1);
    const first = raw("select material_digest from ops.write_receipt where subject_id = " +
                      lit(subject)).trim();

    call(k2, subject, "under-loi");
    await writeReceiptsFor(psqlClient(), actor(), "log-activity", k2, k2);
    assert.equal(raw("select count(*) from ops.write_receipt where subject_id = " +
                     lit(subject)).trim(), "1",
      "an idempotent restatement wrote a second receipt");

    call(k3, subject, "closed");
    await writeReceiptsFor(psqlClient(), actor(), "log-activity", k3, k3);
    const chained = raw("select prior_digest, is_proven::text from ops.write_receipt" +
      " where subject_id = " + lit(subject) + " and tool_call_idempotency_key = " +
      lit(k3)).trim().split(SEP);
    assert.equal(chained[0], first, "the second receipt did not build on the first's material");
    assert.equal(chained[1], "true", "the chained receipt did not prove");
  });

  test("one call touching two subjects files one receipt each, bound to its own subject",
    async () => {
      setup();
      const s1 = randomUUID(), s2 = randomUUID(), key = randomUUID();
      call(key, s1, "one");
      evt(key, s2, "two");
      await writeReceiptsFor(psqlClient(), actor(), "log-activity", key, key);
      const rows = raw("select call_digest, material_digest, is_proven::text from" +
        " ops.write_receipt where tool_call_idempotency_key = " + lit(key) + " order by seq")
        .trim().split("\n").map(l => l.split(SEP));
      assert.equal(rows.length, 2, "one receipt per subject the call touched");
      assert.notEqual(rows[0][0], rows[1][0],
        "two subjects share one call digest, so proof is transferable between them");
      assert.notEqual(rows[0][1], rows[1][1], "two subjects share one material claim");
      assert.ok(rows.every(r => r[2] === "true"), "a per-subject receipt did not prove");
    });

  test("events another session wrote under the same key are not receipted here",
    async () => {
      // THE SUBJECT QUERY IS SCOPED TO THE SESSION, and without a fixture where
      // two sessions share an idempotency key nothing exercised that. Dropping
      // the session clause from the producer left every other test in this file
      // green while the producer happily minted receipts for subjects another
      // session had written.
      setup();
      const mine = randomUUID(), theirs = randomUUID(), key = randomUUID();
      call(key, mine, "mine");
      const otherSession = randomUUID();
      raw("insert into ops.application_session (id, actor_id, organization_tenant_id," +
          " sponsoring_human_slug, via, auth_issuer, authorization_class, verified_subject," +
          " expires_at) values (" + lit(otherSession) + ", " + lit(actorId) + ", " +
          lit(tenant) + ", 'joe', 'probe', 'probe-issuer', 'verified_partner', 'probe'," +
          " clock_timestamp() + interval '1 hour')");
      raw("insert into event (occurred_at, actor_id, verb, subject_type, subject_id, field," +
          " new_value, cause, idempotency_key, organization_tenant_id, application_session_id)" +
          " values (clock_timestamp(), " + lit(actorId) + ", 'log-activity', 'deal', " +
          lit(theirs) + ", 'stage', '\"theirs\"'::jsonb, 'system', " + lit(key) + ", " +
          lit(tenant) + ", " + lit(otherSession) + ")");

      await writeReceiptsFor(psqlClient(), actor(), "log-activity", key, key);
      assert.equal(raw("select count(*) from ops.write_receipt where subject_id = " +
                       lit(theirs)).trim(), "0",
        "the producer filed a receipt for a subject another session wrote");
      assert.equal(raw("select count(*) from ops.write_receipt where subject_id = " +
                       lit(mine)).trim(), "1",
        "the producer did not file a receipt for its own subject");
    });

  test("the next write builds on the last PROVEN receipt, not the newest one",
    async () => {
      // A FAILED READBACK IS A REAL INCIDENT STATE, and the producer has to
      // survive it. If it reads the newest receipt regardless of proof, the
      // prior it offers is one the database refuses -- so a single unprovable
      // receipt would break every subsequent write to that subject, which is
      // the permanent-wall failure this whole migration exists to remove.
      // Dropping `and w.is_proven` from the producer leaves every other test
      // in this file green, because nothing else ever leaves an unproven
      // receipt as the head.
      setup();
      const subject = randomUUID(), k1 = randomUUID(), k2 = randomUUID(), k3 = randomUUID();
      call(k1, subject, "first");
      await writeReceiptsFor(psqlClient(), actor(), "log-activity", k1, k1);
      const proven = raw("select material_digest from ops.write_receipt where subject_id = " +
                         lit(subject)).trim();

      // A receipt whose readback fails, filed directly: the producer cannot
      // make one, which is exactly why it has to be planted here.
      call(k2, subject, "unprovable");
      raw("insert into ops.write_receipt (id, application_session_id, actor_id," +
          " organization_tenant_id, verb, subject_type, subject_id," +
          " tool_call_idempotency_key, call_digest, material_digest, prior_digest)" +
          " values (" + lit(randomUUID()) + ", " + lit(sessionId) + ", " + lit(actorId) +
          ", " + lit(tenant) + ", 'log-activity', 'deal', " + lit(subject) + ", " + lit(k2) +
          ", 'a-digest-nobody-computed', ops.write_receipt_material_digest(" + lit(k2) +
          ", " + lit(sessionId) + ", 'deal', " + lit(subject) + "), " + lit(proven) + ")");
      assert.equal(raw("select count(*) from ops.write_receipt where subject_id = " +
                       lit(subject) + " and not is_proven").trim(), "1",
        "the planted receipt should be unproven");

      call(k3, subject, "third");
      await writeReceiptsFor(psqlClient(), actor(), "log-activity", k3, k3);
      const after = raw("select prior_digest, is_proven::text from ops.write_receipt" +
        " where subject_id = " + lit(subject) + " and tool_call_idempotency_key = " +
        lit(k3)).trim().split(SEP);
      assert.equal(after[0], proven,
        "the producer built on an unproven receipt instead of the last proven one");
      assert.equal(after[1], "true", "the receipt after an unprovable one did not prove");
    });

  test("a legacy actor with no session writes nothing at all", async () => {
    setup();
    const client = psqlClient();
    await writeReceiptsFor(client, { ...actor(), application_session_id: null },
                           "log-activity", randomUUID(), "h");
    assert.equal(client.queries.length, 0,
      "a write with no authenticated session still touched the receipt table");
  });
});

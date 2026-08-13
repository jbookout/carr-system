// release.test.mjs — unit coverage for the /release payload builder (Phase 1,
// 2026-08-13, closing the deploy-provenance gap named in the Phase 0 audit).
//
// release.js holds no cloudflare: import (unlike index.js, which cannot be
// loaded under plain node — see its own header note), so this exercises the
// real payload-building logic with a fake env and a fake `sql` tag function.
// No live database, no deploy, no KV.
//
//   node --test mcp-server/test/release.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { buildRelease } from "../src/release.js";

const FIXED_NOW = () => new Date("2026-08-13T21:00:00.000Z");

// A minimal stand-in for neon()'s tagged-template `sql` function. Routes each
// call by matching a substring of the query text against `responses`, so
// tests read as "when asked about X, answer Y" rather than depending on
// exact SQL formatting.
function fakeSql(responses) {
  return async (strings, ..._values) => {
    const q = strings.join(" ");
    for (const [needle, result] of responses) {
      if (q.includes(needle)) {
        if (result instanceof Error) throw result;
        return result;
      }
    }
    throw new Error("release.test.mjs: unmocked query: " + q);
  };
}

test("buildRelease: full shape when everything is reachable and stamped", async () => {
  const sql = fakeSql([
    ["schema_migrations", [{ applied_count: 101, highest_applied_migration: "0099_thing.sql" }]],
    ["doctrine_meta", [{ generation: 42 }]],
  ]);
  const out = await buildRelease({
    env: { GIT_SHA: "a".repeat(40) },
    sql,
    verbCount: 103,
    now: FIXED_NOW,
  });

  assert.equal(out.ok, true);
  assert.equal(out.ts, "2026-08-13T21:00:00.000Z");
  assert.equal(out.verb_count, 103);
  assert.deepEqual(out.git_sha, { value: "a".repeat(40), reason: null });
  assert.equal(out.schema.highest_applied_migration, "0099_thing.sql");
  assert.equal(out.schema.applied_count, 101);
  assert.equal(out.schema.reason, null);
  // The honesty note belongs on every response, success included — the field
  // is "what the tracking table claims" even when the table is reachable.
  assert.match(out.schema.note, /not ground truth/);
  assert.deepEqual(out.doctrine_generation, { value: 42, reason: null });
});

test("buildRelease: git_sha reports null + a specific reason when GIT_SHA was never stamped", async () => {
  const sql = fakeSql([
    ["schema_migrations", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: {}, sql, verbCount: 1, now: FIXED_NOW });

  assert.equal(out.git_sha.value, null);
  assert.equal(out.git_sha.reason, "not stamped: deployed outside bin/deploy-worker.sh");
  // An empty string is exactly the shape a broken --var wiring could produce;
  // it must fall to the same honest null, never render as a truthy sha.
});

test("buildRelease: an empty-string GIT_SHA degrades to the same not-stamped reason", async () => {
  const sql = fakeSql([
    ["schema_migrations", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: { GIT_SHA: "" }, sql, verbCount: 1, now: FIXED_NOW });
  assert.equal(out.git_sha.value, null);
  assert.equal(out.git_sha.reason, "not stamped: deployed outside bin/deploy-worker.sh");
});

test("buildRelease: a database failure degrades schema and doctrine_generation to null + reason, never throws", async () => {
  const sql = fakeSql([
    ["schema_migrations", new Error("connection terminated unexpectedly")],
    ["doctrine_meta", new Error("connection terminated unexpectedly")],
  ]);
  const out = await buildRelease({
    env: { GIT_SHA: "b".repeat(40) },
    sql,
    verbCount: 50,
    now: FIXED_NOW,
  });

  // The response is still ok:true — this is an identity endpoint, not a
  // health gate, and a database outage does not make the Worker's own
  // identity (git_sha, verb_count) any less true.
  assert.equal(out.ok, true);
  assert.equal(out.schema.highest_applied_migration, null);
  assert.equal(out.schema.applied_count, null);
  assert.match(out.schema.reason, /database unreachable/);
  assert.equal(out.doctrine_generation.value, null);
  assert.match(out.doctrine_generation.reason, /database unreachable/);
  // git_sha and verb_count have no database dependency and must survive a
  // database outage untouched.
  assert.deepEqual(out.git_sha, { value: "b".repeat(40), reason: null });
  assert.equal(out.verb_count, 50);
});

test("buildRelease: an empty schema_migrations table reports zero, not an error", async () => {
  const sql = fakeSql([
    ["schema_migrations", [{ applied_count: 0, highest_applied_migration: null }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: { GIT_SHA: "c".repeat(40) }, sql, verbCount: 1, now: FIXED_NOW });
  assert.equal(out.schema.applied_count, 0);
  assert.equal(out.schema.highest_applied_migration, null);
  assert.equal(out.schema.reason, null);
});

test("buildRelease: doctrine_meta with no id=1 row reports null + a specific reason", async () => {
  const sql = fakeSql([
    ["schema_migrations", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", []],
  ]);
  const out = await buildRelease({ env: { GIT_SHA: "d".repeat(40) }, sql, verbCount: 1, now: FIXED_NOW });
  assert.equal(out.doctrine_generation.value, null);
  assert.match(out.doctrine_generation.reason, /doctrine_meta has no row/);
});

test("buildRelease: never returns a secret — env is never echoed back, whatever it holds", async () => {
  const sql = fakeSql([
    ["schema_migrations", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const env = {
    GIT_SHA: "e".repeat(40),
    DATABASE_URL_READER: "postgres://reader:supersecret@db.example/carr",
    DATABASE_URL_WRITER: "postgres://writer:evensecreter@db.example/carr",
    INGEST_TOKENS: '{"some-source":"top-secret-token"}',
    PROBE_TOKENS: '{"smoke-probe":"another-secret"}',
    REVIEW_TOKENS: '{"codex-reviewer":"yet-another-secret"}',
    AGENT_TOKENS: '{"codex":"agent-secret"}',
    LOCAL_TOKENS: '{"joe-local":"local-secret"}',
    GOOGLE_CLIENT_SECRET: "google-secret",
  };
  const out = await buildRelease({ env, sql, verbCount: 1, now: FIXED_NOW });
  const rendered = JSON.stringify(out);

  for (const secretValue of [
    "supersecret", "evensecreter", "top-secret-token", "another-secret",
    "yet-another-secret", "agent-secret", "local-secret", "google-secret",
  ]) {
    assert.ok(!rendered.includes(secretValue), `leaked secret value: ${secretValue}`);
  }
  // Only the one field this endpoint is FOR should surface from env.
  assert.equal(out.git_sha.value, "e".repeat(40));
});

test("buildRelease: response is JSON-safe (no undefined, no function, round-trips clean)", async () => {
  const sql = fakeSql([
    ["schema_migrations", [{ applied_count: 1, highest_applied_migration: "0001_x.sql" }]],
    ["doctrine_meta", [{ generation: 1 }]],
  ]);
  const out = await buildRelease({ env: {}, sql, verbCount: 1, now: FIXED_NOW });
  const roundTripped = JSON.parse(JSON.stringify(out));
  assert.deepEqual(roundTripped, out);
});

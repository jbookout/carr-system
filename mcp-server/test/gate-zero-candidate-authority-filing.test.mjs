// THE RELEASE-CANDIDATE RECORD IS FILED UNDER THE AUTHORITY IDENTITY, AND THE
// GATE ZERO STORE READS IT (2026-09-13, the third release candidate's refusal,
// finding 1).
//
// WHAT THE OUTSIDE REVIEW REFUSED. Standing-rule amendment 9(c) says the
// release-candidate record is filed by the deploy wrapper under the AUTHORITY
// identity, because the Gate Zero producer takes its SUBJECT MAKER out of that
// row. Migration 0504 records the filing login from `session_user` and derives
// the generated `maker_authority_verified` column from it; the seam store reads
// only rows where that column is true. The refused candidate filed on the
// ordinary ledger writer, so 0504 marked every row unauthenticated and the store
// read none of them — the subject-maker seat was unreachable in production while
// every test stayed green.
//
// WHY IT FILED THERE, AND WHAT CLOSED IT. Until migration 0503, carr_authority
// held no INSERT on ops.release at all (0161 built the bundle without one), and
// admitting the grant was a new DB mutation capability SIEP-11 accepts only
// through a mutation-registry successor — open loop #594. 0503 IS that successor
// and it carries the grant. What remained missing was the two READS the filing
// command performs: `select id from ops.service where key = $1`, and the five
// ops.release columns its INSERT returns. Migration 0505 grants exactly those,
// column-scoped, and tools/ops-record.py's `release candidate` now runs on the
// authority connection.
//
// WHAT THIS FILE ESTABLISHES, end to end and against a real database:
//
//   1. The REAL command files the row over a connection that authenticated as
//      carr_authority_joe, holding only carr_authority and explicitly not the
//      forbidden carr_writer bundle. Not a re-implementation of its SQL: the
//      actual
//      tools/ops-record.py, over a manifest the actual tools/release-manifest.py
//      built, so a grant this path needs and does not hold is a failure here.
//   2. The row's 0504 provenance columns mark it AUTHENTICATED — the filing
//      login recorded, the maker derived from it, the generated column true.
//   3. The Gate Zero seam store's own reader BINDS it, by git_sha, through
//      fetchCandidateBuildRecordRows and its real predicate.
//   4. AND THE FALSIFIER: the same command on the ordinary writer connection
//      produces a row the same reader does NOT bind. Without this, case 3 would
//      pass against a reader that admitted anything.
//
// THE PRODUCTION CREDENTIAL IS NEVER REACHABLE FROM HERE. tools/ops-record.py
// reads ~/.config/carr/db.env by `setdefault`, so an unset variable is silently
// re-supplied from a developer's real production DSNs — the accident that wrote
// 46 fabricated rows into production's ops.run in 2026-08. Every name
// credential_names() lists is set explicitly below, and HOME is pointed at an
// empty directory so there is no db.env to read at all.
//
// IT SKIPS WITHOUT A DATABASE and REFUSES a database that is not loopback.
//
//   DATABASE_URL=postgresql://localhost/... node --test \
//     mcp-server/test/gate-zero-candidate-authority-filing.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { fetchCandidateBuildRecordRows, isSeamStoreUnreachable }
  from "../src/gate-zero-seam-stores.v5.js";

const REPO = fileURLToPath(new URL("../../", import.meta.url));
const VENV_PYTHON = join(REPO, ".venv", "bin", "python");
const PYTHON = process.env.CARR_TEST_PYTHON || (existsSync(VENV_PYTHON) ? VENV_PYTHON : "python");
const DSN = process.env.CARR_GATE_ZERO_RACE_DSN || process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_GATE_ZERO_RACE_REQUIRED === "1";
const LOOPBACK = /@(localhost|127\.0\.0\.1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

const PROVIDER = "cloudflare-workers";
const AUTHORITY_LOGIN = "carr_authority_joe";
const PROVIDER_VERSION_AUTHORITY = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa";
const PROVIDER_VERSION_WRITER = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb";

/**
 * EVERY VARIABLE tools/ops-record.py WILL READ A DSN FROM, read out of the tool
 * itself rather than listed here. `credential_names()` is public in that file
 * for exactly this reason: a suite that blinds the names it remembers is not
 * blinding the names the tool reads.
 */
function credentialNames() {
  const out = execFileSync(PYTHON, ["-c", `
import importlib.util, json, pathlib
spec = importlib.util.spec_from_file_location("r", pathlib.Path(${JSON.stringify(REPO)}) / "tools" / "ops-record.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(json.dumps(list(m.credential_names())))
`], { cwd: REPO, encoding: "utf8" });
  const names = JSON.parse(out);
  assert.ok(names.includes("CARR_DB_AUTHORITY_JOE_URL") && names.includes("DATABASE_URL"),
    "ops-record.py's credential list no longer names the two DSNs this proof pins");
  return names;
}

/** The DSN with its user replaced, retaining the throwaway cluster password. */
function asUser(dsn, user) {
  const url = new URL(dsn);
  url.username = encodeURIComponent(user);
  return url.toString();
}

test("a candidate filed on the authority connection is authenticated, and the Gate Zero store reads it",
  async t => {
    if (!DSN) {
      assert.equal(REQUIRED, false,
        "this proof was required and no database URL was given to it");
      return t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
    }
    assert.ok(LOOPBACK.test(DSN),
      "REFUSED: this proof writes release rows and runs against a throwaway only");

    let pg;
    let client;
    try {
      const mod = await import("pg");
      const PG = mod.default ?? mod;
      pg = PG;
      client = new PG.Client({ connectionString: DSN });
      await client.connect();
    } catch (error) {
      if (client) await client.end().catch(() => {});
      assert.equal(REQUIRED, false,
        `this proof was required and Postgres was unreachable: ${error.message}`);
      return t.skip(`no reachable Postgres: ${error.message}`);
    }
    t.after(() => client.end().catch(() => {}));

    // THE HUMAN AUTHORITY LOGIN, which db/schema.sql deliberately does NOT
    // create: it is provisioned in the database provider's console, and minting
    // one in a snapshot would manufacture a login that authenticates as Joe's
    // authority principal on every machine that rebuilds. On a throwaway cluster
    // it is made here, given the base fixture's ephemeral password when one is
    // present, and joined to the bundle exactly as migration 0273 joins it in
    // production. Keeping that password makes this proof work both with the
    // local trust-auth cluster and GitHub's password-authenticated service.
    await client.query(`
      do $$
      begin
        if not exists (select 1 from pg_roles where rolname = '${AUTHORITY_LOGIN}') then
          execute 'create role ${AUTHORITY_LOGIN} login';
        else
          execute 'alter role ${AUTHORITY_LOGIN} login';
        end if;
      end $$;`);
    await client.query(`grant carr_authority to ${AUTHORITY_LOGIN}`);
    const authorityPassword = decodeURIComponent(new URL(DSN).password);
    if (authorityPassword) {
      const passwordStatement = (await client.query(
        "select format('alter role %I login password %L', $1::text, $2::text) as sql",
        [AUTHORITY_LOGIN, authorityPassword])).rows[0].sql;
      await client.query(passwordStatement);
    }

    // THE EXACT ROLE AND GRANTS 0503 AND 0505 CARRY, asked of the database
    // rather than assumed from the files. The login holds only carr_authority,
    // and all seven columns the real command reads are named here. If any 0505
    // column grant is absent, or a broad grant masks it, this is where the proof
    // stops instead of passing on carr_writer and failing later in production.
    const memberships = (await client.query(`
      select granted.rolname
        from pg_auth_members membership
        join pg_roles granted on granted.oid = membership.roleid
        join pg_roles member on member.oid = membership.member
       where member.rolname = $1
       order by granted.rolname`, [AUTHORITY_LOGIN])).rows.map(row => row.rolname);
    assert.deepEqual(memberships, ["carr_authority"],
      "the synthetic authority login must hold only the production authority bundle");

    const acl = (await client.query(`
      select pg_has_role($1, 'carr_authority', 'member') as authority_member,
             pg_has_role($1, 'carr_writer', 'member') as forbidden_writer_member,
             has_table_privilege($1,'ops.release','insert') as release_insert,
             has_column_privilege($1,'ops.service','key','select') as service_key,
             has_column_privilege($1,'ops.service','id','select') as service_id,
             has_table_privilege($1,'ops.service','select') as service_whole_table,
             has_column_privilege($1,'ops.release','id','select') as release_id,
             has_column_privilege($1,'ops.release','release_key','select') as release_key,
             has_column_privilege($1,'ops.release','maker_actor','select') as release_maker,
             has_column_privilege($1,'ops.release','maker_session_user','select')
               as release_session_user,
             has_column_privilege($1,'ops.release','maker_authority_verified','select')
               as release_marker,
             has_table_privilege($1,'ops.release','select') as release_whole_table,
             has_column_privilege($1,'ops.release','plan_hash','select')
               as release_outside_column`, [AUTHORITY_LOGIN]))
      .rows[0];
    assert.equal(acl.authority_member, true, "the synthetic login lacks carr_authority");
    assert.equal(acl.forbidden_writer_member, false,
      "the synthetic authority login inherits the forbidden carr_writer bundle");
    assert.equal(acl.release_insert, true, "0503's insert on ops.release is absent");
    assert.equal(acl.service_key, true, "0505's ops.service.key read is absent");
    assert.equal(acl.service_id, true, "0505's ops.service.id read is absent");
    assert.equal(acl.service_whole_table, false,
      "the ops.service read is a whole-table grant, which 0505 deliberately did not give");
    assert.equal(acl.release_id, true, "0505's ops.release.id read is absent");
    assert.equal(acl.release_key, true, "0505's ops.release.release_key read is absent");
    assert.equal(acl.release_maker, true, "0505's ops.release.maker_actor read is absent");
    assert.equal(acl.release_session_user, true,
      "0505's ops.release.maker_session_user read is absent");
    assert.equal(acl.release_marker, true, "0505's ops.release provenance read is absent");
    assert.equal(acl.release_whole_table, false,
      "the ops.release read is a whole-table grant, which 0505 deliberately did not give");
    assert.equal(acl.release_outside_column, false,
      "the ops.release read reaches a column outside 0505's five-column grant");

    // A HOME WITH NO db.env IN IT. This is the blind, not a convenience: with
    // HOME pointed here, _load_db_env() finds nothing and cannot re-supply a
    // production DSN for any name this test forgot.
    const blindHome = mkdtempSync(join(tmpdir(), "carr-candidate-filing-home-"));
    const work = mkdtempSync(join(tmpdir(), "carr-candidate-filing-"));
    t.after(() => { for (const d of [blindHome, work]) rmSync(d, { recursive: true, force: true }); });

    const authorityDsn = asUser(DSN, AUTHORITY_LOGIN);
    const blinded = Object.fromEntries(credentialNames().map(name =>
      [name, "postgresql://nobody@127.0.0.1:1/absent"]));
    const opsRecord = (env, ...args) => spawnSync(
      PYTHON, [join(REPO, "tools", "ops-record.py"), ...args],
      { cwd: REPO, encoding: "utf8", timeout: 300000,
        env: { ...process.env, ...blinded, HOME: blindHome, ...env } });

    // The service catalog this candidate's foreign key points at, applied
    // through the tool's own registry sync rather than inserted here.
    const synced = opsRecord({ DATABASE_URL: DSN }, "sync-registry");
    assert.equal(synced.status, 0, `sync-registry failed: ${(synced.stderr || "").slice(-400)}`);

    // ONE REAL MANIFEST, built and provider-bound by the canonical tool. A
    // synthetic shape would be refused by the intake path before any credential
    // was reached, which would prove nothing about the credential.
    const head = execFileSync("git", ["-C", REPO, "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    const buildManifest = versionId => {
      const source = spawnSync(PYTHON,
        [join(REPO, "tools", "release-manifest.py"), "build", "--sha", head,
          "--environment", "production",
          "--performance-budget-ref", "runbook:worker-performance-v1",
          "--performance-budget-ms", "1500",
          "--recovery-strategy", "rollback",
          "--rollback-plan-ref", "runbook:rollback-worker-v1"],
        { cwd: REPO, encoding: "utf8", timeout: 300000 });
      assert.equal(source.status, 0,
        `the source manifest did not build: ${(source.stderr || "").slice(-400)}`);
      const sourcePath = join(work, `source-${versionId}.json`);
      writeFileSync(sourcePath, source.stdout);
      const bound = spawnSync(PYTHON,
        [join(REPO, "tools", "release-manifest.py"), "bind-provider",
          "--manifest", sourcePath, "--provider", PROVIDER,
          "--provider-version-id", versionId],
        { cwd: REPO, encoding: "utf8", timeout: 300000 });
      assert.equal(bound.status, 0,
        `the provider binding failed: ${(bound.stderr || "").slice(-400)}`);
      const boundPath = join(work, `bound-${versionId}.json`);
      writeFileSync(boundPath, bound.stdout);
      return boundPath;
    };

    const fileCandidate = (key, versionId, env) => opsRecord(env,
      "release", "candidate", "--key", key, "--manifest", buildManifest(versionId),
      "--service", "carr-mcp", "--environment", "production",
      "--provider", PROVIDER, "--provider-version-id", versionId,
      "--test-evidence", "ops/ci.sh#candidate-filing-proof",
      "--security-evidence", "ops/ci.sh#candidate-filing-proof");

    // ── (1) THE REAL COMMAND, ON THE AUTHORITY CONNECTION ───────────────────
    // Only CARR_DB_AUTHORITY_JOE_URL points anywhere real. DATABASE_URL stays
    // blinded to a dead port, so a command that reached for the writer
    // connection instead would fail rather than quietly file an unauthenticated
    // row — which is the exact defect this proof exists for.
    const filed = fileCandidate("gate-zero-authority-filing", PROVIDER_VERSION_AUTHORITY,
      { CARR_DB_AUTHORITY_JOE_URL: authorityDsn });
    assert.equal(filed.status, 0,
      `the candidate did not file on the authority connection: ${(filed.stderr || "").slice(-600)}`);
    // The tool prints what the DATABASE recorded, which is the line a deploy log
    // carries; asserting it keeps the wrapper's own report honest.
    assert.match(filed.stderr, /maker joe filed by carr_authority_joe \(authority-verified\)/);

    // ── (2) THE 0504 PROVENANCE COLUMNS ─────────────────────────────────────
    const row = (await client.query(
      `select maker_session_user, maker_actor, maker_verification_ref,
              maker_authority_verified, source_kind, git_sha, state
         from ops.release where release_key = $1`,
      ["gate-zero-authority-filing"])).rows[0];
    assert.ok(row, "the authority-filed candidate left no row");
    assert.equal(row.maker_session_user, AUTHORITY_LOGIN,
      "the database did not record the authority login as the filer");
    assert.equal(row.maker_authority_verified, true,
      "0504 marked the authority-filed candidate unauthenticated");
    assert.equal(row.maker_actor, "joe");
    assert.equal(row.maker_verification_ref, "ops.authority-principal:joe");
    assert.equal(row.source_kind, "wrapper");
    assert.equal(row.git_sha, head);

    // ── (3) THE GATE ZERO STORE BINDS IT ────────────────────────────────────
    // The shipped reader, over its real predicate. DATABASE_URL_READER is what
    // readOnlyStatements opens, and it is set to this throwaway for the call and
    // put back afterwards.
    const priorReader = process.env.DATABASE_URL_READER;
    process.env.DATABASE_URL_READER = DSN;
    t.after(() => {
      if (priorReader === undefined) delete process.env.DATABASE_URL_READER;
      else process.env.DATABASE_URL_READER = priorReader;
    });
    const bound = await fetchCandidateBuildRecordRows({ gitSha: head });
    assert.equal(isSeamStoreUnreachable(bound), false,
      "the candidate-build store was unreachable over a row it should bind");
    assert.equal(bound.rows.length, 1,
      "the Gate Zero store did not bind exactly the authority-filed candidate");
    assert.equal(bound.rows[0].maker_actor, "joe");

    // ── (4) THE FALSIFIER: THE WRITER-FILED ROW IS NOT READ ─────────────────
    // The same command, same manifest shape, on the ordinary ledger writer —
    // which is where the refused candidate filed. 0504 marks it unauthenticated
    // and the reader must not return it. Without this, case (3) would pass
    // against a reader that admitted any row for the revision.
    //
    // IT USES A SECOND REVISION, because 0504's partial unique index admits one
    // authority-filed row per git_sha and the store refuses an ambiguity — so
    // the honest way to ask "is an unauthenticated row read?" is to ask it about
    // a revision that has no authenticated row at all.
    const otherSha = execFileSync("git", ["-C", REPO, "rev-parse", "HEAD~1"],
      { encoding: "utf8" }).trim();
    await client.query(
      `insert into ops.release
         (correlation_id, release_key, service_id, environment, state, git_sha,
          provider, provider_version_id, source_kind, source_ref, maker_verification_ref)
       select gen_random_uuid(), $1, s.id, 'production', 'candidate', $2,
              $3, $4, 'wrapper', 'tools/release-manifest.py', null
         from ops.service s where s.key = 'carr-mcp'`,
      ["gate-zero-writer-filed", otherSha, PROVIDER, PROVIDER_VERSION_WRITER]);
    const writerRow = (await client.query(
      `select maker_session_user, maker_authority_verified, maker_verification_ref
         from ops.release where release_key = $1`, ["gate-zero-writer-filed"])).rows[0];
    assert.notEqual(writerRow.maker_session_user, AUTHORITY_LOGIN);
    assert.equal(writerRow.maker_authority_verified, false,
      "a row filed on a non-authority login was marked authenticated");
    assert.match(writerRow.maker_verification_ref, /^ops\.session-login:/);

    const unbound = await fetchCandidateBuildRecordRows({ gitSha: otherSha });
    assert.equal(isSeamStoreUnreachable(unbound), false);
    assert.equal(unbound.rows.length, 0,
      "the Gate Zero store read a candidate row the database marked unauthenticated");
  });

// release.js — builds the /release payload: what code this Worker is
// actually running, so a verification pass never has to guess (Phase 1,
// 2026-08-13, closing the deploy-provenance gap named in the Phase 0 audit).
//
// WHY A SEPARATE FILE. index.js imports OAuthProvider from
// @cloudflare/workers-oauth-provider, which pulls in `cloudflare:` runtime
// modules — that file cannot be loaded outside the Worker runtime, so
// nothing implemented there is provable before a deploy (see index.js's
// "local token" comment, same reasoning identity.js was split out for).
// This module holds no cloudflare: import and no OAuth machinery, so
// node --test can exercise the actual payload-building logic with a fake
// env and a fake `sql` tag function — no live database, no deploy required.
//
// FOUR FIELDS, EACH HONEST ON ITS OWN:
//   verb_count          — Object.keys(TOOLS).length, computed from the code
//                          bundled INTO THIS DEPLOY. Never a written marker
//                          (mcp-server/.last-deployed-verb-count is exactly
//                          that, and going stale is the gap this endpoint
//                          closes) — this number cannot lie about what
//                          shipped because it is read from the shipped code.
//   git_sha              — the ONE field with no runtime source: a Worker
//                          cannot read git at request time. Stamped in at
//                          deploy time as env.GIT_SHA (bin/deploy-worker.sh:
//                          `wrangler deploy --var GIT_SHA:<sha>`). Missing
//                          means a deploy happened OUTSIDE that script —
//                          reported as null + a reason, never guessed or
//                          left silently absent.
//   schema                — schema_migrations is tools/migrate.py's ledger
//                          of applied migrations, read the same way
//                          tools/migrate.py itself reads it. It is NOT
//                          ground truth: tools/ledger-repair.py exists
//                          because this exact table drifted from reality
//                          once already (four migrations physically applied
//                          to production outside the runner were invisible
//                          to it until repaired 2026-07-31). So this field
//                          reports what the TRACKING TABLE CLAIMS, and says
//                          so in its own `note`, every time.
//   doctrine_generation   — same query doctrine.js's standing-context verb
//                          already runs (`select generation from
//                          doctrine_meta where id = 1`), reused rather than
//                          reinvented.
//
// A field that cannot be read returns { value: null, reason: "<why>" } (or
// the schema-shaped equivalent) — never omitted, never a guess. A stale or
// absent value must be visibly absent, per the honesty requirement this was
// built against.

export async function buildRelease({ env, sql, verbCount, now = () => new Date() }) {
  const sha = (env && env.GIT_SHA) || null;

  let schema;
  try {
    const rows = await sql`
      select count(*)::int as applied_count, max(filename) as highest_applied_migration
        from schema_migrations`;
    const row = (rows && rows[0]) || {};
    schema = {
      highest_applied_migration: row.highest_applied_migration ?? null,
      applied_count: row.applied_count != null ? Number(row.applied_count) : 0,
      reason: null,
    };
  } catch (e) {
    schema = {
      highest_applied_migration: null,
      applied_count: null,
      reason: "database unreachable: " + String((e && e.message) || e).slice(0, 200),
    };
  }

  let doctrineGeneration;
  try {
    const rows = await sql`select generation from doctrine_meta where id = 1`;
    doctrineGeneration = rows && rows.length
      ? { value: Number(rows[0].generation), reason: null }
      : { value: null, reason: "doctrine_meta has no row with id=1" };
  } catch (e) {
    doctrineGeneration = {
      value: null,
      reason: "database unreachable: " + String((e && e.message) || e).slice(0, 200),
    };
  }

  return {
    ok: true,
    ts: now().toISOString(),
    verb_count: verbCount,
    git_sha: sha
      ? { value: sha, reason: null }
      : { value: null, reason: "not stamped: deployed outside bin/deploy-worker.sh" },
    schema: {
      highest_applied_migration: schema.highest_applied_migration,
      applied_count: schema.applied_count,
      reason: schema.reason,
      note: "what the tracking table claims, not ground truth — tools/ledger-repair.py "
          + "records a past drift where schema_migrations fell behind migrations already "
          + "applied to production",
    },
    doctrine_generation: doctrineGeneration,
  };
}

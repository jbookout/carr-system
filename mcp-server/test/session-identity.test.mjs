// WR-000117 — the session-identity read pair's acceptance proofs.
//
// EVERY IDENTITY CASE MINTS TWO ACTORS. Both verbs derive the acting actor
// server-side, so a case that mints ONE actor and asserts ONE answer passes
// under an implementation that ignores the derivation entirely. A single-actor
// case is a false pass by construction, and there is not one in this file.
//
// EVERY FIXTURE TIMESTAMP IS RELATIVE TO now(). work_state separates idle from
// disconnected by an age, and a hard-coded instant passes for months and fails
// on one run.
//
// The store cases skip in the unit class; the migration class supplies
// DATABASE_URL and sets CARR_SESSION_IDENTITY_DB_REQUIRED=1, which turns a skip
// into a failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { sessionIdentityTools, sessionIdentityProjection, sessionDispatchProjection }
  from "../src/session-identity.js";
import { TOOLS } from "../src/tools.js";
import { PROFILES } from "../src/mcp.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_SESSION_IDENTITY_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

class ToolError extends Error {
  constructor(fields) { super(fields.error); Object.assign(this, fields); }
}

const tools = sessionIdentityTools({ ToolError });

async function skipUnlessDatabase(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof seeds four session books and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

const wrap = client => ({ query: (text, values = []) => client.query(text, values) });

/**
 * ONE fixture, TWO actors, rolled back whole. The four books stamp updated_at
 * from BEFORE triggers, so the ageing rows are seeded with the session's
 * replication role relaxed and every READ below runs with it restored --
 * nothing the FUNCTIONS do is measured under a relaxed session.
 */
async function seeded(pg, fn) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  const tag = randomUUID().slice(0, 8);
  const alpha = `si-${tag}-alpha`;
  const beta = `si-${tag}-beta`;
  try {
    await client.query("begin");
    const actors = await client.query(
      `insert into public.actor(slug, kind, display_name, active)
       values ($1,'automation','WR117 alpha',true), ($2,'automation','WR117 beta',true)
       returning id, slug`, [alpha, beta]);
    const idOf = slug => actors.rows.find(row => row.slug === slug).id;
    const requests = await client.query(
      `insert into ops.work_request(ref, title, requester_actor)
       values ($1,'WR117 node proof alpha',$3), ($2,'WR117 node proof beta',$3)
       returning id, ref`,
      [`WR-117-NODE-A-${tag}`, `WR-117-NODE-B-${tag}`, alpha]);
    await client.query("set local session_replication_role = replica");
    await client.query(
      `insert into public.claude_continuity_leaf
        (organization_tenant_id, surface_principal_actor_id, owner_actor_id, session_id,
         transcript_path_digest, project_affinity, parent_session_id, native_agent_id,
         latest_cwd, latest_model_id, created_at, updated_at)
       values
        ('wr117',$1,$1,$3,repeat('a',64),'wr117-project',$4,'agent-child','/wr117','claude-opus-5',
         now() - interval '2 minutes', now() - interval '2 minutes'),
        ('wr117',$1,$1,$4,repeat('b',64),'wr117-project',null,'agent-root','/wr117','claude-opus-5',
         now() - interval '45 minutes', now() - interval '45 minutes'),
        ('wr117',$2,$2,$5,repeat('c',64),'wr117-project',null,'agent-beta','/wr117','claude-opus-5',
         now() - interval '3 days', now() - interval '3 days')`,
      [idOf(alpha), idOf(beta), `${tag}-claude-child`, `${tag}-claude-root`, `${tag}-claude-beta`]);
    await client.query(
      `insert into public.codex_continuity_checkpoint
        (organization_tenant_id, owner_actor_id, native_task_id, project_id, cwd, state,
         created_at, updated_at)
       values ('wr117',$1,$3,'wr117-project','/wr117','{}',
               now() - interval '5 minutes', now() - interval '5 minutes'),
              ('wr117',$2,$4,'wr117-project','/wr117','{}',
               now() - interval '5 minutes', now() - interval '5 minutes')`,
      [idOf(alpha), idOf(beta), `${tag}-codex-alpha`, `${tag}-codex-beta`]);
    const sessions = await client.query(
      `insert into ops.capability_agent_session
        (work_request_id, executor_actor_id, created_by_actor_id, state, source_commit_sha,
         worktree_ref, started_at, updated_at)
       values ($1,$3,$3,'in_progress',repeat('1',40),$5,
               now() - interval '4 minutes', now() - interval '4 minutes'),
              ($2,$4,$4,'in_progress',repeat('2',40),$6,
               now() - interval '4 minutes', now() - interval '4 minutes')
       returning id, worktree_ref`,
      [requests.rows[0].id, requests.rows[1].id, idOf(alpha), idOf(beta),
        `${tag}-alpha-tree`, `${tag}-beta-tree`]);
    await client.query(
      `insert into public.session_work(id, kind, title, last_seen)
       values ($1,'worktree','wr117 harvested worktree', now() - interval '1 minute')`,
      [`worktree:${tag}-harvested`]);
    await client.query(
      `insert into public.partner_room_turn(room_id, sponsor, seat, kind, body, msg_id,
                                            origin_channel, origin_actor, at)
       values ('model-room','joe',$2,'turn',$3,gen_random_uuid(),'mcp',$1, now() - interval '30 minutes'),
              ('model-room','joe',$2,'turn',$4,gen_random_uuid(),'mcp',$1, now() - interval '10 minutes'),
              ('model-room','joe','si-outsider','turn',$5,gen_random_uuid(),'mcp','si-outsider',
               now() - interval '20 minutes')`,
      [alpha, beta,
        `dispatch one for ${tag}-claude-child`,
        `dispatch two for ${tag}-claude-child, superseding the first`,
        `a turn naming ${tag}-claude-child that neither actor sent`]);
    await client.query("set local session_replication_role = origin");
    const acting = async slug => {
      await client.query("select set_config('carr.acting_actor_slug',$1,true)", [slug]);
    };
    await fn({
      client, c: wrap(client), tag, alpha, beta, acting,
      capabilityAlpha: sessions.rows[0].id,
      requestAlpha: requests.rows[0].id,
    });
  } finally {
    await client.query("rollback").catch(() => {});
    await client.end();
  }
}

// ---------------------------------------------------------------------------

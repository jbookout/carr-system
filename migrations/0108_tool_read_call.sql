-- 0108_tool_read_call.sql — Phase 1: read verbs leave a trace too.
--
-- THE GAP (verified 2026-08-13, chair-authorized Phase 1 instrumentation work).
-- withEnvelope() in tools.js is the ONLY writer of tool_call, and it runs
-- exclusively inside WRITE handler bodies (mcp.js's callTool: the write
-- branch opens a carr_writer transaction and calls executeRegisteredTool
-- through it; the read branch calls executeRegisteredTool DIRECTLY against a
-- carr_reader connection, with no envelope at all). Consequence: 2,838
-- tool_call rows over the 14 days checked, every one a write, zero read calls
-- ever recorded — for anyone, ever. The system could not answer "did a
-- session boot from the store, and whose?", which is exactly the question
-- blocking confirmation of Dell's 2026-08-21 cutover (ops/cutover-readiness.py,
-- shipped the same day) and a question that should be answerable passively
-- rather than by a human running a manual test.
--
-- CHECKED FIRST WHETHER THIS WAS DELIBERATE. Searched decision history
-- (v_decision_entry) for "tool_call", "read verb", "logging", "unlogged" —
-- found the ORIGINAL schema decision that created tool_call as a WRITE-replay
-- table (idempotency via tool_call replay table, 2026-07-30 blueprint) and
-- the ORDER 7 amendment-11 ruling about carr_reader being views-only (a
-- PERMISSIONS decision, about which tables reads may touch, not a decision
-- to leave reads unrecorded). No ruling anywhere says reads should stay
-- unlogged. This is an oversight of the write-envelope design, not a
-- deliberate omission — proceeding.
--
-- WHY A SIBLING TABLE, NOT A REUSE OF tool_call. tool_call's shape is built
-- entirely around WRITE replay semantics that do not apply to reads:
--   * idempotency_key is the PRIMARY KEY and the whole POINT of the table —
--     a caller-supplied key that withEnvelope looks up to detect and replay a
--     retried write. Reads carry no such key; every value would be a
--     synthetic, meaningless UUID, and the column name would lie about what
--     it holds.
--   * request_hash exists to CATCH a key reused with different arguments
--     (key_reuse). There is no such concept for a read — nothing replays a
--     read, and there is nothing to detect reuse against.
--   * response jsonb NOT NULL exists to SERVE a replay — it is the actual
--     return value withEnvelope hands back on the second call with the same
--     key. Storing a response for a read would (a) require faking a value to
--     satisfy the NOT NULL constraint, since the instrumentation is
--     explicitly forbidden from recording read response bodies (privacy +
--     size), or (b) loosening a constraint that exists for a completely
--     different reason on the write side, silently changing what a null
--     response is allowed to mean for every future write, an audit-integrity
--     risk to avoid for the sake of read logging.
-- Mixing reads into tool_call would force every one of those mismatches and
-- leave a table where half the rows answer "what would this write produce on
-- retry" and the other half answer "who looked, and when" — two unrelated
-- questions sharing one schema because the queries happen to look similar.
-- A sibling table keeps both questions honest in the columns that actually
-- describe them.
--
-- GROWTH ESTIMATE, from real numbers (not invented precision). tool_call
-- carried 2,838 rows over its first 14 days live (~203 write rows/day) across
-- 74 registered write verbs. The registry carries 29 read verbs today,
-- including the one every session calls at boot (standing-context) plus the
-- ones a normal session reaches for repeatedly before ever writing anything
-- (find, catch-me-up, deal-board, doctrine-index, read-doctrine, today-triage).
-- Reads structurally out-volume writes in an agentic session — a session
-- reads several times for every write it makes. A conservative 3-5x
-- multiplier on the write rate puts read volume at roughly 600-1,000
-- rows/day, or 8,000-14,000 rows over the same 14-day window tool_call was
-- measured against. This is an ESTIMATE stated so the next grower of this
-- table has a real number to check against, not a guess dressed as one.
--
-- LIFECYCLE RULE AT CREATION (house rule; no unbounded accumulator without
-- one). This table follows the SAME policy migration 0071 already settled
-- for tool_call, event, record_flag and friends: "the no-DELETE posture
-- stands... forgetting here means EXPIRING-AS-UNVERIFIED and SEEING the
-- slope, never erasing history." Nothing about a read log changes that
-- reasoning — deleting rows here for the sake of tidiness would be inventing
-- a new posture for one table rather than following the one this system
-- already committed to for its audit-shaped accumulators. The lifecycle
-- mechanism is observability, not deletion: this table is added to
-- forgetting-check.py's TRACKED list in the same change, so growth_snapshot
-- (0071) and v_growth_slope start carrying it from day one, and a monthly
-- size sweep (CLAUDE.md's "monthly playbook review and size sweep, the
-- 15th, prunes bloat") is the standing venue where an actual TTL-prune
-- policy gets decided IF the slope this view now makes visible ever
-- justifies one. No metric without a bound action (rule 590b11e1): the bound
-- action here is that venue, not a guess at a retention window made today
-- with no real growth data to check it against.
--
-- WHAT IS STORED, AND WHAT NEVER IS. Identity/provenance columns only —
-- exactly tool_call's own audit columns (0037, 0095) minus the write-replay
-- machinery: verb, actor, via, client_id, sponsoring_human_slug,
-- personal_scope, authorization_class, organization_tenant_id, created_at.
-- Plus ok/error_kind so a FAILED read still leaves a row (a refused or
-- errored read is still evidence someone tried to boot or read something,
-- which is exactly the fact this table exists to keep). NEVER stored:
-- argument values, response bodies, or raw exception messages/details —
-- error_kind is a short caller-facing error code only (e.g. "not_found",
-- "unknown_marker"), the same vocabulary ToolError payloads already use
-- elsewhere, never a message that could carry an argument value through it.
--
-- GRANTS. The Worker's read branch calls this from a carr_writer connection
-- (DATABASE_URL_WRITER) via ctx.waitUntil, fired AFTER the response is sent —
-- see mcp-server/src/mcp.js's recordReadCall — so carr_writer needs INSERT.
-- ops/cutover-readiness.py's boot-evidence line (and any future health
-- check or `find`-style read) needs SELECT, granted to carr_reader (direct
-- table grant, same posture 0071 used for growth_snapshot) and explicitly
-- also to carr_exporter — belt-and-braces, following 0107's own lesson that
-- carr_exporter's membership in carr_reader has NOT reliably meant it
-- inherits every reader grant in practice on this schema, so a table an
-- exporter script needs gets an explicit grant rather than a hoped-for one.
-- carr_jobs is DELIBERATELY NOT granted here, matching its siblings: 0107
-- notes carr_jobs holds no SELECT on tool_call, event, record_flag, activity,
-- source_capture or loop_item either — ORDER 19(a) keeps carr_jobs' grants
-- "exactly as listed, nothing wider", and widening it for this table alone,
-- as an incidental side effect of an unrelated change, is exactly the kind
-- of scope creep that rule exists to refuse. (forgetting-check.py's growth
-- snapshot for this table will therefore skip today, same as it already
-- silently skips for those five siblings — a pre-existing gap, not one this
-- migration introduces or is the right place to fix.)

begin;

create table tool_read_call (
  id                      uuid primary key default gen_random_uuid(),
  verb                    text not null,
  actor_slug              text not null,
  actor_id                uuid references actor(id),
  ok                      boolean not null default true,
  error_kind              text,
  via                     text,
  client_id               text,
  organization_tenant_id  text,
  sponsoring_human_slug   text,
  personal_scope          text,
  authorization_class     text,
  created_at              timestamptz not null default now(),
  check ((ok = true and error_kind is null) or (ok = false and error_kind is not null)),
  check (sponsoring_human_slug is null or sponsoring_human_slug in ('joe', 'dell')),
  check (personal_scope is null or personal_scope in ('joe-personal', 'dell-personal', 'none'))
);

comment on table tool_read_call is
  'Phase 1 (0108). One row per READ-verb call through the MCP Worker — the '
  'read-side sibling of tool_call''s write-replay log, not a reuse of it '
  '(see file header for why they are separate tables). Identity/provenance '
  'only: verb, who, via which surface, when, ok/error_kind. Never an argument '
  'value or a response body. No DELETE — observed through growth_snapshot '
  '(0071) like every other audit-shaped accumulator, never pruned in place.';
comment on column tool_read_call.error_kind is
  'A short caller-facing ToolError code only (e.g. not_found, unknown_marker) '
  'when ok=false. Never a raw exception message or constraint detail, which '
  'can carry argument values through it.';

-- boot-evidence lookup: latest standing-context call per sponsor, the exact
-- query ops/cutover-readiness.py adds in this same change.
create index tool_read_call_verb_sponsor_idx
  on tool_read_call (verb, sponsoring_human_slug, created_at desc);
create index tool_read_call_created_at_idx on tool_read_call (created_at);

grant insert on tool_read_call to carr_writer;
grant select on tool_read_call to carr_reader, carr_exporter;

do $$
begin
  if not has_table_privilege('carr_writer', 'tool_read_call', 'insert') then
    raise exception '0108: carr_writer cannot insert tool_read_call — read-call recording stays a no-op';
  end if;
  if has_table_privilege('carr_writer', 'tool_read_call', 'delete') then
    raise exception '0108: carr_writer holds DELETE on tool_read_call — no role in this schema deletes (A9)';
  end if;
  if not has_table_privilege('carr_reader', 'tool_read_call', 'select') then
    raise exception '0108: carr_reader cannot select tool_read_call';
  end if;
  if not has_table_privilege('carr_exporter', 'tool_read_call', 'select') then
    raise exception '0108: carr_exporter cannot select tool_read_call — cutover-readiness stays blind to boot evidence';
  end if;
  if has_table_privilege('carr_jobs', 'tool_read_call', 'select') then
    raise exception '0108: carr_jobs unexpectedly holds a grant on tool_read_call — ORDER 19(a) keeps its grants exactly as listed';
  end if;
end $$;

commit;

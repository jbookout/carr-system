-- 0037_write_provenance.sql — record WHICH SURFACE made each write.
--
-- Joe, 2026-08-02, on the attribution gap: "lets do it" (do it before Dell is in the
-- system, not after).
--
-- WHAT THIS FIXES, STATED HONESTLY, BECAUSE THE ORIGINAL SPEC OVERSTATED IT.
-- specs/agent-write-attribution.md proposed deriving actor from connection identity so an
-- agent write could not masquerade as a human one. Half of that was already true:
-- identity.js maps a verified Google identity to joe/dell, and no verb takes an actor
-- argument. The other half does not work the way the spec assumed:
--
--   Every write already comes through an agent. Joe never touches the database; he tells
--   Claude and Claude writes. So there is no transport-level signal that separates "Joe
--   decided this" from "the agent decided this" — that is INTENT, and connection identity
--   cannot carry it. A boolean claiming otherwise would be a guess stored as a fact.
--
-- So this migration records only what the server actually knows, and does not pretend to
-- more:
--   via       — how the caller authenticated ('oauth-google' | 'partner-token-legacy').
--               Already present in ctx.props since the OAuth build; actorFromProps was
--               dropping it, so it never reached a row.
--   client_id — WHICH OAuth client holds the grant. Distinguishes surfaces (Claude Code,
--               a phone connector, a script), server-derived from the grant, never passed
--               by the caller. An attestation the writer controls is worthless; this one
--               the writer cannot set.
--
-- THE INTENT SIGNAL IS ALREADY IN THE SCHEMA AND IS NOT THIS: event.human_quote. A write
-- carrying the partner's verbatim words is human-directed; one without is not. That is the
-- same no-fabrication rule log-decision applies with quote_absent. Read human_quote for
-- "did a human drive this", and via/client_id for "what came in over the wire".
--
-- Both columns are nullable with no default and no backfill: rows written before this
-- migration genuinely do not know their surface, and a blank that reads as unknown is the
-- correct record. Per the standing no-retroactive-backfill rule, they stay blank.

begin;

alter table event     add column if not exists via       text;
alter table event     add column if not exists client_id text;
alter table tool_call add column if not exists via       text;
alter table tool_call add column if not exists client_id text;

comment on column event.via is
  'How the caller authenticated (oauth-google | partner-token-legacy). Server-derived from '
  'the grant props; never accepted as a verb argument. Null on rows written before 0037. '
  'This is NOT a human-vs-agent flag — read human_quote for that.';
comment on column event.client_id is
  'The OAuth client holding the grant: which SURFACE made the write. Server-derived, not '
  'caller-assertable. Null before 0037 and on the legacy token path, which has no client.';

commit;

-- guard: additive only. No existing row may have been touched, and the columns must be
-- nullable — a NOT NULL here would have required inventing a surface for 193k of history.
do $$
declare c int; notnull_cols int;
begin
  select count(*) into c from information_schema.columns
   where table_name in ('event','tool_call') and column_name in ('via','client_id');
  if c <> 4 then raise exception 'expected 4 provenance columns, found %', c; end if;

  select count(*) into notnull_cols from information_schema.columns
   where table_name in ('event','tool_call') and column_name in ('via','client_id')
     and is_nullable = 'NO';
  if notnull_cols <> 0 then
    raise exception '% provenance column(s) are NOT NULL — they must stay nullable so '
                    'pre-0037 rows read as unknown rather than being backfilled', notnull_cols;
  end if;

  raise notice 'write provenance columns live; existing rows left blank by design';
end $$;

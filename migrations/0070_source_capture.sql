-- 0070_source_capture.sql — the Source Material capture log becomes a record
-- (Joe, 2026-08-06: "Go ahead and build it", after asking whether the markdown
-- accumulators a session had just edited should be write-protected like the
-- loop files, "so Dell and I don't have to worry about two write issues").
--
-- THE DEFECT THIS CLOSES. DNA/Marketing/Source Material/INDEX.md is a table
-- wearing a markdown costume: one row per captured source, append-only, with a
-- check-before-capture dedup step ("if it's here, it's already absorbed"). That
-- dedup step is a classic two-writer race — Joe's session and Dell's session
-- both check the file, both find nothing, both append the same source. As a
-- record, the check becomes a query inside the write transaction and cannot
-- race. Both brains capture sources, so single-writer-by-seat (ORDER 38's
-- mechanism) does not fit; record-mastering (0024's mechanism) does.
--
-- WHY A DEDICATED TABLE AND NOT loop_item, stated because import_idea_bank.py
-- argues the other way for the idea bank. The idea bank fit loop_item because
-- its shape IS the loop shape: numbered rows with an open/close lifecycle. A
-- capture has neither — no number, and its states (merged / declined / queued)
-- are CONTENT, not lifecycle; a capture never closes, and close-loop's
-- refuse-without-outcome semantics would be wrong the day someone hit them.
-- The verb this table exists for is a dedup-first insert (log-capture), which
-- add-loop does not do and should not learn.
--
-- WHY THE FILE'S HEADER PROSE LIVES IN THE EXPORTER, not in a block table —
-- the one deliberate divergence from 0024's loop_block reasoning, flagged for
-- the seat to overrule. 0024 stored prose as data so Joe editing doctrine
-- would not be a code change. Since the corpus flip (decision 9b60a2d5,
-- 2026-08-06), doctrine's canonical home IS git and Drive is the render — so
-- "a prose change is a git edit the seat lands" is now the system's stated
-- direction, not a workaround. One stable header block does not justify a
-- scaffolding table.
--
-- The render target (exporters/targets.py "source-captures") makes
-- hooks/record-home-gate.py treat the INDEX.md path as generated automatically
-- — the gate parses targets.py live. Hand-edits stop the moment that code
-- lands, which is a few hours BEFORE this migration reaches production on
-- Joe's tap. That gap is accepted and reported: a capture in the window waits
-- for the verb rather than reopening the hand-edit path.

begin;

create table source_capture (
  id           uuid primary key default gen_random_uuid(),
  captured_on  date not null,
  session      text not null,        -- the 'Session' cell: what the source is,
                                     -- in the words a dedup check would search
  source_url   text,                 -- primary link when one exists; exact-match
                                     -- dedup key when present
  visibility   text not null default 'public'
               check (visibility in ('public','member_gated','colleague','internal')),
  status       text not null default 'merged'
               check (status in ('merged','declined','queued')),
  merge_note   text not null default '',
                                     -- the 'Status' cell verbatim: where it
                                     -- merged, what was declined and why. The
                                     -- knowledge itself lives in the domain
                                     -- playbooks; this is the pointer trail.
  version      int not null default 1,
  created_at   timestamptz not null default now(),
  created_by   uuid not null references actor(id),
  updated_at   timestamptz not null default now(),
  updated_by   uuid not null references actor(id)
);
create index source_capture_session_trgm on source_capture using gin (session gin_trgm_ops);
create index source_capture_date_idx on source_capture (captured_on);
create trigger source_capture_touch before update on source_capture
  for each row execute function trg_touch_row();

comment on table source_capture is
  'The Source Material capture log as records (0070). One row per learning '
  'source captured (podcast, article, video, portal session). Its ONE job is '
  'the dedup guard: log-capture checks here before any capture starts. '
  'Knowledge never lives here — it merges into the domain playbooks; '
  'merge_note records where. Renders to DNA/Marketing/Source Material/INDEX.md. '
  'No delete grant: a capture log never shrinks (a wrong row is corrected in '
  'place, versioned).';

create view v_export_source_captures as
select id, captured_on, session, source_url, visibility, status, merge_note
  from source_capture
 order by captured_on, created_at;

-- 0004's schema-wide grant was one-time (0023/0024 lesson): new tables grant
-- explicitly. No DELETE anywhere — the log never shrinks.
grant select, insert, update on source_capture to carr_writer;
grant select on v_export_source_captures to carr_reader;
grant select on v_export_source_captures to carr_writer;

commit;

-- Guards in their own transaction (0043 lesson) -------------------------------
do $$
declare n int;
begin
  select count(*) into n from information_schema.tables
   where table_name = 'source_capture';
  if n <> 1 then raise exception '0070: source_capture missing'; end if;

  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_writer' and table_name = 'source_capture'
     and privilege_type in ('SELECT','INSERT','UPDATE');
  if n <> 3 then raise exception '0070: carr_writer grants incomplete (% of 3)', n; end if;

  -- has_table_privilege, not role_table_grants: the table OWNER always shows
  -- DELETE in the grants view, which is not a grant anyone made (0024's form).
  if has_table_privilege('carr_writer', 'source_capture', 'delete') then
    raise exception '0070: DELETE granted to carr_writer — the log never shrinks';
  end if;

  select count(*) into n from information_schema.role_table_grants
   where grantee = 'carr_reader' and table_name = 'source_capture';
  if n <> 0 then raise exception '0070: carr_reader has a BASE TABLE grant — views-only is the leak guard'; end if;
end $$;

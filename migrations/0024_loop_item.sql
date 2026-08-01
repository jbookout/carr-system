-- 0024: loop_item + loop_block — the three markdown accumulators become records
-- (ORDER 31, Phase A; binding design record-layer/one-writer-design-2026-07-31.md,
-- Joe's ruling 2026-07-31 night: "the thing i dont like is the two writer system
-- creating issues when we are both writing to the same file... if i do something
-- in my session, i want dell to be able to instantly recall it in his session").
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHAT MOVES. Four files stop being hand-written and become generated renders on
-- the A8 gate, exactly like the registry did at the Wave 1 cutover:
--     00_Context/open-loops.md          (kind open_loop, the hot half)
--     00_Context/open-loops-backlog.md  (kind open_loop, the cold half)
--     DNA/Team/action-required.md       (kind action_required)
--     DNA/Team/team-loops.md            (kind team_loop)
-- Instant recall = Dell's session asks the verbs and gets an answer in seconds;
-- the rendered files follow on the refresh cadences, unchanged in shape so the
-- heartbeat, the Monday brief and Dell's own reads keep working untouched.
--
-- WHY TWO TABLES AND NOT THE DESIGN'S ONE. The design says "one table,
-- loop_item", and loop_item is one table — every ITEM is a row in it. But an
-- item cannot rebuild its file on its own: each of the four files carries
-- doctrine prose that outweighs its rows (open-loops.md's marker convention
-- paragraph is the rule the heartbeat obeys; action-required.md's escalation
-- rules are the reason the file is loud). If that prose lived in the exporter's
-- Python, Joe editing a marker rule would be a code change, and the render would
-- stop being a render of the record. loop_block holds the file scaffolding — the
-- prose, the section order, the table headers — as DATA, so the render
-- reproduces the file and the file stays Joe's to change. Additive, and reported
-- rather than absorbed (ORDER 31 log).
--
-- NUMBERS ARE PRESERVED, NOT MADE UNIQUE. The order says "numbers preserved
-- exactly", and the files' numbers COLLIDE — measured 2026-07-31, not feared:
-- #111 appears twice inside open-loops.md; #103, #95, #88 and #108 each name one
-- item in the hot file and a DIFFERENT item in the backlog; T34 names one row in
-- team-loops' Open table and another in its Done table; two Done rows use the
-- glyph `🔓` where a number belongs. Making `number` unique would force a
-- renumber, and a renumber is a content change nobody ruled. So `number` is text
-- and NOT unique: it is the visible ref, verbatim, and the collisions are
-- reported to Joe on the review list instead of being resolved by this schema.
-- Identity is the surrogate id; position is (block_id, render_seq).
--
-- PLACEMENT IS STORED, PROMOTION IS DERIVED — and the order of those two is the
-- one place this migration departs from the design's letter. The design puts the
-- hot/backlog split in the view ("bell + now-due only, promotion logic in the
-- view"). Against today's files that rule loses six rows: #108, #109 and #106 sit
-- in the hot file carrying ✅ rather than a marker; #86 reads `🗓TABLED` with no
-- date; #93 (🗓2026-08-04) and #94 (🗓2026-08-03) are future-dated and sit hot
-- anyway. A marker-derived view would move all six and the done-test — zero
-- content loss on every OPEN row — would fail on the first run. So the block a
-- row lives in is DATA (it is where Joe put it), and the promotion rule lives in
-- v_loop_promotion_due, which NAMES the rows that have come due. The heartbeat
-- promotes by calling update-loop, which is a recorded act by an actor, instead
-- of a view silently relocating Joe's rows underneath him.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── the file scaffolding ─────────────────────────────────────────────────────
create table loop_block (
  id          uuid primary key default gen_random_uuid(),

  rel_path    text not null,        -- vault-relative path of the file it renders into
  kind        text not null
              check (kind in ('open_loop','team_loop','action_required')),
  seq         int  not null,        -- position of this block within the file

  block_key   text,                 -- 'hot','backlog','backlog-orphan','open','done'.
                                    -- NULL = a prose-only block (no rows hang off it).
                                    -- This is what an item points at when it says
                                    -- which section of which file it lives in.

  prose_md    text not null default '',
                                    -- the markdown emitted BEFORE this block's table,
                                    -- VERBATIM. Headings, the marker-convention
                                    -- paragraph, the promotion log, the trailing
                                    -- pointer sections — all of it. Joe's prose stays
                                    -- Joe's; the exporter never composes it.

  header_cols text[],               -- the table's header cells, verbatim
                                    -- ('#','Owner','Item','Since',...). NULL on a
                                    -- prose-only block, and NULL on the one HEADERLESS
                                    -- row run this vault actually contains (backlog #87
                                    -- sits under '## Closed' with no header above it;
                                    -- kept where it is, flagged, never relocated).

  col_order   text[],               -- semantic column names, positional, one per
                                    -- header cell. The render maps a row's fields
                                    -- back into this order. Vocabulary below.

  renders_closed boolean not null default false,
                                    -- true on the DONE / Done tables that
                                    -- action-required.md and team-loops.md carry
                                    -- INLINE. Those tables are today's file content
                                    -- and the round-trip has to reproduce them, so
                                    -- their block renders closed rows rather than
                                    -- open ones. open-loops.md and its backlog have
                                    -- no Done table — a closed row leaves the file
                                    -- for open-loops-closed.md — so their blocks
                                    -- stay false and closing a loop drops it off the
                                    -- render, exactly as moving the row always did.

  version     int not null default 1,
  created_at  timestamptz not null default now(),
  created_by  uuid not null references actor(id),
  updated_at  timestamptz not null default now(),
  updated_by  uuid not null references actor(id),

  unique (rel_path, seq),
  -- a prose-only block has neither a header nor a column order; a table block has
  -- a column order whether or not it prints a header row.
  constraint loop_block_prose_has_no_columns
    check ((block_key is null) = (col_order is null)),
  constraint loop_block_header_needs_columns
    check (header_cols is null or col_order is not null)
);

create unique index loop_block_key_idx on loop_block (rel_path, block_key)
  where block_key is not null;

comment on table loop_block is
  'File scaffolding for the generated loop renders: the prose, the section order '
  'and the table headers of open-loops.md, open-loops-backlog.md, '
  'action-required.md and team-loops.md, stored as data so the render reproduces '
  'the file and the doctrine prose stays editable by the human rather than by a '
  'code change.';
comment on column loop_block.col_order is
  'Positional semantic column names. Vocabulary: number, owner, title, body, '
  'since_text, unblocks, source_note, closed_text, outcome, and extra:<key> for a '
  'cell the canonical set has no home for. A row may override this with its own '
  'col_order when the source row''s width disagrees with the header.';

-- ── the items ────────────────────────────────────────────────────────────────
create table loop_item (
  id          uuid primary key default gen_random_uuid(),

  kind        text not null
              check (kind in ('open_loop','team_loop','action_required')),
  number      text not null,        -- '111', 'A13', 'T58', '🔓' — VERBATIM, and
                                    -- deliberately NOT unique. See the header note.

  block_id    uuid not null references loop_block(id),
  render_seq  int  not null,        -- position within the block

  col_order   text[],               -- per-row override, NULL = use the block's.
                                    -- Exists because three real rows disagree with
                                    -- their own header and nobody has ruled on the
                                    -- fix: backlog #66 carries 5 cells against a
                                    -- 6-column header (the Owner cell is absent),
                                    -- backlog #76 carries 7, and action-required A10
                                    -- sits in the 5-column DONE table with the OPEN
                                    -- table's 6. Preserved positionally and reported;
                                    -- guessing which cell means what would put
                                    -- invented data in Joe's file.

  -- ── the content, one semantic column per column the files actually carry ────
  title        text,                -- team-loops 'Ask' / action-required 'Action'
  body         text,                -- open-loops 'Item' / team-loops 'Notes / links'
  owner        text,                -- 'Joe', 'Joe/Claude', 'Dell', 'Joe→Dell',
                                    -- "Dell's brain→Joe" — an ownership LABEL as the
                                    -- file states it, not a foreign key. These files
                                    -- name pairs, brains and roles, and resolving
                                    -- them to actor rows would drop what the label says.
  since_text   text,                -- VERBATIM, and text on purpose: the column holds
                                    -- '2026-07-31', 'Jul 1', 'Jul 2-4' and (on the
                                    -- malformed #66) 'Joe'. A date type would have to
                                    -- discard three of those four.
  unblocks     text,                -- 'What it unblocks' / 'Why it matters'
  source_note  text,                -- 'Source / detail' / 'Details'
  closed_text  text,                -- the Closed column, verbatim
  outcome      text,                -- the Outcome column, verbatim
  extra_cells  jsonb not null default '{}'::jsonb,
                                    -- cells the canonical set has no home for, keyed
                                    -- by the suffix in col_order's 'extra:<key>'.

  -- ── the markers the heartbeat and the Monday brief read ────────────────────
  marker         text not null default 'none'
                 check (marker in ('bell','dated','decision','none')),
  marker_literal text,              -- the glyph exactly as it opens the cell:
                                    -- '🔔', '🗓2026-07-31', '🗓TABLED', '❓'. Stored
                                    -- because '🗓TABLED' is a real value in this vault
                                    -- and a date column cannot hold it; the render
                                    -- re-emits this literal, so nothing is normalized
                                    -- into a shape Joe did not write.
  due_on         date,              -- parsed from a dated marker when it IS a date
  drift_critical boolean not null default false,   -- the ⚡

  -- ── lifecycle ──────────────────────────────────────────────────────────────
  status       text not null default 'open'
               check (status in ('open','done','dropped')),
  close_outcome text,               -- REQUIRED to leave 'open'. The files' own
                                    -- convention: team-loops says "the receiving
                                    -- partner moves the row to Done with a one-line
                                    -- outcome — outcomes are how the asker finds out
                                    -- without asking twice." close-loop enforces it at
                                    -- the verb; this check enforces it at the record,
                                    -- so no path reaches a closed row with no reason.
  closed_by    uuid references actor(id),
  closed_at    timestamptz,

  -- ── tier, the v_compiled_rules precedent ───────────────────────────────────
  tier         text not null check (tier in ('personal','shared')),
  personal_to  uuid references actor(id),
                                    -- open-loops lives in 00_Context (Joe-personal);
                                    -- action-required and team-loops live in DNA
                                    -- (shared). The reader view carries both columns
                                    -- and the consumer filters, exactly as
                                    -- v_compiled_rules does for taught rules.

  version      int not null default 1,
  created_at   timestamptz not null default now(),
  created_by   uuid not null references actor(id),
  updated_at   timestamptz not null default now(),
  updated_by   uuid not null references actor(id),

  -- idempotency: a rerun of the importer writes 0 new rows. Position in the source
  -- file IS the natural key, because number is not unique and never will be.
  unique (block_id, render_seq),

  constraint loop_item_closed_has_outcome
    check (status = 'open' or close_outcome is not null),
  constraint loop_item_closed_stamped
    check ((status = 'open') = (closed_at is null)),
  constraint loop_item_personal_tier
    check ((tier = 'personal') = (personal_to is not null))
);

create trigger loop_block_touch before update on loop_block
  for each row execute function trg_touch_row();
create trigger loop_item_touch  before update on loop_item
  for each row execute function trg_touch_row();

create index loop_item_block_idx  on loop_item (block_id, render_seq);
create index loop_item_status_idx on loop_item (status);
create index loop_item_kind_idx   on loop_item (kind, status);
create index loop_item_number_idx on loop_item (number);
create index loop_item_due_idx    on loop_item (due_on) where due_on is not null;
create index loop_item_drift_idx  on loop_item (drift_critical) where drift_critical;

comment on table loop_item is
  'The three markdown accumulators as records (one-writer Phase A). One row per '
  'item in open-loops.md, open-loops-backlog.md, action-required.md and '
  'team-loops.md. Items change via the loop verbs (add-loop, update-loop, '
  'close-loop); the four files are rendered views of this table. NO SESSION '
  'HAND-EDITS THOSE FOUR FILES after the live flip.';
comment on column loop_item.number is
  'The visible ref, verbatim and NOT unique — the source files contain real '
  'collisions (#111 twice in open-loops.md; #103/#95/#88/#108 across hot and '
  'backlog; T34 across Open and Done). Renumbering is a content change nobody '
  'ruled, so the collisions are reported, not resolved here.';
comment on column loop_item.close_outcome is
  'Required to leave open. A closed row with no outcome is how the asker stops '
  'finding out, which is the failure team-loops was built to end.';
comment on column loop_item.owner is
  'An ownership LABEL as the file states it (Joe/Claude, Joe→Dell, Dell''s '
  'brain→Joe), not a foreign key. Resolving it to actor rows would drop what the '
  'label says.';

-- ── grants ───────────────────────────────────────────────────────────────────
-- 0004's schema-wide grant was one-time, not standing: a table created in 0024
-- starts with none. The loop verbs run as carr_writer. carr_reader gets NOTHING
-- on either base table and reads v_loops — the views-only boundary (amendment 11)
-- is what makes the tier split structural rather than remembered.
grant select, insert, update on loop_item  to carr_writer;
grant select, insert, update on loop_block to carr_writer;

-- ── the reader surface (v_ref_index safe-column precedent, 0016) ─────────────
-- THE COLUMN LIST IS A SECURITY BOUNDARY. What it withholds is not contact detail
-- (a loop carries none as a column) but the WRITE surface and the raw scaffolding:
-- no block internals, no extra_cells, no actor uuids. What it must carry is tier
-- and personal_to, because the boundary that matters for loops is the personal /
-- shared split — open-loops.md is Joe-personal and has never been in the DNA share,
-- while action-required.md and team-loops.md are shared-tier by design. The
-- consumer filters on those two columns, exactly as the compiled-rules exporter
-- filters v_compiled_rules on personal_to. Do not widen this list without a ruling.
create view v_loops as
select li.id            as loop_id,
       li.kind,
       li.number,
       lb.rel_path      as renders_into,
       lb.block_key     as section,
       li.render_seq,
       li.title,
       li.body,
       li.owner,
       li.since_text,
       li.unblocks,
       li.source_note,
       li.closed_text,
       li.outcome,
       li.marker,
       li.marker_literal,
       li.due_on,
       li.drift_critical,
       li.status,
       li.close_outcome,
       li.closed_at,
       li.tier,
       a.slug           as personal_to,
       li.created_at,
       li.updated_at,
       li.version
  from loop_item li
  join loop_block lb on lb.id = li.block_id
  left join actor a  on a.id  = li.personal_to;

comment on view v_loops is
  'Reader surface for the loop accumulators. SAFE COLUMNS ONLY (v_ref_index '
  'precedent): no block internals, no extra_cells, no actor uuids. tier and '
  'personal_to are carried BECAUSE the boundary that matters here is the '
  'personal/shared split — open-loops.md is Joe-personal, action-required.md and '
  'team-loops.md are shared. The consumer filters; the reader never sees the base '
  'table. This column list is a security boundary.';

grant select on v_loops to carr_reader;
grant select on v_loops to carr_writer;

-- ── the promotion rule, as a view that NAMES rows rather than moving them ────
-- The heartbeat's job today is "scan the backlog for 🗓 rows that have come due and
-- promote those to open-loops.md." This is that scan. It reports; update-loop
-- moves the row, so a promotion is an act by an actor with an event, not a silent
-- relocation. `🗓TABLED` and every other undateable marker literal simply never
-- appear here, which is correct: a tabled row has no day to come due on.
create view v_loop_promotion_due as
select li.id          as loop_id,
       li.number,
       li.marker_literal,
       li.due_on,
       li.owner,
       li.title,
       li.body,
       lb.block_key   as currently_in,
       (current_date - li.due_on) as days_due
  from loop_item li
  join loop_block lb on lb.id = li.block_id
 where li.kind = 'open_loop'
   and li.status = 'open'
   and li.marker = 'dated'
   and li.due_on is not null
   and li.due_on <= current_date
   and lb.block_key <> 'hot';

comment on view v_loop_promotion_due is
  'Backlog rows whose dated marker has arrived. The heartbeat reads this and calls '
  'update-loop to move each one; nothing here relocates a row by itself.';

grant select on v_loop_promotion_due to carr_reader;
grant select on v_loop_promotion_due to carr_writer;

-- ── the exporter surface ─────────────────────────────────────────────────────
-- Exporter-scoped, like every other v_export_*: it carries the scaffolding the
-- render needs (prose, headers, column order, extra cells) and carr_reader must
-- never reach it.
create view v_export_loops as
select lb.rel_path,
       lb.kind,
       lb.seq          as block_seq,
       lb.block_key,
       lb.prose_md,
       lb.header_cols,
       lb.renders_closed,
       lb.col_order    as block_col_order,
       li.id           as loop_id,
       li.render_seq,
       li.col_order    as row_col_order,
       li.number,
       li.owner,
       li.title,
       li.body,
       li.since_text,
       li.unblocks,
       li.source_note,
       li.closed_text,
       li.outcome,
       li.marker_literal,
       li.extra_cells,
       li.status
  from loop_block lb
  left join loop_item li
         on li.block_id = lb.id
        and (li.status = 'open' or lb.renders_closed)
 order by lb.rel_path, lb.seq, li.render_seq;

comment on view v_export_loops is
  'Render source for the four loop files. A block renders its OPEN items, except '
  'the inline DONE / Done tables (renders_closed), which are today''s file content '
  'and must round-trip. Closing an open_loop takes it off the render, which is '
  'what moving the row to open-loops-closed.md always did.';

grant select on v_export_loops to carr_exporter;

-- The closed items, so an outcome is never invisible just because the row left the
-- open render. Separate view rather than a flag on the one above: the exporter
-- asks two different questions and mixing them is how a closed row leaks back onto
-- a live list.
create view v_export_loops_closed as
select lb.rel_path,
       lb.kind,
       lb.block_key,
       li.number,
       li.owner,
       li.title,
       li.body,
       li.close_outcome,
       li.closed_at,
       a.slug as closed_by
  from loop_item li
  join loop_block lb on lb.id = li.block_id
  left join actor a  on a.id  = li.closed_by
 where li.status <> 'open'
 order by li.closed_at desc nulls last, li.number;

grant select on v_export_loops_closed to carr_exporter;

-- ── guards: assert what this migration claims, rather than trusting it ───────
do $$
declare n int;
begin
  select count(*) into n from information_schema.columns
   where table_schema='public' and table_name='loop_item';
  if n < 30 then
    raise exception 'loop_item has only % columns — the semantic column set is incomplete', n;
  end if;

  -- number must NOT be unique: the source files collide and a renumber is a
  -- content change nobody ruled. Asserted, because a well-meaning later migration
  -- adding this constraint would silently force one.
  if exists (select 1 from pg_indexes
              where schemaname='public' and tablename='loop_item'
                and indexdef ilike '%unique%' and indexdef ilike '%(number)%') then
    raise exception 'a UNIQUE index exists on loop_item.number — the source files collide by design';
  end if;

  -- the reader is views-only, and the export surface is exporter-scoped.
  if has_table_privilege('carr_reader', 'loop_item', 'select') then
    raise exception 'carr_reader has a BASE TABLE grant on loop_item — views-only is the leak guard';
  end if;
  if has_table_privilege('carr_reader', 'loop_block', 'select') then
    raise exception 'carr_reader has a BASE TABLE grant on loop_block — views-only is the leak guard';
  end if;
  if has_table_privilege('carr_reader', 'v_export_loops', 'select') then
    raise exception 'carr_reader can read v_export_loops — the export surface must stay exporter-scoped';
  end if;
  if not has_table_privilege('carr_reader', 'v_loops', 'select') then
    raise exception 'carr_reader cannot read v_loops — the reader surface is unreachable';
  end if;

  -- v_loops must not grow a write-side or scaffolding column by accident.
  if exists (select 1 from information_schema.columns
              where table_schema='public' and table_name='v_loops'
                and column_name in ('extra_cells','col_order','block_id','created_by','updated_by')) then
    raise exception 'v_loops exposes scaffolding or actor columns — the safe-columns boundary is broken';
  end if;

  -- the writer can do exactly the three the verbs need; no delete exists anywhere
  -- in this schema by design (A9: a loop is closed, never erased).
  select count(*) into n from information_schema.role_table_grants
   where grantee='carr_writer' and table_schema='public'
     and table_name='loop_item' and privilege_type in ('SELECT','INSERT','UPDATE');
  if n <> 3 then
    raise exception 'carr_writer grants incomplete on loop_item (% of 3)', n;
  end if;
  if has_table_privilege('carr_writer', 'loop_item', 'delete') then
    raise exception 'DELETE was granted on loop_item — a loop is closed, never erased';
  end if;

  -- ORDER 31's own stop rule: nothing here may touch lead, client or deal.
  select count(*) into n from information_schema.columns
   where table_schema='public' and table_name='lead';
  if n <> 32 then
    raise exception 'lead has % columns, expected 32 — ORDER 31 must not touch lead', n;
  end if;

  raise notice 'ORDER 31(a) guards: loop_item + loop_block created, number deliberately '
               'non-unique, close requires an outcome at the record level, v_loops '
               'safe-columns only, v_export_loops exporter-scoped, lead untouched.';
end $$;

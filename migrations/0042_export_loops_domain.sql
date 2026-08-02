-- 0042_export_loops_domain.sql — the render source learns about domains.
--
-- 0038-0041 gave loops a domain and classified all 79, but v_export_loops (0024) does
-- not select it, so open-loops.md still renders one undifferentiated table and Joe still
-- cannot see his deals apart from the infrastructure. The column exists; the render is
-- blind to it. This is the last link.
--
-- Adds three columns and nothing else: the slug, its label (so the render never hardcodes
-- display text) and its sort (so the render never hardcodes the order either). Both live
-- in loop_domain, which means changing the order or the wording of a heading is a row a
-- human updates, not an exporter edit and a deploy — the same ORDER 3 reasoning that made
-- loop_domain a ref table in the first place.
--
-- domain_sort uses 999 for an UNCLASSIFIED loop so it sorts last rather than first. A
-- NULL domain would sort first under Postgres' default ASC NULLS LAST/FIRST rules
-- depending on direction, and "unsorted" leading the file is exactly the burial being
-- fixed. Unclassified is honest, but it is not urgent.
--
-- Column order at the end of the select list is deliberate: the exporter unpacks
-- v_export_loops rows POSITIONALLY in build_loop_file, so appending is safe and inserting
-- would silently shift every field after it.

begin;

create or replace view v_export_loops as
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
       li.status,
       li.domain,
       ld.label                    as domain_label,
       coalesce(ld.sort, 999)      as domain_sort
  from loop_block lb
  left join loop_item li
         on li.block_id = lb.id
        and (li.status = 'open' or lb.renders_closed)
  left join loop_domain ld
         on ld.slug = li.domain
 order by lb.rel_path, lb.seq, coalesce(ld.sort, 999), li.render_seq;

comment on view v_export_loops is
  'Render source for the four loop files. A block renders its OPEN items, except the '
  'inline DONE / Done tables (renders_closed), which are today''s file content and must '
  'round-trip. Closing an open_loop takes it off the render, which is what moving the row '
  'to open-loops-closed.md always did. Since 0042 it also carries domain / domain_label / '
  'domain_sort so the render can group by lane — deals first, system last, unclassified '
  '(sort 999) last of all. Label and sort come from loop_domain so headings and ordering '
  'are rows a human edits, never exporter code.';

grant select on v_export_loops to carr_exporter;

commit;

-- guard: the three columns exist, ordering puts deals first and unclassified last, and
-- no loop was lost or duplicated by the new join.
do $$
declare c int; first_dom text; unclassified_sort int; before_n int; after_n int;
begin
  select count(*) into c from information_schema.columns
   where table_name = 'v_export_loops' and column_name in ('domain','domain_label','domain_sort');
  if c <> 3 then raise exception 'expected 3 new columns on v_export_loops, found %', c; end if;

  select domain into first_dom from v_export_loops
   where loop_id is not null and domain is not null
   order by domain_sort limit 1;
  if first_dom <> 'deals' then
    raise exception 'deals must render first, got % — the ordering IS the fix', first_dom;
  end if;

  select min(domain_sort) into unclassified_sort from v_export_loops
   where loop_id is not null and domain is null;
  if unclassified_sort is not null and unclassified_sort <> 999 then
    raise exception 'unclassified loops must sort last (999), got %', unclassified_sort;
  end if;

  -- the loop_domain join is LEFT and on a primary key, so it cannot multiply rows.
  -- Prove it rather than assume it: one render row per open loop, as before.
  select count(*) into before_n from loop_item li join loop_block lb on lb.id = li.block_id
   where li.status = 'open' or lb.renders_closed;
  select count(*) into after_n from v_export_loops where loop_id is not null;
  if before_n <> after_n then
    raise exception 'render row count changed: % loops but % render rows — the domain '
                    'join duplicated or dropped rows', before_n, after_n;
  end if;

  raise notice 'v_export_loops carries domain (% render rows, deals first)', after_n;
end $$;

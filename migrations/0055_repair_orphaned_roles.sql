-- 0055_repair_orphaned_roles.sql — role rows rejoin the person they belong to.
--
-- confirm-merge set merged_into on the loser party and did nothing else, so the loser's
-- lead/client/vendor rows were left pointing at a party that no longer resolves. They kept
-- existing while disappearing from every party-based view.
--
-- FOUND BY DOING ONE MERGE AND CHECKING. Joe approved 10 merges; after the first (Petersen),
-- v_contact showed him as "Client" with no lead_ref and v_orphaned_role went 3 -> 4. The
-- merge had DELETED a role from view rather than consolidating it — the exact opposite of
-- the point, which was one person holding both roles. The remaining nine were stopped.
--
-- The three older orphans came from the same defect on earlier merges: V-GC-013 "Trey",
-- V-MSC-024 "Nate", T-040 "Ben Clark" — first-name-only vendor rows whose parties were
-- merged away while the vendor rows stayed behind.
--
-- The verb is fixed in the same commit (it now re-points lead/client/vendor to the survivor
-- and reports any same-role collision it creates). This migration repairs what the broken
-- version left. Following the merged_into pointer is exact and needs no name matching —
-- these rows are not ambiguous, they are simply attached to a redirect.

begin;

update lead   l set party_id = p.merged_into from party p
 where p.id = l.party_id and p.merged_into is not null;
update client c set party_id = p.merged_into from party p
 where p.id = c.party_id and p.merged_into is not null and c.merged_into is null;
update vendor v set party_id = p.merged_into from party p
 where p.id = v.party_id and p.merged_into is not null;

do $$
declare remaining int; petersen text; dups int;
begin
  select count(*) into remaining from v_orphaned_role;
  if remaining <> 0 then
    raise exception '% role row(s) still attached to a merged party', remaining;
  end if;

  -- Petersen is the proof: he must now carry BOTH refs on one contact row.
  select concat_ws(' / ', contact_type, lead_ref, client_ref) into petersen
    from v_contact where name = 'Dr. Erik Petersen, DO';
  if petersen is null or petersen not like '%L-201%' or petersen not like '%C-126%' then
    raise exception 'Petersen did not regain both roles: %', coalesce(petersen, '(no row)');
  end if;

  -- A survivor holding two rows of the same role is a second duplicate hiding behind the
  -- first. Report it; resolving which record is authoritative is a human call.
  select count(*) into dups from (
    select party_id from lead group by party_id having count(*) > 1
    union all select party_id from vendor group by party_id having count(*) > 1
    union all select party_id from client where merged_into is null group by party_id having count(*) > 1
  ) x;

  raise notice 'orphaned roles repaired. Petersen now reads: %. Parties holding a duplicate '
               'role row: % (human call, not auto-resolved)', petersen, dups;
end $$;

commit;

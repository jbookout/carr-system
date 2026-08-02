-- 0044_fix_registry_pointers.sql — two dossiers cited a registry row belonging to
-- someone else.
--
-- `run.sh registry-audit` has been reporting these as ERRORs:
--   FirstCallDPC-Petersen.md    -> L-164, but L-164 holds Dr. Kent Heim, M.D. / Dothan OBGYN
--   SerenityCardiology-Brown.md -> L-165, but L-165 holds Modern Psychiatry (Mobile)
--
-- LESS SEVERE THAN IT READS, and worth stating plainly because the first report of this
-- called it "two dossiers point at different humans", which implied broken records. The
-- STRUCTURED links were correct the whole time:
--   lead L-201 (Dr. Erik Petersen, DO)         notes_path -> FirstCallDPC-Petersen.md
--   lead L-207 (Dr. Randolph Eamonn Brown, MD) notes_path -> SerenityCardiology-Brown.md
-- and both clients (C-126, C-121) carry the same notes_path from their side. Nothing in
-- the record layer pointed at the wrong person. What was wrong is the human-readable
-- "Lead source:" sentence, which renders from client.acquisition_source and still quoted
-- the ref from before each lead was re-created. Prose contradicting the structured link —
-- the same defect class as everything else in this audit, in the one place a reader looks.
--
-- IDENTIFICATION IS STRUCTURAL, NOT BY NAME. This system merged the wrong Beasley once, so
-- a name match alone is not evidence. Each correction here rests on three independent
-- confirmations: (1) the lead's own notes_path resolves to that exact dossier file, (2) the
-- client's notes_path resolves to the same file from the other side, and (3) the party name
-- on the lead equals the party name on the client. For Serenity there is a fourth — the
-- dossier's own line 107 already recorded the fix in prose on 2026-07-27: "registry row
-- created 2026-07-27 as L-207; the original L-165 pointer was never this prospect's row."
-- Someone found this, wrote the correction into a later line, and never touched the header
-- the audit reads.
--
-- NOT TOUCHED: C-125 (Renalus / Jennifer Thomas) cites registry L-163, and L-163 IS Jennifer
-- Thomas. It matched the same search pattern and is correct. Left exactly as it is.
--
-- No verb writes client.acquisition_source (there is no update-client), so this is SQL.
-- Targeted replacement of the ref token only: the rest of each sentence records real
-- provenance — the CARR house-lead origin, the Sunbiz filing number — and is still true.

begin;

-- C-126 Dr. Erik Petersen, DO — L-164 (Kent Heim) -> L-201
update client
   set acquisition_source = replace(acquisition_source, 'registry L-164', 'registry L-201')
 where roster_ref = 'C-126'
   and acquisition_source like '%registry L-164%';

-- C-121 Dr. Randolph Eamonn Brown, MD — L-165 (Modern Psychiatry) -> L-207
update client
   set acquisition_source = replace(acquisition_source, 'registry L-165', 'registry L-207')
 where roster_ref = 'C-121'
   and acquisition_source like '%registry L-165%';

-- guards INSIDE the transaction (the 0043 lesson: a guard that cannot roll back is a report)
do $$
declare bad int; petersen text; brown text; renalus text;
begin
  select acquisition_source into petersen from client where roster_ref = 'C-126';
  select acquisition_source into brown    from client where roster_ref = 'C-121';
  select acquisition_source into renalus  from client where roster_ref = 'C-125';

  if petersen not like '%registry L-201%' then
    raise exception 'C-126 did not take the corrected ref: %', petersen;
  end if;
  if brown not like '%registry L-207%' then
    raise exception 'C-121 did not take the corrected ref: %', brown;
  end if;

  -- the corrected refs must actually resolve to these people, checked structurally
  if not exists (select 1 from lead l join client c on c.id = l.client_id
                  where l.registry_ref = 'L-201' and c.roster_ref = 'C-126')
     and not exists (select 1 from lead l where l.registry_ref = 'L-201'
                      and l.notes_path = (select notes_path from client where roster_ref='C-126'))
  then
    raise exception 'L-201 does not resolve to C-126 by client_id or notes_path — refusing '
                    'to leave a pointer that only looks right';
  end if;
  if not exists (select 1 from lead l join client c on c.id = l.client_id
                  where l.registry_ref = 'L-207' and c.roster_ref = 'C-121')
     and not exists (select 1 from lead l where l.registry_ref = 'L-207'
                      and l.notes_path = (select notes_path from client where roster_ref='C-121'))
  then
    raise exception 'L-207 does not resolve to C-121 by client_id or notes_path — refusing '
                    'to leave a pointer that only looks right';
  end if;

  -- Renalus was a false positive on the search pattern; it must be untouched.
  if renalus not like '%registry L-163%' then
    raise exception 'C-125 (Renalus) was modified — it cites L-163 and L-163 IS Jennifer '
                    'Thomas; it was never one of the faults: %', renalus;
  end if;

  -- No client may cite a ref belonging to a DIFFERENT PERSON. That is the actual fault
  -- and the only one worth failing on.
  --
  -- An earlier version of this guard demanded a structural link (client_id or a shared
  -- notes_path) and rolled the whole migration back over three records that were entirely
  -- correct: C-122 cites L-009 and L-009 IS Sara Randall-MacDonnell; C-123/L-010 is
  -- Jeremiah; C-124/L-011 is Elysse Lerner. Those leads simply predate the linking — no
  -- client_id, no notes_path — so "unlinked" was being read as "wrong". Demanding a link
  -- that older rows never had would have condemned accurate data.
  --
  -- The missing lead->client link on those three is real and is loop #127's job
  -- (read-path traversal), not this migration's. Recorded here so it is not lost.
  -- Names compare with any PARENTHETICAL stripped. A bracketed clause is an annotation
  -- about the record, not part of the person's name, and C-123 proved it: the client reads
  -- "Jeremiah (last name TBD — pull from the Axon email)" while lead L-010 reads "Jeremiah
  -- (last name — pull from Axon email)". Same unidentified Jeremiah, same Axon-email
  -- provenance, two hand-typed versions of the same note. Comparing the note is meaningless.
  --
  -- This is NOT fuzzy matching, and the distinction matters given this system merged the
  -- wrong Beasley once: everything outside the bracket must still match EXACTLY. "Dr. Kent
  -- Heim, M.D." against "Dr. Erik Petersen, DO", and "Modern Psychiatry" against "Dr.
  -- Randolph Eamonn Brown, MD", both still fail — which is precisely what this guard is for.
  select count(*) into bad
    from client c
    join party cp on cp.id = c.party_id
    join lead l on l.registry_ref = substring(c.acquisition_source from 'registry (L-\d{3})')
    join party lp on lp.id = l.party_id
   where c.acquisition_source ~ 'registry L-\d{3}'
     and lower(btrim(split_part(lp.name, '(', 1))) <> lower(btrim(split_part(cp.name, '(', 1)));
  if bad > 0 then
    raise exception '% client(s) cite a registry ref held by a DIFFERENT person', bad;
  end if;

  raise notice 'registry pointers corrected: C-126 -> L-201, C-121 -> L-207 (C-125 untouched)';
end $$;

commit;

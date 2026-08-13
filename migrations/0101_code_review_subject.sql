-- 0101_code_review_subject.sql
-- CODE-REVIEW FINDINGS GET A DURABLE SUBJECT (loop #211).
--
-- THE BLOCKER, twice-proven live during the Review Council's first end-to-end runs
-- on 2026-08-06: record-finding resolves its subject through resolveSubject(), which
-- accepts business refs only (L- / C- / V- / P- / a deal name). A review of a repo
-- commit therefore had nowhere to land — not its findings, and not its FAILURE
-- finding either, which is the one a reader most needs. Absence-visibility survived
-- only in the runner's local status sidecars. Code evidence was the one class of
-- evidence the record layer could not hold.
--
-- THE SHAPE, following 0066 exactly rather than inventing a second pattern. 0066
-- established that record_flag is polymorphic on (subject_type, subject_id) and that
-- a new subject class earns a small registry table minting one stable uuid per thing,
-- NOT a new pointer column on record_flag. marketing_subject did that for platforms,
-- pillars and formats. code_subject does it for a repo and a commit.
--
-- WHERE THIS DELIBERATELY DIVERGES FROM 0066, and why. marketing_subject refuses to
-- mint: a typo'd slug minting a new pillar is how a taxonomy becomes noise, so naming
-- a pillar is a human modelling act. A COMMIT SHA IS NOT A TAXONOMY. It is a
-- self-evidencing identifier — it either names an object in the repo or it does not,
-- and no amount of minting can pollute a vocabulary that has no vocabulary. So
-- record-finding mints a code_subject row on first use, the same way filing a finding
-- against a client does not first require someone to hand-register the client. The
-- CHECK constraints below are what keep that safe: a repo must look like a repo and a
-- sha must look like a sha.
--
-- THE ONE-REPO RULE IS ENCODED, NOT ASSUMED. CARR CLAUDE.md rev 10: "the code lives
-- in ONE repo: jbookout/carr-system". This migration does not hard-code that name —
-- a second repo is a real possibility and a schema that forbids it would be a lie —
-- but the verb defaults to it, so `commit:<sha>` with no repo named resolves to the
-- one repo the system actually has.

begin;

-- ── 0. PRECONDITION ──────────────────────────────────────────────────────────────────────
-- The CHECK added by 0066 must still be the live one and must still accept every row
-- that exists, or dropping and recreating it below would either fail or silently widen
-- something else. 0066's own extension instructions say to do exactly this.
do $$
declare bad text; n_flag int;
begin
  if not exists (select 1 from pg_constraint where conname = 'record_flag_subject_type_check') then
    raise exception '0101 precondition: record_flag_subject_type_check is missing — 0066 is not applied, '
                    'or something else already replaced it. Stop and look before widening it.';
  end if;
  select string_agg(distinct subject_type, ', ') into bad
    from record_flag
   where subject_type not in ('lead','client','vendor','party','deal',
                              'campaign','platform','pillar','format');
  if bad is not null then
    raise exception '0101 precondition: record_flag carries subject_type(s) [%] outside the 0066 '
                    'vocabulary. The new CHECK would reject existing rows.', bad;
  end if;
  select count(*) into n_flag from record_flag;
  raise notice '0101 preconditions ok — % record_flag rows, all inside the 0066 vocabulary', n_flag;
end $$;

-- ── 1. THE REGISTRY ──────────────────────────────────────────────────────────────────────
-- One row = one reviewable code object. commit_sha NULL means the subject is the REPO
-- ITSELF, which is the right home for a finding that is not about one commit ("the
-- worker has no test for the reviewer bearer's write cap"). A repo row and a commit row
-- are different subjects and must not collapse into each other, which is why the unique
-- index below coalesces the null rather than relying on NULL-distinct semantics.
create table code_subject (
  id           uuid primary key default gen_random_uuid(),
  repo         text not null,
  commit_sha   text,
  label        text,
  note         text,
  created_at   timestamptz not null default now(),
  created_by   uuid references actor(id),
  -- 'owner/name' with no whitespace. Loose enough for any host, tight enough that a
  -- sentence, a path or an empty string cannot become a repo.
  constraint code_subject_repo_shape
    check (repo = lower(btrim(repo)) and repo ~ '^[a-z0-9._-]+/[a-z0-9._-]+$'),
  -- A git object name: 7 to 40 lowercase hex. Short shas are accepted because that is
  -- what a human and a reviewer actually quote; they are stored as given, never padded.
  constraint code_subject_sha_shape
    check (commit_sha is null or commit_sha ~ '^[0-9a-f]{7,40}$')
);

create unique index code_subject_identity_uniq
  on code_subject (repo, coalesce(commit_sha, ''));

comment on table code_subject is
  'One stable uuid per reviewable code object — a repo, or a repo at one commit — so '
  'record_flag''s existing (subject_type, subject_id) pointer can address code evidence '
  'the same way it addresses a client. commit_sha NULL = the repo itself. UNLIKE '
  'marketing_subject (0066) this registry is minted on demand by record-finding: a sha '
  'is self-evidencing, not a taxonomy, so there is no vocabulary for a typo to pollute.';

grant select on code_subject to carr_reader, carr_writer, carr_exporter;
grant insert on code_subject to carr_writer;

-- ── 2. record_flag LEARNS THE CODE BRANCH ────────────────────────────────────────────────
-- TO EXTEND THIS AGAIN: drop and recreate the constraint in a NEW migration and teach
-- v_record_flag_subject the new branch in the SAME file. A subject_type the resolution
-- view does not know renders as an unlabelled uuid, which is the blocker 0066 named.
alter table record_flag
  drop constraint record_flag_subject_type_check;

alter table record_flag
  add constraint record_flag_subject_type_check
  check (subject_type in ('lead','client','vendor','party','deal',
                          'campaign','platform','pillar','format',
                          'repo','commit'));

-- ── 3. THE READ SIDE ─────────────────────────────────────────────────────────────────────
-- Same reason as 0066: carr_reader holds no grant on any base table, so this view is the
-- ONLY way a read session sees a finding at all. A code finding that cannot be read back
-- is the same blocker as one that cannot be written.
--
-- The label is what a human sees, so it is written the way a human writes it:
-- 'jbookout/carr-system@f7abde7' for a commit, the bare repo name for a repo. subject_ref
-- carries the SAME string, because unlike a campaign a code object HAS a natural printed
-- ref and hiding it behind a uuid would earn rule 3a9dbafd's complaint directly.
create or replace view v_record_flag_subject as
select f.id            as flag_id,
       f.subject_type,
       f.subject_id,
       case f.subject_type
         when 'campaign' then (select c.name from campaign c where c.id = f.subject_id)
         when 'platform' then (select m.label from marketing_subject m where m.id = f.subject_id)
         when 'pillar'   then (select m.label from marketing_subject m where m.id = f.subject_id)
         when 'format'   then (select m.label from marketing_subject m where m.id = f.subject_id)
         when 'repo'     then (select coalesce(k.label, k.repo)
                                 from code_subject k where k.id = f.subject_id)
         when 'commit'   then (select coalesce(k.label, k.repo || '@' || left(k.commit_sha, 7))
                                 from code_subject k where k.id = f.subject_id)
         else (select r.display_name from v_ref_index r
                where r.subject_type = f.subject_type and r.subject_id = f.subject_id limit 1)
       end             as subject_label,
       case
         when f.subject_type in ('campaign','platform','pillar','format') then null
         when f.subject_type = 'repo' then
           (select k.repo from code_subject k where k.id = f.subject_id)
         when f.subject_type = 'commit' then
           (select k.repo || '@' || k.commit_sha from code_subject k where k.id = f.subject_id)
         else (select r.ref from v_ref_index r
                where r.subject_type = f.subject_type and r.subject_id = f.subject_id limit 1)
       end             as subject_ref,
       f.kind,
       -- record-finding stores found:false for a searched-and-empty result. Lifting it out
       -- of the jsonb is what keeps "we looked and there was nothing" distinguishable from
       -- "nobody looked" at the read surface, which is the same rail as unmeasured vs zero.
       coalesce((f.value ->> 'found')::boolean, true)        as found,
       (f.value ? 'proposes_correction')                     as proposes_correction,
       f.value, f.source, f.observed_at, f.expires_on,
       (f.expires_on is not null and f.expires_on < current_date) as expired,
       a.slug          as recorded_by
  from record_flag f
  left join actor a on a.id = f.created_by;

comment on view v_record_flag_subject is
  'Every record_flag with its subject resolved to a NAME, across all eleven branches '
  '(0066 added four marketing branches, 0101 added repo and commit). The read side of the '
  'finding store: without it a platform or code finding is an opaque uuid, and carr_reader '
  'cannot see record_flag at all. `found` is lifted out of the jsonb on purpose — a '
  'searched-and-empty finding must not read like an absent one.';

grant select on v_record_flag_subject to carr_reader, carr_writer, carr_exporter;

-- ── 4. THE CODE-FINDING READ SURFACE ─────────────────────────────────────────────────────
-- The narrow question a reviewing seat actually asks: what has ever been found about this
-- commit? Answering it off v_record_flag_subject alone means string-matching a label, so
-- the repo and the sha travel as their own columns.
create or replace view v_code_finding as
select k.repo,
       k.commit_sha,
       f.subject_type,
       f.id            as flag_id,
       f.kind,
       coalesce((f.value ->> 'found')::boolean, true) as found,
       f.value ->> 'epistemic_status'                 as epistemic_status,
       f.value,
       f.source,
       f.observed_at,
       a.slug          as recorded_by
  from record_flag f
  join code_subject k on k.id = f.subject_id
  left join actor a on a.id = f.created_by
 where f.subject_type in ('repo','commit');

comment on view v_code_finding is
  'Findings filed against code (0101), with repo and commit_sha as first-class columns so '
  '"what did review ever find about this commit" is a query rather than a label match. A '
  'row with found=false is a review that ran and found nothing, which is the signal the '
  'Review Council could not previously persist anywhere but a local sidecar.';

grant select on v_code_finding to carr_reader, carr_writer, carr_exporter;

-- ── 5. DONE-TEST ─────────────────────────────────────────────────────────────────────────
-- Asserted rather than assumed (rule c53beeaa: an ok from a call proves the call parsed,
-- never that the values landed). This exercises the whole path — mint, file, read back —
-- and rolls its own test rows away.
do $$
declare k_id uuid; f_id uuid; sys uuid; got text; got_ref text; n int;
begin
  select id into sys from actor where slug = 'system';
  insert into code_subject (repo, commit_sha, created_by)
       values ('jbookout/carr-system', 'f7abde7', sys) returning id into k_id;
  insert into record_flag (subject_type, subject_id, kind, value, source, created_by)
       values ('commit', k_id, 'review', '{"found": true}'::jsonb,
               'https://github.com/jbookout/carr-system/commit/f7abde7', sys)
    returning id into f_id;
  select subject_label, subject_ref into got, got_ref
    from v_record_flag_subject where flag_id = f_id;
  if got is distinct from 'jbookout/carr-system@f7abde7' then
    raise exception '0101 done-test: commit label resolved to [%], expected jbookout/carr-system@f7abde7', got;
  end if;
  if got_ref is distinct from 'jbookout/carr-system@f7abde7' then
    raise exception '0101 done-test: commit ref resolved to [%]', got_ref;
  end if;
  select count(*) into n from v_code_finding where flag_id = f_id and commit_sha = 'f7abde7';
  if n <> 1 then
    raise exception '0101 done-test: v_code_finding returned % rows for the test finding', n;
  end if;
  -- The repo-level branch (commit_sha null) is a different subject and must resolve too.
  delete from record_flag where id = f_id;
  delete from code_subject where id = k_id;
  insert into code_subject (repo, created_by) values ('jbookout/carr-system', sys) returning id into k_id;
  insert into record_flag (subject_type, subject_id, kind, value, source, created_by)
       values ('repo', k_id, 'review', '{"found": false}'::jsonb,
               'https://github.com/jbookout/carr-system', sys) returning id into f_id;
  select subject_ref into got_ref from v_record_flag_subject where flag_id = f_id;
  if got_ref is distinct from 'jbookout/carr-system' then
    raise exception '0101 done-test: repo ref resolved to [%]', got_ref;
  end if;
  delete from record_flag where id = f_id;
  delete from code_subject where id = k_id;
  raise notice '0101 done-test ok — commit and repo subjects mint, file and read back';
end $$;

commit;

#!/usr/bin/env bash
# check-application-session.sh — ONE command that proves the authenticated
# application-session substrate, end to end, against a real PostgreSQL.
#
# WHY THIS EXISTS. The contract suite it runs was, for a while, reachable only
# by a human reading a docstring and retyping the command. That is the same
# shape as the defect the suite was written to catch: a guarantee that looks
# present and is never exercised. A review found the suite wired into nothing —
# no runner, no gate, no reference anywhere outside its own file.
#
# NOT WIRED INTO HOSTED CI, deliberately. It needs a live PostgreSQL, and this
# repo's GitHub Actions minutes are metered and over the free allowance. Wiring
# it there is a cost decision for Joe, not a default. Run it locally before
# touching the substrate, and after any change to migration 0257.
#
# Risk colour GREEN: entirely local. No network, no Neon, no staging, no
# production, nothing metered.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Resolve an interpreter rather than hardcoding one checkout's venv: a path into
# ~/carr-system/.venv does not exist in a worktree or on a second machine.
PYBIN="${CARR_PYTHON:-}"
if [ -z "$PYBIN" ]; then
  for cand in "$REPO/.venv/bin/python" "$HOME/carr-system/.venv/bin/python" "$(command -v python3 || true)"; do
    [ -x "$cand" ] && { PYBIN="$cand"; break; }
  done
fi
[ -n "$PYBIN" ] || { echo "no python interpreter found; set CARR_PYTHON" >&2; exit 1; }
"$PYBIN" -c "import psycopg" 2>/dev/null || {
  echo "psycopg is not importable under $PYBIN; set CARR_PYTHON to an interpreter that has it" >&2
  exit 1; }
MIGRATION="$REPO/migrations/0257_authenticated_application_session.sql"
# 0258 chooses the credential that joins carr_session_minter. It MUST be applied
# after 0257 and never before: 0257 asserts the role is memberless, which is its
# "inert by construction" contract, and 0258 is what ends that state.
#
# ROLE MEMBERSHIP IS CLUSTER-WIDE WHILE MIGRATIONS ARE PER-DATABASE. Once 0258
# has run anywhere in this cluster, 0257 can no longer apply to a FRESH database
# in the same cluster -- it will find the member it requires to be absent. That
# is an artifact of one cluster hosting many test databases, not a production
# path (each Neon database is its own cluster), but it means this script must
# stand up its own cluster per run, which it does.
MIGRATION_ISSUER="$REPO/migrations/0258_session_issuer_credential.sql"
# 0259 lets the door mint from an actor SLUG. The door has no actor id --
# actor.id is not resolved until callTool, long after authentication -- and the
# issuer holds no table privilege with which to resolve one.
MIGRATION_SLUG_MINT="$REPO/migrations/0259_mint_session_by_actor_slug.sql"
# 0260 adds write receipts: a session, a claimed digest, and a readback the
# DATABASE computes from the frozen evidence row rather than accepting.
MIGRATION_RECEIPT="$REPO/migrations/0260_write_receipt.sql"
# 0261 introduces the reducer and the acceptance surface — deliberately, and
# only after receipts can prove themselves.
MIGRATION_ACCEPT="$REPO/migrations/0261_continuity_reducer_and_acceptance.sql"
# 0262 is the LAST slice: Drive retirement resolved from proven receipts plus
# an authority acceptance, which is the record-layer verifier the static
# preflight says it cannot be.
MIGRATION_RETIRE="$REPO/migrations/0262_drive_retirement.sql"
# 0263 splits the receipt digest. 0260 made claimed_digest carry both the proof
# that a receipt is attached to a real call AND the claim about what a subject
# now says; those are different facts, and one column could not be honest about
# both. Under the old shape an exact reversal could never prove and a single
# unproven receipt barred acceptance forever — this suite bricked itself on its
# own first run. It applies AFTER 0262 because it rewrites 0262's retirement
# trigger as well as 0260's and 0261's functions.
MIGRATION_SPLIT="$REPO/migrations/0263_receipt_digest_split.sql"
SUITE="$REPO/mcp-server/test/db/application_session_contract.py"
export LC_ALL=C LANG=C
export CARR_DISPOSABLE_PG_DIR="${CARR_DISPOSABLE_PG_DIR:-${TMPDIR:-/tmp}/carr-appsession-check}"

cleanup() { "$REPO/ops/disposable-pg.sh" stop >/dev/null 2>&1 || true; }

# Start FIRST, arm the trap only after it succeeds. Armed beforehand, a refused
# start ("already initialised") aborted under set -e and the trap deleted a
# directory this run did not create — and RUNDIR defaults to a fixed shared path
# on a machine that runs many worktree sessions at once, so the victim was
# whatever was already there.
DSN="$("$REPO/ops/disposable-pg.sh" start)"
trap cleanup EXIT
# An empty DSN sends psql to the default socket, where everything "fails" for
# the wrong reason and a whole run reports nonsense. Refuse instead.
[ -n "$DSN" ] || { echo "harness did not start; no DSN" >&2; exit 1; }

BASE="${DSN%/carr_h}"
psql "$BASE/postgres" -q -c "create database subject template carr_h"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION"
echo "migration 0257 applied (its own apply-time assertions passed)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_ISSUER"
echo "migration 0258 applied (the minting credential is chosen and asserted)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_SLUG_MINT"
echo "migration 0259 applied (the door can mint without an actor id)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_RECEIPT"
echo "migration 0260 applied (receipts bind a session and prove by readback)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_ACCEPT"
echo "migration 0261 applied (the reducer folds, and acceptance is gated)"
psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_RETIRE"
echo "migration 0262 applied (retirement needs two proven receipts and authority)"

# ---------------------------------------------------------- the seq backfill
# THE ONE FIX IN 0263 THAT ITS OWN APPLY-TIME PROOF CANNOT SEE, and an evidence
# auditor confirmed both halves of that: the fix is real, and a mutant reverting
# it passes the migration's proof untouched.
#
# 0263 adds ops.write_receipt.seq and makes the continuity reducer fold in that
# order instead of by (recorded_at, id), because recorded_at is clock_timestamp()
# and id is a random uuid, so two receipts inside one clock tick folded in
# whichever order two random numbers happened to sort. Adding an identity column
# to a POPULATED table numbers the existing rows in HEAP order, which for this
# table is close to meaningless -- every receipt is rewritten when its readback
# lands. So 0263 numbers the pre-existing rows explicitly, by (recorded_at, id),
# the order the reducer used before it.
#
# THAT CONCERNS ROWS THAT EXIST BEFORE THE MIGRATION RUNS, which is precisely
# what an apply-time proof inside the migration cannot arrange -- it runs after
# the backfill, against whatever the target database happened to hold, and on a
# fresh cluster that is nothing at all. This script can arrange it, because it
# owns the cluster: stand up a database at 0262, seed receipts whose HEAP order
# is deliberately the reverse of their (recorded_at, id) order, then apply 0263
# and read the numbering back.
#
# THE FIXTURE IS BUILT SO THE TWO ORDERS DISAGREE. Three receipts are inserted
# newest-first, so heap position 1 carries the LATEST recorded_at. A backfill
# that numbered rows in heap order gives 1,2,3 in insertion order; the correct
# one gives 3,2,1. A mutant that deletes the explicit numbering and lets the
# identity assign in heap order fails here and nowhere else.
psql "$BASE/postgres" -q -c "create database seqcheck template subject"
psql "$BASE/seqcheck" -v ON_ERROR_STOP=1 -q <<'SEED'
do $$
declare
  a      uuid;
  s      uuid := gen_random_uuid();
  t0     timestamptz := timestamptz '2026-01-01 00:00:00+00';
  n      int;
begin
  select id into a from public.actor where kind = 'human' order by slug limit 1;
  if a is null then
    raise exception 'seq fixture: the schema snapshot carries no human actor';
  end if;
  insert into ops.application_session (id, actor_id, organization_tenant_id,
    sponsoring_human_slug, via, auth_issuer, authorization_class, verified_subject, expires_at)
  values (s, a, 'carr-internal', 'joe', 'seq-fixture', 'seq-issuer',
          'verified_partner', 'seq-fixture', clock_timestamp() + interval '1 hour');
  -- Inserted NEWEST FIRST. Heap order is therefore 'third','second','first'
  -- while (recorded_at, id) order is 'first','second','third'.
  for n in 1..3 loop
    insert into ops.write_receipt (id, application_session_id, actor_id,
      organization_tenant_id, verb, subject_type, subject_id,
      tool_call_idempotency_key, claimed_digest, prior_digest, recorded_at)
    values (gen_random_uuid(), s, a, 'carr-internal', 'log-activity', 'seq-fixture',
            gen_random_uuid(), 'seq-fixture-' || n,
            'seq-fixture-claim-' || (4 - n), 'origin', t0 + ((4 - n) || ' minutes')::interval);
  end loop;
end $$;
SEED
psql "$BASE/seqcheck" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_SPLIT"
psql "$BASE/seqcheck" -v ON_ERROR_STOP=1 -q <<'ASSERT'
do $$
declare
  got  text;
  want text := 'seq-fixture-claim-1,seq-fixture-claim-2,seq-fixture-claim-3';
  nxt  bigint;
  top  bigint;
begin
  select string_agg(call_digest, ',' order by seq) into got
    from ops.write_receipt where subject_type = 'seq-fixture';
  if got is distinct from want then
    raise exception
      '0263 SEQ BACKFILL FAILED: pre-existing receipts were numbered in the '
      'wrong order, so the reducer''s fold over rows that predate the migration '
      'is decided by heap position rather than by (recorded_at, id). '
      'expected [%] got [%]', want, got;
  end if;
  select max(seq) into top from ops.write_receipt;
  -- AND THE IDENTITY TAKES OVER FROM THE NEXT NUMBER, not from 1. A sequence
  -- left at its default start would hand the next real receipt a seq that
  -- collides with a backfilled one, and the fold would interleave new writes
  -- into the middle of history.
  --
  -- READ AS THE SEQUENCE'S START, NOT AS ITS NEXT VALUE, and the difference is
  -- the whole reliability of this assertion. 0263's own apply-time proof files
  -- dozens of receipts and then rolls back -- but a sequence does not roll back,
  -- so nextval() here reports wherever that proof left the counter and says
  -- nothing about where the backfill set it. seqstart is the number the
  -- migration computed, untouched by anything drawn from the sequence since.
  select s.seqstart into nxt from pg_sequence s
   where s.seqrelid = pg_get_serial_sequence('ops.write_receipt', 'seq')::regclass;
  if nxt is distinct from top + 1 then
    raise exception
      '0263 SEQ BACKFILL FAILED: the identity starts at % but the backfill '
      'numbered up to %, so a receipt written after the migration takes a '
      'position already occupied in the fold', nxt, top;
  end if;
  raise notice 'seq backfill: pre-existing receipts numbered by (recorded_at, id), identity starts at %', nxt;
end $$;
ASSERT
echo "seq backfill proven against rows that predate 0263"

psql "$BASE/subject" -v ON_ERROR_STOP=1 -q -f "$MIGRATION_SPLIT"
echo "migration 0263 applied (the call digest and the material claim are two columns)"

# --------------------------------------------------------- the collation fold
# THE HALF OF (15a) AND (15d) NO IN-FILE PROBE CAN REACH. The material digest
# sorts its four keys under `collate "C"` so that the same events fold to the
# same digest everywhere -- this harness, staging, Neon. 0263 can only check that
# by SHAPE, because the disposable cluster's own collation IS C, so deleting the
# clause changes nothing there; a reviewer duly walked past the shape check by
# moving the text into a comment, since pg_get_functiondef returns comments too.
#
# This script owns the cluster, so it can do the thing the migration cannot:
# build a second database with a NON-C collation, put the real migrated schema in
# it, and fold the IDENTICAL fixture 0263 pins. Same bytes in, same digest out,
# or the cross-environment claim is false. The fixture's two fields are 'a_b' and
# 'ab', which sort one way under C and the other way under en_US, so the order
# alone is enough to change the answer.
#
# THE SCHEMA ARRIVES BY DUMP RATHER THAN BY RE-APPLYING THE CHAIN, and that is
# forced: role membership is CLUSTER-WIDE while migrations are per-database, so
# once 0258 has run anywhere here, 0257 refuses to apply to a fresh database --
# this script's own header says so. A dump of the database the chain just built
# is the same schema by construction.
COLLATION_DB="${CARR_COLLATION_LOCALE:-en_US.UTF-8}"
if psql "$BASE/postgres" -q -c \
     "create database collate_probe template template0 encoding 'UTF8'
        lc_collate '$COLLATION_DB' lc_ctype '$COLLATION_DB'" 2>/dev/null; then
  pg_dump "$BASE/subject" | psql "$BASE/collate_probe" -q -v ON_ERROR_STOP=1 >/dev/null
  psql "$BASE/collate_probe" -v ON_ERROR_STOP=1 -q <<'FOLD'
do $$
declare
  a    uuid;
  s    uuid := gen_random_uuid();
  k    text := 'collation-fold-fixture';
  subj uuid := '00000000-0000-4000-8000-000000000263';
  want text := '5659c63df9186781f263c644941b0dba9054ce75e1d7a1d4a409bd1a5f4f8de2';
  got  text;
  coll text;
begin
  select datcollate into coll from pg_database where datname = current_database();
  -- THE PROBE MUST BE ABLE TO FAIL, ASKED OF THIS HOST RATHER THAN ASSUMED.
  -- The first version of this check used fields 'a_b' and 'ab' on the reasoning
  -- that a language collation weighs punctuation below letters; measured on
  -- PostgreSQL 17 that is false for libc en_US.UTF-8 AND for ICU en-US, so the
  -- fixture folded identically under every collation and this check passed with
  -- the collate clauses deleted from the function. Green over a live defect,
  -- which is the failure mode this whole file exists to refuse.
  --
  -- So the fixture's own discriminating pair is tested first: under THIS
  -- database's collation the two field names must order differently than they
  -- do under C. If they do not, nothing below can distinguish a fold that sorts
  -- under C from one that does not, and this says so instead of passing.
  if coll = 'C' then
    raise exception 'collation fold: this database is collated C, so it proves nothing';
  end if;
  if ('Stage' < 'amount') = ('Stage' collate "C" < 'amount' collate "C") then
    raise exception
      'collation fold: under % the fixture pair Stage/amount sorts the SAME as '
      'under C, so folding it here cannot tell a C-sorted digest from any other '
      'and this check would pass over a fold that had lost its collate clauses. '
      'Pick a locale that actually orders differently and set '
      'CARR_COLLATION_LOCALE to it.', coll;
  end if;
  select id into a from public.actor where kind = 'human' order by slug limit 1;
  insert into ops.application_session (id, actor_id, organization_tenant_id,
    sponsoring_human_slug, via, auth_issuer, authorization_class, verified_subject, expires_at)
  values (s, a, 'carr-internal', 'joe', 'fold-fixture', 'fold-issuer',
          'verified_partner', 'fold-fixture', clock_timestamp() + interval '1 hour');
  insert into public.tool_call (idempotency_key, verb, actor_id, request_hash,
    response, organization_tenant_id, application_session_id)
  values (k,'log-activity',a,k,'{}'::jsonb,'carr-internal',s);
  insert into public.event (occurred_at, actor_id, verb, subject_type, subject_id,
    field, new_value, cause, idempotency_key, organization_tenant_id, application_session_id)
  values (clock_timestamp(),a,'log-activity','deal',subj,'Stage','1'::jsonb,'system',k,'carr-internal',s),
         (clock_timestamp(),a,'log-activity','deal',subj,'amount','2'::jsonb,'system',k,'carr-internal',s),
         (clock_timestamp(),a,'aa-activity','deal',subj,'zz','3'::jsonb,'system',k,'carr-internal',s);
  got := ops.write_receipt_material_digest(k, s, 'deal', subj);
  if got is distinct from want then
    raise exception
      'COLLATION FOLD FAILED: under collation % the same events fold to a '
      'DIFFERENT material digest than 0263 pins under C. Identical writes would '
      'get different receipts on different databases, and a restatement would '
      'stop being recognisable as a no-op. expected % got %', coll, want, got;
  end if;
  raise notice 'collation fold: identical under % and under C', coll;
end $$;
FOLD
  echo "material fold proven collation-independent (against $COLLATION_DB)"
else
  # DISCLOSED, NEVER SILENT. A host without the locale is a real possibility and
  # a legitimate skip; a skip nobody is told about is how a guarantee becomes
  # decorative. Set CARR_COLLATION_LOCALE to a locale this host does have.
  echo "WARNING: collation fold SKIPPED -- this host cannot create a database" >&2
  echo "         collated '$COLLATION_DB', so the cross-collation half of the" >&2
  echo "         material-digest guarantee was NOT exercised on this run." >&2
  echo "         Set CARR_COLLATION_LOCALE to a locale 'locale -a' lists here." >&2
fi

# THE PRODUCER, AGAINST A REAL DATABASE, before the contract suite runs. Its own
# unit test drives a hand-written fake client, and an audit showed that fake
# refuses what the database refuses -- so the exact SQL the Worker sends had no
# coverage anywhere. Runs against a TEMPLATE COPY taken now, so the rows it
# writes cannot colour the contract suite's global acceptance counts.
psql "$BASE/postgres" -q -c "create database producer template subject"
( cd "$REPO/mcp-server" && CARR_TEST_DSN="$BASE/producer" \
    node --test test/receipt-producer-live.test.mjs )
echo "producer exercised against a real database"

# Run TWICE. The suite must be re-runnable; a contract that only passes against
# a virgin database is testing ordering, not the substrate.
"$PYBIN" "$SUITE" "$BASE/subject"
echo "--- second run, same database ---"
"$PYBIN" "$SUITE" "$BASE/subject"

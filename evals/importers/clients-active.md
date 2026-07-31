# Eval set — `pipelines/import_clients_active.py`

Per tool-contracts §5 (*evals-per-lane, failures become curriculum*): every
production failure becomes a permanent test case in its lane's eval set. The
grader checks the REAL END STATE — rows in the database — never the importer's
own report of success.

Both cases below were caught on a dry run during the 2026-07-30 freeze, before
anything was written. They are recorded because a dry run catching them once is
luck; a test catching them every time is the discipline.

---

## Case 1 — a repeated markdown header must not become a record

**Class:** structural parse error, multi-table source.

**What happened.** `clients-active.md` holds more than one pipe table (Joe's
curated index, then Dell's Salesforce backfill), each with its own header row.
The parser latched onto the first header and treated every later `| Owner | Name
| C-ID | ...` line as data. Two junk rows resulted, whose C-ID was the literal
string `C-ID`. They failed to match a client and were reported as "C-ID with no
client" — an error message that read as a data problem in the vault rather than a
bug in the importer.

**Why it matters beyond this file.** Any markdown source with two tables hits
this. The failure is quiet: it produces plausible-looking rejects, not a crash.

**Test.** Feed a source with two tables sharing one header shape.
**Grader.** Parsed row count equals the sum of real data rows in both tables; no
record exists whose key equals a header cell name; `no_match` is empty.

---

## Case 2 — the source's null marker must not become a commitment

**Class:** placeholder ingested as data (no-fabrication violation).

**What happened.** Dell's backfill rows carry `—` in Next Step, which is the
file's own way of writing "nothing here." The importer treated any non-empty
string as a real next action and would have created **35 open `next_action` rows
whose description was `—`**. Because `next_action` rows with no due date surface
in `v_today_triage` as permanently due, the effect would have been 35 phantom
balls in Joe's and Dell's triage on day one of the record layer — each one a
commitment the record claimed a human owed and nobody did.

**Why it matters beyond this file.** Every legacy source uses some marker for
absence (`—`, `-`, `TBD`, `n/a`, `(enrich)`). Treating a marker as content is how
a migration invents obligations. The existing freeze watch-out already said these
markers are "preserved verbatim by design" — preserved in a *notes* field, not
promoted into a *commitment* table. The distinction is the test.

**Test.** A source row whose Next Step is each of `—`, `-`, `–`, `TBD`, `n/a`,
`none`, and empty.
**Grader.** Zero `next_action` rows created for those clients; each appears in
the report's "Next Step empty or a null marker" list; a row with real text in the
same run still produces exactly one `next_action`.

---

---

## Case 3 — an internal note must not become a contact date

**Class:** annotation written to the contact log (silent corruption of a derived
figure).

**What happened.** During the same freeze, 13 internal context notes — Joe's
rulings on merges, ownership, and deal outcomes — were written as `activity`
rows. `v_last_touch` aggregates *all* activity, so each one set **Last Touch =
today on a record nobody had contacted**. It surfaced only because two of them
landed on leads (L-058, L-063) and appeared as unexplained cell diffs in the
reconciliation. On the 11 client records it would have shipped silently.

**Why it matters.** Last Touch drives follow-up decisions. A note that fakes a
touch date makes a cold record look warm and pushes a real follow-up off the
radar — the exact failure the record layer exists to prevent.

**The rule.** `activity` is the CONTACT log (call, email, meeting, tour, text).
Internal annotation, correction, and ruling go in `event`. Both appear in
`v_subject_timeline`, so nothing is hidden from a reader either way.

**Test.** Write an internal note about a client with no prior activity.
**Grader.** `v_last_touch` returns no row for that client; the note is still
visible in `v_subject_timeline`.

**Do not "fix" this by excluding `kind='note'` from `v_last_touch`.** The Wave-1
importer carries legacy Last Touch dates in as `kind='note'`; excluding them
blanks the column it exists to fill. That wrong fix is itself worth testing for.

---

## Standing invariants for this importer

Regressions here are failures regardless of which case triggered them.

1. **Never creates, merges, or restates a client.** The file is a source for
   next actions and touch dates; membership is derived (amendment 0).
2. **Idempotent.** A second run writes nothing new (`record_source`,
   `source_system = 'clients-active'`, key = C-ID).
3. **A C-ID listed twice in the file is handled once.** C-127 appears in both
   tables; the activity stamp must not double.
4. **Unmapped owner is reported, never defaulted.** An owner cell the patterns
   don't match must not silently become Joe.
5. **Status differences are reported, never written.** The importer has no
   authority to overwrite a roster status; that is Joe's call, applied as a
   separate data fix with its own event row.
6. **Only real dates become activity stamps.** `2026-07`, `—`, and
   `2026-06-23 (lead delivered)` are prose, not dates.

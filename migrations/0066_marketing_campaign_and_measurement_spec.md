# 0066 — marketing campaign, non-party findings, and the end of the false zero

*Companion to `0066_marketing_campaign_and_measurement.sql` and to the four verbs added to
`mcp-server/src/tools.js`. Written 2026-08-02. The migration header carries what a reader needs
to judge the SQL; this file carries the shape comparisons and the decisions that were close.*

---

## The state of the lane, measured

Every number below came off a query run read-only against production on 2026-08-02.

| fact | value |
|---|---|
| `campaign` rows | **0** |
| `content_piece` rows | 89, of which **0** carry a `campaign_id` |
| `placement` rows | 89 (exactly one per piece — a single bucket in the per-piece count) |
| `placement_metric` rows | 259, **all** with `source = 'blotato_api'` |
| measured placements | **16 of 89** |
| by platform | instagram 16/16 · facebook 0/16 · linkedin 0/15 · **twitter 0/42** |
| metric values that are legitimately `0` | 173 of 259 |
| analytics snapshots that were **entirely** zero | **0 of 26** |
| Instagram `views_count`, naive `sum()` vs latest-snapshot | **621 vs 490** |
| verbs writing any marketing table (before this) | **none of 40** |
| the only writer of these tables | `pipelines/pull_placement_metrics.py` |

Three of those lines are the whole brief. Nothing states what any content was for. A finding
about a platform has no subject. And 73 unmeasured placements read as zeros.

---

## Decision 1 — a subject registry, not a second pointer on `record_flag`

`record_flag` is already polymorphic: `(subject_type text, subject_id uuid)`, no foreign key,
five branches in use. Three shapes were considered for giving a platform / pillar / format a
subject.

| shape | verdict |
|---|---|
| **(a) nullable `subject_ref text` beside `subject_id`** | Rejected. Two ways to say what a flag is about means every reader must know which column to consult, and a row can carry both. That is the 0045 fault (two homes for one fact) reproduced one table over. |
| **(b) a table per concept — `platform`, `pillar`, `format`** | Rejected. Three tables, three FKs, three branches in the resolution view, to hold at most a few dozen rows that differ only in what kind of thing they name. |
| **(c) one `marketing_subject` registry keyed `(subject_type, slug)`** | **Chosen.** It supplies a uuid, which is the only thing the existing pointer was missing. `record_flag` gains no column. A campaign needs no entry at all, because it already has a uuid — so `subject_type='campaign'` works through the same pointer with nothing added anywhere. |

The registry is seeded **only** from values already present in the data: four platforms from
`select distinct platform from placement`, two formats from `select distinct kind from
content_piece`, and **zero pillars**, because no pillar is evidenced anywhere. The ingest's own
header explains why: `content_piece.features` carries mechanical facts only, and hook family,
voice and pillar are judgments deliberately excluded from a shared surface. Seeding a pillar
list would invent the taxonomy this table exists to hold. A guard asserts the pillar count is
still zero, so a future edit that helpfully seeds one fails the migration.

`record-finding` was extended rather than duplicated: `subject_kind` grows four values and
resolves through the same code path. It registers nothing — an unknown slug is refused with the
list of known ones. A typo that mints a new pillar is how a taxonomy becomes noise.

**Reopen condition.** If findings against non-party subjects ever need their own attributes
(an owner, a review cadence, a retirement policy per kind), split the registry then. Today all
three kinds want exactly the same four columns.

---

## Decision 2 — four verbs, not three

The brief asked for three and asked explicitly whether scoring is a second verb. It is.

**The case for folding scoring into the campaign verb:** one row, one state machine, one
resolver, one version guard.

**The case for splitting, which won:**

1. **House precedent is unambiguous.** `activate-rule` and `retire-rule` are separate.
   `update-loop` and `close-loop` are separate. In both cases the split falls on the same line:
   a state transition that carries a **judgment** gets its own verb.
2. **The argument sets are disjoint and mode-dependent.** Scoring requires `verdict`,
   `evidence`, `base_version` and a coverage check. Opening requires `success_criterion`,
   `starts_on` and `channels`. A single verb would have required fields that depend on a mode
   flag, and a mode flag is exactly how a campaign gets closed by accident while somebody is
   editing a start date.
3. **The refusals only make sense at one end.** `score-campaign` must refuse a `worked` verdict
   over zero measured placements. That refusal is meaningless at open time and would have to be
   conditionally suppressed, which is a guard nobody trusts.

The cost is one extra verb. The benefit is that "we decided this worked" can never be a side
effect of an edit.

`score-campaign` also carries `close: false`, so a mid-flight read-out records a verdict with
evidence without closing the campaign. That is not a mode flag in the sense above — it changes
one column and none of the required arguments.

---

## Decision 3 — no verb creates a `content_piece`

Checked before deciding, which is the only reason the answer is worth anything.

All 89 pieces were created by `pipelines/pull_placement_metrics.py` at publish time, keyed on
`placement.external_id`. The ingest matches existing rows on that key alone. A hand-made piece
has no external id, so the next pull would not recognise it and would mint a second piece and a
second placement for the same post. One verb call would become two rows and a permanently
double-counted metric.

So pieces arrive by publishing, and `attach-to-campaign` **binds**. Two consequences, both
stated rather than hidden:

- **Planned-but-unpublished content has no record-layer home.** The content calendar is still a
  spreadsheet. That is a real gap and it belongs to Joe to rule on, not to be papered over by
  minting orphan rows through a verb whose name says "attach".
- **A campaign is attached to after the fact, at least until that gap closes.** Which is fine
  for the 89 pieces that already exist and is the only way to make them answerable at all.

`placement` deliberately gets no `campaign_id`. Measured: all 89 placements sit 1:1 under 89
pieces, because Joe writes per-platform copy so each platform post is its own piece. A second
pointer would add no expressive power today and would add a way for two rows to disagree.
**Reopen when one piece is ever placed twice** — the FK moves to `placement` then, and
`content_piece.campaign_id` becomes derived.

---

## Decision 4 — the attempt table, and why derivation is not enough

A left join from `placement` to `placement_metric` already tells you a placement has no
metrics. It cannot tell you **which kind of nothing** that is:

- nobody has ever pulled for this placement, or
- the pull ran and the platform returned nothing.

For X's 42 placements that difference decides the next action — chase the integration, or stop
expecting numbers that are never coming. So the attempt is its own record, and it is the exact
analogue of `record-finding`'s `found:false`: a searched-and-empty result is a fact, and a fact
needs a row.

It could not live in `placement_metric`: the primary key is `(placement_id, kind, observed_at)`
and `value` is `numeric not null`, so the only way to express "nothing" there is a row with
value 0 — precisely the false zero this migration exists to abolish.

**Known incompleteness, stated up front.** `pull_placement_metrics.py` is outside this change's
file scope and does not yet write attempt rows. Until it does, all 89 existing placements report
`unmeasured_reason = 'no measurement attempt recorded'`. For the 73 that is exactly true. For
the 16 measured Instagram ones it is harmless — `measured` is computed from the metrics
themselves, not from the attempt — but they deserve `recorded` attempt rows and will not have
them until that job is taught. Follow-up, not a defect.

---

## Decision 5 — null, not zero, and where the line sits

Three places had to be got right, and they are not all the same answer.

| quantity | when nothing was measured | why |
|---|---|---|
| a metric total (`views_total`, `interactions_total`) | **NULL** | 0 asserts the content earned nothing. It earned an unknown amount. |
| `coverage_pct` | **0.0**, not null | 0% coverage is a real, known measurement *about the measuring*. Nulling it would hide the very fact the view exists to publish. |
| `metric_kind_count` | **0**, with `measured = false` beside it | The count of rows genuinely is zero; the guard is that `measured` is adjacent and must be read first, and `unmeasured_reason` is never null when `measured` is false. |

Both directions are asserted on live data in the closing guard: X must report NULL views over 42
placements with `coverage_pct` 0.0, **and** Instagram must report a real number — otherwise the
null-not-zero rule would have quietly become a null-always rule that reports nothing at all.
The Instagram assertion additionally requires the total to differ from the naive sum while
multi-snapshot placements exist, which is the double-count tripwire (490 vs 621).

---

## Decision 6 — the plausibility bands, and the numbers behind them

Bands ask (`needs_confirm`); they do not block. Every one of them is set from measured data
rather than taste.

- **All-zero metric payload → confirm.** 173 of 259 real values are 0, so a zero is ordinary and
  must be accepted. But **0 of 26** real analytics snapshots were *entirely* zero. An all-zero
  payload is what an empty API response looks like, not what data looks like, and writing one
  converts an unmeasured post into a measured zero. The hint points at `unavailable:true`.
- **Single value above 1,000,000 → confirm.** The largest real value on 2026-08-02 was 845,877
  (`view_time_ms_sum`), so the band sits above real data and catches a units error.
- **Coverage below 50% at scoring → confirm.** A judgment, not a measurement, and deliberately
  not 100: a verb demanding perfection gets routed around with `confirm:true` every time, which
  teaches the caller to ignore the gate. Lives in `system_config` so Joe can move it without a
  deploy.
- **`starts_on` more than 90 days back, or a window over 365 days → confirm.** Backdating a
  campaign over content that already published is the *first* thing this lane needs (all 89
  pieces are historical), so it must be possible, not blocked.

**One thing is a hard refusal rather than a band:** a `worked` or `did_not_work` verdict over
**zero** measured placements. Not thin evidence — no evidence. `inconclusive` is always
available and is the true answer, so a confirm prompt here would only ever be clicked through.

**One source string is refused outright:** `blotato_api` on `measure-placement`. That provenance
belongs to the scheduled pull. A hand-written row wearing it would make every API row
unverifiable.

---

## What is enforced where

The verb layer and the schema deploy separately, and were demonstrably out of step for twelve
hours in July. So the rules that must not lapse are in the **database**:

| rule | home |
|---|---|
| a campaign has an objective, a criterion, a start and an author | four `NOT NULL`s |
| a campaign has at least one channel | `CHECK (cardinality(channels) > 0)` |
| channels name registered platforms | trigger (Postgres has no array-element FK, and a `CHECK` may not contain a subquery) |
| a closed campaign carries a verdict | `campaign_closed_is_scored_check` |
| a verdict and its timestamp arrive together | `campaign_scored_pair_check` |
| one campaign per name | `campaign_name_uniq` on `lower(btrim(name))` |
| an attempt row says what landed, or why nothing did | table `CHECK` |
| a flag's subject_type is in the vocabulary | `record_flag_subject_type_check` |

The verb layer adds what SQL cannot judge: the criterion is not a restated goal, the source is
not the pull's, the payload is not all zeros, the batch attaches whole or not at all.

The unique index on campaign name is 0059's lesson applied **before** the damage instead of
after it. 415 organisation rows existed for 306 real organisations because every writer did a
blind insert and none looked first. `campaign` has zero rows today, so the index is free now and
impossible once the same name has been minted three times.

---

## Reversal

No row is deleted and no existing column changes type. To reverse:

```sql
drop view  v_campaign_scorecard, v_marketing_measurement_coverage,
           v_placement_measurement, v_placement_metric_latest, v_record_flag_subject;
drop table placement_measurement;
drop trigger campaign_channels_check on campaign;
drop function campaign_channels_valid();
drop trigger campaign_touch on campaign;
drop index  campaign_name_uniq;
alter table campaign
  drop column starts_on, drop column ends_on, drop column success_criterion,
  drop column channels, drop column outcome_verdict, drop column outcome_note,
  drop column coverage_at_scoring, drop column scored_at, drop column scored_by,
  drop column version, drop column created_at, drop column created_by,
  drop column updated_at, drop column updated_by;
alter table campaign alter column goal drop not null;
alter table campaign drop constraint campaign_status_check;
alter table record_flag drop constraint record_flag_subject_type_check;
drop table marketing_subject;   -- only if no record_flag row points at it
delete from system_config where key like 'marketing.%';
```

The one destructive line is the last `drop table`: a finding filed against a platform points at
a `marketing_subject.id`, and dropping the table orphans the pointer. Check first.

---

## Not verified, and it cannot be until Joe acts

This migration has **not** been applied and has **not** been rehearsed on a Neon branch by the
session that wrote it. What was verified: every fact in the header came off a query actually run
against production read-only; the whole file parses as Postgres grammar (41 statements) and both
`DO` blocks and the trigger body parse as plpgsql. What was **not** verified: that it applies,
that the guards pass, and that the views return what the comments claim.

Rehearse it on a branch before production, the way 0059 was rehearsed — that is the house
pattern and this file is long enough to deserve it.

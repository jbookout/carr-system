# Doctrine Store Build — the database-first migration of the prose tier

*Design ratified by the 2026-08-07 frontier council (host + Codex chair + Grok chair, three rounds;
decisions d8fd29d6, 20dfdfcc, and the ratification entry pointing here). Binding partner rulings:
database-first (rule 14181e60), build-for-trajectory (rule b42f11a0), no Drive markdown
("humans aren't reading them"). Trajectory target: both partners at full book (~40 active deals
each), a growing fleet of concurrent agent sessions, content volume well past 40 documents.
This is a repo design doc for a repo build — the decision ledger, not this file, is the authority
on WHAT was decided; this file details HOW. Check the ledger before treating any dated detail
here as current (house rule d5dcfe26).*

## 1. What this build does

Moves every hand-authored prose file (playbooks, SOPs, INDEX/routing, dossier narratives,
study distillations) out of Drive markdown into the record layer, served by connector verbs on
every surface. At cutoff, ALL generated .md renders retire too (open-loops, idea-bank,
decision-history, briefs, dossiers, clients-active). The only surviving .md is machine-required,
on an exact-path manifest: the CLAUDE.md bootstrap stub, the skill trees (repo canonical +
Drive copy Cowork executes), and compiled-rules renders only until the rules-load verb replaces
them. Retired files MOVE to _to_delete staging, never deleted in place.

## 2. Schema (ships whole, day one — trajectory-sized)

All IDs UUIDv7. Append-only tables get created_at only. Single seeded workspace_id column
throughout (cheap insurance against the most painful retrofit; NO tenancy machinery).

| Table | Key columns | Notes |
|---|---|---|
| actors | actor_id, actor_type(human/agent/service), external_key, display_name | both partners + agent identities |
| sessions | session_id, actor_id, parent_session_id, session_kind(interactive/scheduled/subagent/phone/migration) | a subagent is a session, not an ownership construct |
| documents | document_id, slug UNIQUE, title, content_class, visibility(shared/personal), owner_actor_id, review_policy_id | content_class enum: playbook, sop, index, reference, dossier_narrative, distillation, rule (rule class reserved — see §8) |
| document_slug_aliases | alias_slug PK, document_id, replaced_at | old links keep resolving |
| sections | section_id, document_id, section_key (UNIQUE per doc), ordinal, current_revision_id, current_version, review_after, body_hash | reorder changes ordinal, never identity |
| section_revisions | revision_id, section_id, version (UNIQUE per section), parent_revision_id, change_set_id, actor_id, session_id, body jsonb, plain_text, content_hash, search_vector tsvector GIN | append-only; body validated by per-class JSON schema |
| change_sets | change_set_id, actor_id, session_id, idempotency_key (UNIQUE per actor), state | multi-section all-or-nothing |
| change_set_items | change_set_id, section_id, expected_version, proposed_body | |
| content_links | source_section_id, target_kind, target_id, role(citation/related/example/source) | plain references; target must exist at write; NO precedence |
| edge_types | edge_type PK, acyclic bool, precedence_rank, validator_key | seed: OVERRIDES(10), SUPERSEDES(20), EXCEPTION_TO(30), DEPENDS_ON, APPLIES_TO, CONFLICTS_WITH |
| doctrine_edges | source_section_id, target_section_id, edge_type, scope jsonb, introduced_by_revision_id, retired_by_revision_id | typed semantics, split from links — do not collapse |
| review_policies | max_age_days, revalidate_on_dep_change | staleness is COMPUTED state, never lifecycle rows |
| doctrine_meta | singleton generation bigint | bumps on every commit |
| document_snapshots | document_id, generation, snapshot_json, content_hash | rebuilt on commit, read-through cache in Postgres |
| section_claims | section_id, holder session/actor, purpose, expires_at | soft expiring claims, TTL 300s default / 1800s max |
| gate_checks / gate_runs / gate_run_findings | check_key, severity(block/warn), applies_to jsonb, impl_key, config jsonb | the gate registry (§4) |
| migration_batches | batch_no, phase, source_paths, source_hashes, state, row_counts | the migration ledger |
| md_write_manifest | path PK, purpose, writer, retires_at | the exact-path allowlist (compiled_rules rows carry retires_at) |
| skill_sync_state | skill_path, repo_hash, cloud_hash, drift bool | skills-as-code release artifact |
| doctrine_events | event_type, actor, session, payload | every verb emits one |

Search: Postgres FTS day one (weighted tsvector, websearch_to_tsquery, trigram on slugs/titles,
filters by class/visibility/staleness, telemetry on every query). Embeddings DEFERRED — named
trigger: on a maintained sample of ≥50 real queries, recall@10 <85% for 4 consecutive weeks
after FTS tuning, OR >15% of sampled sessions re-search within 60s with substantially different
terms. Both chairs independently produced these exact numbers.

## 3. Verb surface (~14)

Writes (all require idempotency_key + base_version; validation runs as hard preconditions):
document.create · section.write · section.move · section.retire · refs.set ·
change.prepare/commit (multi-section atomic) · section.claim / claim_renew / claim_release

Reads: doc.read (whole doc, one round trip, if_generation_match short-circuit) ·
section.batch_get (≤50) · doc.index · doctrine.search · doctrine.snapshot (pin=true gives a
session a coherent generation for multi-step work) · doctrine.resolve_rules (precedence trace;
fail closed on cycles / unresolved CONFLICTS_WITH) · gates.dry_run

Conflict contract: version_conflict returns expected/actual versions, last writer's actor +
session_kind + time, diff summary, and resolution options. Humans get reload-merge /
confirm-at-new-base / history. Agents must re-read and re-plan. There is NEVER a
last-writer-wins path, and agents never self-waive a gate.

## 4. Gate framework (a registry, never verb rewrites)

Checks are code in the connector package, registered as gate_checks rows (severity, applies_to
by content_class + operation, config jsonb for data-driven thresholds/phrase lists). Every write
verb calls GateRunner; any `block` finding aborts the transaction; `warn` records and returns.
Only synchronous deterministic checks may be preconditions. A checker crash blocks if that
check is a precondition. New check ships as: function + fixtures → registry row → deploy →
optional shadow dry_run → promote severity. Seed blocks: target_exists, edge_class_allowed,
edge_acyclic, unresolved_conflict, banned_phrases (the writing-lint incident class), body_schema,
slug_unique, base_version_match, claim_holder_or_free, personal_owner_required, no_md_escape.

## 5. Read path at fleet scale

Day one: generation counter + document_snapshots in Postgres + connector process-local LRU
(keyed by document_id|generation, TTL 60s), NOTIFY as optimization never correctness; pooled
connections (5–10 per connector process). Neon free tier is a COST INSTRUMENT, not a design
wall: preflight load bar = 20 concurrent sessions, 5 hot doc.reads + a search every 10s for 15
min → p95 read <150ms warm, p95 search <300ms. Promote to paid tier (~$19–25/mo, partner
pre-approved as a cost class) when projected monthly compute >70 hrs for 2 consecutive weeks
or pool-wait p95 >100ms in ordinary workload. Redis only at ≥2 connector replicas AND
(DB CPU >60% for 15 min OR read p95 >200ms after tuning).

## 6. Phases with acceptance criteria

Dependency: 0 → 1 → 2 → (3 ∥ 4) → 5 → 6. Central estimate 17–29 engineer-days
(existing connector machinery discounts the Codex greenfield bound of 32–48); calendar 3–5
weeks with migration observation.

**P0 — manifest + hard write-block (1–2d).** Done when: any unlisted .md write fails on every
write surface (Bash, Write/Edit, connector, scheduled jobs) naming the verb to use instead;
manifest holds ONLY bootstrap stub + skill trees + compiled_rules rows with retires_at; tests
cover forbidden task-list/brief/dossier/playbook paths, path traversal, symlinks, case; no warn
mode exists in production config.

**P1 — schema + gate skeleton (3–5d).** Done when: migrations apply clean on a Neon branch
and roll back; revision tables writable only through the connector role; gates.dry_run returns a
report on a fixture SOP; the banned_phrases fixture fails as designed.

**P2 — core verbs (4–6d).** Done when: two concurrent same-base writes → exactly one wins,
loser gets the full conflict payload; idempotency replay returns the original result with no second
revision; dead refs and override cycles cannot commit; a 10-section changeset commits fully or
leaves zero rows; generation bumps once per commit; personal-visibility docs invisible to
non-owners; round-trips preserve content exactly.

**P3 — search + resolve + claims + health (3–4d).** Done when: FTS finds a known section
top-5 on fixtures; A-OVERRIDES-B resolves A operative B suppressed with a printed trace;
unresolved CONFLICTS_WITH blocks; a foreign unexpired claim blocks and an expired one frees;
stale sections flag in doc.index; every doctrine health row prints its bound action inline
(house rule 590b11e1); preflight load bar measured and recorded.

**P4 — forced early batch (2–3d + review).** INDEX/routing (imported as edges + thin
narrative), writing-rules, auto-load-referenced doctrine, every incident-touched file. Done when:
batch ledger row verified — source hashes match, counts reconcile, links resolve or are recorded
orphans with tickets; dual-read window opens with a FIRM end date ≤14 days out; connector
reads match normalized source bodies.

**P5 — bounded batches → cutoff (3–5d calendar).** Done when: migration ledger covers 100%
of the doctrine inventory; dual-read code disabled; ALL generated .md renders moved to
_to_delete staging and their exporters retired; legacy jobs attempting recreation fail with audit
events; compiled-rules .md retired IF the rules-load verb passed its cold-start test (else its
manifest row keeps retires_at and P6 finishes it).

**P6 — fleet readiness + skills (3–4d).** Done when: the three preflights pass live — Cowork
executes a Drive-synced skill, a local session cold-starts from stub + verbs with rules recited
correctly, load bar met on measured tier (tier decision recorded with numbers); skill CI fails on
repo-vs-cloud hash drift; pinned snapshot stays coherent across 10 reads under concurrent
writes; a filesystem sweep finds zero .md outside the manifest.

**P7 — taught-rule store integration (POST-cutoff, study first).** Both chairs designed rule
precedence unaware the taught-rule store already exists and works. Do NOT build parallel
precedence. After cutoff: a study phase decides whether the rule store merges into the doctrine
store as content_class='rule' (gaining typed OVERRIDES edges) or stays separate with resolve_rules
reading both. Council check-in before either; the rule store remains the binding surface throughout.

## 7. Still not built (each with its named trigger)

Redis (≥2 replicas + measured breach) · embeddings (§2 trigger) · graph DB (resolve p95 >500ms
or >8-hop traversals) · Kafka (>1 independent replay consumer) · CRDTs/silent merge (NEVER for
binding text; disjoint-metadata rebase only) · sharding (post-paid-tier vertical limits) ·
per-section ACL (first non-partner human) · tenancy machinery (second firm) · CMS
draft/publish lifecycle (a named dual-control policy for client-facing doctrine only) ·
whole-document replace as a normal path (migration-import only, session_kind='migration') ·
warn-only .md mode (never) · Drive human projections (only a new partner ruling).

## 8. Open items the build must respect

- The retrieval-as-code index re-points to store sections during P3; dual-read only inside the
  migration window.
- The idea-inbox intake path becomes document.create(content_class='distillation') from P4 on.
- Skills fixtures framework seeds with the two highest-consequence skills (council,
  social-media-manager) in P6.
- Dell touch: nothing in his workflow changes before cutoff; his sessions gain the same verbs.

-- 0125_ai_capability_program.sql
-- One governed, ordered AI-engineering portfolio.  Each project is a canonical
-- Work Request; this migration adds only the ordering and build-context fields
-- the existing Work Request machine lacks.  It is not a generic workflow engine.

begin;

alter table ops.work_request
  add column if not exists program_key text,
  add column if not exists program_ordinal smallint,
  add column if not exists disposition text,
  add column if not exists existing_status text,
  add column if not exists project_context jsonb not null default '{}'::jsonb,
  add column if not exists completion_kind text,
  add column if not exists completion_evidence jsonb,
  add column if not exists version integer not null default 1;

alter table ops.work_request
  add constraint work_request_program_ordinal_positive
    check (program_ordinal is null or program_ordinal > 0),
  add constraint work_request_program_fields_travel_together
    check ((program_key is null) = (program_ordinal is null)),
  add constraint work_request_program_disposition
    check (disposition is null or disposition in ('build','extend','adopt','decline')),
  add constraint work_request_program_completion_kind
    check (completion_kind is null or completion_kind in ('built','extended','adopted','declined')),
  add constraint work_request_program_close_has_bundle
    check (program_key is null or state <> 'confirmed_closed' or
      (completion_kind is not null and completion_evidence is not null));

create unique index if not exists work_request_program_ordinal_uniq
  on ops.work_request (program_key, program_ordinal)
  where program_key is not null;

comment on column ops.work_request.project_context is
  'Session-complete context for one governed portfolio item: scope, non-goals, prerequisites, first deliverable, acceptance, rollback, data risk, effort, evidence and completion meaning. Operational metadata only; never client payload or secrets.';

create or replace view ops.v_capability_program_next as
select w.*
  from ops.work_request w
 where w.program_key is not null
   and w.state <> 'confirmed_closed'
   and not exists (
     select 1 from ops.work_request p
      where p.program_key = w.program_key
        and p.program_ordinal < w.program_ordinal
        and p.state <> 'confirmed_closed'
   );

comment on view ops.v_capability_program_next is
  'Exactly the first not-yet-verified Work Request in each ordered capability program. Blocked, failed, needs-Joe and verification rows remain current; later rows never skip them.';

grant select on ops.v_capability_program_next to carr_reader, carr_writer, carr_jobs;

insert into ops.work_request
  (program_ordinal, program_key, ref, title, disposition, existing_status, state,
   desired_outcome, acceptance_criteria, project_context, requester_actor, owner_actor)
values
  (1, 'carr-ai-engineering-suite-v1', 'WR-AI-001', 'LLM evaluation harness', 'extend', 'operational_offline', 'ready',
   'Make provider and model changes replaceable and regression-tested against CARR grounding, refusal, privacy, authority, cost and latency requirements.',
   '["Synthetic provider output is scored against all ten normative AI evaluation areas","Every run binds suite, route and policy digests","Failures redact canaries and never enable actions"]'::jsonb,
   '{"scope":"Add a synthetic provider-output adapter, redacted observed-run artifact and baseline history to the existing offline evaluator.","non_goals":["No live client data","No model action authority","No promotion threshold until repeated observed runs exist"],"prerequisites":["Existing model-boundary v1 suite","Human-approved synthetic provider route"],"first_deliverable":"Provider-neutral response adapter and one observed synthetic scorecard","rollback_exit":"Disable adapter and retain offline suite","data_risk":"D1 synthetic","effort":"M","evidence":["evals/ai/model-boundary.v1.json","ops/ai_eval.py"],"completion_definition":"Extended when actual synthetic provider outputs are replayable, redacted, attributed and independently verified."}'::jsonb, 'joe', 'joe'),
  (2, 'carr-ai-engineering-suite-v1', 'WR-AI-002', 'Structured-output parser', 'extend', 'partial', 'ready',
   'Turn model output into a typed, fail-closed object before any downstream route can use it.',
   '["Missing, malformed, extra or stale fields refuse","Validation retries are bounded","No invalid response reaches a write path"]'::jsonb,
   '{"scope":"Create response-envelope v1 with JSON-schema validation, exact references and bounded repair.","non_goals":["No custom CFG engine","No free-form repair loop","No authority from model fields"],"prerequisites":["Project 1 adapter","Route and policy identifiers"],"first_deliverable":"Versioned response-envelope contract and negative fixtures","rollback_exit":"Disable parser-bound route","data_risk":"D1 synthetic","effort":"S","evidence":["MCP input schemas","ops/ai_eval.py"],"completion_definition":"Extended when every model-facing pilot route fails closed through the envelope."}'::jsonb, 'joe', 'joe'),
  (3, 'carr-ai-engineering-suite-v1', 'WR-AI-003', 'Function-calling router', 'extend', 'partial', 'ready',
   'Ensure a model can propose only registered actions while server policy owns identity, tenant, capability and authorization.',
   '["Unknown tools and argument mismatches refuse","Action allowlist is server-derived","No model field widens capability"]'::jsonb,
   '{"scope":"Bind validated response envelopes to the existing MCP registry and action-risk policy.","non_goals":["No arbitrary tool names","No model-selected tenant or capability","No generic plugin executor"],"prerequisites":["Projects 1 and 2","Action-risk registry gap review"],"first_deliverable":"Typed read-only router adapter with refusal fixtures","rollback_exit":"Feature flag off; direct typed verbs remain","data_risk":"D1 synthetic","effort":"M","evidence":["mcp-server/src/mcp.js","control-room/contracts/action-risk-registry.v1.json"],"completion_definition":"Extended when allowed synthetic routes work and every authority-widening case refuses."}'::jsonb, 'joe', 'joe'),
  (4, 'carr-ai-engineering-suite-v1', 'WR-AI-004', 'Guardrails system', 'extend', 'operational_partial', 'ready',
   'Close known authority, concurrency and data-boundary gaps with deterministic controls instead of model promises.',
   '["Known NONE protections have owned dispositions","Concurrent/stale writes are refused","Negative authority and data tests pass"]'::jsonb,
   '{"scope":"Remediate the highest-risk action-registry gaps and bind guard results to route evidence.","non_goals":["No model-only safety layer","No broad policy engine rewrite","No silent degradation"],"prerequisites":["Projects 1 through 3","Per-action owner decision"],"first_deliverable":"Ranked remediation slice with prewritten negative tests","rollback_exit":"Revert isolated action protection; preserve refusal default","data_risk":"D2 internal metadata","effort":"M","evidence":["mcp-server/src/mcp.js","hooks","control-room/contracts/action-risk-registry.v1.json"],"completion_definition":"Extended when the selected gaps are enforced and independently regression-tested."}'::jsonb, 'joe', 'joe'),
  (5, 'carr-ai-engineering-suite-v1', 'WR-AI-005', 'AI gateway', 'adopt', 'absent', 'ready',
   'Make provider substitution, health and cost routing possible without distributing data more widely or weakening assurances.',
   '["Fallback is only among preapproved read-only routes","Provider attribution and cost remain visible","Unavailable assurance fails closed"]'::jsonb,
   '{"scope":"Evaluate a minimal route registry and circuit breaker after multi-provider evidence exists.","non_goals":["No transparent unsafe retry","No generic proxy","No action fallback"],"prerequisites":["Projects 1 through 4","Documented provider outage, cost or quality need"],"first_deliverable":"Gateway decision memo and synthetic comparison","rollback_exit":"Remove alternate routes and retain direct provider path","data_risk":"D1 synthetic","effort":"M","evidence":[],"completion_definition":"Adopted only with pin, attribution, fail-closed tests, removal proof and owner decision; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (6, 'carr-ai-engineering-suite-v1', 'WR-AI-006', 'RAG pipeline', 'extend', 'operational_lexical', 'ready',
   'Improve grounded retrieval while preserving current-source, provenance and workspace boundaries.',
   '["Golden queries identify current source in top results","Archive and superseded material are excluded","Workspace filtering occurs before ranking"]'::jsonb,
   '{"scope":"Build a golden retrieval scorecard for the existing section index and Postgres FTS before changing retrieval architecture.","non_goals":["No vector rewrite without measured failure","No unsourced generated answer","No graph as authority"],"prerequisites":["Representative query set","Current-source labels"],"first_deliverable":"Versioned golden-query fixture and score report","rollback_exit":"Regenerate index; retain lexical baseline","data_risk":"D2 internal metadata","effort":"S","evidence":["pipelines/build-section-index.py","mcp-server/src/doctrine.js"],"completion_definition":"Extended when retrieval quality and provenance are measured and regressions gate changes."}'::jsonb, 'joe', 'joe'),
  (7, 'carr-ai-engineering-suite-v1', 'WR-AI-007', 'Agent loop / ReAct', 'extend', 'partial_bounded_workflows', 'ready',
   'Support bounded plan-tool-verify work without recursive, unpriced or authority-blurring autonomy.',
   '["Step, time, cost and action caps are enforced","Unknown or repeated failure stops","Completion requires external evidence"]'::jsonb,
   '{"scope":"Add one typed plan-to-tool-to-verification loop for a synthetic read-only task.","non_goals":["No general recursive agent","No self-approval","No merge, deploy or external action"],"prerequisites":["Projects 1 through 4","Named repeatable task"],"first_deliverable":"Bounded-loop state contract and failure fixtures","rollback_exit":"Disable route and keep explicit workflow","data_risk":"D1 synthetic","effort":"M","evidence":["mcp-server/src/mcp.js","workflow contracts"],"completion_definition":"Extended when one qualified use case completes within caps and all stop cases are proven."}'::jsonb, 'joe', 'joe'),
  (8, 'carr-ai-engineering-suite-v1', 'WR-AI-008', 'Data-curation and deduplication pipeline', 'extend', 'operational_deterministic', 'ready',
   'Reduce duplicate identities without hiding false merges behind opaque similarity scores.',
   '["False-merge and false-split fixtures are adjudicated","Same-name without corroboration stays distinct","No automatic canonical identity merge"]'::jsonb,
   '{"scope":"Create a labelled dedup scorecard for existing lead and capture identity rules.","non_goals":["No MinHash or embeddings before measured need","No model-owned identity","No destructive merge"],"prerequisites":["Human-adjudicated fixture set"],"first_deliverable":"Dedup evaluation fixture and baseline","rollback_exit":"Retain current corroboration rule","data_risk":"D2 internal metadata","effort":"S","evidence":["workflows/lead-sweep.workflow.js","mcp-server/src/capture.js"],"completion_definition":"Extended when false merge/split rates are measurable and regression-tested."}'::jsonb, 'joe', 'joe'),
  (9, 'carr-ai-engineering-suite-v1', 'WR-AI-009', 'Synthetic-data generator', 'extend', 'partial_handcrafted', 'ready',
   'Expand privacy-safe evaluation coverage without copying client data or mistaking generated records for facts.',
   '["Generation is deterministic from a seed","No real identifiers or canaries leak","Generated data preserves declared invariants"]'::jsonb,
   '{"scope":"Add property-based generators for evaluation and contract fixtures only.","non_goals":["No realistic client dossier synthesis","No production baseline from generated data","No retained model prompts"],"prerequisites":["Project 1 fixture schemas","Data classification review"],"first_deliverable":"Seeded fixture generator and manifest","rollback_exit":"Delete generated artifacts; handcrafted fixtures remain","data_risk":"D1 synthetic","effort":"S","evidence":["evals/ai/model-boundary.v1.json"],"completion_definition":"Extended when seeded fixtures are reproducible, invariant-safe and independently leakage-tested."}'::jsonb, 'joe', 'joe'),
  (10, 'carr-ai-engineering-suite-v1', 'WR-AI-010', 'Knowledge-graph builder', 'extend', 'operational_derived', 'ready',
   'Improve traceability and navigation through sourced relations without making inferred graph edges canonical facts.',
   '["Every edge carries source and freshness","Stale or orphan edges are visible","Graph output is reproducible and non-authoritative"]'::jsonb,
   '{"scope":"Add edge coverage, provenance and health scoring to the current derived graph.","non_goals":["No graph inference as truth","No generic graph database","No automatic business relationship claim"],"prerequisites":["Named edge semantics and source set"],"first_deliverable":"Graph health report with stale-edge fixtures","rollback_exit":"Regenerate or delete derived graph","data_risk":"D2 internal metadata","effort":"S","evidence":["pipelines/build-system-graph.py","mcp-server/src/doctrine.js"],"completion_definition":"Extended when graph quality is measured and all edges remain attributable."}'::jsonb, 'joe', 'joe'),
  (11, 'carr-ai-engineering-suite-v1', 'WR-AI-011', 'Semantic router', 'extend', 'partial_deterministic', 'ready',
   'Route work to the cheapest qualified executor using typed task, risk, data and capability facts.',
   '["Unknown routes default safe","Cost and risk constraints are testable","A model suggestion cannot authorize its route"]'::jsonb,
   '{"scope":"Create a typed deterministic route table and validator; model classification may be advisory only.","non_goals":["No LLM-selected authority","No hidden cost escalation","No generic agent marketplace"],"prerequisites":["Projects 1 through 4","Task and risk taxonomy"],"first_deliverable":"Route table with cheap-qualified and refusal fixtures","rollback_exit":"Default to main qualified route","data_risk":"D1 synthetic","effort":"M","evidence":["hooks/delegation-gate.py","hooks/executor-tier-gate.py"],"completion_definition":"Extended when representative routes select safely and unknowns refuse."}'::jsonb, 'joe', 'joe'),
  (12, 'carr-ai-engineering-suite-v1', 'WR-AI-012', 'Prompt caching', 'extend', 'partial_domain_specific', 'ready',
   'Reduce latency and cost for safely repeatable inputs without caching authority, stale claims or cross-tenant context.',
   '["Keys bind tenant, source, policy and capability versions","Revocation and expiry invalidate cache","Decision and health outputs never cache as authority"]'::jsonb,
   '{"scope":"Pilot one non-authoritative, versioned local cache on synthetic or already-local inputs.","non_goals":["No client prompt cache","No cached approval or decision","No shared unscoped cache"],"prerequisites":["Cache key, TTL and revocation policy"],"first_deliverable":"Cache contract and invalidation test suite","rollback_exit":"Purge cache and disable route","data_risk":"D1 synthetic","effort":"S","evidence":["tools/dictation-rig","control-room/contracts/security-redaction.v1.json"],"completion_definition":"Extended when hit value is measured and stale/cross-scope reads are impossible."}'::jsonb, 'joe', 'joe'),
  (13, 'carr-ai-engineering-suite-v1', 'WR-AI-013', 'Code-interpreter sandbox', 'adopt', 'absent', 'ready',
   'Permit useful computation on synthetic inputs inside a disposable, evidence-bound environment.',
   '["Network, filesystem and resource isolation negative tests pass","Every run has TTL and immutable image digest","Cleanup and artifact redaction are proven"]'::jsonb,
   '{"scope":"Evaluate one hardened replaceable runtime for a named no-secret computation.","non_goals":["No live records","No default network","No claim that worktrees are sandboxes"],"prerequisites":["Named computation existing scripts cannot handle","Isolation threat model"],"first_deliverable":"Sandbox manifest and negative rehearsal","rollback_exit":"Destroy runtime, image and artifacts","data_risk":"D1 synthetic","effort":"L","evidence":[],"completion_definition":"Adopted only after isolation, cleanup, pin, rebuild and removal evidence; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (14, 'carr-ai-engineering-suite-v1', 'WR-AI-014', 'Text-to-SQL', 'decline', 'absent_unnecessary', 'ready',
   'Make an explicit evidence-backed decision on whether natural-language database access has any safe CARR use.',
   '["Decision addresses injection, authorization and data exposure","Typed verbs are compared as baseline","Any future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No natural-language production queries","No generated SQL execution","No new database credentials"],"prerequisites":[],"first_deliverable":"Decline decision with reconsideration trigger","rollback_exit":"Retain typed MCP verbs","data_risk":"D3 forbidden","effort":"XS","evidence":["mcp-server typed verb surface"],"completion_definition":"Declined when owner decision and independent review establish typed verbs as the safer boundary."}'::jsonb, 'joe', 'joe'),
  (15, 'carr-ai-engineering-suite-v1', 'WR-AI-015', 'Graph RAG', 'extend', 'absent', 'ready',
   'Determine whether sourced graph relations improve retrieval beyond lexical and metadata baselines.',
   '["Comparison uses fixed queries","Every result traces to current source edges","Graph-derived output never becomes authority"]'::jsonb,
   '{"scope":"Read-only synthetic comparison only after Projects 6 and 10 expose a measured gap.","non_goals":["No production graph retrieval","No inferred canonical facts","No cross-workspace graph"],"prerequisites":["Golden retrieval failures","Healthy sourced graph"],"first_deliverable":"Graph retrieval comparison report","rollback_exit":"Remove derived graph index","data_risk":"D1 synthetic","effort":"M","evidence":["pipelines/build-system-graph.py"],"completion_definition":"Extended only if measured improvement survives provenance tests; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (16, 'carr-ai-engineering-suite-v1', 'WR-AI-016', 'Vector database / HNSW', 'adopt', 'absent', 'ready',
   'Add semantic recall only if lexical retrieval cannot meet a named, measured need.',
   '["Tenant filter precedes rank, snippets and counts","Delete, revoke and reindex are proven","Recall gain exceeds maintenance cost"]'::jsonb,
   '{"scope":"Synthetic disposable pgvector or equivalent comparison after Project 6.","non_goals":["No custom vector database","No PII embeddings","No canonical identity from similarity"],"prerequisites":["Golden-query failure evidence","Embedding policy"],"first_deliverable":"Read-only vector pilot report","rollback_exit":"Drop disposable index and extension","data_risk":"D1 synthetic","effort":"M","evidence":[],"completion_definition":"Adopted only with measurable gain, scope isolation and removal proof; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (17, 'carr-ai-engineering-suite-v1', 'WR-AI-017', 'Embedding model', 'adopt', 'absent', 'ready',
   'Choose a replaceable semantic representation only if it improves an already-measured retrieval task.',
   '["Model and license are pinned","Deletion and re-embedding are reproducible","Quality is compared against lexical baseline"]'::jsonb,
   '{"scope":"Evaluate an adopted embedding model on synthetic golden queries.","non_goals":["No custom model training","No business PII","No embedding as fact or identity"],"prerequisites":["Projects 6 and 16","License and retention review"],"first_deliverable":"Embedding comparison manifest","rollback_exit":"Remove weights and index","data_risk":"D1 synthetic","effort":"M","evidence":[],"completion_definition":"Adopted only with quality, license, rebuild and removal evidence; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (18, 'carr-ai-engineering-suite-v1', 'WR-AI-018', 'Adversarial-attack generator', 'extend', 'absent', 'ready',
   'Systematically expand prompt-injection, leakage and refusal regression coverage without attacking live systems.',
   '["Generated cases are deterministic and redacted","Targets are synthetic/local only","Expected refusal is explicit"]'::jsonb,
   '{"scope":"Add a bounded property/fuzz mutator for the AI eval fixtures.","non_goals":["No live target scanning","No exploit deployment","No retained unsafe payload corpus outside tests"],"prerequisites":["Project 1","Measured fixture coverage gap"],"first_deliverable":"Versioned adversarial fixture pack","rollback_exit":"Delete generated pack; keep core fixtures","data_risk":"D1 synthetic","effort":"S","evidence":["workspace/contracts/phase0-acceptance.v1.json"],"completion_definition":"Extended when new cases reproduce safety failures and pass redaction review."}'::jsonb, 'joe', 'joe'),
  (19, 'carr-ai-engineering-suite-v1', 'WR-AI-019', 'Whisper-style ASR', 'extend', 'operational_local', 'ready',
   'Strengthen private, resilient local transcription with pinned, observable and consent-aware operation.',
   '["Health and rebuild paths pass","Consent and retention boundaries are tested","Accuracy is measured on representative permitted audio"]'::jsonb,
   '{"scope":"Harden the existing local whisper.cpp lane and provider-independent interface.","non_goals":["No self-trained ASR","No third-party voiceprints","No unconsented capture"],"prerequisites":["Existing dictation rig","Consent fixture set"],"first_deliverable":"Pinned rebuild and health manifest","rollback_exit":"Uninstall model and return to manual capture","data_risk":"D3 separately governed audio","effort":"S","evidence":["tools/dictation-rig/README.md"],"completion_definition":"Extended when local rebuild, consent, retention, health and accuracy evidence are current."}'::jsonb, 'joe', 'joe'),
  (20, 'carr-ai-engineering-suite-v1', 'WR-AI-020', 'Text-to-speech pipeline', 'extend', 'partial_provider_backed', 'ready',
   'Provide accessible voice output through a replaceable, reviewed and privacy-scoped boundary.',
   '["Provider or local engine can be disabled cleanly","Text and cache follow redaction rules","Quality failure has a non-voice fallback"]'::jsonb,
   '{"scope":"Define provider-neutral TTS input, cache, consent and output-review contract around the current pipeline.","non_goals":["No neural TTS training","No unapproved voice cloning","No unreviewed external publishing"],"prerequisites":["Voice, license and retention decision"],"first_deliverable":"TTS boundary manifest and comparison","rollback_exit":"Disable voice route and purge cache","data_risk":"D2 internal content","effort":"M","evidence":["tools/doc-convo/bin/speak.py"],"completion_definition":"Extended when one replaceable route passes redaction, quality and fallback tests; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (21, 'carr-ai-engineering-suite-v1', 'WR-AI-021', 'Small language model', 'adopt', 'partial_ungoverned', 'ready',
   'Create a local outage/privacy option for one synthetic mechanical task without making it an authority dependency.',
   '["Quality, latency, energy and maintenance are measured","Weights and license are pinned","Rebuild and removal are proven"]'::jsonb,
   '{"scope":"Benchmark one licensed local compact model on a synthetic R0 extraction/classification task.","non_goals":["No live business records","No action capability","No recovery dependency"],"prerequisites":["Project 1 adapter","Suitable hardware and license review"],"first_deliverable":"Local-model benchmark manifest","rollback_exit":"Remove weights and runtime","data_risk":"D1 synthetic","effort":"M","evidence":["local llama.cpp path in post-call tooling"],"completion_definition":"Adopted only for the named route with reproducible value and removal evidence; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (22, 'carr-ai-engineering-suite-v1', 'WR-AI-022', 'Inference server', 'adopt', 'absent_for_llm', 'ready',
   'Serve an approved local model reproducibly if Project 21 earns an operational route.',
   '["Runtime is pinned and health-checked","Resource limits and startup are reproducible","No custom server implementation"]'::jsonb,
   '{"scope":"Adopt a replaceable OSS runtime for the one approved local route.","non_goals":["No C++ or Rust server build","No public endpoint","No live action authority"],"prerequisites":["Project 21 adoption decision"],"first_deliverable":"Runtime manifest and cold-start rehearsal","rollback_exit":"Uninstall runtime and remove service","data_risk":"D1 synthetic","effort":"L","evidence":[],"completion_definition":"Adopted with pin, health, resource, rebuild and removal evidence; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (23, 'carr-ai-engineering-suite-v1', 'WR-AI-023', 'Quantization library', 'adopt', 'absent', 'ready',
   'Reduce local model resource cost only when quality loss and maintenance are acceptable.',
   '["Quantized quality is compared to baseline","Runtime and license stay pinned","Resource improvement is measured"]'::jsonb,
   '{"scope":"Use the selected runtime quantization path for Project 21 only.","non_goals":["No custom quantizer","No production quality assumption","No new model route"],"prerequisites":["Projects 21 and 22","Measured resource constraint"],"first_deliverable":"Quantized-versus-baseline report","rollback_exit":"Remove quantized weights","data_risk":"D1 synthetic","effort":"M","evidence":[],"completion_definition":"Adopted if resource gains justify measured quality loss and removal is proven; otherwise declined."}'::jsonb, 'joe', 'joe'),
  (24, 'carr-ai-engineering-suite-v1', 'WR-AI-024', 'Feature store', 'decline', 'absent_unnecessary', 'ready',
   'Record whether CARR has a real high-volume ML feature lifecycle that warrants a feature platform.',
   '["Decision names current alternatives and future trigger","Freshness and PII costs are considered","No speculative platform is installed"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No ML feature platform","No duplicated business facts","No new PII store"],"prerequisites":[],"first_deliverable":"Decline decision with reconsideration trigger","rollback_exit":"Retain canonical record and derived views","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined when owner decision confirms no measured ML product need."}'::jsonb, 'joe', 'joe'),
  (25, 'carr-ai-engineering-suite-v1', 'WR-AI-025', 'Recommendation system', 'decline', 'absent_unnecessary', 'ready',
   'Decide explicitly whether opaque personalized ranking creates any justified CARR outcome.',
   '["Decision addresses fairness, accountability and feedback loops","Deterministic prioritization is the baseline","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No client or lead ranking model","No two-tower training","No automated business decision"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Retain governed deterministic queues","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision and independent fairness/authority review."}'::jsonb, 'joe', 'joe'),
  (26, 'carr-ai-engineering-suite-v1', 'WR-AI-026', 'Vector database driver', 'decline', 'absent_dependency', 'ready',
   'Avoid adding a maintenance dependency before any vector-store adoption is earned.',
   '["Decision binds reconsideration to Project 16 adoption","No premature package lands","Driver authority and tenant filtering are named"]'::jsonb,
   '{"scope":"Decision review only unless Project 16 adopts a store.","non_goals":["No custom database driver","No unused dependency","No direct unscoped vector queries"],"prerequisites":[],"first_deliverable":"Defer-or-decline decision tied to Project 16","rollback_exit":"No change","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined now with an exact adoption trigger, or adopted later only as part of Project 16."}'::jsonb, 'joe', 'joe'),
  (27, 'carr-ai-engineering-suite-v1', 'WR-AI-027', 'Reasoner / Chain-of-Thought implementation', 'decline', 'absent_unnecessary', 'ready',
   'Preserve auditable outcomes without storing or exposing hidden model reasoning.',
   '["Decision distinguishes evidence/rationale from hidden reasoning","Privacy and prompt-leak risks are documented","No scratchpad store is created"]'::jsonb,
   '{"scope":"Decision review and output-contract clarification only.","non_goals":["No Chain-of-Thought capture","No hidden-reasoning scoring","No reasoner model build"],"prerequisites":[],"first_deliverable":"Decline decision and evidence-output rule","rollback_exit":"Retain source, uncertainty and concise rationale fields","data_risk":"D0 none","effort":"XS","evidence":["AI response envelope"],"completion_definition":"Declined when the owned boundary requires evidence and refusal, never hidden reasoning."}'::jsonb, 'joe', 'joe'),
  (28, 'carr-ai-engineering-suite-v1', 'WR-AI-028', 'Interpretability / SAE tooling', 'decline', 'absent_unnecessary', 'ready',
   'Decide whether representation-level research has a measurable CARR safety or product outcome.',
   '["Decision compares provider evaluation and behavioral tests","Compute and model-access limits are documented","Future trigger is explicit"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No sparse autoencoder research","No provider-weight inspection","No interpretability claims from toy models"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Retain behavioral evals","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with evidence that behavioral boundaries dominate current value."}'::jsonb, 'joe', 'joe'),
  (29, 'carr-ai-engineering-suite-v1', 'WR-AI-029', 'LoRA trainer', 'decline', 'absent_unnecessary', 'ready',
   'Prevent premature model tuning on governed business data without a measured need.',
   '["Decision covers dataset, consent, provenance and weight license","Provider/local baselines are considered","No training artifacts remain"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No fine-tuning CARR data","No trainer implementation","No derivative weight publication"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use prompting/retrieval/evals","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined until a governed dataset and measured baseline gap exist."}'::jsonb, 'joe', 'joe'),
  (30, 'carr-ai-engineering-suite-v1', 'WR-AI-030', 'PEFT library', 'decline', 'absent_unnecessary', 'ready',
   'Avoid owning fine-tuning infrastructure when no governed tuning program exists.',
   '["Decision is linked to Project 29","No unused training dependency lands","Reconsideration trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No PEFT implementation","No fine-tuning stack","No business-data training"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"No change","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with Project 29 dependency recorded."}'::jsonb, 'joe', 'joe'),
  (31, 'carr-ai-engineering-suite-v1', 'WR-AI-031', 'Model-distillation pipeline', 'decline', 'absent_unnecessary', 'ready',
   'Avoid derivative-model compute, licensing and provenance work without a validated local-model outcome.',
   '["Decision addresses teacher rights and data provenance","Project 21 baseline is the future trigger","No derivative weights are produced"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No distillation training","No synthetic teacher corpus","No derivative weight hosting"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"No change","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined until Project 21 proves a value gap distillation uniquely solves."}'::jsonb, 'joe', 'joe'),
  (32, 'carr-ai-engineering-suite-v1', 'WR-AI-032', 'DPO loss', 'decline', 'absent_unnecessary', 'ready',
   'Record that preference-training internals are outside CARR product scope absent governed data and outcomes.',
   '["Decision covers preference-data consent and bias","No standalone toy implementation is mistaken for capability","Future trigger is explicit"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No DPO training","No preference dataset","No model-weight mutation"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use provider evaluation","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with owner and independent review."}'::jsonb, 'joe', 'joe'),
  (33, 'carr-ai-engineering-suite-v1', 'WR-AI-033', 'RLHF / PPO pipeline', 'decline', 'absent_unnecessary', 'ready',
   'Avoid a high-risk preference-training program with no CARR dataset, compute or product case.',
   '["Decision covers human labor, consent, reward gaming and compute","No training cluster or data store is created","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No RLHF pipeline","No reward model","No PPO training"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use governed provider selection","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision and risk review."}'::jsonb, 'joe', 'joe'),
  (34, 'carr-ai-engineering-suite-v1', 'WR-AI-034', 'Model merger', 'decline', 'absent_unnecessary', 'ready',
   'Avoid combining model weights without a measurable outcome, compatible licenses and evaluation corpus.',
   '["Decision addresses provenance and licenses","No merged weights are created","Reconsideration requires Project 21 evidence"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No model soups","No SLERP","No derivative model distribution"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"No change","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined until a governed local-model program proves need."}'::jsonb, 'joe', 'joe'),
  (35, 'carr-ai-engineering-suite-v1', 'WR-AI-035', 'KV-cache paging', 'decline', 'absent_unnecessary', 'ready',
   'Keep inference-runtime internals replaceable rather than becoming CARR-maintained infrastructure.',
   '["Decision compares adopted runtime capability","No runtime fork is created","Future trigger is resource evidence"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No KV pager implementation","No inference runtime fork","No production benchmark theater"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Adopt runtime features if ever needed","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined unless Projects 21 and 22 expose a measured runtime bottleneck."}'::jsonb, 'joe', 'joe'),
  (36, 'carr-ai-engineering-suite-v1', 'WR-AI-036', 'Speculative decoding', 'decline', 'absent_unnecessary', 'ready',
   'Avoid decoder research until local inference latency is a proven business constraint.',
   '["Decision references measured latency baseline","No decoder fork is created","Future trigger is explicit"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No speculative decoder implementation","No draft-model training","No runtime fork"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use adopted runtime","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined until Projects 21 and 22 demonstrate need."}'::jsonb, 'joe', 'joe'),
  (37, 'carr-ai-engineering-suite-v1', 'WR-AI-037', 'Tokenizer', 'decline', 'absent_unnecessary', 'ready',
   'Keep tokenization inside replaceable model runtimes because CARR gains no distinct advantage from owning it.',
   '["Decision records lack of measured product outcome","No compatibility surface is added","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No BPE implementation","No custom vocabulary training","No tokenizer fork"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use runtime tokenizer","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision."}'::jsonb, 'joe', 'joe'),
  (38, 'carr-ai-engineering-suite-v1', 'WR-AI-038', 'Transformer', 'decline', 'absent_unnecessary', 'ready',
   'Explicitly reject foundation-model construction as unrelated to CARR differentiation.',
   '["Decision covers data, GPU, security and maintenance cost","Provider/runtime substitution is the baseline","No model code is created"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No transformer from scratch","No pretraining","No foundation-model repository"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Own model-use boundary, not model internals","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with strategic rationale."}'::jsonb, 'joe', 'joe'),
  (39, 'carr-ai-engineering-suite-v1', 'WR-AI-039', 'Vision Transformer', 'decline', 'absent_unnecessary', 'ready',
   'Use scoped replaceable vision tools when needed rather than owning a vision-model training program.',
   '["Decision covers image consent, IP and compute","No training data is collected","Future product trigger is explicit"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No ViT training","No image dataset","No owned vision weights"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Adopt task-specific vision tool if earned","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with owner and privacy review."}'::jsonb, 'joe', 'joe'),
  (40, 'carr-ai-engineering-suite-v1', 'WR-AI-040', 'Multimodal projector / CLIP', 'decline', 'absent_unnecessary', 'ready',
   'Avoid multimodal-model construction without a named CARR task that existing scoped tools cannot solve.',
   '["Decision compares OCR and existing vision tools","Consent and retention risks are addressed","No model artifacts are created"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No CLIP training","No projector implementation","No broad image ingestion"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use scoped media analysis","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined until a measured multimodal gap exists."}'::jsonb, 'joe', 'joe'),
  (41, 'carr-ai-engineering-suite-v1', 'WR-AI-041', 'Diffusion model', 'decline', 'absent_unnecessary', 'ready',
   'Separate image-generation use from the cost and risk of building an owned generative model.',
   '["Decision covers IP, dataset, safety and compute","Replaceable generation tools are considered","No weights or datasets are created"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No UNet or scheduler build","No diffusion training","No image corpus"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use approved replaceable image tool","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with strategic and IP rationale."}'::jsonb, 'joe', 'joe'),
  (42, 'carr-ai-engineering-suite-v1', 'WR-AI-042', 'Audio Spectrogram Transformer', 'decline', 'absent_unnecessary', 'ready',
   'Retain the proven local ASR path instead of adding a second owned audio-model architecture.',
   '["Decision compares Project 19 baseline","Consent and model-maintenance costs are covered","No new audio weights are created"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No AST implementation","No audio-model training","No new recording corpus"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Maintain local Whisper lane","data_risk":"D3 forbidden","effort":"XS","evidence":["tools/dictation-rig/README.md"],"completion_definition":"Declined unless Project 19 exposes a measured gap."}'::jsonb, 'joe', 'joe'),
  (43, 'carr-ai-engineering-suite-v1', 'WR-AI-043', 'Logit processor', 'decline', 'absent_unnecessary', 'ready',
   'Keep decoder controls inside replaceable runtimes and typed output validation.',
   '["Decision compares structured-output enforcement","No decoder hook is added","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No custom logit processor","No token-level steering","No provider-specific fork"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use Project 2 response contract","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision."}'::jsonb, 'joe', 'joe'),
  (44, 'carr-ai-engineering-suite-v1', 'WR-AI-044', 'State Space Model / Mamba', 'decline', 'absent_unnecessary', 'ready',
   'Avoid architecture research with no CARR data, compute or product case.',
   '["Decision records lack of measured outcome","No architecture code is created","Future trigger is explicit"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No SSM implementation","No Mamba training","No model research track"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use replaceable models","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with strategic rationale."}'::jsonb, 'joe', 'joe'),
  (45, 'carr-ai-engineering-suite-v1', 'WR-AI-045', 'Mixture-of-Experts routing layer', 'decline', 'absent_unnecessary', 'ready',
   'Distinguish CARR task routing from foundation-model expert routing and avoid owning the latter.',
   '["Decision distinguishes Project 11 semantic routing","No model architecture work occurs","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No MoE layer","No expert training","No GPU routing infrastructure"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use deterministic task router","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with architecture distinction recorded."}'::jsonb, 'joe', 'joe'),
  (46, 'carr-ai-engineering-suite-v1', 'WR-AI-046', 'Distributed training / FSDP / tensor parallelism', 'decline', 'absent_unnecessary', 'ready',
   'Avoid training-cluster infrastructure because CARR has no governed large-model training program.',
   '["Decision covers compute, security, data and staffing","No cluster or cloud spend is authorized","Future trigger is explicit"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No FSDP","No tensor parallelism","No training cluster"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"No change","data_risk":"D3 forbidden","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision and cost review."}'::jsonb, 'joe', 'joe'),
  (47, 'carr-ai-engineering-suite-v1', 'WR-AI-047', 'Autograd engine', 'decline', 'absent_unnecessary', 'ready',
   'Avoid rebuilding commodity training primitives that create no CARR advantage.',
   '["Decision records no product outcome","No numeric engine is created","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No autograd implementation","No training framework","No pedagogical project in production repo"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use mature runtime libraries","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with strategic rationale."}'::jsonb, 'joe', 'joe'),
  (48, 'carr-ai-engineering-suite-v1', 'WR-AI-048', 'Matrix multiplication kernel', 'decline', 'absent_unnecessary', 'ready',
   'Keep low-level compute kernels in mature upstream runtimes.',
   '["Decision records no measurable CARR advantage","No kernel code or toolchain lands","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No CPU or GPU matmul kernel","No compiler toolchain","No benchmark-only project"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use upstream runtime","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision."}'::jsonb, 'joe', 'joe'),
  (49, 'carr-ai-engineering-suite-v1', 'WR-AI-049', 'Softmax optimization', 'decline', 'absent_unnecessary', 'ready',
   'Avoid kernel optimization work that belongs to adopted model runtimes.',
   '["Decision records no measured bottleneck","No kernel fork is created","Future trigger is named"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No softmax kernel","No CUDA code","No inference-runtime fork"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use upstream runtime","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision."}'::jsonb, 'joe', 'joe'),
  (50, 'carr-ai-engineering-suite-v1', 'WR-AI-050', 'FlashAttention CUDA kernel', 'decline', 'absent_unnecessary', 'ready',
   'Avoid owning CUDA and GPU supply-chain maintenance for a commodity runtime optimization.',
   '["Decision covers GPU/toolchain/security burden","No CUDA code lands","Future trigger is explicit"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No FlashAttention implementation","No CUDA extension","No GPU build chain"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"Use pinned upstream runtime","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with strategic and maintenance rationale."}'::jsonb, 'joe', 'joe'),
  (51, 'carr-ai-engineering-suite-v1', 'WR-AI-051', 'Neural Architecture Search', 'decline', 'absent_unnecessary', 'ready',
   'Close the portfolio with an explicit decision against open-ended model-architecture search.',
   '["Decision covers compute, data and research staffing","No search infrastructure or spend is authorized","Program completion remains evidence-bound"]'::jsonb,
   '{"scope":"Decision review only.","non_goals":["No NAS controller","No architecture search","No model-training program"],"prerequisites":[],"first_deliverable":"Decline decision","rollback_exit":"No change","data_risk":"D0 none","effort":"XS","evidence":[],"completion_definition":"Declined with owner decision and independent review; this verified close completes the ordered program."}'::jsonb, 'joe', 'joe');

commit;

do $$
declare v_count integer; v_current integer;
begin
  select count(*), min(program_ordinal) filter (where state <> 'confirmed_closed')
    into v_count, v_current
    from ops.work_request where program_key='carr-ai-engineering-suite-v1';
  if v_count <> 51 then raise exception '0125 FAILED: expected 51 program rows, got %', v_count; end if;
  if v_current <> 1 then raise exception '0125 FAILED: expected project 1 current, got %', v_current; end if;
  raise notice '0125: 51 ordered capability Work Requests seeded; project 1 is current';
end $$;

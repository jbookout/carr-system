-- 0272_control_catalog_from_enforcement_map.sql
--
-- approve-rule refuses unless every control a rule names is registered here and
-- verified. Registration was a hand-written migration per control, so on
-- 2026-08-22 the repository's enforcement map described 59 controls and this
-- table held THREE. Approving any rule enforced by the other 56 meant writing
-- and shipping SQL first — ceremony restating what the repository already
-- declares, and a second copy that could drift from the first.
--
-- These rows are GENERATED from ops/config/rule-enforcement-map.json by
-- ops/sync_control_catalog.py, never typed. ops/control-catalog-parity-gate.py
-- then fails CI whenever the two disagree, so the map stays the single source
-- and this table stays its projection.
--
-- THE TRUST BOUNDARY DOES NOT MOVE. A control could only ever be registered by
-- a merged, reviewed repository change; a migration was one such change and the
-- map is another. What is removed is the second transcription.
--
-- installed=false rows are deliberate and are NOT a failure. A control is only
-- marked installed when every path it names is tracked by git AND at least one
-- of its tests is one CI actually runs. Seven controls do not clear that bar --
-- three say so in the map itself ("no dedicated suite yet") -- and they are
-- written here as uninstalled rather than omitted, so approve-rule refuses them
-- with a reason instead of a silence (rule ab814a26: a rule ships with its
-- enforcement, and recitation is not enforcement).

begin;

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('artifact_read', 'hooks/unread-artifact-gate.py', 'ops/unread-artifact-gate-selftest.py', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('blocker_decider', 'hooks/blocker-decider-gate.py', 'ops/blocker-decider-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('calendar_intake', 'tools/calendar-intake-gate.py; bin/calendar-eventkit-capture.sh', 'ops/calendar-intake-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('candidate_match', 'pipelines/import_candidate_pool.py', 'ops/candidate-match-threshold-selftest.py', 'judgment_ambient', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('canonical_edit', 'hooks/canonical-edit-gate.py; hooks/worktree-self-plumb.py', 'ops/canonical-edit-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('chat_lint', 'hooks/chat-lint-gate.py; hooks/chat-lint-carryover.py', 'ops/chat-lint-gate-selftest.py; ops/unlinked-file-ask-selftest.py', 'surfacing', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('client_asset_packet', 'lib/client_asset_controls.py; pipelines/build-space-search.py', 'ops/client-asset-controls-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('close_before_open', 'hooks/close-before-open-gate.py; hooks/session-brief.py; ops/built_unclosed.py', 'ops/close-before-open-gate-selftest.py; ops/built-unclosed-selftest.py; ops/session-brief-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('cognition_token_admission', 'ops/config/control-plane-workflows.v1.json; lib/control_plane.py; lib/control_plane_runner.py; tools/control-plane.py', 'ops/control-plane-selftest.py; ops/control-plane-db-gate.py', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('commit_claims', 'ops/githooks/commit-claims-check.py; ops/githooks/commit-msg', 'ops/commit-claims-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('completion_evidence', 'hooks/completion-evidence-gate.py', 'ops/completion-evidence-gate-selftest.py', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('conduct_stop', 'hooks/conduct-stop-gate.py', 'ops/conduct-gate-selftest.py; ops/delta-resend-selftest.py', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('context_rail', 'hooks/context-handoff-gate.py', 'ops/context-handoff-gate-selftest.py', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('costar_lane', 'hooks/costar-lane-gate.py', 'ops/costar-lane-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('deal_guard', 'mcp-server/src/tools.js; migrations/0079_deal_room_api.sql; migrations/0091_deal_reconciliation_read.sql', 'mcp-server/test/dealroom.test.js', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('delegation_tripwire', 'hooks/delegation-gate.py', 'ops/delegation-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('draft_export_read', 'hooks/draft-export-gate.py', 'ops/draft-export-gate-selftest.py', 'surfacing', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('drift_claim', 'hooks/drift-claim-gate.py; hooks/drift-assertion-gate.py; hooks/run-record-gate.py', 'ops/drift-assertion-gate-selftest.py; ops/drive-runtime-hooks-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('egress_guard', 'hooks/guard-unattended.py', 'ops/guard-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('escalation', 'hooks/escalation-gate.py', 'ops/escalation-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('executor_tier', 'hooks/executor-tier-gate.py', 'external:no dedicated suite yet', 'deny_gate', false, null)
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('gate_edit', 'hooks/gate-edit-gate.py', 'ops/gate-edit-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('gate_integrity', 'hooks/gate-integrity.py', 'command:python3 hooks/gate-integrity.py --selftest', 'stop_gate', false, null)
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('git_writer', 'hooks/git-writer-gate.py', 'ops/git-writer-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('glanceable_lead', 'ops/glanceable-lead-check.py', 'ops/glanceable-lead-selftest.py', 'schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('human_authority_runtime', 'migrations/0161_control_plane_authority_boundary.sql; mcp-server/src/mcp.js', 'mcp-server/test/control-plane-authority-boundary.test.mjs; ops/control-plane-authority-runtime-preflight-selftest.py', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('index_upkeep', 'ops/githooks/index-upkeep-check.py; ops/githooks/pre-commit', 'ops/path-index-hygiene-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('lead_client_pair', 'mcp-server/src/tools.js', 'mcp-server/test/confirm-merge-lead-client.test.mjs', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('ledger_boundary', 'hooks/ledger-boundary-sweep.py', 'external:no dedicated suite yet', 'stop_gate', false, null)
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('ledger_capture', 'hooks/ledger-sweep.py', 'external:ledger scope regressions', 'stop_gate', false, null)
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('loop_guard', 'migrations/0081_loop_blocker.sql; mcp-server/src/tools.js', 'external:migration probes', 'transactional_schema', false, null)
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('loop_successor', 'mcp-server/src/tools.js', 'mcp-server/test/loop-version-and-marker.test.mjs', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('loose_work', 'hooks/loose-work-gate.py; hooks/staging-attribution-gate.py; hooks/staging-observation-tracker.py', 'ops/loose-work-gate-selftest.py', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('map_architecture', 'hooks/map-architecture-gate.py', 'ops/map-architecture-gate-selftest.py; mcp-server/test/map-architecture.test.mjs', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('merge_survivor', 'mcp-server/src/tools.js', 'mcp-server/test/confirm-merge-lead-client.test.mjs', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('model_floor', 'hooks/model-floor-gate.py', 'external:no dedicated suite yet', 'deny_gate', false, null)
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('no_send_worker', 'mcp-server/src/mcp.js', 'glob:mcp-server/test/*.test.js', 'judgment_ambient', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('one_repo', 'hooks/one-repo-gate.py; hooks/bash-write-gate.py', 'ops/one-repo-gate-selftest.py; ops/bash-write-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('outbound_format', 'mcp-server/src/tools.js', 'mcp-server/test/document-outbound-format.test.mjs', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('path_hygiene', 'ops/githooks/path-hygiene-check.py; ops/githooks/pre-commit', 'ops/path-index-hygiene-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('peer_broadcast', 'hooks/peer-broadcast-gate.py', 'ops/peer-broadcast-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('platform_metering_pre_dispatch', 'lib/platform_metering.py; ops/platform-metering-gate.py; hooks/guard-unattended.py', 'ops/platform-metering-gate-selftest.py; ops/platform-metering-policy-selftest.py; ops/guard-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('record_home', 'hooks/record-home-gate.py; hooks/install-record-home-gate.py; hooks/bash-write-gate.py; hooks/guard-unattended.py; hooks/write-effect-check.py', 'tools/test-record-home-gate.py; ops/bash-write-gate-selftest.py; ops/guard-selftest.py; ops/write-effect-check-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('record_research', 'mcp-server/src/tools.js', 'mcp-server/test/intake-research-gates.test.mjs', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('rule_shape', 'hooks/rule-shape-gate.py; migrations/0194_atomic_rule_approval.sql; migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql; mcp-server/src/tools.js', 'ops/rule-shape-gate-selftest.py; ops/atomic-rule-approval-selftest.py; mcp-server/test/rule-admission.test.mjs', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('scheduled_run_record', 'hooks/scheduled-run-record.py', 'ops/scheduled-run-record-selftest.py', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('session_boot', 'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js', 'command:python3 hooks/gate-integrity.py --selftest', 'stop_gate', false, null)
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('settings_change', 'hooks/settings-change-gate.py', 'ops/settings-change-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('shared_rename_absence', 'mcp-server/src/tools.js', 'mcp-server/test/shared-row-rename-registry.test.mjs', 'schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('staged_gate_bless', 'ops/githooks/staged-gate-bless-check.py; ops/githooks/pre-commit', 'ops/gate-baseline-cochange-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('stale_claim', 'hooks/stale-claim-gate.py', 'ops/stale-claim-gate-selftest.py', 'stop_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('stale_count', 'ops/stale-count-check.py', 'ops/stale-count-selftest.py', 'schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('trigger_grant', 'ops/trigger-grant-check.py', 'ops/trigger-grant-selftest.py', 'schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('vault_salvage', 'ops/vault-drift-watch.py', 'ops/vault-drift-watch-selftest.py', 'judgment_ambient', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('vendor_level_drift', 'ops/vendor-level-drift-check.py; bin/nightly.sh', 'ops/vendor-level-drift-selftest.py', 'surfacing', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('verification_status', 'migrations/0104_reverify_queue_supersession.sql; tools/control-plane.py; lib/control_plane_inputs.py', 'ops/control-plane-inputs-selftest.py; ops/control-plane-db-gate.py', 'surfacing', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('versioned_record', 'path:mcp-server/src/tools.js; external:database constraints', 'glob:mcp-server/test/*.test.js; external:migration probes', 'transactional_schema', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('weekend_quiet', 'hooks/weekend-quiet-gate.py', 'ops/weekend-quiet-gate-selftest.py', 'deny_gate', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('weekly_social_route', 'lib/control_plane_runner.py; tools/social-batch-candidates.py', 'ops/control-plane-runner-selftest.py; ops/client-asset-controls-selftest.py', 'judgment_ambient', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('whose_work', 'bin/whose-work.py; tools/health-check.py', 'ops/whose-work-selftest.py', 'surfacing', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

insert into ops.enforcement_control_catalog
    (control_key, implementation_ref, test_ref, enforcement_class, installed, verified_at)
values ('writing_lint', 'tools/writing-lint.py; hooks/lint-gate.py; hooks/chat-lint-gate.py', 'ops/chat-lint-gate-selftest.py; baselines/writing-lint.txt', 'surfacing', true, now())
on conflict (control_key) do update set
    implementation_ref = excluded.implementation_ref,
    test_ref           = excluded.test_ref,
    enforcement_class  = excluded.enforcement_class,
    installed          = excluded.installed,
    verified_at        = excluded.verified_at,
    updated_at         = now();

-- Proof, inside the transaction.
do $$
declare n_total int; n_installed int;
begin
  select count(*), count(*) filter (where installed) into n_total, n_installed
    from ops.enforcement_control_catalog;
  if n_total < 61 then
    raise exception 'control catalog holds % rows, expected at least 61', n_total;
  end if;
  if n_installed < 54 then
    raise exception 'control catalog has % installed, expected at least 54', n_installed;
  end if;
end $$;

commit;

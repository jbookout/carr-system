import { TOOLS, registerTools } from "./tool-registry.js";
import { invoiceTrackerTools } from "./invoice-tracker.js";
import { ToolError } from "./tool-error.js";
import { withEnvelope, writeEvent } from "./versioned-write.js";
import { doctrineTools } from "./doctrine.js";
import { systemWorkTools } from "./system-work-census.v5.js";
import { boardAnswerTools } from "./board-answers.js";
import { scheduleBoardTools } from "./schedule-board.js";
import { situationRetrievalTools } from "./situation-retrieval.js";
import { investigationTools } from "./investigation.js";
import { docConversationTools } from "./doc-conversation.js";
import { docSuggestionTools } from "./doc-suggestions.js";
import { docActivityTools } from "./doc-activity.js";
import { whatsNewTools } from "./whats-new.js";
import { assertNoCallerAuthorityFields, executeRegisteredTool } from "./tool-execution.js";
import { meetingModeTools } from "./meeting-mode.js";
import { notificationTools } from "./notifications.js";
import { deliveryCadenceA05Tools } from "./delivery-cadence-a05-tools.js";
import { sessionIdentityTools } from "./session-identity.js";
import { dispatchSpineTools } from "./dispatch-spine.js";
import { capabilityProgramTools } from "./capability-program.js";
import { workShapeTools } from "./work-shape.js";
import { workRequestIntakeTools } from "./work-request-intake.js";
import { workPortfolioTools } from "./work-portfolio.js";
import { leaseTermComparisonTools } from "./lease-term-comparison.js";
import { partnerRoomTools } from "./partner-room.js";
import { agentProfileTools } from "./agent-profiles.js";
import { botBriefTools } from "./bot-brief.js";
import { evidenceActivationTools } from "./evidence-activation.js";
import { resourceObservationTools } from "./resource-observation.v5.js";
import { invoiceAutomation } from "./invoice-automation.js";
import { lockDealField } from "./verb-support.js";
import { leadAutomationTools } from "./lead-automation.js";
import { jevCallReceiptTools } from "./jev-call-receipt.js";
import { workflowCutoverTools } from "./workflow-cutover.v5.js";
import { memoryTools } from "./memory.js";
import { codexContinuityTools } from "./codex-continuity.js";
import { claudeContinuityTools } from "./claude-continuity.js";
import { incidentTools } from "./incident.js";
import { authenticatedIdentity, authorizationClassForActor } from "./identity.js";
import { engineeringRuntimeTools } from "./engineering-runtime.js";
import { tourRightsProjectionTools } from "./tour-rights-projection.js";
import { tourPropertyJurisdictionTools } from "./tour-property-jurisdiction.js";
import { tourDomainTools } from "./tour-domain.js";
import { tourPropertySearchTools } from "./tour-property-search.js";
import { tourMapPromotionTools } from "./tour-map-promotion.js";
import { tourSharingTools } from "./tour-sharing.js";
import { tourArtifactTools } from "./tour-artifacts.js";
import { benchmarkAcceptanceStoreTools } from "./benchmark-acceptance-store.v5.js";
import { modelRoleStoreTools } from "./model-role-store.v5.js";
import { creLifecycleStoreTools } from "./cre-lifecycle-store.v5.js";
import { salesforceReconciliationStoreTools } from "./salesforce-reconciliation-store-rw02.v5.js";
import { salesforceReadRunStoreTools } from "./salesforce-read-run-store-rw02.v5.js";
import { recordSourceAuthorityStoreTools } from "./record-source-authority-store.v5.js";
import { foundationAssuranceMinimumTools } from "./foundation-assurance-minimum-producer.v5.js";
import { globalBoundariesDoorTools } from "./global-boundaries-door.v5.js";
import { journeyOneClockDoorTools } from "./journey-one-clock-door.v5.js";
import { governedCorrespondenceStoreTools } from "./governed-correspondence-store.v5.js";
import { assuranceHealthStoreTools } from "./assurance-health-store.v5.js";
import { actionClassSuccessorRegistryTools } from "./action-class-successor-registry.v5.js";
import { completeSetReviewA03StoreTools } from "./independent-review-cycle-store.v5.js";
import { ruleContextRuntimeTools } from "./rule-context-runtime.v5.js";
import { searchTools } from "./search-tools.js";
import { workspaceTools } from "./workspace-tools.js";
import { activityTools } from "./activity-tools.js";
import { dealTools } from "./deal-tools.js";
import { partyTools } from "./party-tools.js";
import { leadTools } from "./lead-tools.js";
import { documentTools } from "./document-tools.js";
import { ruleTools } from "./rule-tools.js";
import { decisionTools } from "./decision-tools.js";
import { loopTools } from "./loop-tools.js";
import { campaignTools } from "./campaign-tools.js";
import { gateZeroTools } from "./gate-zero-tools.js";
import { industryEventTools } from "./industry-event-tools.js";
import { dealRoomTools } from "./deal-room-tools.js";
import { introspectionTools } from "./introspection-tools.js";
import { docOutcomeCardsTools } from "./doc-outcome-cards-tools.js";
export { TOOLS } from "./tool-registry.js";
export { auditIdentity, compareVersion, disjointFromIntervening } from "./versioned-write.js";

// Compose declarations once; runtime dispatch and write machinery are leaf modules.

registerTools(searchTools(), "mcp-server/src/search-tools.js");
registerTools(workspaceTools(), "mcp-server/src/workspace-tools.js");
registerTools(activityTools(), "mcp-server/src/activity-tools.js");
registerTools(dealTools(), "mcp-server/src/deal-tools.js");
registerTools(partyTools(), "mcp-server/src/party-tools.js");
registerTools(leadTools(), "mcp-server/src/lead-tools.js");
registerTools(documentTools(), "mcp-server/src/document-tools.js");
registerTools(ruleTools(), "mcp-server/src/rule-tools.js");
registerTools(decisionTools(), "mcp-server/src/decision-tools.js");
registerTools(loopTools(), "mcp-server/src/loop-tools.js");
registerTools(campaignTools(), "mcp-server/src/campaign-tools.js");
registerTools(gateZeroTools(), "mcp-server/src/gate-zero-tools.js");
registerTools(industryEventTools(), "mcp-server/src/industry-event-tools.js");

registerTools(invoiceTrackerTools({ ToolError, withEnvelope, writeEvent }), "mcp-server/src/invoice-tracker.js");registerTools(dealRoomTools(), "mcp-server/src/deal-room-tools.js");registerTools(introspectionTools(), "mcp-server/src/introspection-tools.js");

// Doctrine store verbs (P2, decision 82a2fb62) — same envelope, same contracts.
registerTools(doctrineTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/doctrine.js");
registerTools(systemWorkTools(), "mcp-server/src/system-work-census.v5.js");
registerTools(boardAnswerTools({ withEnvelope, writeEvent }), "mcp-server/src/board-answers.js");
registerTools(scheduleBoardTools(), "mcp-server/src/schedule-board.js");

// WR-AI-006: curation proposals are machine-callable; approval and retirement
// remain human-only inside their handlers and the dispatcher boundary.
registerTools(situationRetrievalTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/situation-retrieval.js");

// Bounded investigation control plane (0098): deterministic signals, one
// reasoning owner, evidence-only worker packets, explicit branch termination.
registerTools(investigationTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/investigation.js");

// WR-000112: the Doc conversation store. The append is authority-only because
// ops.append_doc_conversation_turn is granted to carr_authority alone; the read
// runs on the writer connection because that is the only one that installs the
// acting-actor context ops.doc_conversation_facts is handed.
registerTools(docConversationTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/doc-conversation.js");
registerTools(docSuggestionTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/doc-suggestions.js");
registerTools(docActivityTools({ ToolError }), "mcp-server/src/doc-activity.js");
registerTools(whatsNewTools({ withEnvelope, executeRegisteredTool, ToolError }), "mcp-server/src/whats-new.js");

// V5-UX-B11: non-recording shared Meeting Mode. The store's definer functions
// own every transition; an accepted action points at an existing write verb in
// this registry, looked up at call time, and never runs it in-process.
registerTools(meetingModeTools({ withEnvelope, writeEvent, ToolError,
  lookupTool: name => (Object.hasOwn(TOOLS, name) ? TOOLS[name] : null) }), "mcp-server/src/meeting-mode.js");

// WR-000113: the R03 notification feed and its acknowledgement receipt.
// acknowledge-notification writes ops.notification_read and nothing else, which
// is why a session may never report it as having moved a task.
registerTools(notificationTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/notifications.js");
registerTools(deliveryCadenceA05Tools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/delivery-cadence-a05-tools.js");

// WR-000117: the session-identity read pair. Both verbs are READS on the writer
// connection -- ops.session_identity_facts and ops.session_dispatch_history
// derive the acting actor from a context only the writer path installs -- and
// neither writes a row anywhere, so neither takes the envelope or the event
// helper.
registerTools(sessionIdentityTools({ ToolError }), "mcp-server/src/session-identity.js");

// WR-000119: the dispatch spine write pair. The OPPOSITE declaration to the
// read pair above -- both of these carry write: true as well as the writer
// connection, because ops.record_dispatch_link and ops.acknowledge_dispatch
// insert and 0531 makes them volatile, so a read-only transaction would fail
// them. Both take the envelope and the event helper for that reason.
registerTools(dispatchSpineTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/dispatch-spine.js");registerTools(docOutcomeCardsTools(), "mcp-server/src/doc-outcome-cards-tools.js");

// One fixed ordered AI-capability portfolio over canonical Work Requests.
registerTools(capabilityProgramTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/capability-program.js");

// Evidence-backed implementation form, linked to canonical Work Requests.
registerTools(workShapeTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/work-shape.js");

// Program 6: sourced additive capture and a safe card only. No lifecycle verbs.
registerTools(workRequestIntakeTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/work-request-intake.js");
registerTools(workPortfolioTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/work-portfolio.js");

// Pure workbook-derived lease economics. No database, model, or write path.
registerTools(leaseTermComparisonTools({ ToolError }), "mcp-server/src/lease-term-comparison.js");

// The partner room (Idea 78): shared AI-to-AI transcript both Macs poll; raw
// turns, server-derived attribution, human-watchable. See src/partner-room.js.
registerTools(partnerRoomTools({ withEnvelope, ToolError }), "mcp-server/src/partner-room.js");
registerTools(agentProfileTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/agent-profiles.js");
registerTools(botBriefTools({ ToolError, assertNoCallerAuthorityFields }), "mcp-server/src/bot-brief.js");
registerTools(evidenceActivationTools({ withEnvelope, ToolError }), "mcp-server/src/evidence-activation.js");
// DoctorCRE v5 V5-UX-C02/C06: the resource-metering read contract and the
// one write door the local, credential-less collector uses. See
// src/resource-observation.v5.js.
registerTools(resourceObservationTools({ withEnvelope, ToolError }), "mcp-server/src/resource-observation.v5.js");
// Server-side Jev call log: the Worker calls TypeSafe itself and appends a
// server-timestamped receipt (migration 0587) before returning the answers, so
// Jev gates credit only rows the gated model could not forge locally. See
// src/jev-call-receipt.js.
const invoices=invoiceAutomation({withEnvelope,writeEvent,ToolError,lockDealField,
  updateDeal:(c,actor,args)=>TOOLS["update-deal"].handler(c,actor,args),
  invoicingMailbox:process.env.CARR_INVOICING_MAILBOX});
registerTools(invoices.tools,"mcp-server/src/invoice-automation.js");
registerTools(leadAutomationTools({ withEnvelope, writeEvent, ToolError, invoices }), "mcp-server/src/lead-automation.js");
registerTools(jevCallReceiptTools({ withEnvelope, ToolError }), "mcp-server/src/jev-call-receipt.js");
// DoctorCRE V5-R02: workflow cutover, caller migration and retirement
// readiness. Composes accept-workflow / disable-legacy-schedule rather than
// duplicating their evidence; retire-workflow-cutover-plan is authority-only.
// See src/workflow-cutover.v5.js.
registerTools(workflowCutoverTools({ withEnvelope, ToolError }), "mcp-server/src/workflow-cutover.v5.js");
// Phase 1 CARR-native learning memory: evidence-backed context with explicit
// candidate/promotion/correction/forgetting lifecycle. Memory never grants
// authority; actor and sponsor scope are resolved by the server.
registerTools(memoryTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }), "mcp-server/src/memory.js");
// Native Codex continuity is a separate, bounded surface.  It stores semantic
// checkpoint revisions and lifecycle receipts; transcript bodies stay local to
// the Codex adapter and Claude never reaches these verbs through its config.
registerTools(codexContinuityTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }), "mcp-server/src/codex-continuity.js");
registerTools(claudeContinuityTools({ withEnvelope, writeEvent, ToolError, assertNoCallerAuthorityFields }), "mcp-server/src/claude-continuity.js");

// The operational incident ledger gets a front door (2026-08-23 rules-and-verbs
// council, item 1 from both chairs). ops.incident has been written by two
// collectors since 0115 and read by none: seeing it meant tools/ops-record.py on
// a partner's Mac, and closing one meant the break-glass database tap. Five
// verbs — board, card, open, close, adjudicate — with the close and the
// adjudication carrying partner authority, because 0117 already wrote that
// boundary into the grants and the verb surface should not be laxer than the
// grants are. See migrations/0286 for the permission half.
registerTools(incidentTools({ withEnvelope, writeEvent, ToolError, authorizationClassForActor }), "mcp-server/src/incident.js");

// Engineering Passport runtime: typed plan registration, server-derived
// admission, and read-only closure projection over the canonical job ledger.
registerTools(engineeringRuntimeTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/engineering-runtime.js");

// Tour Operations Slice 2: bounded rights, evidence, assertion, and immutable
// public-projection seams. Sealing is authority-only; publication is absent.
registerTools(tourRightsProjectionTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/tour-rights-projection.js");

// Tour Operations Slice 3: narrow, rights-bound identity assertions,
// coordinate candidates, and human entrance-verification receipts. No map,
// route, publication, or promotion seam is exposed here.
registerTools(tourPropertyJurisdictionTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/tour-property-jurisdiction.js");

// Tour Operations Slice 4: immutable Tour route versions and internal-only
// cheat-sheet revisions. Route acceptance is authority-only; publication is absent.
registerTools(tourDomainTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/tour-domain.js");

// Tour Operations delivery surfaces: governed property search and cart,
// confidential sharing, and deterministic PDF request/review records.
registerTools(tourPropertySearchTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/tour-property-search.js");
registerTools(tourMapPromotionTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/tour-map-promotion.js");
registerTools(tourSharingTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/tour-sharing.js");
registerTools(tourArtifactTools({ withEnvelope, writeEvent, ToolError }), "mcp-server/src/tour-artifacts.js");
registerTools(benchmarkAcceptanceStoreTools({
  withEnvelope, writeEvent, ToolError, authenticatedIdentity,
}),
  "mcp-server/src/benchmark-acceptance-store.v5.js");
registerTools(modelRoleStoreTools({ withEnvelope, writeEvent, ToolError }),
  "mcp-server/src/model-role-store.v5.js");
registerTools(creLifecycleStoreTools({ withEnvelope, ToolError }), "mcp-server/src/cre-lifecycle-store.v5.js");
registerTools(salesforceReconciliationStoreTools({ withEnvelope, ToolError }),
  "mcp-server/src/salesforce-reconciliation-store-rw02.v5.js");
registerTools(salesforceReadRunStoreTools({ withEnvelope, ToolError }), "mcp-server/src/salesforce-read-run-store-rw02.v5.js");
registerTools(recordSourceAuthorityStoreTools({ withEnvelope, ToolError }),
  "mcp-server/src/record-source-authority-store.v5.js");
registerTools(foundationAssuranceMinimumTools({
  withEnvelope, ToolError, authenticatedIdentity,
}), "mcp-server/src/foundation-assurance-minimum-producer.v5.js");
// V5-S01: read-only projection of the settled global boundaries and the
// dispatch door's mode and shadow counters. No database access.
registerTools(globalBoundariesDoorTools({ ToolError }), "mcp-server/src/global-boundaries-door.v5.js");
// DoctorCRE V5-M01: the live door to the Journey 1 clock runtime. The read verb
// derives clock_started from the record; the advance verb takes only an
// idempotency key and is registered WITHOUT an installation resolver, so it
// refuses journey_one_clock_installation_unavailable before any query here. No
// Worker can start, advance or pause the Journey 1 clock through it until a
// verifier for the composed projection is installed by trusted server code.
registerTools(journeyOneClockDoorTools({ withEnvelope, ToolError }), "mcp-server/src/journey-one-clock-door.v5.js");
// DoctorCRE V5-J103: the governed correspondence store. Two reads
// (correspondence-readiness, read-correspondence-thread) and the humanOnly
// consent pair, each partner only for their own carr.us mailbox. There is no
// send verb and no draft verb: a draft can never dispatch, and Joe sends. Reads
// answer unavailable until the F10 local-store adapter lands with a reviewed
// grant for the read-receipt writer.
registerTools(governedCorrespondenceStoreTools({ withEnvelope, writeEvent, ToolError }),
  "mcp-server/src/governed-correspondence-store.v5.js");
registerTools(assuranceHealthStoreTools({ withEnvelope, ToolError }), "mcp-server/src/assurance-health-store.v5.js");
// DoctorCRE V5-D01: inactive action-specific autonomy successors. Three verbs
// over migration 0708's append-only registry -- register-, read- and the
// deterministic read-action-class-gate, which as shipped always denies (no
// activation door exists). Registration grants no authority and no verb here
// can ever produce a row this gate reads as allowed.
registerTools(actionClassSuccessorRegistryTools({ withEnvelope, writeEvent, ToolError }),
  "mcp-server/src/action-class-successor-registry.v5.js");
// DoctorCRE V5-A03: append-only independent complete-set review. Every
// participant registers only its authenticated actor/session duty; all eleven
// dimensions precede one batch repair; regression checks cannot shrink; and a
// stronger adjudicator is the only transition after two unresolved rounds.
registerTools(completeSetReviewA03StoreTools({ withEnvelope, writeEvent, ToolError }),
  "mcp-server/src/independent-review-cycle-store.v5.js");
// DoctorCRE V5-F05: authenticated, actor-scoped rule-universe read plus the
// Joe-authority typed-contract binder. The read uses the writer connection only
// to receive server-established actor/sponsor transaction settings; its tool
// contract remains read-only and the SQL function is stable.
registerTools(ruleContextRuntimeTools({ withEnvelope, ToolError }), "mcp-server/src/rule-context-runtime.v5.js");

Object.freeze(TOOLS);

export { describeConstraint } from "./verb-support.js";
export { pgConstraintError } from "./tool-execution.js";
export { looksLikeToolCallMarkup } from "./tool-execution.js";
export { assertRequiredArgs } from "./tool-execution.js";
export { coerceArgsToSchema } from "./tool-execution.js";
export { linkedInActivityPublishedAt } from "./campaign-tools.js";
export { assertNoCallerAuthorityFields } from "./tool-execution.js";
export { assertRegisteredToolInput } from "./tool-execution.js";
export { executeRegisteredTool } from "./tool-execution.js";
export { docOutcomeCardsProjection } from "./doc-outcome-cards-tools.js";
export { ToolError } from "./tool-error.js";
export { canExercisePartnerAuthority, partnerAuthoritySlugForActor } from "./partner-authority.js";

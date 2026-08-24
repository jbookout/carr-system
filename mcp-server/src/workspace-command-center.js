import { organizationTenantForActor } from "./identity.js";

export const COMMAND_CENTER_PATH = "/api/v1/command-center";
export const DEAL_ATTENTION_DESTINATION = "/deals?workspace=team&filter=flagged&owner=me";
const VALID_ACTORS = new Set(["joe", "dell"]);
const DEPENDENCY_CODES = new Set(["DEPENDENCY_UNAVAILABLE", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND", "08000", "57P01"]);

function typedError(code) {
  const error = new Error(code);
  error.code = code;
  return error;
}

function integer(row, key) {
  const value = Number(row?.[key]);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function source({ source, sourceRef, observedAt, validUntil, correlationId, freshness = "fresh" }) {
  return {
    source, source_ref: sourceRef, observed_at: observedAt, valid_until: validUntil,
    freshness, correlation_id: correlationId,
    safe_explanation: freshness === "fresh"
      ? "Fresh because this is a no-store request-time canonical database aggregate; valid for 60 seconds."
      : "This source is unavailable because legacy unscoped rows prevent a safe tenant-bound aggregate.",
  };
}

function classifyReadError(error) {
  if (error?.code === "DEPENDENCY_UNAVAILABLE") return typedError("DEPENDENCY_UNAVAILABLE");
  if (DEPENDENCY_CODES.has(error?.code)) return typedError("DEPENDENCY_UNAVAILABLE");
  if (error?.code === "INTERNAL_ERROR") return error;
  return typedError("INTERNAL_ERROR");
}

/** Aggregate-only, server-scoped Command Center read. */
export async function readCommandCenterSummary({ client, actor, tenant = organizationTenantForActor(actor), correlationId, now = () => new Date() }) {
  if (!correlationId || typeof correlationId !== "string") throw typedError("INTERNAL_ERROR");
  const boundTenant = organizationTenantForActor(actor);
  if (tenant !== boundTenant || tenant !== "carr-internal") throw typedError("TENANT_SCOPE_REFUSED");
  if (!actor?.slug || !VALID_ACTORS.has(actor.slug)) throw typedError("AUTHORIZATION_REFUSED");
  const observedAtDate = now();
  const observedAt = observedAtDate.toISOString();
  const validUntil = new Date(observedAtDate.valueOf() + 60_000).toISOString();
  let dealResult;
  let workResult;
  try {
    [dealResult, workResult] = await Promise.all([
      client.query(`select
          count(*) filter (where owner = $1::text and workspace_kind = 'team' and coalesce(operating_state, 'active') = 'active') as owned_active,
          count(*) filter (where owner = $1::text and workspace_kind = 'team' and coalesce(operating_state, 'active') = 'active' and attention = true) as owned_flagged
        from v_deal_room_board`, [actor.slug]),
      client.query(`select
          count(*) filter (where organization_tenant_id = $1::text and state = $2::text) as needs_viewer,
          count(*) filter (where organization_tenant_id = $1::text and state in ('claimed', 'in_progress', 'verification', 'awaiting_release', 'released') and executor_actor is not null and executor_actor not in ('joe', 'dell')) as doc_at_work,
          count(*) filter (where organization_tenant_id = $1::text and updated_at >= now() - interval '7 days') as changed_count,
          max(updated_at) filter (where organization_tenant_id = $1::text and updated_at >= now() - interval '7 days') as changed_at,
          count(*) filter (where organization_tenant_id is null and state in ('needs_joe', 'claimed', 'in_progress', 'verification', 'awaiting_release', 'released')) as legacy_unscoped_held,
          count(*) filter (where organization_tenant_id is null and updated_at >= now() - interval '7 days') as legacy_unscoped_recent
        from ops.work_request`, [tenant, actor.slug === "joe" ? "needs_joe" : "__no_needs_joe_state__"]),
    ]);
  } catch (error) {
    throw classifyReadError(error);
  }
  const deal = dealResult?.rows?.[0];
  const work = workResult?.rows?.[0];
  const active = integer(deal, "owned_active");
  const flagged = integer(deal, "owned_flagged");
  const needsViewer = integer(work, "needs_viewer");
  const docAtWork = integer(work, "doc_at_work");
  const changedCount = integer(work, "changed_count");
  const legacyHeld = integer(work, "legacy_unscoped_held");
  const legacyRecent = integer(work, "legacy_unscoped_recent");
  if ([active, flagged].some((value) => value === null) || flagged > active) throw typedError("FRESHNESS_UNKNOWN");
  const dealSource = source({ source: "v_deal_room_board", sourceRef: "v_deal_room_board", observedAt, validUntil, correlationId });
  const workSafe = [needsViewer, docAtWork, changedCount, legacyHeld, legacyRecent].every((value) => value !== null) && legacyHeld === 0 && legacyRecent === 0;
  const workSource = source({ source: "ops.work_request", sourceRef: "ops.work_request", observedAt, validUntil, correlationId, freshness: workSafe ? "fresh" : "unknown" });
  const needs = [{ kind: "owned_flagged_deals", count: flagged, destination: DEAL_ATTENTION_DESTINATION }];
  if (workSafe && actor.slug === "joe" && needsViewer > 0) needs.push({ kind: "needs_joe_work", count: needsViewer, destination: "/system-work.html" });
  const workUnavailable = { state: "unavailable" };
  return {
    viewer: actor.slug,
    needs_you_now: needs,
    this_week: [],
    metrics: [{ owned_active_deals: active, owned_flagged_deals: flagged, source: dealSource }],
    recent_calls: [],
    doc_at_work: workSafe ? [{ kind: "active_nonhuman_work", count: docAtWork, source: workSource }] : [workUnavailable],
    recent_activity: workSafe ? [{ kind: "changed_work", count: changedCount, observed_at: work.changed_at || observedAt, source: workSource }] : [workUnavailable],
    source: source({ source: "command_center", sourceRef: "v_deal_room_board+ops.work_request", observedAt, validUntil, correlationId }),
  };
}

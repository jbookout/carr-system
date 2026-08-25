export const FALLBACK_DESTINATION = "/deals?workspace=team&filter=flagged&owner=me";
const VIEWERS = new Set(["joe", "dell"]);
const NEED_KINDS = new Set(["owned_flagged_deals", "needs_joe_work"]);
const NEED_DESTINATIONS = new Set([FALLBACK_DESTINATION, "/system-work.html"]);
const CARD_SOURCES = new Set(["v_deal_room_board", "ops.work_request"]);
const ALL_SOURCES = new Set(["command_center", ...CARD_SOURCES]);

export function safeDestination(value) {
  return value === FALLBACK_DESTINATION ? value : FALLBACK_DESTINATION;
}

function iso(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function sourceValid(source, allowed = CARD_SOURCES) {
  const sourceKeys = new Set(["source", "source_ref", "observed_at", "valid_until", "freshness", "correlation_id", "safe_explanation"]);
  return source && Object.keys(source).every((key) => sourceKeys.has(key)) && allowed.has(source.source) && ["fresh", "stale", "missing", "unknown"].includes(source.freshness) &&
    iso(source.observed_at) && iso(source.valid_until) && Date.parse(source.valid_until) > Date.parse(source.observed_at) &&
    typeof source.correlation_id === "string" && source.correlation_id.length > 0;
}

function cardSourceValid(source) {
  return sourceValid(source) && source.freshness === "fresh";
}

function count(value) {
  return Number.isInteger(value) && value >= 0;
}

function exactKeys(value, keys) {
  return value && Object.keys(value).sort().join(",") === keys.slice().sort().join(",");
}

export function sourceIsFresh(source, now = () => Date.now()) {
  return sourceValid(source, ALL_SOURCES) && source.freshness === "fresh" && Date.parse(source.valid_until) > now();
}

export function validWorkspacePayload(payload) {
  if (!payload || Object.keys(payload).sort().join(",") !== "doc_at_work,metrics,needs_you_now,recent_activity,recent_calls,source,this_week,viewer") return false;
  if (!VIEWERS.has(payload.viewer) || !sourceValid(payload.source, new Set(["command_center"]))) return false;
  if (!iso(payload.source.valid_until) || Date.parse(payload.source.valid_until) <= Date.parse(payload.source.observed_at)) return false;
  if (!Array.isArray(payload.needs_you_now) || !payload.needs_you_now.every((item) => exactKeys(item, ["kind", "count", "destination"]) && NEED_KINDS.has(item.kind) && count(item.count) && NEED_DESTINATIONS.has(item.destination))) return false;
  if (!Array.isArray(payload.this_week) || !Array.isArray(payload.recent_calls)) return false;
  if (!Array.isArray(payload.metrics) || payload.metrics.length !== 1) return false;
  const metric = payload.metrics[0];
  if (!exactKeys(metric, ["owned_active_deals", "owned_flagged_deals", "source"]) || !count(metric.owned_active_deals) || !count(metric.owned_flagged_deals) || metric.owned_flagged_deals > metric.owned_active_deals || !cardSourceValid(metric.source)) return false;
  if (!Array.isArray(payload.doc_at_work) || payload.doc_at_work.length !== 1 || !payload.doc_at_work.every((item) => (exactKeys(item, ["state"]) && item.state === "unavailable") || (exactKeys(item, ["kind", "count", "source"]) && item.kind === "active_nonhuman_work" && count(item.count) && cardSourceValid(item.source)))) return false;
  if (!Array.isArray(payload.recent_activity) || payload.recent_activity.length !== 1 || !payload.recent_activity.every((item) => (exactKeys(item, ["state"]) && item.state === "unavailable") || (exactKeys(item, ["kind", "count", "observed_at", "source"]) && item.kind === "changed_work" && count(item.count) && iso(item.observed_at) && cardSourceValid(item.source)))) return false;
  return true;
}

export function summarizeWorkspacePayload(payload, now = () => Date.now()) {
  if (!validWorkspacePayload(payload)) return { state: "unavailable", count: null, active: null };
  const metric = payload.metrics[0];
  if (!sourceIsFresh(payload.source, now) || !sourceIsFresh(metric.source, now)) {
    return { state: "stale", count: null, active: null, destination: FALLBACK_DESTINATION };
  }
  return { state: metric.owned_flagged_deals === 0 ? "empty" : "attention", count: metric.owned_flagged_deals, active: metric.owned_active_deals, destination: FALLBACK_DESTINATION };
}

export function aggregateCardState(payload, now = () => Date.now()) {
  if (!validWorkspacePayload(payload)) return { needs: "unavailable", doc: "unavailable", recent: "unavailable" };
  if (!sourceIsFresh(payload.source, now) || !sourceIsFresh(payload.metrics[0].source, now)) return { needs: "stale", doc: "stale", recent: "stale" };
  const doc = payload.doc_at_work[0];
  const recent = payload.recent_activity[0];
  return {
    needs: "fresh",
    doc: doc.state === "unavailable" ? "unavailable" : sourceIsFresh(doc.source, now) ? "fresh" : "unavailable",
    recent: recent.state === "unavailable" ? "unavailable" : sourceIsFresh(recent.source, now) ? "fresh" : "unavailable",
  };
}

export function viewerWorkspaceLabel(viewer) {
  return viewer === "joe" ? "Joe’s workspace" : viewer === "dell" ? "Dell’s workspace" : "Partner workspace";
}

export function humanSourceLabel(source) {
  return ({
    command_center: "Command Center read",
    v_deal_room_board: "Deal Room board",
    "ops.work_request": "System work",
  })[source] || "Source unavailable";
}

export function primaryHomeAction(payload, { now = () => Date.now(), unauthorized = false } = {}) {
  if (unauthorized) return { label: "Sign in", href: "/auth/login?return_to=%2F", state: "unauthorized" };
  const summary = summarizeWorkspacePayload(payload, now);
  if (summary.state === "attention") return { label: "Review flagged deals", href: FALLBACK_DESTINATION, state: "attention" };
  if (summary.state === "empty") return { label: "Open Lead Board", href: "/leads", state: "empty" };
  return { label: "Open Deal Room", href: "/deals", state: summary.state === "stale" ? "stale" : "unavailable" };
}

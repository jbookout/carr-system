const ENDPOINT = "/api/v1/command-center";
import { sourceIsFresh, summarizeWorkspacePayload, validWorkspacePayload } from "./workspace-command-center-model.js";

const card = document.querySelector("#dealAttention");
const observedAt = document.querySelector("#observedAt");
const healthOrb = document.querySelector("#healthOrb");
const healthLabel = document.querySelector("#healthLabel");
const aggregateCards = {
  needs: document.querySelector("#needsYouNow"),
  doc: document.querySelector("#docAtWork"),
  activity: document.querySelector("#recentActivity"),
};

function setHealth(state) {
  if (healthOrb) healthOrb.className = `status-orb ${state}`;
  if (healthLabel) healthLabel.textContent = state === "available" ? "Workspace available" : state === "loading" ? "Checking workspace…" : "Workspace unavailable";
}

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));
function observedLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Observed time unavailable";
  const label = date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  if (observedAt) observedAt.textContent = `Observed ${label}`;
  return `Observed ${label}`;
}

function sourceLabel(source) {
  if (!source) return "Source unavailable · freshness unknown";
  return `Source: ${escapeHtml(source.source)} · freshness: ${escapeHtml(source.freshness)} · ${escapeHtml(observedLabel(source.observed_at))}`;
}

function renderAggregate(target, title, detail, source, unavailable = false) {
  if (!target) return;
  target.className = `aggregate-card glass${unavailable ? " unavailable" : ""}`;
  target.innerHTML = `<p class="eyebrow">${escapeHtml(title)}</p><h2>${escapeHtml(unavailable ? "Unavailable" : detail.value)}</h2><p class="aggregate-detail">${escapeHtml(unavailable ? "This source is withheld until its tenant scope and freshness can be verified." : detail.copy)}</p><p class="source">${sourceLabel(source)}</p>`;
}

function renderAggregates(payload) {
  const now = () => Date.now();
  if (!sourceIsFresh(payload.source, now) || !sourceIsFresh(payload.metrics[0].source, now)) {
    Object.values(aggregateCards).forEach((target) => renderAggregate(target, "Stale read", { value: "—", copy: "Counts withheld until freshness is verified." }, payload.source, true));
    return;
  }
  const needs = payload.needs_you_now.reduce((sum, item) => sum + item.count, 0);
  renderAggregate(aggregateCards.needs, "Needs you now", { value: String(needs), copy: needs ? "Aggregate items have an owning surface." : "Nothing is explicitly asking for your attention." }, payload.source);
  const doc = payload.doc_at_work[0];
  renderAggregate(aggregateCards.doc, "Doc at work", { value: doc?.count ?? "—", copy: doc?.count ? "Active nonhuman work is in progress." : "No active nonhuman work is reported." }, doc?.source, doc?.state === "unavailable" || !sourceIsFresh(doc?.source, now));
  const activity = payload.recent_activity[0];
  renderAggregate(aggregateCards.activity, "Changed in 7 days", { value: activity?.count ?? "—", copy: activity?.count ? `Last observed ${observedLabel(activity.observed_at)}.` : "No changed work is reported." }, activity?.source, activity?.state === "unavailable" || !sourceIsFresh(activity?.source, now));
}

function renderUnauthorized() {
  setHealth("unavailable");
  card.className = "attention-card glass unavailable";
  card.setAttribute("aria-busy", "false");
  card.innerHTML = `<div class="attention-icon" aria-hidden="true"><span></span></div><div class="attention-content"><p class="eyebrow">Private workspace</p><h2 id="attentionTitle">Your session has ended</h2><p class="attention-copy">Sign in again to read the current Command Center.</p><a class="action" href="/auth/login?return_to=%2Fworkspace">Sign in again</a></div>`;
  Object.values(aggregateCards).forEach((target) => renderAggregate(target, "Unavailable", { value: "—", copy: "Sign in to read this aggregate." }, null, true));
}

function renderUnavailable(message = "The canonical read is unavailable, so Workspace will not show a stale count as current.") {
  setHealth("unavailable");
  card.className = "attention-card glass unavailable";
  card.setAttribute("aria-busy", "false");
  card.innerHTML = `<div class="attention-icon" aria-hidden="true"><span></span></div><div class="attention-content"><p class="eyebrow">Team Book · unavailable</p><h2 id="attentionTitle">Progress could not be checked</h2><p class="attention-copy">${escapeHtml(message)}</p><button class="action" type="button" id="retryDealAttention">Retry canonical read</button></div>`;
  Object.values(aggregateCards).forEach((target) => renderAggregate(target, "Unavailable", { value: "—", copy: "The overall read is unavailable." }, null, true));
  document.querySelector("#retryDealAttention")?.addEventListener("click", load);
}

function renderSummary(payload) {
  const summary = summarizeWorkspacePayload(payload);
  const active = summary.active;
  const flagged = summary.count;
  const freshness = payload.source.freshness;
  const stale = summary.state === "stale";
  const empty = summary.state === "empty";
  const destination = summary.destination;
  const observed = observedLabel(payload.source.observed_at);
  const title = stale ? "Read needs verification" : empty ? "Team Book is clear" : `${flagged} flagged ${flagged === 1 ? "deal needs" : "deals need"} attention`;
  const copy = stale ? "This canonical read is older than its freshness window. Open the owning Deal Room view to verify before acting."
    : empty ? `You own ${active} active Team Book ${active === 1 ? "deal" : "deals"}; none are explicitly flagged.`
      : "These records were explicitly flagged for partner attention. Review the owning Deal Room view before deciding what changes.";
  card.className = `attention-card glass ${stale ? "unavailable" : empty ? "empty" : "ready"}`;
  card.setAttribute("aria-busy", "false");
  if (stale) {
    setHealth("unavailable");
    Object.values(aggregateCards).forEach((target) => renderAggregate(target, "Stale read", { value: "—", copy: "Counts withheld until freshness is verified." }, payload.source, true));
  } else {
    setHealth("available");
    renderAggregates(payload);
  }
  card.innerHTML = `<div class="attention-icon" aria-hidden="true"><span></span></div><div class="attention-content"><p class="eyebrow">Team Book · ${stale ? "stale read" : empty ? "no flagged work" : "attention"}</p><h2 id="attentionTitle">${escapeHtml(title)}</h2><p class="attention-copy">${escapeHtml(copy)}</p><div class="count-line"><strong>${stale ? "—" : flagged}</strong><span>${stale ? "count withheld" : `${active} active owned ${active === 1 ? "deal" : "deals"}`}</span></div><a class="action" href="${escapeHtml(destination)}">${stale || !empty ? "Review in Deal Room" : "Open your Team Book"}</a><p class="source">Source: ${escapeHtml(payload.source.source || "Command Center")} · ${escapeHtml(observed)} · freshness: ${escapeHtml(freshness)}</p></div>`;
}

async function load() {
  if (!card) return;
  setHealth("loading");
  card.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(ENDPOINT, { headers: { accept: "application/json" }, cache: "no-store" });
    if (response.status === 401 || response.status === 403) return renderUnauthorized();
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      if (failure.error === "AUTHENTICATION_REQUIRED" || failure.error === "AUTHORIZATION_REFUSED") return renderUnauthorized();
      return renderUnavailable(failure.error === "DEPENDENCY_UNAVAILABLE" ? "The canonical read is unavailable right now." : undefined);
    }
    const payload = await response.json();
    if (!validWorkspacePayload(payload)) return renderUnavailable("The canonical read returned an unexpected shape, so the count was withheld.");
    renderSummary(payload);
  } catch {
    renderUnavailable("The workspace could not reach the canonical read. Nothing here has been inferred.");
  }
}

load();

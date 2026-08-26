const ENDPOINT = "/api/v1/command-center";
import { humanSourceLabel, primaryHomeAction, sourceIsFresh, summarizeWorkspacePayload, validWorkspacePayload, viewerWorkspaceLabel } from "./workspace-command-center-model.js";

const card = document.querySelector("#dealAttention");
const observedAt = document.querySelector("#observedAt");
const healthOrb = document.querySelector("#healthOrb");
const healthLabel = document.querySelector("#healthLabel");
const viewerWorkspace = document.querySelector("#viewerWorkspace");
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
  return `Source: ${escapeHtml(humanSourceLabel(source.source))} · freshness: ${escapeHtml(source.freshness)} · ${escapeHtml(observedLabel(source.observed_at))}`;
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
  const flaggedNeed = payload.needs_you_now.find((item) => item.kind === "owned_flagged_deals" && item.count > 0);
  const workNeed = payload.needs_you_now.find((item) => item.kind === "needs_joe_work" && item.count > 0);
  if (aggregateCards.needs) aggregateCards.needs.href = flaggedNeed?.destination || workNeed?.destination || "/deals";
  renderAggregate(aggregateCards.needs, "Needs you now", { value: String(needs), copy: needs ? "Aggregate items have an owning surface." : "Nothing is explicitly asking for your attention." }, payload.source);
  const doc = payload.doc_at_work[0];
  renderAggregate(aggregateCards.doc, "Doc at work", { value: doc?.count ?? "—", copy: doc?.count ? "Active nonhuman work is in progress." : "No active nonhuman work is reported." }, doc?.source, doc?.state === "unavailable" || !sourceIsFresh(doc?.source, now));
  const activity = payload.recent_activity[0];
  renderAggregate(aggregateCards.activity, "Changed in 7 days", { value: activity?.count ?? "—", copy: activity?.count ? `Last observed ${observedLabel(activity.observed_at)}.` : "No changed work is reported." }, activity?.source, activity?.state === "unavailable" || !sourceIsFresh(activity?.source, now));
}

function renderHomeCard({ eyebrow, title, copy, action, retry = false, source = null, count = null, countLabel = "" }) {
  card.innerHTML = `<div class="attention-icon" aria-hidden="true"><span></span></div><div class="attention-content"><p class="eyebrow">${escapeHtml(eyebrow)}</p><h2 id="attentionTitle">${escapeHtml(title)}</h2><p class="attention-copy">${escapeHtml(copy)}</p>${count === null ? "" : `<div class="count-line"><strong>${escapeHtml(count)}</strong><span>${escapeHtml(countLabel)}</span></div>`}<div class="home-actions"><a id="homePrimaryAction" class="action primary-action" data-primary-action data-state="${escapeHtml(action.state)}" href="${escapeHtml(action.href)}">${escapeHtml(action.label)}</a>${retry ? '<button class="action secondary-action" type="button" id="retryHome">Retry</button>' : ""}</div>${source ? `<p class="source">${sourceLabel(source)}</p>` : ""}</div>`;
  document.querySelector("#retryHome")?.addEventListener("click", load);
}

function renderUnauthorized() {
  setHealth("unavailable");
  card.className = "attention-card glass unavailable";
  card.setAttribute("aria-busy", "false");
  renderHomeCard({ eyebrow: "Private workspace", title: "Your session has ended", copy: "Sign in again to return Home.", action: primaryHomeAction(null, { unauthorized: true }) });
  Object.values(aggregateCards).forEach((target) => renderAggregate(target, "Unavailable", { value: "—", copy: "Sign in to read this aggregate." }, null, true));
}

function renderUnavailable(message = "Home cannot verify the current read, so no stale count is shown as current.") {
  setHealth("unavailable");
  card.className = "attention-card glass unavailable";
  card.setAttribute("aria-busy", "false");
  renderHomeCard({ eyebrow: "Home read unavailable", title: "Progress could not be checked", copy: message, action: primaryHomeAction({}), retry: true });
  Object.values(aggregateCards).forEach((target) => renderAggregate(target, "Unavailable", { value: "—", copy: "The overall read is unavailable." }, null, true));
}

function renderSummary(payload) {
  const summary = summarizeWorkspacePayload(payload);
  const active = summary.active;
  const flagged = summary.count;
  const stale = summary.state === "stale";
  const empty = summary.state === "empty";
  const title = stale ? "Read needs verification" : empty ? "Team Book is clear" : `${flagged} flagged ${flagged === 1 ? "deal needs" : "deals need"} attention`;
  const copy = stale ? "This canonical read is older than its freshness window. Open the owning Deal Room view to verify before acting."
    : empty ? `You own ${active} active Team Book ${active === 1 ? "deal" : "deals"}; none are explicitly flagged.`
      : "These records were explicitly flagged for partner attention. Review the owning Deal Room view before deciding what changes.";
  card.className = `attention-card glass ${stale ? "unavailable" : empty ? "empty" : "ready"}`;
  card.setAttribute("aria-busy", "false");
  if (viewerWorkspace) viewerWorkspace.textContent = viewerWorkspaceLabel(payload.viewer);
  if (stale) {
    setHealth("unavailable");
    Object.values(aggregateCards).forEach((target) => renderAggregate(target, "Stale read", { value: "—", copy: "Counts withheld until freshness is verified." }, payload.source, true));
  } else {
    setHealth("available");
    renderAggregates(payload);
  }
  renderHomeCard({ eyebrow: `Team Book · ${stale ? "stale read" : empty ? "no flagged work" : "attention"}`, title, copy, action: primaryHomeAction(payload), retry: stale, source: payload.source, count: stale ? "—" : flagged, countLabel: stale ? "count withheld" : `${active} active owned ${active === 1 ? "deal" : "deals"}` });
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

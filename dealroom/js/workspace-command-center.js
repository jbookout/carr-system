const ENDPOINT = "/api/v1/workspace/command-center/deal-attention";
const DESTINATION = "/deals?workspace=team&filter=flagged&owner=me";
const card = document.querySelector("#dealAttention");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function renderUnavailable() {
  card.className = "attention-card unavailable";
  card.setAttribute("aria-busy", "false");
  card.innerHTML = `<div class="count" aria-hidden="true">—</div><div><p class="eyebrow">Team Book · unavailable</p>
    <h2 id="dealAttentionTitle">Flagged deals could not be checked</h2>
    <p>The canonical read is unavailable, so Workspace will not show a stale count as current.</p>
    <button type="button" id="retryDealAttention">Retry</button></div>`;
  document.querySelector("#retryDealAttention").addEventListener("click", load);
}

function renderUnauthorized() {
  card.className = "attention-card unavailable";
  card.setAttribute("aria-busy", "false");
  card.innerHTML = `<div class="count" aria-hidden="true">—</div><div><p class="eyebrow">Private workspace</p>
    <h2 id="dealAttentionTitle">Your session has ended</h2><p>Sign in again to read your Command Center.</p>
    <a class="action" href="/auth/login?return_to=/">Sign in again</a></div>`;
}

function renderSummary(payload) {
  const flagged = Number(payload.summary?.owned_flagged || 0);
  const active = Number(payload.summary?.owned_active || 0);
  const empty = flagged === 0;
  const observed = new Date(payload.observed_at);
  const observedLabel = Number.isNaN(observed.valueOf()) ? "read time unavailable" : `read ${observed.toLocaleString()}`;
  card.className = `attention-card ${empty ? "empty" : "ready"}`;
  card.setAttribute("aria-busy", "false");
  card.innerHTML = `<div class="count" aria-label="${flagged} flagged deals">${flagged}</div><div>
    <p class="eyebrow">Team Book · ${empty ? "clear" : "attention"}</p>
    <h2 id="dealAttentionTitle">${empty ? "No flagged deals need your attention" : `${flagged} flagged ${flagged === 1 ? "deal needs" : "deals need"} your attention`}</h2>
    <p>${empty ? `You own ${active} active Team Book ${active === 1 ? "deal" : "deals"}; none are explicitly flagged.` : "These records were explicitly flagged for partner attention. Review the owning Deal Room view before deciding what changes."}</p>
    <a class="action" href="${DESTINATION}">${empty ? "Open your Team Book deals" : "Review flagged deals"}</a>
    <p class="source">Source: ${escapeHtml(payload.source?.ref || "v_deal_room_board")} · ${escapeHtml(observedLabel)} · record freshness unknown (read time only)</p>
  </div>`;
}

async function load() {
  card.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(ENDPOINT, { headers: { accept: "application/json" }, cache: "no-store" });
    if (response.status === 401 || response.status === 403) return renderUnauthorized();
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      if (failure.error === "canonical_read_unavailable" || response.status === 503) return renderUnavailable();
      return renderUnavailable();
    }
    const payload = await response.json();
    if (payload.schema_version !== "workspace-command-center-deal-attention/v1" ||
        payload.destination !== DESTINATION || payload.source?.ref !== "v_deal_room_board" ||
        !payload.observed_at || !Number.isInteger(payload.summary?.owned_active) ||
        !Number.isInteger(payload.summary?.owned_flagged) || payload.summary.owned_active < 0 ||
        payload.summary.owned_flagged < 0 || payload.summary.owned_flagged > payload.summary.owned_active)
      return renderUnavailable();
    renderSummary(payload);
  } catch {
    renderUnavailable();
  }
}

load();
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});

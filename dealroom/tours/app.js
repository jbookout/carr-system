(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const state = { csrf: "", tours: [], tour: null, rawShareToken: "", shareGrantId: "", projectionId: "" };
  const uuid = () => crypto.randomUUID();
  const status = (message) => { $("#status").textContent = message; };
  const text = (value, fallback = "") => typeof value === "string" && value ? value : fallback;
  const digest = (value) => /^sha256:[0-9a-f]{64}$/i.test(value);
  const id = (value) => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const base64url = (bytes) => btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  async function sha256(value) { const bytes = new TextEncoder().encode(value); const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)); return `sha256:${[...hash].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`; }
  function newShareToken() { const bytes = new Uint8Array(32); crypto.getRandomValues(bytes); return base64url(bytes); }
  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    let payload = null; try { payload = await response.json(); } catch { /* only sanitized server errors are shown */ }
    if (!response.ok) throw new Error(payload?.error || "request_failed");
    if (typeof payload?.csrf_token === "string") state.csrf = payload.csrf_token;
    return payload?.data || {};
  }
  function post(path, body) { return request(path, { method: "POST", headers: { "content-type": "application/json", "x-carr-csrf": state.csrf }, body: JSON.stringify(body) }); }
  function renderLibrary() {
    const list = $("#tour-list"); list.replaceChildren();
    for (const tour of state.tours) { const button = document.createElement("button"); button.type = "button"; button.className = "tour-button"; button.textContent = `${text(tour.name, "Untitled tour")} · ${text(tour.status, "draft")}`; button.addEventListener("click", () => void loadTour(tour.id)); const item = document.createElement("li"); item.append(button); list.append(item); }
    if (!state.tours.length) list.textContent = "No tours are available.";
    list.setAttribute("aria-busy", "false");
  }
  function stops() { return Array.isArray(state.tour?.stops) ? state.tour.stops : []; }
  function renderTour() {
    const tour = state.tour; if (!tour) return;
    $("#empty-state").hidden = true; $("#tour-panel").hidden = false;
    $("#tour-name").textContent = text(tour.name, "Untitled tour"); $("#tour-state").textContent = text(tour.status, "Draft");
    $("#tour-meta").textContent = [tour.client_name, tour.market, tour.updated_at].filter(Boolean).join(" · ");
    $("#route-version").textContent = text(tour.route_version_label, tour.route_version_id ? "Current version" : "No route version");
    state.projectionId = text(tour.projection_id); state.shareGrantId = text(tour.share_grant_id);
    $("#projection-state").textContent = state.projectionId ? "Ready" : "Not generated"; $("#share-state").textContent = state.shareGrantId ? "Active" : "Not issued";
    $("#projection-note").textContent = state.projectionId ? "Projection is ready for a deliberately scoped, expiring share." : "A projection is required before an external link can be issued.";
    $("#cheat-content").value = typeof tour.cheat_sheet?.content === "string" ? tour.cheat_sheet.content : JSON.stringify(tour.cheat_sheet?.content || {}, null, 2);
    $("#sheet-state").textContent = text(tour.cheat_sheet?.revision_label, "Not saved");
    const list = $("#route-stops"); list.replaceChildren();
    for (const stop of stops()) { const row = document.createElement("li"); row.className = "stop"; row.dataset.stopId = text(stop.id); const label = document.createElement("span"); label.textContent = text(stop.label, text(stop.name, "Tour stop")); const controls = document.createElement("span"); for (const [word, delta] of [["Up", -1], ["Down", 1]]) { const button = document.createElement("button"); button.type = "button"; button.textContent = word; button.addEventListener("click", () => moveStop(stop.id, delta)); controls.append(button); } row.append(label, controls); list.append(row); }
    if (!stops().length) list.textContent = "No stops in this route cart.";
  }
  async function loadLibrary() { status("Loading tours…"); const data = await request("/api/tours/library"); state.tours = Array.isArray(data.tours) ? data.tours.filter((tour) => id(tour?.id)) : []; renderLibrary(); status("Tour library ready."); }
  async function loadTour(tourId) { status("Loading tour…"); state.tour = await request(`/api/tours/detail?tour_id=${encodeURIComponent(tourId)}`); renderTour(); status("Tour ready."); }
  function moveStop(stopId, delta) { const list = stops(); const index = list.findIndex((stop) => stop.id === stopId); const destination = index + delta; if (index < 0 || destination < 0 || destination >= list.length) return; [list[index], list[destination]] = [list[destination], list[index]]; renderTour(); }
  async function saveRoute(reorder = false) { if (!state.tour) return; const stopIds = stops().map((stop) => stop.id).filter(id); const path = reorder ? "/api/tours/route-reorder" : "/api/tours/route-version"; const payload = reorder ? { tour_id: state.tour.id, route_version_id: state.tour.route_version_id, expected_route_version: Number(state.tour.route_version || 0), stop_ids: stopIds, idempotency_key: uuid() } : { tour_id: state.tour.id, expected_route_version: Number(state.tour.route_version || 0), stop_ids: stopIds, idempotency_key: uuid() }; await post(path, payload); await loadTour(state.tour.id); status("Route version saved."); }
  async function saveSheet() { if (!state.tour) return; let content; try { content = JSON.parse($("#cheat-content").value || "{}"); } catch { content = { notes: $("#cheat-content").value }; } await post("/api/tours/cheat-sheet/autosave", { tour_id: state.tour.id, content, expected_revision_number: Number(state.tour.cheat_sheet?.revision_number || 0), idempotency_key: uuid() }); await loadTour(state.tour.id); status("Internal cheat sheet saved."); }
  async function issueShare(rotate = false) { if (!state.projectionId) throw new Error("projection_required"); const raw = newShareToken(); const tokenDigest = await sha256(raw); const scopes = [...document.querySelectorAll('input[name="scope"]:checked')].map((box) => box.value); const expires = new Date($("#share-expiry").value).toISOString(); const receipt = $("#receipt-digest").value.trim(); if (!digest(receipt) || !scopes.length || !Number.isFinite(Date.parse(expires))) throw new Error("share_details_invalid"); const payload = { projection_id: state.projectionId, token_digest: tokenDigest, permission_scopes: scopes, expires_at: expires, receipt_digest: receipt, idempotency_key: uuid() }; const data = await post(rotate ? "/api/tours/share/rotate" : "/api/tours/share/issue", rotate ? { share_grant_id: state.shareGrantId, ...payload } : payload); state.rawShareToken = raw; state.shareGrantId = text(data.share_grant_id, state.shareGrantId); const url = `https://reports.doctorcre.com/share#token=${raw}`; $("#share-url").value = url; $("#share-link").hidden = false; $("#share-state").textContent = "Active"; status("Confidential link generated. Copy it now."); }
  async function action(work) { try { await work(); } catch { status("That change could not be saved."); } }
  $("#refresh").addEventListener("click", () => void action(loadLibrary)); $("#save-route").addEventListener("click", () => void action(() => saveRoute(false))); $("#reorder-route").addEventListener("click", () => void action(() => saveRoute(true)));
  $("#accept-route").addEventListener("click", () => void action(async () => { if (!state.tour?.route_version_id) return; await post("/api/tours/route-accept", { route_version_id: state.tour.route_version_id, expected_prior_route_version: Number(state.tour.route_version || 0), acceptance_digest: await sha256(`${state.tour.route_version_id}:${state.tour.route_version || 0}`), idempotency_key: uuid() }); await loadTour(state.tour.id); status("Route version accepted."); }));
  $("#save-sheet").addEventListener("click", () => void action(saveSheet)); $("#restore-sheet").addEventListener("click", () => void action(async () => { const revision = state.tour?.cheat_sheet?.restore_revision_id; if (!state.tour || !id(revision)) return; await post("/api/tours/cheat-sheet/restore", { tour_id: state.tour.id, restore_revision_id: revision, expected_revision_number: Number(state.tour.cheat_sheet?.revision_number || 0), idempotency_key: uuid() }); await loadTour(state.tour.id); }));
  $("#generate-projection").addEventListener("click", () => void action(async () => { if (!state.tour?.route_version_id) return; const data = await post("/api/tours/projection", { tour_id: state.tour.id, route_version_id: state.tour.route_version_id, as_of: new Date().toISOString(), idempotency_key: uuid() }); state.projectionId = text(data.projection_id); await loadTour(state.tour.id); status("Client projection generated."); }));
  $("#share-form").addEventListener("submit", (event) => { event.preventDefault(); void action(() => issueShare(false)); }); $("#rotate-share").addEventListener("click", () => void action(() => issueShare(true)));
  $("#revoke-share").addEventListener("click", () => void action(async () => { if (!id(state.shareGrantId)) return; await post("/api/tours/share/revoke", { share_grant_id: state.shareGrantId, reason: "Internal operator revoked link", revoked_at: new Date().toISOString(), receipt_digest: $("#receipt-digest").value.trim(), idempotency_key: uuid() }); state.rawShareToken = ""; $("#share-link").hidden = true; $("#share-state").textContent = "Revoked"; status("Share link revoked."); }));
  $("#copy-share").addEventListener("click", () => void action(async () => { await navigator.clipboard.writeText($("#share-url").value); status("Confidential link copied."); }));
  $("#share-expiry").value = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 16); void action(loadLibrary);
})();

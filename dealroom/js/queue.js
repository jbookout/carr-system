import {
  QUEUE_COLUMNS, queueColumnFor, queueIsStale, queueProjection,
} from "./queue-model.mjs";


const $ = (id) => document.getElementById(id);
const text = (tag, value, className = "") => { const node = document.createElement(tag); node.textContent = String(value ?? ""); if (className) node.className = className; return node; };
const fingerprint = (card) => JSON.stringify([card.title, card.summary, card.target, card.effective_model, card.status, card.priority, card.cap, card.updated_at]);

function cardNode(card) {
  const node = document.createElement("button"); node.type = "button"; node.className = "queue-card";
  node.setAttribute("data-task-id", card.task_id);
  node.append(text("strong", "", "queue-card-title"), text("span", "", "queue-card-summary"), text("small", "", "queue-card-meta"));
  node.addEventListener("click", () => showDrawer(node._card));
  updateCard(node, card); return node;
}
function updateCard(node, card) {
  const mark = fingerprint(card); if (node.dataset.fingerprint === mark) return;
  node.dataset.fingerprint = mark; node._card = card;
  node.querySelector(".queue-card-title").textContent = card.title;
  node.querySelector(".queue-card-summary").textContent = card.summary;
  node.querySelector(".queue-card-meta").textContent = `${card.target} · ${card.priority} · ${card.status}`;
}
function showDrawer(card) {
  $("queueDrawer").hidden = false; $("drawerTitle").textContent = card.title; $("drawerSummary").textContent = card.summary;
  const meta = $("drawerMeta"); meta.replaceChildren();
  for (const [label, value] of [["Task", card.task_id], ["Status", card.status], ["Target", card.target], ["Model", card.effective_model || "—"], ["Updated", card.updated_at]]) {
    meta.append(text("dt", label), text("dd", value));
  }
}
function filtered(cards) {
  const target = $("queueTarget").value, status = $("queueStatus").value;
  return cards.filter((card) => (!target || card.target === target) && (!status || card.status === status));
}
function render(projection) {
  const cards = filtered(projection.cards);
  for (const column of QUEUE_COLUMNS) {
    const host = document.querySelector(`[data-column="${column.id}"] .queue-card-list`);
    const wanted = cards.filter((card) => queueColumnFor(card.status) === column.id);
    const retained = new Map([...host.querySelectorAll("[data-task-id]")].map((node) => [node.dataset.taskId, node]));
    for (const card of wanted) { const node = retained.get(card.task_id) || cardNode(card); updateCard(node, card); host.appendChild(node); retained.delete(card.task_id); }
    for (const node of retained.values()) node.remove();
    host.closest("section").querySelector(".queue-count").textContent = String(wanted.length);
  }
}
function buildColumns() {
  const host = $("queueColumns");
  for (const column of QUEUE_COLUMNS) { const section = document.createElement("section"); section.className = "queue-column panel"; section.dataset.column = column.id;
    const head = document.createElement("h2"); head.append(text("span", column.label), text("span", "0", "queue-count")); section.append(head, Object.assign(document.createElement("div"), { className: "queue-card-list" })); host.appendChild(section); }
  const status = $("queueStatus"); for (const column of QUEUE_COLUMNS) for (const value of column.statuses) status.append(new Option(value, value));
}
function setTargets(cards) {
  const select = $("queueTarget"), prior = select.value; select.replaceChildren(new Option("All targets", ""));
  for (const target of [...new Set(cards.map((card) => card.target))].sort()) select.append(new Option(target, target)); select.value = prior;
}
async function csrfToken() { const response = await fetch("/api/room/turns?limit=1", { cache: "no-store", credentials: "same-origin" }); const body = await response.json(); if (!response.ok || !body.csrf_token) throw new Error("Room sign-in is required to enqueue work."); return body.csrf_token; }
async function enqueue(event) { event.preventDefault(); const title = $("enqueueTitle").value.trim(), body = $("enqueueBody").value.trim(); if (!title) return;
  const command = `@queue enqueue target=${$("enqueueTarget").value} cap=read :: ${title}${body ? `\n${body}` : ""}`;
  try { const csrf = await csrfToken(); const response = await fetch("/api/room/turn", { method: "POST", credentials: "same-origin", cache: "no-store", headers: { "content-type": "application/json", "x-carr-csrf": csrf, origin: location.origin, "sec-fetch-site": "same-origin" }, body: JSON.stringify({ body: command }) }); if (!response.ok) throw new Error("The Queue command was not accepted."); $("queueComposer").reset(); $("queueNotice").textContent = "Sent to Hermes; waiting for the projection."; }
  catch (error) { $("queueNotice").textContent = error.message; }
}
async function poll() {
  try { const response = await fetch("/api/room/queue", { cache: "no-store", credentials: "same-origin" }); const body = await response.json(); if (!response.ok) throw new Error(body.error || "Queue unavailable");
    const stale = body.live !== true || queueIsStale(body.projected_at); const model = queueProjection(body.events); setTargets(model.cards); render(model);
    $("queueLive").textContent = stale ? "Stale" : "Live"; $("queueLive").classList.toggle("is-stale", stale); $("queueSync").textContent = stale ? "Queue projection is stale — cards are not live state." : `Synced ${new Date(body.projected_at).toLocaleTimeString()}.`;
  } catch { $("queueLive").textContent = "Offline"; $("queueLive").classList.add("is-stale"); $("queueSync").textContent = "Queue is offline — previously shown cards are not live state."; }
}
if (typeof document !== "undefined") { buildColumns(); let latest = { cards: [] }; const originalRender = render; render = (model) => { latest = model; originalRender(model); }; ["queueTarget", "queueStatus"].forEach((id) => $(id).addEventListener("change", () => originalRender(latest))); $("drawerClose").addEventListener("click", () => { $("queueDrawer").hidden = true; }); $("queueComposer").addEventListener("submit", enqueue); poll().then(() => {}); setInterval(poll, document.hidden ? 30_000 : 5_000); }

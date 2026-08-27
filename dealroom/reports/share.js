(() => {
  "use strict";

  const status = document.querySelector("#status");
  const summary = document.querySelector("#report-summary");
  const list = document.querySelector("#report-list");
  const openButton = document.querySelector("#open-tour");
  const downloadPdf = document.querySelector("#download-pdf");
  const commentPanel = document.querySelector("#comment-panel");
  const commentForm = document.querySelector("#comment-form");
  const propertySelect = document.querySelector("#property-ref");
  let csrfToken = "";
  let shareToken = "";
  let allowReactions = false;

  function setStatus(message) {
    status.textContent = message;
  }

  function fragmentToken() {
    const fragment = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
    const params = new URLSearchParams(fragment);
    if (/^[A-Za-z0-9_-]{43}$/.test(fragment)) return fragment;
    const token = params.size === 1 && params.has("token") ? params.get("token") : "";
    return /^[A-Za-z0-9_-]{43}$/.test(token || "") ? token : "";
  }

  function removeFragment() {
    window.history.replaceState(null, document.title, `${window.location.pathname}${window.location.search}`);
  }

  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    let data = null;
    try { data = await response.json(); } catch { /* server errors remain generic */ }
    if (!response.ok) throw new Error(data?.error || "request_failed");
    return data;
  }

  function text(value, fallback) {
    return typeof value === "string" && value ? value : fallback;
  }

  function validPropertyRef(value) {
    return typeof value === "string" && /^property:public:[A-Za-z0-9_-]{16,128}$/.test(value);
  }

  function routeOrder(item, index) {
    return Number.isFinite(item?.route_sequence) ? item.route_sequence : index + 1;
  }

  function reactionLabel(reaction) {
    return ({ interested: "Interested", discuss: "Discuss", remove: "Remove" })[reaction] || "";
  }

  async function sendReaction(propertyRef, reaction, button) {
    if (!allowReactions || !csrfToken || !validPropertyRef(propertyRef)) return;
    button.disabled = true;
    try {
      await request("/api/share/reaction", {
        method: "POST",
        headers: { "content-type": "application/json", "x-tour-share-csrf": csrfToken },
        body: JSON.stringify({ property_ref: propertyRef, reaction, idempotency_key: crypto.randomUUID() }),
      });
      await loadReport();
      setStatus("Your response was saved.");
    } catch {
      button.disabled = false;
      setStatus("Your response could not be saved.");
    }
  }

  function render(report) {
    const title = text(report?.tour_name || report?.title || report?.name, "Tour report");
    const items = Array.isArray(report?.stops) ? report.stops : (Array.isArray(report?.items) ? report.items : (Array.isArray(report?.properties) ? report.properties : []));
    const properties = items.map((item, index) => ({ item, index })).filter(({ item }) => validPropertyRef(item?.property_ref))
      .sort((left, right) => routeOrder(left.item, left.index) - routeOrder(right.item, right.index));
    allowReactions = report?.allow_reactions === true;
    downloadPdf.hidden = report?.allow_pdf_download !== true;
    document.querySelector("#report-title").textContent = title;
    summary.textContent = text(report?.summary, `${properties.length} property${properties.length === 1 ? "" : "ies"} in this report.`);
    list.replaceChildren();
    propertySelect.replaceChildren();
    for (const { item, index } of properties) {
      const propertyRef = item.property_ref;
      const label = text(item.route_label, `Stop ${routeOrder(item, index)}`);
      const name = text(item.name, text(item.title, "Tour property"));
      const row = document.createElement("li");
      row.className = "report-item";
      const route = document.createElement("p");
      route.className = "route-label";
      route.textContent = label;
      const heading = document.createElement("h3");
      heading.textContent = name;
      const detail = document.createElement("p");
      detail.textContent = text(item.summary, text(item.status, text(item.address, "Details available in the packet.")));
      row.append(route, heading, detail);
      const latest = reactionLabel(item.latest_reaction || item.reaction);
      if (latest) {
        const state = document.createElement("p");
        state.className = "reaction-state";
        state.textContent = `Latest response: ${latest}`;
        row.append(state);
      }
      if (allowReactions) {
        const controls = document.createElement("div");
        controls.className = "reaction-controls";
        for (const reaction of ["interested", "discuss", "remove"]) {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = reactionLabel(reaction);
          button.setAttribute("aria-pressed", String((item.latest_reaction || item.reaction) === reaction));
          button.addEventListener("click", () => { void sendReaction(propertyRef, reaction, button); });
          controls.append(button);
        }
        row.append(controls);
      }
      list.append(row);
      const option = document.createElement("option");
      option.value = propertyRef;
      option.textContent = `${label}: ${name}`;
      propertySelect.append(option);
    }
    if (!properties.length) list.textContent = "No properties are available in this report.";
    list.setAttribute("aria-busy", "false");
    commentPanel.hidden = report?.allow_comments !== true || !properties.length;
  }

  async function loadReport() {
    const payload = await request("/api/share/report");
    csrfToken = typeof payload.csrf_token === "string" ? payload.csrf_token : "";
    render(payload.data || {});
  }

  async function openTour() {
    const token = shareToken;
    shareToken = "";
    openButton.disabled = true;
    if (!token) return;
    try {
      await request("/api/share/exchange", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token }),
      });
      await loadReport();
      setStatus("Report loaded.");
    } catch {
      setStatus("This shared report is unavailable.");
      list.setAttribute("aria-busy", "false");
    }
  }

  function bootstrap() {
    shareToken = fragmentToken();
    removeFragment();
    if (!shareToken) {
      setStatus("This shared report link is incomplete or has expired.");
      list.setAttribute("aria-busy", "false");
      return;
    }
    openButton.disabled = false;
    setStatus("Select Open tour to view this shared report.");
  }

  commentForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const propertyRef = propertySelect.value;
    const body = new FormData(commentForm).get("body");
    if (typeof body !== "string" || !csrfToken || !validPropertyRef(propertyRef)) return;
    try {
      await request("/api/share/comment", {
        method: "POST",
        headers: { "content-type": "application/json", "x-tour-share-csrf": csrfToken },
        body: JSON.stringify({ property_ref: propertyRef, body, idempotency_key: crypto.randomUUID() }),
      });
      commentForm.reset();
      setStatus("Your note was sent.");
    } catch {
      setStatus("Your note could not be sent.");
    }
  });

  openButton.addEventListener("click", () => { void openTour(); });
  bootstrap();
})();

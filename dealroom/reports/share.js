(() => {
  "use strict";

  const status = document.querySelector("#status");
  const summary = document.querySelector("#report-summary");
  const list = document.querySelector("#report-list");
  const openButton = document.querySelector("#open-tour");
  let shareToken = "";

  function setStatus(message) { status.textContent = message; }

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
    try { data = await response.json(); } catch { /* errors remain generic */ }
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

  function render(report) {
    const title = text(report?.tour_name || report?.title || report?.name, "Tour report");
    const items = Array.isArray(report?.stops) ? report.stops :
      (Array.isArray(report?.items) ? report.items : (Array.isArray(report?.properties) ? report.properties : []));
    const properties = items.map((item, index) => ({ item, index }))
      .filter(({ item }) => validPropertyRef(item?.property_ref))
      .sort((left, right) => routeOrder(left.item, left.index) - routeOrder(right.item, right.index));
    document.querySelector("#report-title").textContent = title;
    summary.textContent = text(report?.summary, `${properties.length} property${properties.length === 1 ? "" : "ies"} in this report.`);
    list.replaceChildren();
    for (const { item, index } of properties) {
      const row = document.createElement("li");
      row.className = "report-item";
      const route = document.createElement("p");
      route.className = "route-label";
      route.textContent = text(item.route_label, `Stop ${routeOrder(item, index)}`);
      const heading = document.createElement("h3");
      heading.textContent = text(item.name, text(item.title, "Tour property"));
      const detail = document.createElement("p");
      detail.textContent = text(item.summary, text(item.status, text(item.address, "Details available in the packet.")));
      row.append(route, heading, detail);
      list.append(row);
    }
    if (!properties.length) list.textContent = "No properties are available in this report.";
    list.setAttribute("aria-busy", "false");
  }

  async function loadReport() {
    const payload = await request("/api/share/report");
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

  openButton.addEventListener("click", () => { void openTour(); });
  bootstrap();
})();

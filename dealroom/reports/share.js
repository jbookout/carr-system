(() => {
  "use strict";

  const status = document.querySelector("#status");
  const summary = document.querySelector("#report-summary");
  const list = document.querySelector("#report-list");
  const openButton = document.querySelector("#open-tour");
  let shareToken = typeof globalThis.__CARR_TOUR_TAKE_SHARE_TOKEN__ === "function"
    ? globalThis.__CARR_TOUR_TAKE_SHARE_TOKEN__() : "";
  let reportProperties = new globalThis.Map();
  let mapInstance = null;
  // Client feedback (shortlist and comment). The projection ref comes only from
  // the feedback read; a property gets controls only if that read lists it.
  let feedback = null;
  const shortlisted = new globalThis.Map();
  const pendingKeys = new globalThis.Map();
  const inFlight = new globalThis.Set();

  function setStatus(message) { status.textContent = message; }

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

  function propertyAddress(item, fallback) {
    const parts = [item?.address, item?.suite].filter(value => typeof value === "string" && value.trim());
    return parts.length ? parts.join(" · ") : fallback;
  }

  async function send(path, body) {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    return response.status;
  }

  // One idempotency key per unsent action. A dropped connection or a 5xx keeps
  // the key, so the retry replays the same write; any answer that settles the
  // action (saved or refused) drops it so the next action gets a fresh key.
  async function submitFeedback(kind, propertyRef, value) {
    if (!feedback || !feedback.refs.has(propertyRef)) return "refused";
    const signature = `${kind}|${propertyRef}|${value}`;
    if (inFlight.has(signature)) return "busy";
    inFlight.add(signature);
    const key = pendingKeys.get(signature) || crypto.randomUUID();
    pendingKeys.set(signature, key);
    const body = kind === "shortlist"
      ? { projection_ref: feedback.projectionRef, property_ref: propertyRef, shortlisted: value, idempotency_key: key }
      : { projection_ref: feedback.projectionRef, property_ref: propertyRef, comment: value, idempotency_key: key };
    try {
      const code = await send(`/api/share/${kind}`, body);
      if (code === 200) { pendingKeys.delete(signature); return "saved"; }
      if (code >= 500) return "retry";
      pendingKeys.delete(signature);
      if (code === 401 || code === 403 || code === 404) { feedback = null; return "unavailable"; }
      return "refused";
    } catch { return "retry"; }
    finally { inFlight.delete(signature); }
  }

  function feedbackControls(propertyRef, note) {
    const box = document.createElement("div");
    box.className = "feedback";
    const say = message => { note.textContent = message; };
    const outcome = {
      saved: "Saved.", retry: "Not saved yet. Try again.", busy: "Still saving…",
      unavailable: "This link is no longer active. Ask your broker for a new one.", refused: "That could not be saved.",
    };
    if (feedback.scopes.has("shortlist")) {
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "shortlist-toggle";
      const paint = () => {
        const on = shortlisted.get(propertyRef) === true;
        pick.setAttribute("aria-pressed", String(on));
        pick.textContent = on ? "Shortlisted" : "Add to shortlist";
      };
      paint();
      pick.addEventListener("click", async () => {
        const wanted = shortlisted.get(propertyRef) !== true;
        const result = await submitFeedback("shortlist", propertyRef, wanted);
        if (result === "saved") { shortlisted.set(propertyRef, wanted); paint(); }
        say(outcome[result]);
        if (result === "unavailable") disableFeedback();
      });
      box.append(pick);
    }
    if (feedback.scopes.has("comment")) {
      const field = document.createElement("textarea");
      field.className = "comment-field";
      field.maxLength = 1000;
      field.rows = 2;
      field.setAttribute("aria-label", "Comment for your broker on this property");
      const post = document.createElement("button");
      post.type = "button";
      post.className = "comment-send";
      post.textContent = "Send comment";
      post.addEventListener("click", async () => {
        const comment = field.value.trim();
        if (!comment) { say("Write a comment first."); return; }
        const result = await submitFeedback("comment", propertyRef, comment);
        if (result === "saved") field.value = "";
        say(outcome[result]);
        if (result === "unavailable") disableFeedback();
      });
      box.append(field, post);
    }
    return box;
  }

  function disableFeedback() {
    for (const control of list.querySelectorAll(".feedback")) control.remove();
  }

  function render(report) {
    const items = Array.isArray(report?.stops) ? report.stops :
      (Array.isArray(report?.items) ? report.items : (Array.isArray(report?.properties) ? report.properties : []));
    const properties = items.map((item, index) => ({ item, index }))
      .filter(({ item }) => validPropertyRef(item?.property_ref))
      .sort((left, right) => routeOrder(left.item, left.index) - routeOrder(right.item, right.index));
    reportProperties = new globalThis.Map(properties.map(({ item }) => [item.property_ref, item]));
    document.querySelector("#report-title").textContent = "Tour report";
    summary.textContent = `${properties.length} ${properties.length === 1 ? "property" : "properties"} in this report.`;
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
      detail.textContent = text(item.summary, text(item.status, propertyAddress(item, "Details available in the packet.")));
      row.append(route, heading, detail);
      if (feedback && feedback.refs.has(item.property_ref)) {
        const note = document.createElement("p");
        note.className = "status feedback-note";
        note.setAttribute("role", "status");
        row.append(feedbackControls(item.property_ref, note), note);
      }
      list.append(row);
    }
    if (!properties.length) list.textContent = "No properties are available in this report.";
    list.setAttribute("aria-busy", "false");
  }

  function validMapPoint(point) {
    return validPropertyRef(point?.property_ref) && Number.isInteger(point?.route_sequence) && point.route_sequence > 0 &&
      Number.isFinite(point?.latitude) && point.latitude >= -90 && point.latitude <= 90 &&
      Number.isFinite(point?.longitude) && point.longitude >= -180 && point.longitude <= 180;
  }

  async function renderMap(payload) {
    const points = (Array.isArray(payload?.points) ? payload.points : []).filter(validMapPoint)
      .sort((left, right) => left.route_sequence - right.route_sequence);
    if (!points.length) return;
    const { LngLatBounds, Map: MapLibreMap, Marker, NavigationControl, Popup, setWorkerUrl } =
      await import("/vendor/maplibre-gl-6.4.1/maplibre-gl.mjs");
    setWorkerUrl("/vendor/maplibre-gl-6.4.1/maplibre-gl-worker.mjs");
    const mapSection = document.querySelector("#map-section");
    mapSection.hidden = false;
    if (mapInstance) mapInstance.remove();
    mapInstance = new MapLibreMap({
      container: "tour-map",
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#eef4f8" } }] },
      center: [points[0].longitude, points[0].latitude], zoom: 11, attributionControl: false,
    });
    mapInstance.addControl(new NavigationControl({ showCompass: false }), "top-right");
    const bounds = new LngLatBounds();
    for (const point of points) {
      const coordinate = [point.longitude, point.latitude];
      bounds.extend(coordinate);
      const property = reportProperties.get(point.property_ref) || {};
      const marker = document.createElement("button");
      marker.type = "button";
      marker.textContent = typeof point.route_label === "string" ? point.route_label : String(point.route_sequence);
      marker.setAttribute("aria-label", `Stop ${point.route_sequence}: ${text(property.name, "Tour property")}`);
      const popupBody = document.createElement("div");
      const popupTitle = document.createElement("strong");
      popupTitle.textContent = text(property.name, `Stop ${point.route_sequence}`);
      const popupAddress = document.createElement("div");
      popupAddress.textContent = propertyAddress(property, "Verified access point");
      popupBody.append(popupTitle, popupAddress);
      new Marker({ element: marker }).setLngLat([point.longitude, point.latitude])
        .setPopup(new Popup({ offset: 18 }).setDOMContent(popupBody)).addTo(mapInstance);
    }
    mapInstance.on("load", () => {
      if (points.length > 1) mapInstance.fitBounds(bounds, { padding: 56, maxZoom: 14, duration: 0 });
    });
  }

  async function fetchReport() {
    const payload = await request("/api/share/report");
    return payload.data || {};
  }

  async function fetchMap() {
    const payload = await request("/api/share/map");
    return payload.data || {};
  }

  async function fetchFeedback() {
    const payload = await request("/api/share/feedback");
    const data = payload.data || {};
    const scopes = new globalThis.Set((Array.isArray(data.permission_scopes) ? data.permission_scopes : [])
      .filter(scope => scope === "shortlist" || scope === "comment"));
    const refs = new globalThis.Set((Array.isArray(data.items) ? data.items : []).map(item => item?.property_ref).filter(validPropertyRef));
    if (typeof data.projection_ref !== "string" || !/^projection:public:[A-Za-z0-9_-]{16,128}$/.test(data.projection_ref) || !scopes.size || !refs.size) return null;
    return { projectionRef: data.projection_ref, scopes, refs };
  }

  async function loadTour() {
    try {
      // Feedback is optional: a packet-only or map-only grant simply has none.
      feedback = await fetchFeedback().catch(() => null);
      // Packet and map are independently scoped. Fetch both, then render in a
      // stable order so a valid map-only or packet-only grant still opens.
      const [reportResult, mapResult] = await Promise.allSettled([fetchReport(), fetchMap()]);
      const reportLoaded = reportResult.status === "fulfilled";
      const mapLoaded = mapResult.status === "fulfilled";
      if (!reportLoaded && !mapLoaded) throw new Error("share_scope_unavailable");
      if (reportLoaded) render(reportResult.value);
      else {
        document.querySelector("#report-title").textContent = "Shared tour map";
        summary.textContent = "Verified access points included in this share.";
        list.textContent = "This link includes the interactive map only.";
        list.setAttribute("aria-busy", "false");
      }
      if (mapLoaded) await renderMap(mapResult.value);
      else document.querySelector("#map-section").hidden = true;
      setStatus(reportLoaded && mapLoaded ? "Report and map loaded." : reportLoaded ? "Report loaded." : "Map loaded.");
    } catch {
      setStatus("This shared report is unavailable.");
      list.setAttribute("aria-busy", "false");
    }
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
      await loadTour();
    } catch {
      setStatus("This shared report is unavailable.");
      list.setAttribute("aria-busy", "false");
    }
  }

  function bootstrap() {
    if (!shareToken) {
      openButton.hidden = true;
      setStatus("Opening your shared report…");
      void loadTour();
      return;
    }
    openButton.disabled = false;
    setStatus("Select Open tour to view this shared report.");
  }

  openButton.addEventListener("click", () => { void openTour(); });
  bootstrap();
})();

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

  function render(report) {
    const items = Array.isArray(report?.stops) ? report.stops :
      (Array.isArray(report?.items) ? report.items : (Array.isArray(report?.properties) ? report.properties : []));
    const properties = items.map((item, index) => ({ item, index }))
      .filter(({ item }) => validPropertyRef(item?.property_ref))
      .sort((left, right) => routeOrder(left.item, left.index) - routeOrder(right.item, right.index));
    reportProperties = new globalThis.Map(properties.map(({ item }) => [item.property_ref, item]));
    document.querySelector("#report-title").textContent = "Tour report";
    summary.textContent = `${properties.length} property${properties.length === 1 ? "" : "ies"} in this report.`;
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
      await import("/vendor/maplibre-gl-6.1.0/maplibre-gl.mjs");
    setWorkerUrl("/vendor/maplibre-gl-6.1.0/maplibre-gl-worker.mjs");
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

  async function loadTour() {
    try {
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

import { LngLatBounds, Map as MapLibreMap, Marker, NavigationControl, Popup, setWorkerUrl } from "/vendor/maplibre-gl-6.1.0/maplibre-gl.mjs";

setWorkerUrl("/vendor/maplibre-gl-6.1.0/maplibre-gl-worker.mjs");

(() => {
  "use strict";

  const status = document.querySelector("#status");
  const summary = document.querySelector("#report-summary");
  const list = document.querySelector("#report-list");
  const openButton = document.querySelector("#open-tour");
  let shareToken = "";
  let reportProperties = new globalThis.Map();
  let mapInstance = null;

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
    reportProperties = new globalThis.Map(properties.map(({ item }) => [item.property_ref, item]));
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

  function validMapPoint(point) {
    return validPropertyRef(point?.property_ref) && Number.isInteger(point?.route_sequence) && point.route_sequence > 0 &&
      Number.isFinite(point?.latitude) && point.latitude >= -90 && point.latitude <= 90 &&
      Number.isFinite(point?.longitude) && point.longitude >= -180 && point.longitude <= 180;
  }

  function renderMap(payload) {
    const points = (Array.isArray(payload?.points) ? payload.points : []).filter(validMapPoint)
      .sort((left, right) => left.route_sequence - right.route_sequence);
    if (!points.length) return;
    const mapSection = document.querySelector("#map-section");
    mapSection.hidden = false;
    if (mapInstance) mapInstance.remove();
    mapInstance = new MapLibreMap({
      container: "tour-map",
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#eef4f8" } }] },
      center: [points[0].longitude, points[0].latitude], zoom: 11, attributionControl: false,
    });
    mapInstance.addControl(new NavigationControl({ showCompass: false }), "top-right");
    const routeCoordinates = [];
    const bounds = new LngLatBounds();
    for (const point of points) {
      const coordinate = [point.longitude, point.latitude];
      routeCoordinates.push(coordinate);
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
      popupAddress.textContent = text(property.address, "Verified access point");
      popupBody.append(popupTitle, popupAddress);
      new Marker({ element: marker }).setLngLat([point.longitude, point.latitude])
        .setPopup(new Popup({ offset: 18 }).setDOMContent(popupBody)).addTo(mapInstance);
    }
    mapInstance.on("load", () => {
      if (routeCoordinates.length > 1) {
        mapInstance.addSource("tour-route", { type: "geojson", data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: routeCoordinates } } });
        mapInstance.addLayer({ id: "tour-route", type: "line", source: "tour-route", paint: { "line-color": "#F57F29", "line-width": 4, "line-opacity": .9 } });
        mapInstance.fitBounds(bounds, { padding: 56, maxZoom: 14, duration: 0 });
      }
    });
  }

  async function loadReport() {
    const payload = await request("/api/share/report");
    render(payload.data || {});
  }

  async function loadMap() {
    try { const payload = await request("/api/share/map"); renderMap(payload.data || {}); }
    catch { document.querySelector("#map-section").hidden = true; }
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
      await loadMap();
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

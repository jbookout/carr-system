---
name: build-interactive-map
description: Build, redesign, review, or recommend governed CARR maps, GIS analyses, market maps, property-tour maps, route maps, day-trip plans, and interactive maps using the canonical record, reviewed enrichment, deterministic routing, synchronized map/list order, native navigation, and promotion gates. Use whenever Joe or Dell asks for a map, mapping stack, GIS integration, pins, waypoints, route optimization, Google Maps, Mapbox, MapLibre, Leaflet, or Tour Mode. Do not use for figurative roadmaps, mind maps, concept maps, or merely saying “map out the steps.”
---

# Build Interactive Map

Use the live record-layer method, not remembered advice. The Stop gate prevents a governed CARR session from finishing a map task without this read.

## Required workflow

1. Call `map-architecture` immediately after the request. If the verb is unavailable or reports a missing source, stop map work and repair the governed method; do not improvise from memory.
2. When repo access exists, read `workspace/contracts/market-map-route-planning.v1.json`. The live verb supplies the two operative doctrine sections and exact contract version.
3. Start with the business question, not the renderer. Identify the decision the map must improve, the human audience, and whether this is Search Mode, Tour Mode, or governed spatial analysis.
4. Build the recursive source graph. Read the direct source, author thread, quoted source, linked article/repository/documentation, material video transcript or frames, and dataset metadata. For X, use Grok Build first, then verify against the actual post and artifacts. Label every branch `direct_source`, `linked_artifact`, `public_mirror`, `inference`, or inaccessible; never mark blocked content read.
5. Build one structured brief with the audience/task, verified canonical dataset, route constraints, property-card fields, default view, optional layers, provenance requirements, device behavior, offline fallback, native-navigation handoff, sharing scope, and acceptance test.
6. Query domain data through typed functions: lookup, text, bounding-box, radius, drawn-polygon, batch, and time-series. Record canonical IDs, pagination, rate limits, freshness, provenance, coordinate precision, and estimate/allocation caveats.
7. Keep five layers separate: canonical record, reviewed GIS/OSINT enrichment, deterministic geocoding/routing, interactive presentation, and native navigation.
8. Treat AI as a bounded researcher and spatial author. It may inspect, import, normalize, join, filter, style, compare, validate, export, and prototype through typed tools. It may not approve coordinates, become route authority, resolve conflicts silently, invent legal GIS conclusions, or publish.
9. Generate alternatives only as disposable prototypes. Promote one after testing it with real structured content and the versioned component registry; model-generated one-off HTML or JavaScript is not production evidence.
10. Require the contract promotion receipt before calling any map client-ready. Unknown/conflicting route inputs, unreviewed enrichment, order mismatch, missing mobile/offline/native-navigation tests, or stale provider-rights evidence block promotion.

## Deterministic construction

- Default to one MapLibre-based renderer. Use paid Mapbox, Google, satellite, 3D, or another provider only when a verified capability and current rights receipt justify it.
- Assemble promoted maps from tested components: `MapShell`, `MapListSync`, `StopCard`, `SourceBadge`, `LayerControl`, `RouteSummary`, `ExclusionPanel`, `NativeNavAction`, `OfflineItinerary`, and `StoryTourController`.
- Use typed events for feature click, bounds change, draw result, filter state, slider state, selected record, and route-stop change. Update layers and paint properties without remounting the map or discarding camera and selection state.
- Use GeoJSON for small web working sets, GeoPackage for portable editable GIS review, GeoParquet with DuckDB for analysis, and PMTiles for large read-only layers. Preserve stable IDs, nulls, provenance, precision, review state, and source version through every round trip.
- Use Turf only for immediate interaction over already-authorized loaded features. Legal determinations, large data, and projection-sensitive buffers, unions, intersections, or differences stay in authoritative server-side GIS.

## Search Mode and Tour Mode

- Search Mode supports typed text, bounding-box, radius, and drawn-polygon discovery. Clicking a feature opens the exact CARR record; filters and style changes preserve map state.
- Tour Mode is deterministic story choreography: intro or stop card, verified marker, optional isochrone/trade-area layer, route summary, then next stop. Camera, marker, card, list, route, and reverse cleanup derive from one route version.
- `property_id` is immutable property identity. `route_sequence` and `route_label` such as A, B, and C are mutable itinerary fields. Reordering the drive never renames the property or canonical option.
- Every reorder records an old-to-new stop mapping with the old and new route versions, stop IDs, sequences, labels, property ID, and disposition. Never leave the fate of old A implicit when B becomes the new A.
- Native navigation requires a human-approved entrance, driveway, or parking-access coordinate. For a bad parking-lot or centroid pin: resolve the authoritative property location, inspect access imagery or site evidence, select the access role, obtain human coordinate approval, record the evidence, then rerun the route.
- A phone link carries the approved coordinate, route stop and version, travel mode, scoped return URI and expiry, and device-test receipt. Keep client names, deal facts, internal IDs, and access notes out of provider labels and public URLs.

## Non-negotiable output behavior

- Keep marker number, list position, route order, and offline itinerary order identical.
- Preserve locked appointments, start/end points, dwell time, and buffers.
- Show exclusions and unknowns instead of inventing coordinates or facts.
- Put satellite, 3D, parcels, demographics, and OSINT behind optional layers.
- Hand exact approved coordinates to Google Maps or Apple Maps for navigation.
- Keep an ordered offline itinerary usable without tiles or network access.
- Preserve source, as-of date, rights, confidence, and review state for every enrichment.
- Keep official source, CRS, buffer method, union/difference rules, geometry caveats, and determination status with every parcel, zoning, parking, demographic, flood, or policy overlay.
- Keep temporary provider results out of the canonical database. Store coordinates only when they are independently authoritative or the exact provider SKU and rights receipt permit permanent storage.
- Test hosting, security, caching, performance, pagination, rate limits, provider cost behavior, attribution, and export before production promotion.
- Require explicit confirmation before destructive or expensive GIS operations and publication.

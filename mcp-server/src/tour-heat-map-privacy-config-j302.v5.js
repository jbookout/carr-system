// V5-J302 heat-map privacy parameters, held apart from the kernel so a change
// to a number is a change to this file and nothing else.
//
// EVERY VALUE HERE IS A DEFAULT SET BY THE ORCHESTRATOR, REVERSIBLE. None is a
// Joe decision recorded in the design basis; each is the conservative internal
// default the orchestrator set on 2026-09-24, with Jev's agreement, for the four
// parameters the slice contract left open. Reverse one by editing this file; the
// kernel validates the shape at load and its policy digest moves with it.
//
// Nulls fail closed, as in root-trust-config.js: an absent reference table is
// not permission, it is a denial naming what is missing.

export const V5_J302_PRIVACY_CONFIG = Object.freeze({
  schema_version: "doctorcre-v5-j302-privacy-config.v1",
  provenance: "default_set_by_orchestrator_reversible",
  set_on: "2026-09-24",

  // 1. Platform-wide small-cell floor under BOTH routes: the CMS cell-size
  //    suppression standard (counts 1-10 suppressed). A source or expert
  //    threshold replaces it only when that threshold is stricter. The kernel
  //    refuses any config below 11 (V5_J302_KERNEL_MINIMUM_SMALL_CELL_FLOOR),
  //    so this value can be raised here but never lowered.
  platform_small_cell_floor: 11,
  platform_small_cell_floor_basis: "cms_cell_size_suppression_policy_1_to_10",

  // 2. Client-visible heat-map content: none. Clients see exactly the Tour PDF
  //    fields (decision 4ab3933e, as relayed by the orchestrator), and no
  //    heat-map-derived content is on the PDF.
  client_visible_heat_map_content: Object.freeze([]),
  client_visibility_decision_ref: "decision:4ab3933e",

  // 3. Safe Harbor binds no budget, so its differencing and export stay refused.
  safe_harbor_unbudgeted_operations: "refuse",

  // 4. A ZIP3 survives Safe Harbor only when NEITHER the 2000 Census (HHS's
  //    restricted list, held in the kernel) NOR the 2020 Census puts it at
  //    20,000 or fewer. No 2020 ZIP3 table is available offline in this
  //    repository, so the slot is explicitly unknown and every non-000 ZIP3
  //    is denied until a reviewed table digest is pinned here.
  census_2020_zip3_population: Object.freeze({
    vintage: "2020",
    status: "unavailable_offline",
    table_digest: null,
  }),

  // County-bearing geography (county, census tract, block group) is checked
  // against a bound list of county FIPS codes, because five digits alone cannot
  // tell a county from a ZIP5. No reviewed list is available offline, so the
  // slot is unknown and every county-bearing cell is denied until one is
  // pinned. The kernel checks the two-digit state prefix on its own.
  county_fips_codes: Object.freeze({
    status: "unavailable_offline",
    codes: null,
  }),
});

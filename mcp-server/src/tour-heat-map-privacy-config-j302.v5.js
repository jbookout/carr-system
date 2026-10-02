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

import { V5_J302_COUNTY_FIPS_2020_CODES } from "./tour-heat-map-census-2020-data-j302.v5.js";

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

  // 1b. Minimum protection interval: with a published total, every suppressed
  //     cell must still have at least this many possible values (primary
  //     suppression being 1..floor-1). 1 would allow exact disclosure; the
  //     kernel refuses any config below 2 (V5_J302_KERNEL_MINIMUM_PROTECTION_
  //     INTERVAL_VALUES). 3 refuses cells bounded to one or two values.
  minimum_protection_interval_values: 3,

  // 1c. Revision tolerance: two releases of the same cells may disagree by a
  //     revision (a refresh, a late record). Every cross-release check is run
  //     at each residual difference within +/- this many patients, and a pair
  //     that disagrees by more is refused as inconsistent, never skipped. The
  //     kernel refuses any value below 2 (V5_J302_KERNEL_MINIMUM_REVISION_
  //     TOLERANCE_PATIENTS).
  //     Consequence: a revision LARGER than this to the same suppressed cells
  //     locks that recipient's series — every later release of those cells is
  //     refused against the prior, permanently, because release history is
  //     never dropped. Recovery is a new series or new period keys, not a
  //     retry.
  revision_tolerance_patients: 2,

  // 2. Client-visible heat-map content: none. Clients see exactly the Tour PDF
  //    fields (decision 4ab3933e, as relayed by the orchestrator), and no
  //    heat-map-derived content is on the PDF.
  client_visible_heat_map_content: Object.freeze([]),
  client_visibility_decision_ref: "decision:4ab3933e",

  // 3. Safe Harbor binds no budget, so its differencing and export stay refused.
  safe_harbor_unbudgeted_operations: "refuse",

  // 4. A ZIP3 survives Safe Harbor only when NEITHER the 2000 Census (HHS's
  //    restricted list, held in the kernel) NOR the 2020 Census puts it at
  //    20,000 or fewer. The 2020 reading is the table in
  //    tour-heat-map-census-2020-data-j302.v5.js: 2020 DHC P1_001N summed over
  //    ZCTA5s by three-digit prefix, derived on 2026-09-25 from the national DHC
  //    summary file (us2020.dhc.zip, sha256 1a6f3aee...8c4cb). A receipt must
  //    carry exactly the table under this digest; a prefix absent from it is
  //    unknown and denies. Whether ZCTA-summed population is the right Safe
  //    Harbor basis is the privacy owner's (Joe's) call; the pin is reversed by
  //    setting this back to unavailable_offline with a null digest.
  census_2020_zip3_population: Object.freeze({
    vintage: "2020",
    status: "pinned",
    table_digest: "sha256:cb84fc0cc495609fd8c8f93e57fa5aa3c43915e95dafc5285c038badbd2be94c",
  }),

  // County-bearing geography (county, census tract, block group) is checked
  // against a bound list of county FIPS codes, because five digits alone cannot
  // tell a county from a ZIP5. The list is the 2020 national county reference
  // file (national_county2020.txt, sha256 9f6e5f6e...2970d6): 3,235 rows less
  // 74300 (U.S. Minor Outlying Islands, a state code the kernel does not bind),
  // 3,234 codes. The kernel checks the two-digit state prefix on its own.
  county_fips_codes: Object.freeze({
    status: "pinned",
    codes: V5_J302_COUNTY_FIPS_2020_CODES,
  }),
});

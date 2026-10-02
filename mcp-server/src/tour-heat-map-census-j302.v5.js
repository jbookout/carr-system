// DoctorCRE v5 slice V5-J302 — deterministic derivation of the two Census
// reference tables the heat-map privacy kernel pins in config.
//
// WHAT THIS PRODUCES. From the bytes of two public U.S. Census Bureau sources:
//
//   1. the 2020 ZIP3 population table: 2020 DHC total population (P1_001N) per
//      ZIP Code Tabulation Area, summed by the first three digits of the ZCTA
//      code, and its digest under the kernel's own zip3PopulationTableDigest —
//      the value config.census_2020_zip3_population.table_digest pins;
//   2. the county FIPS code list: STATEFP + COUNTYFP from the 2020 national
//      county reference file — the value config.county_fips_codes.codes pins.
//
// The digest is computed by the kernel's exported function, not a copy of it,
// so the derivation and the kernel cannot disagree about what a table digests to.
//
// WHY THE SUMMARY FILE AND NOT THE API. api.census.gov now redirects every DHC
// query without an API key to missing_key.html, and a key is a registered
// credential. The same dataset is published as the national DHC summary file
// (us2020.dhc.zip) on www2.census.gov. Two members are read:
//   - usgeo2020.dhc, the geographic header: summary level 860 (ZCTA5), geographic
//     component 00, gives each ZCTA's LOGRECNO, its GEOID (860Z200US + ZCTA) and
//     POP100;
//   - us000052020.dhc, segment 5: field 6 of each record is P1_001N (P1, total
//     population), joined to the header by LOGRECNO.
// P1_001N must equal POP100 for every ZCTA. The two are published separately
// in the same file, so a column misread on either side fails the derivation.
//
// FAIL-CLOSED, NEVER REPAIRED. Anything this module cannot read with certainty
// throws; it never skips, trims or guesses a row, because a silently dropped
// ZCTA would lower a prefix's sum and a silently kept malformed one could raise
// it. Specifically:
//   - every header and segment record must have the published field count,
//     file id, and (segment) CIFSN 05;
//   - a ZCTA row's GEOID must be exactly 860Z200US + its ZCTA field;
//   - every ZCTA must be five ASCII digits and appear once; a non-numeric code
//     (the 2000 Census "HH"/"XX" water and land pseudo-ZCTAs) throws;
//   - every population must be a non-negative decimal integer string;
//   - a ZCTA whose record is missing from segment 5, or whose P1_001N differs
//     from its POP100, throws;
//   - a ZCTA whose prefix is "000" throws, since 000 is Safe Harbor's own
//     suppression marker and no real ZIP3 carries it;
//   - the ZCTA count must be inside a plausible national range, so a truncated
//     or state-scoped file cannot be pinned by accident.
// A prefix no ZCTA carries (PO-box-only, unique and military prefixes) is
// ABSENT from the table, and the kernel denies an absent prefix as unknown.
//
// WHAT THIS CANNOT RESOLVE, STATED PLAINLY. A ZCTA code has exactly one prefix,
// but ZCTAs are built from census blocks, not from USPS delivery routes: a
// block is assigned the ZIP most of its addresses use, so a ZCTA's territory
// can hold people whose USPS ZIP has a different prefix, and a ZIP that forms
// no ZCTA contributes its people to a neighbour. The summed table therefore
// approximates, and does not equal, the "combined ZIP codes" unit the Safe
// Harbor rule names. Whether this approximation is the right Safe Harbor basis
// is a privacy-owner judgement (Joe), not something this module settles. The
// kernel's stricter-reading rule (HHS's 2000 list AND this table must both
// clear a prefix) stays in force either way.
//
// PURE. Lines in, frozen values out. No filesystem, network or clock; the
// caller supplies the lines (an array or an async iterable such as readline).

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5_J302_STATE_FIPS, zip3PopulationTableDigest } from "./tour-heat-map-privacy-j302.v5.js";

/** The only two sources this derivation is for (Joe's approval, 2026-09-25). */
export const V5_J302_CENSUS_SOURCES = Object.freeze({
  county_fips_2020: Object.freeze({
    url: "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt",
    publisher: "U.S. Census Bureau",
    dataset: "2020 national county reference file (FIPS codes)",
  }),
  dhc_zcta_population_2020: Object.freeze({
    url: "https://www2.census.gov/programs-surveys/decennial/2020/data/demographic-and-housing-characteristics-file/National/us2020.dhc.zip",
    publisher: "U.S. Census Bureau",
    dataset: "2020 Demographic and Housing Characteristics File, national summary file; P1_001N (total population) by ZCTA5",
    members: Object.freeze(["usgeo2020.dhc", "us000052020.dhc"]),
  }),
});

/** Published record layout of the 2020 DHC national summary file (0-based field indices). */
export const V5_J302_DHC_LAYOUT = Object.freeze({
  file_id: "DHCUS",
  geo: Object.freeze({ fields: 97, sumlev: 2, geocomp: 4, logrecno: 7, geoid: 8, zcta: 79, pop100: 90 }),
  segment5: Object.freeze({ fields: 204, cifsn_index: 3, cifsn: "05", logrecno: 4, p1_001n: 5 }),
  zcta_sumlev: "860",
  zcta_geocomp: "00",
  zcta_geoid_prefix: "860Z200US",
});
export const V5_J302_COUNTY_HEADER = Object.freeze(
  ["STATE", "STATEFP", "COUNTYFP", "COUNTYNS", "COUNTYNAME", "CLASSFP", "FUNCSTAT"]);
/** Anything far outside ~33,800 ZCTAs (50 states, DC and Puerto Rico) is not the national file. */
export const V5_J302_ZCTA_ROW_RANGE = Object.freeze({ min: 30000, max: 40000 });
export const V5_J302_COUNTY_ROW_RANGE = Object.freeze({ min: 3000, max: 4000 });

function fail(code, message) {
  const err = new Error(message);
  err.code = code;
  throw err;
}

function wholeNumber(value, code, label) {
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/.test(value)) {
    fail(code, `${label} ${JSON.stringify(value)} is not a whole number`);
  }
  const n = Number(value);
  if (!Number.isSafeInteger(n)) fail(code, `${label} overflows`);
  return n;
}

function splitRecord(line, expected, code, label) {
  if (typeof line !== "string") fail(code, `${label} is not text`);
  const f = line.replace(/\r$/, "").split("|");
  if (f.length !== expected) fail(code, `${label} has ${f.length} fields, expected ${expected}`);
  return f;
}

/**
 * Derive the 2020 ZIP3 population table from the DHC summary file's geographic
 * header lines and segment-5 lines. Returns { table, table_digest, zcta_count,
 * prefix_count, total_population }.
 */
export async function deriveZip3PopulationTable({ geoLines, segment5Lines }) {
  const L = V5_J302_DHC_LAYOUT;
  if (!geoLines || !segment5Lines) fail("census_input_missing", "both geoLines and segment5Lines are required");
  // 1. ZCTA rows from the geographic header: logrecno -> { zcta, pop100 }.
  const byLogrec = new Map();
  const seenZcta = new Set();
  let geoIndex = 0;
  for await (const line of geoLines) {
    geoIndex += 1;
    if (line === "") continue;
    const f = splitRecord(line, L.geo.fields, "census_geo_record_malformed", `geo record ${geoIndex}`);
    if (f[0] !== L.file_id) fail("census_geo_record_malformed", `geo record ${geoIndex} file id is ${f[0]}`);
    if (f[L.geo.sumlev] !== L.zcta_sumlev || f[L.geo.geocomp] !== L.zcta_geocomp) continue;
    const zcta = f[L.geo.zcta];
    if (!/^[0-9]{5}$/.test(zcta)) {
      fail("census_zcta_not_five_digits", `geo record ${geoIndex} ZCTA ${JSON.stringify(zcta)} is not five digits`);
    }
    if (f[L.geo.geoid] !== `${L.zcta_geoid_prefix}${zcta}`) {
      fail("census_geoid_mismatch", `geo record ${geoIndex} GEOID ${f[L.geo.geoid]} does not name ZCTA ${zcta}`);
    }
    if (seenZcta.has(zcta)) fail("census_zcta_duplicated", `ZCTA ${zcta} appears more than once`);
    seenZcta.add(zcta);
    if (zcta.startsWith("000")) fail("census_zcta_prefix_000", `ZCTA ${zcta} carries the reserved 000 prefix`);
    const logrec = f[L.geo.logrecno];
    if (!/^[0-9]{7}$/.test(logrec) || byLogrec.has(logrec)) {
      fail("census_logrecno_invalid", `geo record ${geoIndex} LOGRECNO ${logrec} is malformed or repeated`);
    }
    byLogrec.set(logrec, { zcta, pop100: wholeNumber(f[L.geo.pop100], "census_population_not_integer",
      `ZCTA ${zcta} POP100`), p1: null });
  }
  if (byLogrec.size < V5_J302_ZCTA_ROW_RANGE.min || byLogrec.size > V5_J302_ZCTA_ROW_RANGE.max) {
    fail("census_row_count_implausible", `geo header has ${byLogrec.size} ZCTA rows, outside the national range`);
  }
  // 2. P1_001N from segment 5, joined by LOGRECNO.
  let segIndex = 0;
  for await (const line of segment5Lines) {
    segIndex += 1;
    if (line === "") continue;
    const f = splitRecord(line, L.segment5.fields, "census_segment_record_malformed", `segment 5 record ${segIndex}`);
    if (f[0] !== L.file_id || f[L.segment5.cifsn_index] !== L.segment5.cifsn) {
      fail("census_segment_record_malformed", `segment 5 record ${segIndex} is not DHC segment 05`);
    }
    const row = byLogrec.get(f[L.segment5.logrecno]);
    if (!row) continue;
    if (row.p1 !== null) fail("census_logrecno_invalid", `segment 5 repeats LOGRECNO ${f[L.segment5.logrecno]}`);
    row.p1 = wholeNumber(f[L.segment5.p1_001n], "census_population_not_integer", `ZCTA ${row.zcta} P1_001N`);
  }
  // 3. Every ZCTA needs its P1_001N, and it must equal POP100.
  const sums = new Map();
  let total = 0;
  for (const { zcta, pop100, p1 } of byLogrec.values()) {
    if (p1 === null) fail("census_zcta_missing_p1", `ZCTA ${zcta} has no segment 5 record`);
    if (p1 !== pop100) fail("census_p1_pop100_mismatch", `ZCTA ${zcta} P1_001N ${p1} != POP100 ${pop100}`);
    const prefix = zcta.slice(0, 3);
    sums.set(prefix, (sums.get(prefix) ?? 0) + p1);
    total += p1;
  }
  const table = Object.freeze(Object.fromEntries([...sums.entries()].sort(([a], [b]) => (a < b ? -1 : 1))));
  return Object.freeze({
    table,
    table_digest: zip3PopulationTableDigest(table),
    zcta_count: byLogrec.size,
    prefix_count: sums.size,
    total_population: total,
  });
}

/**
 * Derive the county FIPS code list from the national county reference file.
 * A code whose state is outside the kernel's state list cannot be pinned (the
 * config reader refuses it), so it is reported in `excluded`, never pinned —
 * a county the kernel cannot bind stays denied.
 * Returns { codes, codes_digest, excluded, row_count }.
 */
export function deriveCountyFipsCodes(countyText) {
  if (typeof countyText !== "string") fail("county_input_not_text", "the county file must be text");
  const lines = countyText.replace(/^﻿/, "").split(/\r?\n/);
  while (lines.length && lines.at(-1) === "") lines.pop();
  if (lines.length < 1) fail("county_input_empty", "the county file is empty");
  const header = lines[0].split("|");
  if (canonicalJson(header) !== canonicalJson(V5_J302_COUNTY_HEADER)) {
    fail("county_header_unexpected", `county header is ${JSON.stringify(header)}`);
  }
  const data = lines.slice(1);
  if (data.length < V5_J302_COUNTY_ROW_RANGE.min || data.length > V5_J302_COUNTY_ROW_RANGE.max) {
    fail("county_row_count_implausible", `county file has ${data.length} rows, outside the national range`);
  }
  const seen = new Set();
  const excluded = [];
  data.forEach((line, i) => {
    const f = line.split("|");
    if (f.length !== V5_J302_COUNTY_HEADER.length) fail("county_row_malformed", `county row ${i + 1} has ${f.length} fields`);
    const [, statefp, countyfp] = f;
    if (!/^[0-9]{2}$/.test(statefp) || !/^[0-9]{3}$/.test(countyfp)) {
      fail("county_code_malformed", `county row ${i + 1} codes ${statefp}/${countyfp} are malformed`);
    }
    const code = statefp + countyfp;
    if (seen.has(code)) fail("county_code_duplicated", `county ${code} appears more than once`);
    seen.add(code);
    if (!V5_J302_STATE_FIPS.includes(statefp)) excluded.push(code);
  });
  const codes = [...seen].filter(c => !excluded.includes(c)).sort();
  return Object.freeze({
    codes: Object.freeze(codes),
    codes_digest: digest({ schema_version: "doctorcre-v5-j302-county-fips.v1", codes }),
    excluded: Object.freeze(excluded.sort()),
    row_count: data.length,
  });
}

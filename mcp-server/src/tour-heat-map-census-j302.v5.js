// DoctorCRE v5 slice V5-J302 — deterministic derivation of the two Census
// reference tables the heat-map privacy kernel pins in config.
//
// WHAT THIS PRODUCES. From the bytes of two public U.S. Census Bureau files:
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
// FAIL-CLOSED, NEVER REPAIRED. Anything this module cannot read with certainty
// throws; it never skips, trims or guesses a row, because a silently dropped
// ZCTA would lower a prefix's sum and a silently kept malformed one could raise
// it. Specifically:
//   - the API header must be exactly the two expected columns;
//   - every ZCTA must be five ASCII digits and appear once; a non-numeric code
//     (the 2000 Census "HH"/"XX" water and land pseudo-ZCTAs) throws;
//   - every population must be a non-negative decimal integer string;
//   - a ZCTA whose prefix is "000" throws, since 000 is Safe Harbor's own
//     suppression marker and no real ZIP3 carries it;
//   - the row count must be inside a plausible national range, so a truncated
//     or state-scoped download cannot be pinned by accident.
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
// PURE. Text in, frozen values out. No filesystem, network or clock.

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
    url: "https://api.census.gov/data/2020/dec/dhc?get=P1_001N&for=zip%20code%20tabulation%20area:*",
    publisher: "U.S. Census Bureau",
    dataset: "2020 Demographic and Housing Characteristics File, P1_001N (total population) by ZCTA5",
  }),
});

export const V5_J302_ZCTA_HEADER = Object.freeze(["P1_001N", "zip code tabulation area"]);
export const V5_J302_COUNTY_HEADER = Object.freeze(
  ["STATE", "STATEFP", "COUNTYFP", "COUNTYNS", "COUNTYNAME", "CLASSFP", "FUNCSTAT"]);
/** 2020 has 33,791 ZCTAs (50 states, DC and Puerto Rico); anything far off is not the national file. */
export const V5_J302_ZCTA_ROW_RANGE = Object.freeze({ min: 30000, max: 40000 });
export const V5_J302_COUNTY_ROW_RANGE = Object.freeze({ min: 3000, max: 4000 });

function fail(code, message) {
  const err = new Error(message);
  err.code = code;
  throw err;
}

/**
 * Derive the 2020 ZIP3 population table from the DHC API's JSON response text.
 * Returns { table, table_digest, zcta_count, prefix_count, total_population }.
 */
export function deriveZip3PopulationTable(dhcJsonText) {
  if (typeof dhcJsonText !== "string") fail("census_input_not_text", "the DHC response must be text");
  let rows;
  try { rows = JSON.parse(dhcJsonText); } catch { fail("census_input_not_json", "the DHC response is not JSON"); }
  if (!Array.isArray(rows) || rows.length < 1) fail("census_input_not_rows", "the DHC response is not a row array");
  const [header, ...data] = rows;
  if (canonicalJson(header) !== canonicalJson(V5_J302_ZCTA_HEADER)) {
    fail("census_header_unexpected", `DHC header is ${JSON.stringify(header)}, expected ${JSON.stringify(V5_J302_ZCTA_HEADER)}`);
  }
  if (data.length < V5_J302_ZCTA_ROW_RANGE.min || data.length > V5_J302_ZCTA_ROW_RANGE.max) {
    fail("census_row_count_implausible", `DHC response has ${data.length} ZCTA rows, outside the national range`);
  }
  const seen = new Set();
  const sums = new Map();
  let total = 0;
  data.forEach((row, i) => {
    if (!Array.isArray(row) || row.length !== 2) fail("census_row_malformed", `DHC row ${i + 1} is not two fields`);
    const [pop, zcta] = row;
    if (typeof zcta !== "string" || !/^[0-9]{5}$/.test(zcta)) {
      fail("census_zcta_not_five_digits", `DHC row ${i + 1} ZCTA ${JSON.stringify(zcta)} is not five digits`);
    }
    if (seen.has(zcta)) fail("census_zcta_duplicated", `ZCTA ${zcta} appears more than once`);
    seen.add(zcta);
    if (typeof pop !== "string" || !/^(0|[1-9][0-9]*)$/.test(pop)) {
      fail("census_population_not_integer", `ZCTA ${zcta} population ${JSON.stringify(pop)} is not a whole number`);
    }
    const n = Number(pop);
    if (!Number.isSafeInteger(n)) fail("census_population_not_integer", `ZCTA ${zcta} population overflows`);
    const prefix = zcta.slice(0, 3);
    if (prefix === "000") fail("census_zcta_prefix_000", `ZCTA ${zcta} carries the reserved 000 prefix`);
    sums.set(prefix, (sums.get(prefix) ?? 0) + n);
    total += n;
  });
  const table = Object.freeze(Object.fromEntries([...sums.entries()].sort(([a], [b]) => (a < b ? -1 : 1))));
  return Object.freeze({
    table,
    table_digest: zip3PopulationTableDigest(table),
    zcta_count: data.length,
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

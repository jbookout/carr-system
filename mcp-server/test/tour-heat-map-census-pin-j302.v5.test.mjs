// V5-J302 — opt-in rederivation of the pinned 2020 Census tables from the
// source bytes. Skipped unless CARR_J302_CENSUS_DIR names a directory holding:
//
//   national_county2020.txt   (as downloaded)
//   us2020.dhc.zip            (as downloaded; optional, hashed when present)
//   usgeo2020.dhc             (unzip -o us2020.dhc.zip usgeo2020.dhc us000052020.dhc)
//   us000052020.dhc
//
// URLs and sha256s are in tour-heat-map-census-2020-data-j302.v5.js. The test
// checks every source sha256, rederives both tables with the committed
// derivation, and requires them to equal the committed data and the pinned
// config digest. Run:
//
//   CARR_J302_CENSUS_DIR=/path/to/files node --test test/tour-heat-map-census-pin-j302.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createReadStream, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";

import { deriveCountyFipsCodes, deriveZip3PopulationTable } from "../src/tour-heat-map-census-j302.v5.js";
import {
  V5_J302_CENSUS_2020_PROVENANCE as P,
  V5_J302_CENSUS_2020_ZIP3_POPULATION,
  V5_J302_COUNTY_FIPS_2020_CODES,
} from "../src/tour-heat-map-census-2020-data-j302.v5.js";
import { V5_J302_PRIVACY_CONFIG } from "../src/tour-heat-map-privacy-config-j302.v5.js";

const DIR = process.env.CARR_J302_CENSUS_DIR;
const skip = DIR ? false : "set CARR_J302_CENSUS_DIR to rederive the pinned Census tables from source bytes";

async function sha256(path) {
  const h = createHash("sha256");
  for await (const chunk of createReadStream(path)) h.update(chunk);
  return h.digest("hex");
}
const lines = path => createInterface({ input: createReadStream(path, { encoding: "latin1" }), crlfDelay: Infinity });

test("PIN (opt-in): the source bytes match their sha256 and rederive the committed tables", { skip }, async () => {
  const county = join(DIR, "national_county2020.txt");
  assert.equal(await sha256(county), P.county_fips_2020.sha256);
  const zip = join(DIR, "us2020.dhc.zip");
  if (existsSync(zip)) assert.equal(await sha256(zip), P.dhc_zcta_population_2020.sha256);
  for (const [member, hex] of Object.entries(P.dhc_zcta_population_2020.members)) {
    assert.equal(await sha256(join(DIR, member)), hex, member);
  }
  const z = await deriveZip3PopulationTable({
    geoLines: lines(join(DIR, "usgeo2020.dhc")), segment5Lines: lines(join(DIR, "us000052020.dhc")) });
  assert.deepEqual({ ...z.table }, { ...V5_J302_CENSUS_2020_ZIP3_POPULATION });
  assert.equal(z.table_digest, V5_J302_PRIVACY_CONFIG.census_2020_zip3_population.table_digest);
  assert.equal(z.zcta_count, P.dhc_zcta_population_2020.zcta_count);
  assert.equal(z.total_population, P.dhc_zcta_population_2020.total_population);
  const c = deriveCountyFipsCodes(readFileSync(county, "latin1"));
  assert.deepEqual([...c.codes], [...V5_J302_COUNTY_FIPS_2020_CODES]);
  assert.equal(c.codes_digest, P.county_fips_2020.codes_digest);
  assert.deepEqual([...c.excluded], [...P.county_fips_2020.excluded_codes]);
  assert.equal(c.row_count, P.county_fips_2020.row_count);
});

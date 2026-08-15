// A JSON-string array must survive the choke point AS AN ARRAY.
//
// The production failure of 2026-08-15: coerceArgsToSchema required
// Array.isArray before it would touch an array argument, so a JSON string went
// through untouched. doctrine-sections then measured .length on ~80 characters
// and refused a batch of TWO as "batch_too_large, max 50"; a single id (~38
// characters) slipped under the limit and died casting a string to uuid[] as a
// bare "internal error". claim-doctrine-sections iterated the same string into
// characters, so no session could claim a doctrine section — and doctrine is
// single-writer by ORDER 38, so the write path was down for hours.
//
// ASSERTED AT THE CHOKE POINT, not through a handler. The 2026-08-13 ruling put
// coercion in one schema-driven place rather than in seventeen handlers, so that
// is where the fix lives and where it has to be proven. Testing it through one
// handler would pass while every other verb taking an array stayed broken.
import { coerceArgsToSchema } from "../src/tools.js";

const schema = { type: "object", properties: {
  section_ids: { type: "array", items: { type: "string" } } } };
const A = "d7eca7c6-95bc-4f40-8690-cb47ca62ac59";
const B = "f85501dd-3625-4f50-a8e0-fcb2cb81456f";

let failures = 0;
function check(name, ok, detail) {
  console.log((ok ? "  ok    " : "  FAIL  ") + name + (ok || !detail ? "" : " — " + detail));
  if (!ok) failures++;
}

const asString = { section_ids: JSON.stringify([A, B]) };
coerceArgsToSchema(schema, asString);
check("a two-id JSON string becomes 2 ids, not 80 characters",
      Array.isArray(asString.section_ids) && asString.section_ids.length === 2
      && asString.section_ids[0] === A,
      JSON.stringify(asString.section_ids).slice(0, 70));

const asArray = { section_ids: [A] };
coerceArgsToSchema(schema, asArray);
check("a plain array still passes through unchanged",
      Array.isArray(asArray.section_ids) && asArray.section_ids.length === 1);

const notJson = { section_ids: "not-json" };
coerceArgsToSchema(schema, notJson);
check("a non-JSON string is left alone for the handler to refuse by name",
      notJson.section_ids === "not-json");

// The control: an object argument is untouched by the array branch.
const other = { section_ids: [A, B], unrelated: "x" };
coerceArgsToSchema(schema, other);
check("an already-correct call is not disturbed", other.section_ids.length === 2);

process.exit(failures ? 1 : 0);

// THE AUDIT HELPER TAKES POSITIONAL ARGUMENTS, AND THE OBJECT FORM IS SILENT.
//
// `writeEvent` in src/tools.js is
//   writeEvent(client, actor, verb, subjectType, subjectId, fields = {})
// A caller who instead writes `writeEvent(c, { subject_type, subject_id, verb,
// payload })` passes an options object where the actor belongs. Nothing in
// JavaScript objects to it: the actor is a plain object with no `id`, and the
// verb, subject type and subject id all arrive undefined. The mistake surfaces
// only at the audit insert, as event.actor_id not_null_violation, which rolls
// back the whole enclosing transaction — so the verb's real work is lost too,
// and the caller sees a constraint message that names nothing they wrote.
//
// That shipped in all three work-portfolio.js write verbs and was found by the
// first live propose-portfolio-revision, the DoctorCRE v5 constitution itself,
// on 2026-09-12 (defect 65ed5e3e-db28-498a-a0e7-84ae53658dea). Five more sites
// in two v5 store modules carried the identical shape, unregistered at the time
// and therefore unable to fail in any test that existed.
//
// THIS IS THE CLASS GUARD, and it is deliberately a source scan rather than a
// behavioural test: the defect's whole character is that an unregistered verb
// cannot be driven, so the only check that catches the NEXT one before it is
// wired is one that reads every line of src/ whether or not anything calls it.
// The per-handler recording-double tests beside it prove the registered verbs
// pass the right values; this proves nobody anywhere has reintroduced the shape.
//
// MUTATION CONTROL, run 2026-09-12 before this file was committed: converting
// benchmark-acceptance-store.v5.js:959 back to the object form made this test
// fail and name that file and line; restoring it made it pass again.

import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../src", import.meta.url));

/** Every .js/.mjs file under src/, recursively. */
function sourceFiles(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) { found.push(...sourceFiles(path)); continue; }
    if (/\.(js|mjs)$/.test(entry)) found.push(path);
  }
  return found;
}

// The object form: any writeEvent call whose SECOND argument opens an object
// literal, on the same line or on the next one. The positional form's second
// argument is always an identifier (`actor`), so this cannot match it.
const OBJECT_FORM = /writeEvent\s*\(\s*[A-Za-z_$][\w$]*\s*,\s*\{/;

test("no writeEvent call in mcp-server/src uses the object form", () => {
  const files = sourceFiles(SRC);
  // A collector that found nothing would pass this test while proving nothing;
  // the count is asserted so an empty sweep fails instead.
  assert.ok(files.length > 50, `the source sweep found only ${files.length} files`);

  const hits = [];
  let calls = 0;
  for (const file of files) {
    const lines = readFileSync(file, "utf8").split("\n");
    for (const [index, line] of lines.entries()) {
      if (!line.includes("writeEvent(")) continue;
      calls += 1;
      // Joined with the next line so a call broken across lines still matches.
      const window = `${line}\n${lines[index + 1] ?? ""}`.replace(/\n/g, " ");
      if (OBJECT_FORM.test(window)) {
        hits.push(`${file.slice(SRC.length + 1)}:${index + 1}: ${line.trim()}`);
      }
    }
  }

  // The same guard against a silent pass: if the scanner stopped finding calls
  // at all, the zero below would be meaningless.
  assert.ok(calls > 50, `the sweep found only ${calls} writeEvent call sites`);
  assert.deepEqual(hits, [],
    `writeEvent takes (client, actor, verb, subjectType, subjectId, fields); ` +
    `these calls pass an options object as the actor:\n${hits.join("\n")}`);
});

test("the scanner actually matches the object form it is looking for", () => {
  // The mutation control, kept executable rather than only described above: the
  // pattern must reject the shape that shipped and accept the one that works.
  assert.ok(OBJECT_FORM.test('await writeEvent(c, { subject_type: "benchmark",'));
  assert.ok(OBJECT_FORM.test("await writeEvent(client, {"));
  assert.equal(
    OBJECT_FORM.test('await writeEvent(c, actor, "propose-portfolio-revision", "portfolio", id,'),
    false);
  assert.equal(OBJECT_FORM.test("await writeEvent(c, actor, verb, \"deal\", dealId, {"), false);
});

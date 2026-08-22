// verb-aliases.test.mjs — the verb rename audit (loop #150), shipped alias-first.
//
// THE AUDIT'S FINDING. Seven of the 45 verb names did not say what the verb
// does, judged against the rule that a name is plain human words matching the
// behaviour. The worst is `set-lead`, which sets a deal's lead AGENT but reads
// as though it creates a lead, and collides with `new-lead` in a list a human
// is scanning.
//
// WHY ALIAS-FIRST RATHER THAN A HARD RENAME. These names are referenced across
// skills, scheduled-job files, agent definitions and docs, and the record layer
// is served by a deployed Worker — so a hard rename is a deploy that strands
// every existing caller at the moment it ships, with `unknown_tool` as the only
// symptom. An alias delivers the whole point of the rename (a human reading the
// verb list sees a name that means something) at zero breakage, and it is
// reversible. The old names keep working and are retired later, deliberately,
// once nothing calls them.
//
// THE ALIASES ARE NOT SEPARATE TOOLS, on purpose. They resolve at dispatch and
// never enter TOOLS, so the verb COUNT is unchanged — which matters, because
// the deploy preflight refuses a drop in verb count and would otherwise read
// seven new entries as a surface change nobody asked for.
//
// Run with: node --test mcp-server/test/verb-aliases.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, VERB_ALIASES, resolveVerb } from "../src/tools.js";

const RENAMES = {
  "set-lead": "set-lead-agent",
  "record-counter": "record-counteroffer",
  "stamp-touch": "log-touch",
  "integrity-digest": "data-health-check",
  "lead-hot": "hot-leads",
  "source-attribution": "how-we-found-them",
  "promote-pool": "promote-candidate-to-lead",
};

test("every new name is a real registered verb", () => {
  for (const next of Object.values(RENAMES))
    assert.ok(TOOLS[next], `${next} is not registered`);
});

test("every OLD name still resolves — nothing that calls it breaks", () => {
  for (const [old, next] of Object.entries(RENAMES)) {
    const resolved = resolveVerb(old);
    assert.ok(resolved, `${old} no longer resolves`);
    assert.equal(resolved.name, next, `${old} should resolve to ${next}`);
    assert.equal(resolved.tool, TOOLS[next]);
    assert.equal(resolved.deprecated, true, `${old} should report as deprecated`);
  }
});

test("the old names are NOT registered as tools — the verb count must not move", () => {
  for (const old of Object.keys(RENAMES))
    assert.equal(TOOLS[old], undefined,
      `${old} must resolve by alias, not by occupying a second registry slot`);
});

test("a current name resolves to itself and is not flagged deprecated", () => {
  const r = resolveVerb("add-loop");
  assert.equal(r.name, "add-loop");
  assert.equal(r.deprecated, false);
});

test("an unknown name resolves to nothing rather than guessing", () => {
  assert.equal(resolveVerb("no-such-verb"), null);
  assert.equal(resolveVerb(""), null);
  assert.equal(resolveVerb(undefined), null);
});

test("the alias map and the rename table agree exactly", () => {
  assert.deepEqual({ ...VERB_ALIASES }, RENAMES);
});

test("no alias shadows a live verb name", () => {
  // An alias that also exists as a real verb would make dispatch depend on
  // lookup order, which is the kind of ambiguity a rename is supposed to remove.
  for (const old of Object.keys(VERB_ALIASES))
    assert.equal(TOOLS[old], undefined, `${old} is both an alias and a live verb`);
});

// ── THE REGRESSION THIS RENAME NEARLY SHIPPED ──────────────────────────────
// Both dispatch doors resolve the alias BEFORE the profile gate runs, which is
// the right order — otherwise an old name would be a way around a scoped
// session's allow-list. But the scoped profiles listed the OLD names, so the
// moment the registry keys moved, `capture`, `away` and `hermes` sessions lost
// stamp-touch and record-counter entirely: resolved to the new name, checked
// against a list that only held the old one, refused as not_in_profile. Caught
// by grepping for the old strings after the rename rather than by any test,
// which is why this test now exists.
import { PROFILES } from "../src/mcp.js";

test("scoped profiles carry the NEW names, so no profile silently loses a verb", () => {
  const expected = {
    capture: ["log-touch"],
    away: ["log-touch", "record-counteroffer"],
    hermes: ["log-touch"],
  };
  for (const [profile, verbs] of Object.entries(expected)) {
    const set = PROFILES[profile];
    assert.ok(set, `profile ${profile} is missing`);
    for (const v of verbs)
      assert.ok(set.has(v), `profile ${profile} lost ${v} in the rename`);
  }
});

test("no scoped profile still lists a retired name", () => {
  for (const [profile, set] of Object.entries(PROFILES)) {
    if (!(set instanceof Set)) continue;
    for (const old of Object.keys(VERB_ALIASES))
      assert.equal(set.has(old), false,
        `profile ${profile} still lists the retired name ${old}, which resolves to ` +
        `something else before the gate sees it`);
  }
});

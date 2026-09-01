// WR-000019 slice S11 (boot diet). Two things standing-context gains here,
// neither of which may touch what a session actually receives while
// ops.rule_delivery_policy stays "shadow":
//
//   1. A shadow-mode `core_preview` measuring the REAL payload an enforced
//      boot would send once slice S13 flips the policy row -- CORE rules
//      (rule-triage.v1.json's `home: "core"` set) in full text, plus the
//      pack/trigger index.
//   2. In the (still unreachable in production) enforced branch, a CORE rule
//      arrives in full text instead of a gist. Non-core rules are unchanged.
//
// "1fddcffb" and "4a53ff82" are deliberately the SAME short ids the existing
// rule-delivery-layers.test.mjs fixture already uses for two Layer 0 rules --
// both are real ids in the S7 triage's core set (ops/config/rule-triage.v1.json),
// so this file can exercise the real CORE_RULE_IDS module (mcp-server/src/
// core-rule-ids.js) rather than a stand-in.
import { test } from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool } from "../src/tools.js";
import { CORE_RULE_IDS } from "../src/core-rule-ids.js";

const ACTOR = { id: "11111111-1111-4111-8111-111111111111", slug: "joe",
                human: true, via: "oauth-google" };

const SHARED = ["1fddcffb", "347a9ca6", "424ba0cc", "4a53ff82"];
const PERSONAL = ["7e9739f2", "57d13061"];

function rows(ids, personal) {
  return ids.map((id, i) => ({
    id: `${id}-0000-4000-8000-${String(i).padStart(12, "0")}`,
    statement: `A statement long enough to produce a real gist for rule ${id}, with more words after it.`,
    human_quote: `quote ${id}`, taught_by: "Joe Bookout",
    personal_to: personal ? "joe" : null, scope: {},
  }));
}

const PLAN = [
  { short_id: "1fddcffb", load_layer: "layer0", packs: [], scope: "shared" },
  { short_id: "347a9ca6", load_layer: "layer0", packs: [], scope: "shared" },
  { short_id: "424ba0cc", load_layer: "pack", packs: ["client-deal"], scope: "shared" },
  { short_id: "4a53ff82", load_layer: "control", packs: ["engineering-git"], scope: "shared" },
  { short_id: "7e9739f2", load_layer: "layer0", packs: [], scope: "joe" },
  { short_id: "57d13061", load_layer: "pack", packs: ["joe-comms"], scope: "joe" },
];

const PACK_INDEX = [
  { pack: "client-deal", title: "Clients, prospects, LOIs and deals",
    triggers: ["deal", "loi", "client"], rule_count: 1 },
  { pack: "engineering-git", title: "Repo, git, gates, migrations and deploys",
    triggers: ["git", "worktree", "ci"], rule_count: 1 },
  { pack: "joe-comms", title: "Joe's X, mail, calendar and social lanes",
    triggers: ["x.com", "mail"], rule_count: 1 },
];

function client({ mode = "shadow", plan = PLAN, packIndex = PACK_INDEX } = {}) {
  return {
    query: async (sql, params = []) => {
      if (/from v_compiled_rules/i.test(sql))
        return { rows: [...rows(SHARED, false), ...rows(PERSONAL, true)] };
      if (/v_guidance_registry_state/i.test(sql)) return { rows: [] };
      if (/with registry as/i.test(sql) && /plan\.rows as delivery_plan/i.test(sql)) {
        const declared = params[1] || [];
        const deliveryPlan = plan
          .filter(r => r.scope === "shared" || (params[0] && r.scope === params[0]))
          .map(r => ({ ...r, selected: r.load_layer === "layer0"
                       || r.packs.some(p => declared.includes(p)) }));
        return { rows: [{ mode, map_versions: 1,
          map_digest: "b513180786cf7212877870ab3bc14c03bb78b17b3397eb6ee474187a152b13f2",
          tagged_rules: plan.length, delivery_plan: deliveryPlan, pack_index: packIndex }] };
      }
      if (/from ops\.rule_delivery_policy/i.test(sql))
        return { rows: mode ? [{ mode }] : [] };
      if (/ops\.rule_delivery_plan/i.test(sql)) {
        const declared = params[1] || [];
        return { rows: plan
          .filter(r => r.scope === "shared" || (params[0] && r.scope === params[0]))
          .map(r => ({ ...r, selected: r.load_layer === "layer0"
                       || r.packs.some(p => declared.includes(p)) })) };
      }
      if (/ops\.rule_pack_index/i.test(sql)) return { rows: packIndex };
      if (/from doctrine_meta/i.test(sql)) return { rows: [{ generation: 7 }] };
      if (/from actor/i.test(sql)) return { rows: [{ id: ACTOR.id }] };
      return { rows: [] };
    },
  };
}

const call = (c, args = {}) => executeRegisteredTool(c, ACTOR, "standing-context", args);

test("1fddcffb and 4a53ff82 really are CORE ids in the shipped triage", () => {
  // Sanity check on the fixture itself, not the verb: if the S7 triage ever
  // drops either id from `home: "core"`, this whole file stops testing what
  // it claims to and should fail loudly here rather than pass for the wrong
  // reason everywhere else.
  assert.ok(CORE_RULE_IDS.includes("1fddcffb"));
  assert.ok(CORE_RULE_IDS.includes("4a53ff82"));
  assert.equal(CORE_RULE_IDS.includes("347a9ca6"), false,
    "347a9ca6 must stay a non-core comparison point for this file to mean anything");
});

test("enforced mode delivers a CORE Layer 0 rule in full text, not a gist", async () => {
  const out = await call(client({ mode: "enforced" }));
  const core = out.shared_rules.find(r => r.id === "1fddcffb");
  assert.ok(core, "the core rule must still be in the Layer 0 boot");
  assert.ok(core.statement, "a core rule must arrive as full text");
  assert.equal(core.gist, undefined, "a core rule must not be shaped as a gist");
  assert.equal(core.taught_by, "Joe Bookout");
});

test("a core rule outside Layer 0 still gets full text once its pack is selected", async () => {
  const out = await call(client({ mode: "enforced" }), { packs: ["engineering-git"] });
  const core = out.shared_rules.find(r => r.id === "4a53ff82");
  assert.ok(core && core.statement, "4a53ff82 is core and must be full text once delivered");
  assert.equal(core.gist, undefined);
});

test("a non-core rule stays a gist under enforced mode, core or not notwithstanding", async () => {
  const bare = await call(client({ mode: "enforced" }));
  const noncore = bare.shared_rules.find(r => r.id === "347a9ca6");
  assert.ok(noncore && noncore.gist, "the non-core Layer 0 rule must still be a gist");
  assert.equal(noncore.statement, undefined,
    "the boot diet must not spend full text on a rule the triage never called core");

  const withPack = await call(client({ mode: "enforced" }), { packs: ["client-deal"] });
  const packRule = withPack.shared_rules.find(r => r.id === "424ba0cc");
  assert.ok(packRule && packRule.gist, "a selected non-core pack rule stays a gist too");
});

test("detail=full and an explicit rule_ids lookup still override a non-core gist", async () => {
  const full = await call(client({ mode: "enforced" }), { detail: "full" });
  assert.ok(full.shared_rules.find(r => r.id === "347a9ca6").statement,
    "detail=full must still be unscoped, core or not");
  const looked = await call(client({ mode: "enforced" }), { rule_ids: ["424ba0cc"] });
  assert.ok(looked.shared_rules.find(r => r.id === "424ba0cc").statement,
    "an explicit rule_ids lookup is never scoped away, core or not");
});

test("shadow mode carries a core_preview measuring the REAL future payload", async () => {
  const out = await call(client({ mode: "shadow" }));
  assert.ok(out.core_preview, "shadow mode must add the preview");
  // The whole point: shadow changes NOTHING delivered.
  assert.equal(out.rule_delivery.enforcing, false);
  assert.equal(out.shared_rules.find(r => r.id === "1fddcffb").gist !== undefined, true,
    "the actual delivery in shadow mode must still be the full gist recitation");

  const previewIds = out.core_preview.core_rules.map(r => r.id);
  assert.ok(previewIds.includes("1fddcffb"));
  assert.ok(previewIds.includes("4a53ff82"));
  assert.equal(previewIds.includes("347a9ca6"), false,
    "a non-core rule must never appear in the core preview");
  for (const r of out.core_preview.core_rules)
    assert.ok(r.statement, "every preview rule must be full text, not a gist");

  assert.equal(out.core_preview.core_rule_count, CORE_RULE_IDS.length);
  assert.ok(out.core_preview.pack_index.length > 0);
  assert.ok(out.core_preview.measured.core_preview_tokens_est > 0);
  assert.ok(out.core_preview.measured.current_recitation_tokens_est > 0);
});

test("core_preview is a shadow-only field", async () => {
  const enforced = await call(client({ mode: "enforced" }));
  assert.equal(enforced.core_preview, undefined,
    "the enforced branch delivers core-full-text directly; it has no separate preview");

  const noTags = await call(client({ mode: null }));
  assert.equal(noTags.core_preview, undefined,
    "a worker running ahead of the migration must stay exactly as it was before this slice");

  const fallback = await call(client({ mode: "enforced", plan: [] }));
  assert.equal(fallback.core_preview, undefined,
    "an unusable enforced plan already has its own fallback story; no preview is bolted on");
});

test("a stale triage id absent from the rule table is reported, not silently dropped", async () => {
  // Simulate the real triage carrying an id retired from the rule table since:
  // the preview must say so rather than just under-count with no explanation.
  const c = client({ mode: "shadow" });
  const out = await call(c);
  const anyMissing = out.core_preview.missing_core_ids;
  // In THIS fixture every real core id present in the live triage is either
  // absent from the tiny row set (expected -- only two of twenty are stocked)
  // so most core ids are legitimately "missing" here; assert the field exists
  // and is shaped as documented rather than asserting a specific count, which
  // depends on how many real core ids this fixture happens to stock.
  assert.ok(Array.isArray(anyMissing));
  assert.ok(out.core_preview.core_rules_found + anyMissing.length === CORE_RULE_IDS.length);
});

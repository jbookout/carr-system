// V5-F05 planted-bug mutants for the context manifest's `decision`.
//
// The hazard: a manifest assembled for read-only exploration under uncertainty
// used to read `decision: "allow"` while its consequential write was blocked.
// The one admission consumer reads `write_gate_field` and was safe; a future
// consumer testing `decision === "allow"`, the idiom used elsewhere in this
// codebase, would have read a read-only manifest as permission. The coverage
// receipt had the same hazard and was closed the same way (#1313).
//
// Each mutant is KILLED the honest way, as in rule-applicability-decision-
// mutants: the same behavioural expectation is run against the real module,
// where it must pass, and against the mutated module, where it must fail.
// Source anchors are exact and unique, so a refactor that turns a mutant into
// a no-op fails here instead of reporting a meaningless green run.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_F05_UNIVERSE_SCHEMA_VERSION,
  compileRuleUniverse,
} from "../src/rule-applicability.v5.js";
import * as real from "../src/context-assembly.v5.js";

const SOURCE = new URL("../src/context-assembly.v5.js", import.meta.url);
const SOURCE_DIR = fileURLToPath(new URL("../src/", import.meta.url));
const WORK = mkdtempSync(join(tmpdir(), "v5-f05-manifest-decision-mutants-"));

function mutate(id, anchor, replacement) {
  const source = readFileSync(SOURCE, "utf8");
  assert.equal(source.split(anchor).length - 1, 1,
    `mutant ${id}: anchor must occur exactly once`);
  const path = join(WORK, `${id}-${Date.now()}.mjs`);
  const relinked = source.replace(anchor, replacement).replace(
    /from "\.\/([^"]+)"/g,
    (_, file) => `from "${pathToFileURL(join(SOURCE_DIR, file)).href}"`,
  );
  writeFileSync(path, relinked);
  return import(`${pathToFileURL(path).href}?mutant=${id}-${Date.now()}`);
}

// --------------------------------------------------------------- fixture
// The smallest request that assembles cleanly: one mandatory workflow rule
// whose provenance resolves to a rule record the manifest carries.

const NOW = "2026-09-26T12:00:00Z";
const OBSERVED = "2026-09-26T11:45:00Z";
const sha = ch => `sha256:${ch.repeat(64)}`;

const partner = slug => ({
  slug, display: slug, human: true, via: "oauth-google",
  client_id: null, sponsoring_human_slug: null, human_slug: null, sponsor_required: false,
});
const SPONSORED_AGENT = {
  slug: "claude", display: "Claude", human: false, via: "oauth-google",
  client_id: "c1", sponsoring_human_slug: "joe", human_slug: "joe", sponsor_required: true,
};

const universe = () => compileRuleUniverse({
  schema_version: V5_F05_UNIVERSE_SCHEMA_VERSION, universe_version: 1,
  tenant: ORGANIZATION_TENANT_ID, completeness: "complete_authoritative_universe",
  declared_actions: ["document.send"], declared_resource_classes: ["document"],
  rules: [{
    rule_id: "client-send-gate", version: 1, rule_class: "workflow", scope: "shared",
    owner: "joe", mandatory: true,
    trigger: { action: ["document.send"], audience: ["client"] },
    control_effect: { control_key: "client_send", effect: "require" },
    binding_text: "A client-facing document is reviewed by a second seat before it is sent.",
    tests: ["check:client-send-review"],
    retirement: { behavior: "permanent_until_superseded" },
    provenance: { source_record_id: "r-rule-send-gate", source_version: 1,
      source_content_digest: sha("d"), retrieved_at: OBSERVED },
  }],
});

const FACTS = {
  action: "document.send", actor_class: "verified_partner", audience: "client",
  environment: "production", lifecycle_transition: "send", resource_class: "document",
  risk_tier: "consequential",
};

function request({ mode = "consequential_action_proposal", actor = partner("joe"),
  dropAudience = false } = {}) {
  const facts = { ...FACTS };
  if (dropAudience) delete facts.audience;
  return {
    schema_version: real.V5_F05_MANIFEST_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID, now: NOW, mode, actor,
    task: { task_id: "t-1", title: "Send the LOI", boundary_action: "business.send_client_document",
      facts },
    controls: { deal_owner_slug: "joe", account_slug: "joe",
      policy_scope: ["business.send_client_document"], capabilities: ["document.send"] },
    universe: universe(),
    records: [
      { record_id: "r-deal", record_kind: "operating_fact", version: 1, content_digest: sha("3"),
        origin: "record_layer", derived_kind: "primary", derived_from: [], query_id: "q-deal",
        observed_at: OBSERVED, estimated_tokens: 10, omissible: false, backs_control: true,
        provenance: { source_id: "src-neon", retrieval_class: "typed_read" } },
      { record_id: "r-rule-send-gate", record_kind: "rule", version: 1, content_digest: sha("d"),
        origin: "record_layer", derived_kind: "primary", derived_from: [], query_id: "q-deal",
        observed_at: OBSERVED, estimated_tokens: 5, omissible: false, backs_control: true,
        provenance: { source_id: "src-neon", retrieval_class: "typed_read" } },
    ],
    sources: [{ source_id: "src-neon", state: "available", required_for_task: true }],
    queries: [{ query_id: "q-deal", query_kind: "deal_by_id", parameters_digest: sha("7"),
      retrieved_at: OBSERVED }],
  };
}

const assemble = (module, options) =>
  module.assembleContextManifest(module.freezeAssemblyInput(request(options)));

const reDigest = (manifest, changes) => {
  const { manifest_digest, effects, ...rest } = manifest;
  const body = { ...rest, ...changes };
  return { ...body, manifest_digest: digest(body), effects };
};

const EXPECTATIONS = {
  // Main suite: "manifest `decision` agrees with the write gate on every manifest".
  readOnlyNeverReadsAllow(module) {
    const explore = assemble(module, { mode: "read_only_exploration", dropAudience: true });
    assert.equal(explore.consequential_action_permitted, false);
    assert.equal(explore.read_only_exploration_permitted, true);
    assert.equal(explore.decision, "read_only");
    module.verifyContextManifest(explore);
  },
  permittedReadsAllow(module) {
    for (const mode of ["consequential_action_proposal", "read_only_exploration"]) {
      const manifest = assemble(module, { mode });
      assert.equal(manifest.consequential_action_permitted, true, mode);
      assert.equal(manifest.decision, "allow", mode);
      module.verifyContextManifest(manifest);
    }
  },
  refusePathUnchanged(module) {
    const blockedWrite = assemble(module, { dropAudience: true });
    assert.equal(blockedWrite.decision, "refuse");
    assert.equal(blockedWrite.reason_id, "typed_facts_unknown");
    for (const mode of ["consequential_action_proposal", "read_only_exploration"]) {
      const refused = assemble(module, { mode, actor: SPONSORED_AGENT });
      assert.equal(refused.decision, "refuse", mode);
      assert.equal(refused.reason_id, "actor_not_verified_partner", mode);
      assert.equal(refused.read_only_exploration_permitted, false, mode);
    }
  },
  // Main suite: "a re-digested manifest whose `decision` contradicts its gates is refused".
  forgedDecisionRefused(module) {
    const explore = assemble(module, { mode: "read_only_exploration", dropAudience: true });
    const permitted = assemble(module);
    for (const forged of [reDigest(explore, { decision: "allow" }),
      reDigest(permitted, { decision: "read_only" })]) {
      assert.throws(() => module.verifyContextManifest(forged),
        error => error?.code === "manifest_decision_inconsistent");
    }
  },
};

async function assertKilled(mutant, expectation) {
  await EXPECTATIONS[expectation](real);
  await assert.rejects(async () => EXPECTATIONS[expectation](mutant),
    `expectation ${expectation} must fail on the mutant, or the mutant survives`);
}

test("M1 killed: the old two-valued manifest decision reads allow on read-only exploration",
  async () => {
    const mutant = await mutate("M1-allow-on-read-only",
      "  const decision = manifestDecision({ mode, consequential_action_permitted, read_only_exploration_permitted });",
      "  const decision = hardRefusals.length > 0 || (mode === \"consequential_action_proposal\" && !consequential_action_permitted) ? \"refuse\" : \"allow\";");
    await assertKilled(mutant, "readOnlyNeverReadsAllow");
  });

test("M2 killed: a permitted manifest reads read_only", async () => {
  const mutant = await mutate("M2-read-only-on-permitted",
    "  return coverageDecision({ consequential_action_permitted, read_only_exploration_permitted });",
    "  return read_only_exploration_permitted === true ? \"read_only\" : \"refuse\";");
  await assertKilled(mutant, "permittedReadsAllow");
});

test("M3a killed: a blocked consequential proposal stops refusing", async () => {
  const mutant = await mutate("M3a-blocked-write-reads-read-only",
    "  if (mode === \"consequential_action_proposal\" && consequential_action_permitted !== true) return \"refuse\";",
    "  if (mode === \"consequential_action_proposal\" && consequential_action_permitted !== true) return \"read_only\";");
  await assertKilled(mutant, "refusePathUnchanged");
});

test("M3b killed: a hard refusal no longer refuses read-only exploration", async () => {
  const mutant = await mutate("M3b-hard-refusal-reads-read-only",
    "  const read_only_exploration_permitted = hardRefusals.length === 0;",
    "  const read_only_exploration_permitted = true;");
  await assertKilled(mutant, "refusePathUnchanged");
});

test("M4 killed: the verifier stops checking decision against the gates", async () => {
  const mutant = await mutate("M4-verifier-skips-decision",
    "  if (manifest.decision !== implied) {",
    "  if (false) {");
  await assertKilled(mutant, "forgedDecisionRefused");
});

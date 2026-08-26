// identity.test.mjs — unit coverage for the identity → actor mapping (loop
// #227's outside-model attribution work). Pure-function tests only: no KV, no
// Neon, no Google, no Worker runtime. Run with:
//
//   node --test mcp-server/test/identity.test.mjs
//
// This exercises exactly the functions google-oidc.js's handleCallback calls
// at /callback time (slugForEmail, agentSlugForClient, propsForSlug,
// actorFromProps) with fixture inputs, so the attribution logic is provable
// before anything is deployed — the Worker's own auth flow needs a live
// Google round-trip and a real OAuth client registration to exercise for
// real, neither of which this branch can do without a deploy.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  slugForEmail,
  agentSlugForClient,
  verifiedAgentSlugForClient,
  propsForSlug,
  actorFromProps,
  isKnownActor,
  isKnownPartner,
  agentActorForToken,
} from "../src/identity.js";

// ---------- slugForEmail: unchanged behavior, regression-guarded ----------

test("slugForEmail: allow-listed identities resolve, case/space tolerant", () => {
  assert.equal(slugForEmail("joe.bookout.carr.us@gmail.com"), "joe");
  assert.equal(slugForEmail("  Joe.Bookout.CARR.us@GMAIL.com  "), "joe");
  assert.equal(slugForEmail("dell.mccraney.carr.us@gmail.com"), "dell");
});

test("slugForEmail: anything not on the list is refused, not guessed", () => {
  assert.equal(slugForEmail("someone.else@gmail.com"), null);
  assert.equal(slugForEmail("codex@openai.com"), null);
  assert.equal(slugForEmail(""), null);
  assert.equal(slugForEmail(undefined), null);
  assert.equal(slugForEmail(123), null);
});

// ---------- agentSlugForClient: the new attribution override ----------

test("agentSlugForClient: Codex CLI's client_name maps to the codex actor", () => {
  assert.equal(agentSlugForClient("Codex"), "codex");
});

test("agentSlugForClient: exact match is case- and space-tolerant, same discipline as slugForEmail", () => {
  assert.equal(agentSlugForClient("codex"), "codex");
  assert.equal(agentSlugForClient("CODEX"), "codex");
  assert.equal(agentSlugForClient("  Codex  "), "codex");
});

test("agentSlugForClient: grok is NOT yet mapped — the real client_name was never confirmed live, so it must not silently match", () => {
  assert.equal(agentSlugForClient("Grok"), null);
  assert.equal(agentSlugForClient("Grok Build"), null);
  assert.equal(agentSlugForClient("grok-build-cli"), null);
});

test("agentSlugForClient: no fuzzy or substring matching — a name that merely contains 'codex' must not match", () => {
  assert.equal(agentSlugForClient("My Codex Wrapper"), null);
  assert.equal(agentSlugForClient("Codexy"), null);
  assert.equal(agentSlugForClient("NotCodex"), null);
});

test("agentSlugForClient: approved Claude Code resolves to its runtime actor", () => {
  assert.equal(agentSlugForClient("Claude Code"), "claude");
  assert.equal(agentSlugForClient("Claude"), null);
});

test("agentSlugForClient: missing/unusual input degrades to null, never throws", () => {
  assert.equal(agentSlugForClient(undefined), null);
  assert.equal(agentSlugForClient(null), null);
  assert.equal(agentSlugForClient(""), null);
  assert.equal(agentSlugForClient(42), null);
});

test("self-declared client_name is attribution only; exact server client-id binding is authority", () => {
  const bindings = JSON.stringify({ "client-codex-verified": "codex" });
  assert.equal(verifiedAgentSlugForClient("client-codex-verified", "codex", bindings), "codex");
  assert.equal(verifiedAgentSlugForClient("attacker-dynamic-client", "codex", bindings), null);
  assert.equal(verifiedAgentSlugForClient("client-codex-verified", "claude", bindings), null);
  assert.equal(verifiedAgentSlugForClient("client-codex-verified", "codex", "not-json"), null);
});

// ---------- isKnownActor / propsForSlug: codex/grok are now real actors ----------

test("isKnownActor: codex and grok are recognized actor slugs", () => {
  assert.equal(isKnownActor("codex"), true);
  assert.equal(isKnownActor("grok"), true);
  assert.equal(isKnownActor("joe"), true);
  assert.equal(isKnownActor("dell"), true);
});

test("isKnownActor: an unrelated slug is still refused", () => {
  assert.equal(isKnownActor("chatgpt"), false);
  assert.equal(isKnownActor("codex-reviewer"), false); // a different lane entirely (REVIEW_TOKENS)
});

test("propsForSlug: builds valid props for an outside-model actor", () => {
  const props = propsForSlug("codex", { email: "joe.bookout.carr.us@gmail.com", human: false, human_slug: "joe" });
  assert.equal(props.slug, "codex");
  assert.equal(props.display, "Codex");
  assert.equal(props.human, false);
  assert.equal(props.human_slug, "joe");
});

test("propsForSlug: still refuses to build props for a genuinely unknown actor", () => {
  assert.throws(() => propsForSlug("chatgpt"), /unknown actor/);
});

// ---------- actorFromProps: what dispatch() actually sees ----------

test("actorFromProps: an ordinary joe session is unaffected by this loop's changes", () => {
  const actor = actorFromProps({ slug: "joe", email: "joe.bookout.carr.us@gmail.com",
                                  via: "oauth-google", client_id: "abc123" });
  assert.equal(actor.slug, "joe");
  assert.equal(actor.display, "Joe");
  assert.equal(actor.human, true);
  assert.equal(actor.human_slug, null); // no override applied — the actor already IS the human
});

test("actorFromProps: an agent-overridden session attributes to codex, keeps the human on record", () => {
  const actor = actorFromProps({
    slug: "codex", human: false, human_slug: "joe",
    via: "oauth-google", client_id: "client-abc",
  });
  assert.equal(actor.slug, "codex");
  assert.equal(actor.display, "Codex");
  assert.equal(actor.human, false);
  assert.equal(actor.human_slug, "joe");
  assert.equal(actor.client_id, "client-abc");
});

test("actorFromProps: fails closed on missing or unrecognized props", () => {
  assert.equal(actorFromProps(null), null);
  assert.equal(actorFromProps({ slug: "chatgpt" }), null);
  assert.equal(actorFromProps({}), null);
});

// ---------- end-to-end simulation of google-oidc.js's handleCallback decision ----------
//
// The exact decision handleCallback makes (agentSlug || humanSlug, with the
// human/human_slug props only added on an override) reproduced here as a pure
// function of fixture inputs, so the whole derivation is provable without a
// live OAuth round-trip.

function deriveWriteActor(humanSlug, clientName) {
  const agentSlug = agentSlugForClient(clientName);
  const slug = agentSlug || humanSlug;
  const extra = { via: "oauth-google" };
  if (agentSlug) {
    extra.human = false;
    extra.human_slug = humanSlug;
  }
  return actorFromProps(propsForSlug(slug, extra));
}

test("derivation: Joe in Claude Code writes as Claude and preserves Joe as sponsor", () => {
  const actor = deriveWriteActor("joe", "Claude Code");
  assert.equal(actor.slug, "claude");
  assert.equal(actor.human, false);
  assert.equal(actor.human_slug, "joe");
});

test("derivation: Joe in Codex CLI writes as codex, not joe", () => {
  const actor = deriveWriteActor("joe", "Codex");
  assert.equal(actor.slug, "codex");
  assert.equal(actor.human, false);
  assert.equal(actor.human_slug, "joe");
});

test("derivation: Dell in Codex CLI writes as codex, human_slug records dell", () => {
  const actor = deriveWriteActor("dell", "Codex");
  assert.equal(actor.slug, "codex");
  assert.equal(actor.human_slug, "dell");
});

test("derivation: Joe in Grok Build CLI still writes as joe today (grok not yet confirmed) — documents the known gap, not a bug", () => {
  const actor = deriveWriteActor("joe", "Grok Build");
  assert.equal(actor.slug, "joe");
  assert.equal(actor.human, true);
  assert.equal(actor.human_slug, null);
});

test("derivation: a missing/unregistered client_name degrades to the human, never throws", () => {
  const actor = deriveWriteActor("joe", undefined);
  assert.equal(actor.slug, "joe");
});

// ---------- agentActorForToken: the AGENT_TOKENS door (loop #227/#239) ----------
// Grok cannot authenticate over OAuth at all (no per-server login command, no
// dynamic client registration, TUI reports "not authenticated" — verified live
// three ways 2026-08-09), so it reaches /mcp with a bearer instead. These tests
// pin the two properties that make that safe: the actor is the TOOL's own, and
// human is false, which is what makes mcp.js's humanOnly gate refuse teach /
// retire-rule / confirm-merge / new-deal / reassign-deal by construction rather
// than by a list anyone has to maintain.

const AGENT_TOKENS = JSON.stringify({ grok: "grok-secret-fixture", codex: "codex-secret-fixture" });

test("agent token resolves to the tool's own actor, never a human", () => {
  const actor = agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS);
  assert.deepEqual(actor, {
    slug: "grok", display: "Agent (grok)", human: false, agent: true,
    via: "agent-token", client_id: null, sponsoring_human_slug: null,
    human_slug: null, sponsor_required: false,
  });
  // This generic agent remains non-human and unsponsored.
  assert.equal(actor.human, false);
  // And it must NOT carry probe/review, which would pin it to a narrow profile.
  assert.equal(actor.probe, undefined);
  assert.equal(actor.review, undefined);
});

test("agent token matches per-slug, so one tool's secret is not another's", () => {
  assert.equal(agentActorForToken("Bearer codex-secret-fixture", AGENT_TOKENS).slug, "codex");
  assert.equal(agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS).slug, "grok");
});

test("agent token accepts the bare token as well as the Bearer form", () => {
  assert.equal(agentActorForToken("grok-secret-fixture", AGENT_TOKENS).slug, "grok");
});

test("agent token fails closed on every bad input", () => {
  for (const [label, header, raw] of [
    ["no header", "", AGENT_TOKENS],
    ["null header", null, AGENT_TOKENS],
    ["unknown token", "Bearer nope", AGENT_TOKENS],
    ["empty map", "Bearer grok-secret-fixture", "{}"],
    ["absent secret", "Bearer grok-secret-fixture", undefined],
    ["unparseable json", "Bearer grok-secret-fixture", "{not json"],
    ["json that is not an object", "Bearer grok-secret-fixture", '"a string"'],
  ]) {
    assert.equal(agentActorForToken(header, raw), null, label);
  }
});

test("agent token refuses a slug that is not a known actor", () => {
  // A typo'd or renamed map key must not mint an identity: DISPLAY is the stop.
  const rogue = JSON.stringify({ "grok-typo": "s3cret", attacker: "s3cret2" });
  assert.equal(agentActorForToken("Bearer s3cret", rogue), null);
  assert.equal(agentActorForToken("Bearer s3cret2", rogue), null);
});

test("an empty-string secret in the map never authenticates", () => {
  // Guards the shape where a slug is provisioned but its secret was never set.
  const blank = JSON.stringify({ grok: "" });
  assert.equal(agentActorForToken("Bearer ", blank), null);
  assert.equal(agentActorForToken("Bearer anything", blank), null);
});

// ---------------------------------------------------------------------------
// LOCAL_TOKENS / joe-local (Phase 1, 2026-08-13, decision 97e76a2f). Closes
// the direct-database bypass in mcp-server/local-verb.mjs: that file is now a
// thin HTTPS client of this Worker, carrying a LOCAL_TOKENS bearer that
// resolves through this SAME function (extended, not duplicated) rather than
// a new profile-matching code path.
// ---------------------------------------------------------------------------

const LOCAL_TOKENS = JSON.stringify({ "joe-local": "local-secret-fixture" });

test("local token resolves to joe-local, human:false, sponsored to joe", () => {
  const actor = agentActorForToken("Bearer local-secret-fixture", LOCAL_TOKENS, "local-token");
  assert.deepEqual(actor, {
    slug: "joe-local", display: "Agent (joe-local)", human: false, agent: true,
    via: "local-token", client_id: null, sponsoring_human_slug: "joe",
    human_slug: "joe", sponsor_required: false,
  });
  // A local token remains non-human and is not one of the approved native
  // Codex/Claude authority-bearing runtime identities.
  assert.equal(actor.human, false);
  assert.equal(actor.probe, undefined);
  assert.equal(actor.review, undefined);
});

test("via label defaults to agent-token when omitted, unaffected by the LOCAL_SPONSOR extension", () => {
  const AGENT_TOKENS = JSON.stringify({ grok: "grok-secret-fixture" });
  assert.equal(agentActorForToken("Bearer grok-secret-fixture", AGENT_TOKENS).via, "agent-token");
});

test("codex/grok remain unsponsored even when resolved through the extended function", () => {
  // LOCAL_SPONSOR names 'joe-local' only. Any other slug — including one
  // presented on the LOCAL_TOKENS map by mistake — must stay shared-only.
  assert.equal(agentActorForToken("Bearer codex-secret-fixture",
    JSON.stringify({ codex: "codex-secret-fixture" }), "local-token").sponsoring_human_slug, null);
});

// dell-local (2026-08-18). Dell's Mac gets its own machine door so his
// unattended runs reach the verbs. TWO gates had to open, and the tests below
// pin both: DISPLAY, or isKnownActor refuses the slug and the token is dead on
// arrival; and LOCAL_SPONSOR, or it authenticates but resolves shared-only with
// no personal brain. The second failure is the dangerous one — it looks like it
// works until something reads the wrong scope.

const DELL_LOCAL_TOKENS = JSON.stringify({ "dell-local": "dell-local-secret-fixture" });

test("dell-local resolves to Dell's personal scope, human:false", () => {
  const actor = agentActorForToken("Bearer dell-local-secret-fixture", DELL_LOCAL_TOKENS, "local-token");
  assert.deepEqual(actor, {
    slug: "dell-local", display: "Agent (dell-local)", human: false, agent: true,
    via: "local-token", client_id: null, sponsoring_human_slug: "dell",
    human_slug: "dell", sponsor_required: false,
  });
  assert.equal(actor.human, false);
});

test("dell-local is a known actor, so the slug survives the isKnownActor stop", () => {
  assert.equal(isKnownActor("dell-local"), true);
});

test("the two machine doors sponsor different humans and never cross", () => {
  // The whole point of a separate slug: Dell's machine must not write as Joe.
  const both = JSON.stringify({
    "joe-local": "local-secret-fixture",
    "dell-local": "dell-local-secret-fixture",
  });
  assert.equal(agentActorForToken("Bearer local-secret-fixture", both, "local-token").sponsoring_human_slug, "joe");
  assert.equal(agentActorForToken("Bearer dell-local-secret-fixture", both, "local-token").sponsoring_human_slug, "dell");
});

test("a machine door is not a partner: dell-local never passes the humanOnly gate", () => {
  // isKnownPartner backs the humanOnly refusal. 'dell' is a partner; the
  // machine credential carrying his scope is not, and must never become one.
  assert.equal(isKnownPartner("dell-local"), false);
  assert.equal(agentActorForToken("Bearer dell-local-secret-fixture",
    DELL_LOCAL_TOKENS, "local-token").human, false);
});

test("local token is per-slug like every other agent token: a stray key does not widen it", () => {
  const mixed = JSON.stringify({ "joe-local": "local-secret-fixture", grok: "grok-secret-fixture" });
  assert.equal(agentActorForToken("Bearer grok-secret-fixture", mixed, "local-token").sponsoring_human_slug, null);
  assert.equal(agentActorForToken("Bearer local-secret-fixture", mixed, "local-token").sponsoring_human_slug, "joe");
});

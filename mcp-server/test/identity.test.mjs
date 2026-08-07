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
  propsForSlug,
  actorFromProps,
  isKnownActor,
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

test("agentSlugForClient: Claude's own connector never resolves to an outside-model actor", () => {
  assert.equal(agentSlugForClient("Claude Code"), null);
  assert.equal(agentSlugForClient("Claude"), null);
});

test("agentSlugForClient: missing/unusual input degrades to null, never throws", () => {
  assert.equal(agentSlugForClient(undefined), null);
  assert.equal(agentSlugForClient(null), null);
  assert.equal(agentSlugForClient(""), null);
  assert.equal(agentSlugForClient(42), null);
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

test("derivation: Joe in Claude Code writes as joe", () => {
  const actor = deriveWriteActor("joe", "Claude Code");
  assert.equal(actor.slug, "joe");
  assert.equal(actor.human, true);
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

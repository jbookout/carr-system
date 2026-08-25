import test from "node:test";
import assert from "node:assert/strict";

import { selectLocalClientCredential } from "../local-client-auth.mjs";
import { hermesActorForToken } from "../src/identity.js";

const tokenFile = [
  "CARR_MCP_LOCAL_TOKEN=joe-secret",
  "CARR_HERMES_MCP_TOKEN=hermes-secret",
].join("\n");

test("ordinary calls keep the local-machine credential", () => {
  const selected = selectLocalClientCredential({}, tokenFile);
  assert.equal(selected.token, "joe-secret");
  assert.equal(selected.profile, "local");
  assert.equal(selected.tokenVariable, "CARR_MCP_LOCAL_TOKEN");
});

test("the queue projector selects the separate Hermes credential", () => {
  const selected = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-projector" }, tokenFile);
  assert.equal(selected.token, "hermes-secret");
  assert.equal(selected.profile, "hermes-projector");
  assert.equal(selected.tokenVariable, "CARR_HERMES_MCP_TOKEN");
  assert.doesNotMatch(selected.identityNotice, /hermes-secret|joe-secret/);
  const serverActor = hermesActorForToken(
    `Bearer ${selected.token}`, JSON.stringify({ "hermes-pilot": "hermes-secret" }));
  assert.deepEqual({ slug: serverActor.slug, sponsor: serverActor.sponsoring_human_slug,
    via: serverActor.via, hermes: serverActor.hermes }, {
    slug: "hermes-pilot", sponsor: "joe", via: "hermes-token", hermes: true,
  }, "the selected client credential resolves to exact server-derived reader provenance");
});

test("a missing Hermes credential never falls back to joe-local", () => {
  const selected = selectLocalClientCredential(
    { CARR_MCP_CLIENT_PROFILE: "hermes-projector" }, "CARR_MCP_LOCAL_TOKEN=joe-secret\n");
  assert.equal(selected.token, "");
  assert.equal(selected.tokenVariable, "CARR_HERMES_MCP_TOKEN");
});

test("unknown client profiles fail closed", () => {
  assert.throws(
    () => selectLocalClientCredential({ CARR_MCP_CLIENT_PROFILE: "joe" }, tokenFile),
    /unsupported local MCP client profile/,
  );
});

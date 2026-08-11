import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

const fixtureDirectory = new URL("../fixtures/", import.meta.url);
const expectedStates = ["conflict", "empty", "loading", "normal", "offline", "partial", "refusal", "retry", "stale", "unauthorized"];
const expectedSurfaces = ["call-review", "command-center", "deal-room", "doc-request", "lead-board", "marketing", "more", "notifications", "tour"];

async function fixtures() {
  const names = (await readdir(fixtureDirectory)).filter(name => name.endsWith(".v1.json")).sort();
  return Promise.all(names.map(async name => ({ name, value: JSON.parse(await readFile(join(fixtureDirectory.pathname, name), "utf8")) })));
}

test("canonical JSON twins cover every assigned surface", async () => {
  const all = await fixtures();
  assert.deepEqual(all.map(({ value }) => value.surface).sort(), expectedSurfaces);
  assert.equal(new Set(all.map(({ value }) => value.surface)).size, all.length);
});

test("every fixture declares all ten authored states", async () => {
  for (const { name, value } of await fixtures()) {
    assert.equal(value.schema_version, "workspace-fixture/v1", name);
    assert.equal(value.synthetic, true, name);
    assert.ok(Array.isArray(value.requirement_ids) && value.requirement_ids.length > 0, name);
    assert.deepEqual(Object.keys(value.states).sort(), expectedStates, name);
    for (const state of expectedStates) {
      assert.ok(value.states[state].freshness, `${name}:${state} freshness`);
      assert.equal(typeof value.states[state].freshness.status, "string", `${name}:${state} status`);
    }
  }
});

test("fixtures contain no client, credential, or transcript payload shapes", async () => {
  for (const { name, value } of await fixtures()) {
    const text = JSON.stringify(value);
    assert.doesNotMatch(text, /@[a-z0-9.-]+\.[a-z]{2,}/i, `${name}: email-shaped value`);
    assert.doesNotMatch(text, /\b(?:sk|pk)_(?:live|test)_[a-z0-9]+\b/i, `${name}: credential-shaped value`);
    assert.doesNotMatch(text, /"(?:password|access_token|refresh_token|client_secret|transcript|audio_blob)"\s*:/i, `${name}: restricted key`);
    assert.doesNotMatch(text, /\b\d{3}[-.) ]\d{3}[- ]\d{4}\b/, `${name}: phone-shaped value`);
  }
});

test("critical synthetic twins retain human gates and safe boundaries", async () => {
  const bySurface = new Map((await fixtures()).map(({ value }) => [value.surface, value]));
  const deal = JSON.stringify(bySurface.get("deal-room").states.normal);
  const call = JSON.stringify(bySurface.get("call-review").states.normal);
  const doc = JSON.stringify(bySurface.get("doc-request").states.normal);
  const tour = JSON.stringify(bySurface.get("tour").states.normal);
  assert.match(deal, /parking_reasons/);
  assert.match(deal, /history/);
  assert.match(call, /consent_confirmed/);
  assert.match(call, /purge_eligible/);
  assert.match(doc, /external_effect[^]*false/);
  assert.match(tour, /locked[^]*true/);
  assert.match(tour, /offline_pack/);
});

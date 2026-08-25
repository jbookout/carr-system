import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { REVIEWED_DEALROOM_HOSTS, resolveDealroomBoot } from "../../dealroom/js/boot-mode.js";

const PRODUCTION = "https://app.doctorcre.com/";
const STAGING = "https://carr-mcp-staging.joe-bookout-carr-us.workers.dev/";
const ROOT = fileURLToPath(new URL("../../", import.meta.url));

test("reviewed production and staging hosts boot the same-origin live client", () => {
  assert.deepEqual(resolveDealroomBoot(new URL(PRODUCTION)), { mode: "live", options: {} });
  assert.deepEqual(resolveDealroomBoot(new URL(STAGING)), { mode: "live", options: {} });
  assert.deepEqual(resolveDealroomBoot(new URL(`${PRODUCTION}?mode=fixture&actor=dell`)),
    { mode: "live", options: {} });
  assert.deepEqual(resolveDealroomBoot(new URL(`${STAGING}?mode=fixture&actor=dell`)),
    { mode: "live", options: {} });
});

test("query parameters cannot turn an arbitrary host or API target live", () => {
  const arbitrary = new URL("https://preview.example/?mode=live&api=https://api.doctorcre.com&actor=joe");
  assert.deepEqual(resolveDealroomBoot(arbitrary), { mode: "fixture", options: { selfActor: "joe" } });

  const stagingOverride = new URL(`${STAGING}?mode=live&api=https://api.doctorcre.com&actor=dell`);
  assert.deepEqual(resolveDealroomBoot(stagingOverride), { mode: "live", options: {} });
});

test("fixture overrides are limited to local or unreviewed hosts", () => {
  assert.deepEqual(resolveDealroomBoot(new URL("https://preview.example/?mode=fixture&actor=dell")),
    { mode: "fixture", options: { selfActor: "dell" } });
  assert.deepEqual(resolveDealroomBoot(new URL("http://localhost:8787/?mode=live&api=https://api.doctorcre.com")),
    { mode: "live", options: {} });
  assert.deepEqual(resolveDealroomBoot(new URL("http://localhost:8787/")),
    { mode: "fixture", options: {} });
});

test("browser reviewed hosts, Wrangler declarations, and service catalog cannot drift", async () => {
  const wrangler = await readFile(`${ROOT}mcp-server/wrangler.toml`, "utf8");
  const configuredHosts = [...wrangler.matchAll(/^(?:APP_HOST|DEALROOM_HOST) = "([^"]+)"$/gm)].map((match) => match[1]);
  assert.deepEqual([...REVIEWED_DEALROOM_HOSTS].sort(), configuredHosts.sort());

  const services = JSON.parse(await readFile(`${ROOT}ops/config/services.json`, "utf8"));
  const carrMcp = services.services.find((service) => service.key === "carr-mcp");
  const staging = carrMcp.environments.find((environment) => environment.environment === "staging");
  assert.equal(staging.endpoint, new URL(STAGING).hostname);
});

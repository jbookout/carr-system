import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

test("Home asset is a dark, visual, responsive workstation with honest states", async () => {
  const html = await readFile(`${ROOT}/workspace.html`, "utf8");
  const dealHtml = await readFile(`${ROOT}/index.html`, "utf8");
  const css = await readFile(`${ROOT}/css/workspace.css`, "utf8");
  const js = await readFile(`${ROOT}/js/workspace-command-center.js`, "utf8");
  const modelJs = await readFile(`${ROOT}/js/workspace-command-center-model.js`, "utf8");
  const dealJs = await readFile(`${ROOT}/js/app.js`, "utf8");
  const surfaceFiles = ["workspace.html", "index.html", "leads.html", "room.html", "queue.html", "system-work.html"];
  const surfaces = Object.fromEntries(await Promise.all(surfaceFiles.map(async (file) => [file, await readFile(`${ROOT}/${file}`, "utf8")])));
  assert.match(html, /id="commandCenterVisual"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /href="\/leads"/);
  assert.match(html, /href="\/deals/);
  assert.match(html, />CALLS</);
  assert.match(html, /href="\/system-work\.html"/);
  assert.match(html, /href="\/room\.html"/);
  assert.match(css, /--ink-0:#0/);
  assert.match(css, /backdrop-filter/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:reduce\)/);
  assert.match(css, /pulse-attention/);
  assert.match(js, /\/api\/v1\/command-center/);
  assert.doesNotMatch(js, /api\/v1\/workspace\/command-center/);
  assert.match(js, /AUTHENTICATION_REQUIRED/);
  assert.match(js, /observed_at/);
  assert.match(js, /source\.freshness/);
  assert.match(js, /\.catch/);
  assert.doesNotMatch(html, /System online/);
  assert.match(html, /Checking workspace/);
  assert.doesNotMatch(html, /pulse-attention[^>]+href="\/system-work\.html"/);
  assert.match(html, /System state/);
  assert.match(modelJs, /valid_until/);
  assert.match(modelJs, /owned_flagged_deals/);
  assert.match(modelJs, /state: "unavailable"/);
  assert.match(dealHtml, /data-filter="flagged"/);
  assert.match(dealJs, /deal\.attention === true/);
  assert.match(dealJs, /params\.get\('owner'\) === 'me'/);
  assert.match(html, /class="mobile-nav"/);
  assert.match(html, />Home</);
  assert.match(html, />Leads</);
  assert.match(html, />Deals</);
  assert.match(html, />System</);
  assert.match(html, />Observe</);
  assert.match(css, /max-width:\s*767px/);
  assert.match(css, /mobile-nav/);
  assert.match(html, /id="needsYouNow"/);
  assert.match(html, /id="docAtWork"/);
  assert.match(html, /id="recentActivity"/);
  assert.match(js, /renderAggregates/);
  Object.values(surfaces).forEach((surface) => assert.match(surface, /href="\/deals"[^>]*>Deals<\/a>/));
});

test("Home has one first-region primary action, one workspace directory, and secondary flow", async () => {
  const html = await readFile(`${ROOT}/workspace.html`, "utf8");
  assert.match(html, /<link rel="manifest" href="\/manifest\.webmanifest">/);
  assert.match(html, /<link rel="apple-touch-icon" href="\/icons\/dealroom-192\.png">/);
  assert.match(html, /<h1[^>]*>Home<\/h1>/);
  assert.doesNotMatch(html, /read-only overview|<h1[^>]*>Command Center<\/h1>/i);
  const primaryRegion = html.match(/<section[^>]+data-home-primary-region[\s\S]*?<\/section>/)?.[0] || "";
  assert.equal((primaryRegion.match(/data-primary-action/g) || []).length, 1);
  assert.match(primaryRegion, /id="homePrimaryAction"/);
  assert.doesNotMatch(html, /glance-card|Where to go|Open the owning surface/);
  assert.equal((html.match(/aria-label="Open a workspace"/g) || []).length, 1);
  assert.ok(html.indexOf("data-home-primary-region") < html.indexOf("id=\"commandCenterVisual\""));
});

test("all six authenticated surfaces expose deterministic global navigation", async () => {
  const expectations = {
    "workspace.html": ["/", "Home"],
    "index.html": ["/deals", "Deals"],
    "leads.html": ["/leads", "Leads"],
    "room.html": ["/room.html", "Observatory"],
    "queue.html": ["/queue.html", "Queue"],
    "system-work.html": ["/system-work.html", "System work"],
  };
  for (const [file, [activeHref, activeLabel]] of Object.entries(expectations)) {
    const html = await readFile(`${ROOT}/${file}`, "utf8");
    assert.match(html, /href="\/"[^>]*>Home<\/a>/);
    assert.match(html, /href="\/leads"[^>]*>Leads<\/a>/);
    assert.match(html, /href="\/deals"[^>]*>Deals<\/a>/);
    assert.match(html, /href="\/system-work\.html"[^>]*>System work<\/a>/);
    assert.match(html, /href="\/room\.html"[^>]*>Observatory<\/a>/);
    assert.match(html, new RegExp(`href="${activeHref.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"[^>]*aria-current="page"[^>]*>${activeLabel}<\\/a>`));
    assert.doesNotMatch(html, /href="#"/);
  }
});

test("mobile Home navigation replaces desktop navigation without occluding content", async () => {
  const css = await readFile(`${ROOT}/css/workspace.css`, "utf8");
  assert.match(css, /@media\(max-width:767px\)[\s\S]*\.primary-nav\{display:none/);
  assert.match(css, /@media\(max-width:767px\)[\s\S]*body\{padding-bottom:/);
  assert.match(css, /\.mobile-nav\{display:none/);
});

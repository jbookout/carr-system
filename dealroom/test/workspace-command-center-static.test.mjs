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
  assert.match(js, /displayedFreshness\(source\)/);
  assert.doesNotMatch(js, /escapeHtml\(source\.freshness\)/);
  assert.match(js, /\.catch/);
  assert.doesNotMatch(html, /System online/);
  assert.match(html, /Checking workspace/);
  assert.doesNotMatch(html, /pulse-attention[^>]+href="\/system-work\.html"/);
  assert.match(html, /System state/);
  assert.match(modelJs, /valid_until/);
  assert.match(modelJs, /flagged_deals/);
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

test("Home defaults to the combined team scope and offers My work as a keyboard and touch secondary", async () => {
  const html = await readFile(`${ROOT}/workspace.html`, "utf8");
  const css = await readFile(`${ROOT}/css/workspace.css`, "utf8");
  const js = await readFile(`${ROOT}/js/workspace-command-center.js`, "utf8");
  const modelJs = await readFile(`${ROOT}/js/workspace-command-center-model.js`, "utf8");
  assert.match(html, /id="scopeSwitch"[^>]*role="group"[^>]*aria-label="Home scope"/);
  assert.match(html, /<button[^>]*data-scope="team"[^>]*aria-pressed="true"/);
  assert.match(html, /<button[^>]*data-scope="mine"[^>]*aria-pressed="false"/);
  assert.match(html, /id="scopeNote"/);
  assert.match(html, /Team book/);
  assert.match(html, /My work/);
  // Buttons are reachable by keyboard and pointer; arrow keys move between the two scopes.
  assert.match(js, /addEventListener\("click"/);
  assert.match(js, /ArrowLeft/);
  assert.match(js, /ArrowRight/);
  assert.match(js, /aria-pressed/);
  assert.match(css, /\.scope-option\{[^}]*min-height:4[4-9]px/);
  assert.match(modelJs, /DEFAULT_SCOPE = "team"/);
  assert.match(modelJs, /SCOPES = \["team", "mine"\]/);
  // No partner ranking or comparison surface is introduced.
  assert.doesNotMatch(html, /rank|leaderboard|vs\. Dell|vs\. Joe/i);
  assert.doesNotMatch(js, /rank|leaderboard/i);
});

test("Home only links to Deal Room filters the board already honors", async () => {
  const modelJs = await readFile(`${ROOT}/js/workspace-command-center-model.js`, "utf8");
  const serverJs = await readFile(`${ROOT}/../mcp-server/src/workspace-command-center.js`, "utf8");
  const dealJs = await readFile(`${ROOT}/js/app.js`, "utf8");
  assert.match(dealJs, /params\.get\('filter'\) === 'flagged'/);
  assert.match(dealJs, /params\.get\('workspace'\) === 'team'/);
  for (const source of [modelJs, serverJs]) {
    assert.match(source, /"\/deals\?workspace=team&filter=flagged"/);
    assert.match(source, /"\/deals\?workspace=team&filter=flagged&owner=me"/);
    assert.match(source, /"\/deals\?workspace=team"/);
    // The board has no URL form for mine-active, waiting or deadline lists.
    assert.doesNotMatch(source, /filter=(mine|waiting|deadline|stale|missing)/);
  }
  // No invented waiting or deadline counts in this unit.
  assert.doesNotMatch(serverJs, /waiting_count|deadline_count|due_soon/);
});

test("Home distinguishes loading, refreshing, stale and unavailable and cannot be repainted by a late read", async () => {
  const js = await readFile(`${ROOT}/js/workspace-command-center.js`, "utf8");
  const modelJs = await readFile(`${ROOT}/js/workspace-command-center-model.js`, "utf8");
  assert.match(modelJs, /export function homeReadPhase/);
  assert.match(modelJs, /export function acceptsResponse/);
  assert.match(js, /view\.status = view\.payload \? "refreshing" : "loading"/);
  assert.match(js, /acceptsResponse\(view\.sequence, sequence\)/);
  assert.match(js, /\+\+view\.sequence/);
  // The local clock re-checks the contract window — including the selected metric and each work
  // card's own deadline — instead of leaving expired counts on screen.
  assert.match(js, /setInterval/);
  assert.match(js, /freshnessSignature\(view\.payload, view\.scope\)/);
  assert.match(modelJs, /export function freshnessSignature/);
  assert.match(modelJs, /export function displayedFreshness/);
  // Retry is an explicit read, and focus survives a repaint — including the repaint that removes Retry.
  assert.match(js, /id="retryHome"/);
  assert.match(js, /load\("retry"\)/);
  assert.match(js, /document\.activeElement/);
  assert.match(js, /card\.querySelector\("#homePrimaryAction"\)/);
  assert.match(js, /\.focus\(\)/);
  // The Needs card's link is reset on every path, so a scope switch cannot leave the other scope's filter.
  assert.match(js, /function setNeedsHref/);
  assert.match(js, /setNeedsHref\(destination\)/);
  const css = await readFile(`${ROOT}/css/workspace.css`, "utf8");
  assert.match(css, /\.refresh-badge\{/);
  assert.match(css, /\.status-orb\.refreshing\{/);
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

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

test("Doc panel script declares the keyboard/focus/modality contract V5-J101 requires", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  assert.match(js, /aria-haspopup/);
  assert.match(js, /aria-expanded/);
  assert.match(js, /aria-controls/);
  assert.match(js, /aria-modal/);
  assert.match(js, /region\.inert\s*=/, "background regions must be made inert while the panel is a phone-width modal");
  assert.match(js, /key !== 'Escape'/);
  assert.match(js, /escapeShouldClose\(/, "Escape must go through the shared pinned/not-pinned rule, not a local re-implementation");
  assert.match(js, /docPanelModality\(/);
  assert.match(js, /toggle\.focus\(\)/, "focus must return to the control that opened the panel on close");
  assert.match(js, /dom\.title\.focus\(\)/, "opening the panel must move focus into it, onto its heading");
  assert.match(js, /registerDocPanelSource/);
  assert.match(js, /runCommand/, "the panel must call the shared command registry, never a client verb directly");
  assert.doesNotMatch(js, /client\.setNextStep\(/, "doc-panel.js must not call the client directly — that would break UI/Doc parity");
  assert.doesNotMatch(js, /client\.addDealNote\(/);
});

test("Doc panel styling respects reduced motion and does not hardcode a single theme's colors", async () => {
  const css = await readFile(`${ROOT}/css/doc-panel.css`, "utf8");
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  assert.match(css, /var\(--orange/, "must theme through the host page's tokens rather than a fixed hex");
  assert.match(css, /@media\(max-width:767px\)/, "must have an explicit phone-width layout, matching the record panel's own breakpoint");
});

test("every J101 surface loads the Doc panel script and its stylesheet", async () => {
  const pages = ["workspace.html", "business.html", "index.html"];
  for (const page of pages) {
    const html = await readFile(`${ROOT}/${page}`, "utf8");
    assert.match(html, /doc-panel\.css/, `${page} must load doc-panel.css`);
  }
  // index.html pulls doc-panel.js indirectly through app.js's own import, so
  // it is checked separately rather than for a duplicate <script> tag.
  const workspace = await readFile(`${ROOT}/workspace.html`, "utf8");
  const business = await readFile(`${ROOT}/business.html`, "utf8");
  assert.match(workspace, /src="\/js\/doc-panel\.js"/);
  assert.match(business, /src="\/js\/doc-panel\.js"/);
  const appJs = await readFile(`${ROOT}/js/app.js`, "utf8");
  assert.match(appJs, /from '\.\/doc-panel\.js'/);
  const indexHtml = await readFile(`${ROOT}/index.html`, "utf8");
  assert.doesNotMatch(indexHtml, /src="js\/doc-panel\.js"/, "index.html must not double-load doc-panel.js alongside app.js's import");
});

test("commands.js never widens scope into Calls or Tours (V5-J101 must keep both inert)", async () => {
  const commands = await readFile(`${ROOT}/js/commands.js`, "utf8");
  assert.doesNotMatch(commands, /call[_-]?mode|startCall|recordCall|CallMode/i);
  assert.doesNotMatch(commands, /\btour\b/i);
});

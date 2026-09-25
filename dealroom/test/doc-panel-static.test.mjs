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
  assert.match(js, /focusWithoutScrolling\(toggle\)/, "focus must return to the control that opened the panel on close");
  assert.match(js, /focusWithoutScrolling\(dom\.title\)/, "opening the panel must move focus into it, onto its heading");
  assert.match(js, /registerDocPanelSource/);
  assert.match(js, /runCommand/, "the panel must call the shared command registry, never a client verb directly");
  assert.doesNotMatch(js, /client\.setNextStep\(/, "doc-panel.js must not call the client directly — that would break UI/Doc parity");
  assert.doesNotMatch(js, /client\.addDealNote\(/);

  // Reuse the record panel's own tab-stop rule rather than a second,
  // independently-drifting implementation of the same heading-is-not-a-stop
  // case (the source of the Shift+Tab-escapes-to-the-toggle defect).
  assert.match(js, /import\s*\{\s*panelTabTarget\s*\}\s*from\s*'\.\/workspace-business-model\.js'/);
  assert.match(js, /panelTabTarget\(\{/);

  // The toggle must no longer be excluded from the inert sweep — that
  // exclusion was exactly what let focus escape onto it.
  assert.doesNotMatch(js, /el\s*!==\s*toggle\s*&&\s*el\s*!==\s*panel/, "toggle must not be excluded from the regions the trap inerts");

  // The Doc panel must be reachable while a native <dialog> (e.g. #dealDialog,
  // #formDialog) is shown modally — it has to reparent into whichever one is
  // open, since showModal() makes everything outside that dialog's own
  // subtree inert.
  assert.match(js, /MutationObserver/);
  assert.match(js, /dialog\[open\]/);

  // Snapshot/restore, not a blind un-set, so Doc can never undo another
  // panel's independently-managed inert state on a shared region.
  assert.match(js, /managedRegions/);
  assert.doesNotMatch(js, /region\.inert\s*=\s*false/, "closing must restore the SNAPSHOTTED prior value, never hardcode false");
});

test("Doc panel's accessible name is the persona 'Dr. CRE'; the visible label stays the 'Doc' nickname", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  // Toggle: accessible name via aria-label, visible text stays "Doc".
  assert.match(js, /aria-label',\s*'Dr\. CRE'/);
  assert.match(js, /doc-toggle-label[^<]*>Doc</);
  // Panel: aria-labelledby resolves to a name containing "Dr. CRE" (the
  // eyebrow), while the visible heading itself stays "Doc".
  assert.match(js, /aria-labelledby',\s*'docPanelEyebrow docPanelTitle'/);
  assert.match(js, /id="docPanelEyebrow">Dr\. CRE</);
  assert.match(js, /id="docPanelTitle"[^>]*>Doc</);
});

test("a failed or unavailable Doc attempt offers Retry, and the retry reuses the same idempotency key", async () => {
  const js = await readFile(`${ROOT}/js/doc-panel.js`, "utf8");
  assert.match(js, /doc-log-retry/);
  assert.match(js, /state\.lastAttempt/);
  assert.match(js, /idempotencyKey:\s*receipt\.idempotencyKey/);
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

test("the four new dealroom/test suites are registered in the one CI shim (workspace-surface-inventory.test.mjs clause)", async () => {
  const shim = await readFile(`${ROOT}/../mcp-server/test/workspace-command-center-browser.test.mjs`, "utf8");
  for (const suite of ["capability-seams", "commands", "doc-panel-model", "doc-panel-static"]) {
    assert.match(shim, new RegExp(`dealroom/test/${suite}\\.test\\.mjs`), `${suite}.test.mjs must be imported by the CI shim`);
  }
});

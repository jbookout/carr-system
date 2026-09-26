import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DEALROOM = path.resolve(HERE, "..");
const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function chromeBinary() {
  for (const candidate of CHROME_CANDIDATES) {
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

class DevTools {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(url);
    this.opened = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      else pending.resolve(message.result || {});
    });
  }

  async call(method, params = {}) {
    await this.opened;
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, method }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  async evaluate(expression) {
    const result = await this.call("Runtime.evaluate", {
      expression, returnByValue: true, awaitPromise: true,
    });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser evaluation failed");
    return result.result?.value;
  }

  close() { this.socket.close(); }
}

// The router serves business.html's content at the exact paths /clients and
// /vendors (mcp-server/src/workspace-business-read.js's CLIENTS_ROUTE /
// VENDORS_ROUTE), and workspace-business.js's start() reads
// window.location.pathname to pick the dataset, redirecting anywhere else as
// a stale bookmark. A file:// URL's pathname is the on-disk path, never
// "/clients", so that redirect used to fire on every load and blow the page
// away with net::ERR_FILE_NOT_FOUND on file:///clients. A real loopback HTTP
// server is what makes the pathname match production's routing, and it also
// makes the root-relative hrefs/srcs in the HTML resolve without any
// asset-locator rewriting.
const CONTENT_TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".webmanifest": "application/manifest+json", ".json": "application/json",
  ".png": "image/png", ".svg": "image/svg+xml",
};

async function staticServer(t) {
  const server = createServer(async (req, res) => {
    const pathname = new URL(req.url, "http://localhost").pathname;
    const routed = pathname === "/clients" || pathname === "/vendors" ? "/business.html" : pathname;
    const file = path.join(DEALROOM, routed);
    if (!file.startsWith(DEALROOM + path.sep)) { res.writeHead(403).end(); return; }
    try {
      const body = await readFile(file);
      res.writeHead(200, { "content-type": CONTENT_TYPES[path.extname(file)] || "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(404).end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const { port } = server.address();
  return `http://127.0.0.1:${port}`;
}

async function launchBrowser(t) {
  const chrome = await chromeBinary();
  if (!chrome) return { unavailableReason: "Chrome/Chromium was not found; real V5-J101 browser evidence was not run" };
  const profile = await mkdtemp(path.join(tmpdir(), "v5-j101-chrome-"));
  const child = spawn(chrome, [
    "--headless=new", "--no-first-run", "--disable-gpu", "--no-sandbox",
    "--allow-file-access-from-files", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  t.after(async () => {
    child.kill("SIGTERM");
    await Promise.race([new Promise((resolve) => child.once("exit", resolve)), wait(3000)]);
    // Chrome can still be flushing its own profile files microseconds after
    // "exit" fires, which occasionally loses this rm/rmdir race with
    // ENOTEMPTY. One short retry clears it without masking a real failure.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await rm(profile, { recursive: true, force: true });
        break;
      } catch (error) {
        if (attempt === 2) throw error;
        await wait(200);
      }
    }
  });
  const portFile = path.join(profile, "DevToolsActivePort");
  // 20s, not 3s: a cold first-ever headless launch on a shared CI runner can
  // take meaningfully longer to write this file than it does on a warm local
  // machine, and 3s (60 x 50ms) was observed to time out in hosted CI even
  // though Chrome was present and did eventually come up.
  for (let attempt = 0; attempt < 200 && !existsSync(portFile); attempt += 1) {
    if (child.exitCode !== null) {
      return { unavailableReason: `Chrome exited before DevTools started (${child.exitCode}): ${stderr.slice(-2000)}` };
    }
    await wait(100);
  }
  if (!existsSync(portFile)) {
    return { unavailableReason: `Chrome did not publish a DevTools endpoint within 20s: ${stderr.slice(-2000)}` };
  }
  const [port] = String(await readFile(portFile)).split(/\r?\n/);
  const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((response) => response.json());
  const target = targets.find((item) => item.type === "page");
  assert.ok(target?.webSocketDebuggerUrl, "Chrome did not expose a page target");
  const cdp = new DevTools(target.webSocketDebuggerUrl);
  await cdp.call("Page.enable");
  await cdp.call("Runtime.enable");
  t.after(() => cdp.close());
  return { cdp };
}

async function configureViewport(cdp, width, { touch = false } = {}) {
  await cdp.call("Emulation.setDeviceMetricsOverride", {
    width, height: 900, deviceScaleFactor: 1, mobile: touch,
    screenWidth: width, screenHeight: 900,
  });
  // maxTouchPoints must be 1-16 even when disabling touch emulation — Chrome's
  // CDP validation rejects 0 outright ("Touch points must be between 1 and 16"),
  // which made every desktop-viewport call (touch defaults to false) throw.
  await cdp.call("Emulation.setTouchEmulationEnabled", { enabled: touch, maxTouchPoints: touch ? 5 : 1 });
}

async function navigate(cdp, url, selector) {
  await cdp.call("Page.navigate", { url });
  for (let attempt = 0; attempt < 160; attempt += 1) {
    const ready = await cdp.evaluate(`document.readyState === "complete" && Boolean(document.querySelector(${JSON.stringify(selector)}))`);
    if (ready) return;
    await wait(50);
  }
  assert.fail(`browser journey did not reach ${selector} at ${url}`);
}

async function tap(cdp, selector) {
  const point = await cdp.evaluate(`(() => { const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}; })()`);
  await cdp.call("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: point.x, y: point.y, radiusX: 2, radiusY: 2, force: 1 }] });
  await cdp.call("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
}

async function key(cdp, value, { shift = false } = {}) {
  const modifiers = shift ? 8 : 0;
  const code = value === "Tab" ? 9 : value === "Escape" ? 27 : 0;
  await cdp.call("Input.dispatchKeyEvent", { type: "keyDown", key: value, code: value, modifiers, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
  await cdp.call("Input.dispatchKeyEvent", { type: "keyUp", key: value, code: value, modifiers, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
}

async function waitFor(cdp, expression, message) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await cdp.evaluate(expression)) return;
    await wait(40);
  }
  assert.fail(message);
}

const SNAPSHOT = `(() => {
  const panel = document.querySelector('#docPanel');
  const visible = (node) => { const r=node.getBoundingClientRect(), s=getComputedStyle(node); return !node.hidden && s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  const controls = [...panel.querySelectorAll('button,input')].filter(visible);
  const outside = [...document.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')]
    .filter((node) => !panel.contains(node) && visible(node));
  const callsTours = [...document.querySelectorAll('.inert-entry')].map((node) => ({
    text: node.textContent.trim(), tag: node.tagName.toLowerCase(), ariaDisabled: node.getAttribute('aria-disabled'),
    href: node.getAttribute('href'), tabIndex: node.tabIndex, onclick: node.getAttribute('onclick'),
  }));
  return {
    width: innerWidth, panelHidden: panel.hidden, panelRole: panel.getAttribute('role'),
    ariaModal: panel.getAttribute('aria-modal'), activeId: document.activeElement?.id || null,
    focusInside: panel.contains(document.activeElement),
    outsideReachable: outside.filter((node) => !node.closest('[inert]')).map((node) => node.id || node.textContent.trim().slice(0,30)),
    minPanelTarget: controls.length ? Math.min(...controls.map((node) => { const r=node.getBoundingClientRect(); return Math.min(r.width,r.height); })) : 0,
    horizontalOverflow: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) - innerWidth,
    callsTours,
  };
})()`;

function modalityGuard(snapshot, expectedRole) {
  const failures = [];
  if (snapshot.panelHidden) failures.push("panel_hidden");
  if (snapshot.panelRole !== expectedRole) failures.push("wrong_role");
  if (expectedRole === "dialog" && snapshot.ariaModal !== "true") failures.push("modal_not_announced");
  if (expectedRole === "dialog" && snapshot.outsideReachable.length) failures.push("background_reachable");
  if (!snapshot.focusInside) failures.push("focus_outside_panel");
  if (snapshot.horizontalOverflow > 1) failures.push("horizontal_overflow");
  if (snapshot.minPanelTarget < 24) failures.push("target_below_wcag_2_2_minimum");
  return failures;
}

function inertBoundaryGuard(snapshot) {
  const expected = ["Calls", "Tours"];
  const actual = snapshot.callsTours.map((item) => item.text).sort();
  const failures = [];
  if (JSON.stringify(actual) !== JSON.stringify(expected)) failures.push("inert_entries_missing");
  for (const item of snapshot.callsTours) {
    if (item.tag === "a" || item.tag === "button" || item.href || item.onclick || item.ariaDisabled !== "true" || item.tabIndex >= 0) {
      failures.push(`${item.text || "unknown"}_actionable`);
    }
  }
  return failures;
}

test("V5-J101 real Chrome desktop/touch journeys preserve modality, focus, reflow, targets, and inert Calls/Tours", { timeout: 30_000 }, async (t) => {
  const origin = await staticServer(t);
  const browser = await launchBrowser(t);
  if (!browser.cdp) {
    if (process.env.CI) assert.fail(browser.unavailableReason);
    t.skip(browser.unavailableReason);
    return;
  }
  const { cdp } = browser;

  await configureViewport(cdp, 1280);
  await navigate(cdp, `${origin}/clients`, "#docPanelToggle");
  await cdp.evaluate("document.querySelector('#docPanelToggle').click()");
  await waitFor(cdp, "!document.querySelector('#docPanel').hidden", "desktop Doc did not open");
  const desktop = await cdp.evaluate(SNAPSHOT);
  assert.deepEqual(modalityGuard(desktop, "complementary"), []);
  assert.deepEqual(inertBoundaryGuard(desktop), []);

  await cdp.evaluate("document.querySelector('#docPanelClose').click()");
  await configureViewport(cdp, 375, { touch: true });
  await tap(cdp, "#docPanelToggle");
  await waitFor(cdp, "!document.querySelector('#docPanel').hidden", "touch Doc did not open");
  const phone = await cdp.evaluate(SNAPSHOT);
  assert.deepEqual(modalityGuard(phone, "dialog"), []);
  assert.deepEqual(inertBoundaryGuard(phone), []);

  await key(cdp, "Tab", { shift: true });
  assert.equal(await cdp.evaluate("document.querySelector('#docPanel').contains(document.activeElement)"), true,
    "Shift+Tab from the panel heading must stay in the modal");
  await key(cdp, "Escape");
  await waitFor(cdp, "document.querySelector('#docPanel').hidden", "Escape did not close an unpinned Doc");
  assert.equal(await cdp.evaluate("document.activeElement?.id"), "docPanelToggle", "closing Doc must restore focus to its opener");
});

test("V5-J101 browser guards kill planted modality, accessibility, and inert-boundary mutants", () => {
  const good = {
    panelHidden: false, panelRole: "dialog", ariaModal: "true", focusInside: true,
    outsideReachable: [], horizontalOverflow: 0, minPanelTarget: 24,
    callsTours: [
      { text: "Calls", tag: "span", ariaDisabled: "true", href: null, tabIndex: -1, onclick: null },
      { text: "Tours", tag: "span", ariaDisabled: "true", href: null, tabIndex: -1, onclick: null },
    ],
  };
  assert.deepEqual(modalityGuard(good, "dialog"), []);
  assert.deepEqual(inertBoundaryGuard(good), []);

  for (const mutant of [
    { ...good, panelRole: "complementary" },
    { ...good, ariaModal: null },
    { ...good, outsideReachable: ["background link"] },
    { ...good, focusInside: false },
    { ...good, horizontalOverflow: 18 },
    { ...good, minPanelTarget: 12 },
  ]) assert.notDeepEqual(modalityGuard(mutant, "dialog"), [], `modality guard survived mutant ${JSON.stringify(mutant)}`);

  const actionableCalls = structuredClone(good);
  actionableCalls.callsTours[0] = { ...actionableCalls.callsTours[0], tag: "button", ariaDisabled: null, tabIndex: 0 };
  assert.notDeepEqual(inertBoundaryGuard(actionableCalls), [], "inert-boundary guard must kill an actionable Calls mutant");
});

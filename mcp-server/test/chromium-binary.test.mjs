import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { findDisposableChromium } from "../../dealroom/test/chromium-binary.mjs";

const SYSTEM_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

test("browser tests refuse the installed auto-updating Chrome", async () => {
  const result = await findDisposableChromium({
    env: { CHROME_FOR_TESTING_PATH: SYSTEM_CHROME, CHROME_PATH: SYSTEM_CHROME },
    home: "/Users/tester",
    platform: "darwin",
    exists: () => true,
    realpath: candidate => candidate,
    listDirectories: async () => [],
  });
  assert.equal(result, null);
});

test("browser tests select Playwright Chrome for Testing", async () => {
  const home = "/Users/tester";
  const cache = path.join(home, "Library/Caches/ms-playwright");
  const executable = path.join(cache, "chromium-1243/chrome-mac-arm64",
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing");
  const result = await findDisposableChromium({
    env: {}, home, platform: "darwin",
    exists: candidate => candidate === executable,
    realpath: candidate => candidate,
    listDirectories: async candidate => candidate === cache ? ["chromium-1243"] : [],
  });
  assert.equal(result, executable);
});

test("browser tests refuse an alias that resolves to installed Chrome", async () => {
  const alias = "/tmp/chrome-for-testing";
  const result = await findDisposableChromium({
    env: { CHROME_FOR_TESTING_PATH: alias },
    home: "/Users/tester",
    platform: "darwin",
    exists: () => true,
    realpath: candidate => candidate === alias ? SYSTEM_CHROME : candidate,
    listDirectories: async () => [],
  });
  assert.equal(result, null);
});

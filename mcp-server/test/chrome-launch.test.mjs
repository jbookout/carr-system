import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { launchChrome } from "../../dealroom/test/chrome-launch.mjs";

const fixture = new URL("./fixtures/chrome-launch-fixture.mjs", import.meta.url);
const nativeKill = process.kill.bind(process);
const realChrome = [
  process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
].filter(Boolean).find(existsSync);
const codeSignCloneDirectory = path.resolve(tmpdir(), "..", "X", "com.google.Chrome.code_sign_clone");
function fakeChrome(t, scenarios, beforeSpawn = () => {}) {
  const calls = [];
  t.after(async () => {
    for (const { child, profile } of calls) {
      const target = process.platform === "win32" ? child.pid : -child.pid;
      try { nativeKill(target, "SIGKILL"); }
      catch (error) { if (error.code !== "ESRCH") throw error; }
      const deadline = performance.now() + 1000;
      for (;;) {
        try { nativeKill(target, 0); }
        catch (error) {
          if (error.code === "ESRCH") break;
          throw error;
        }
        assert.ok(performance.now() < deadline, `fake Chrome process tree ${child.pid} survived SIGKILL`);
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      await rm(profile, { recursive: true, force: true });
    }
  });
  function spawnChrome(binary, args, options) {
    beforeSpawn(calls);
    const profile = args.find((arg) => arg.startsWith("--user-data-dir=")).split("=").slice(1).join("=");
    const scenario = scenarios[calls.length] ?? scenarios.at(-1);
    const child = spawn(process.execPath, [fixture.pathname, profile, scenario], options);
    calls.push({ binary, args, profile, child });
    return child;
  }
  return { calls, spawnChrome };
}

function processAlive(pid) {
  try { nativeKill(pid, 0); return true; }
  catch (error) { if (error.code === "ESRCH") return false; throw error; }
}

test("graceful Chrome shutdown does not grow the macOS code-sign clone directory", {
  skip: process.platform !== "darwin" || !realChrome || !existsSync(codeSignCloneDirectory),
  timeout: 90_000,
}, async (t) => {
  const before = (await readdir(codeSignCloneDirectory)).length;
  let browser;
  try { browser = await launchChrome(realChrome); }
  catch (error) { t.skip(`Chrome could not launch: ${error.message}`); return; }
  t.after(() => browser.close());
  await browser.close();
  const after = (await readdir(codeSignCloneDirectory)).length;
  assert.equal(after, before, `Chrome code-sign clones grew ${before} -> ${after}`);
});

test("Chrome requests a DevTools shutdown before sending process signals", async (t) => {
  const fake = fakeChrome(t, ["ready"]);
  const shutdown = [];
  const spawnChrome = (...args) => {
    const child = fake.spawnChrome(...args);
    const kill = child.kill.bind(child);
    child.kill = (signal) => {
      shutdown.push([child.pid, signal]);
      return kill(signal);
    };
    return child;
  };
  t.mock.method(process, "kill", (pid, signal) => {
    if (pid === -fake.calls[0]?.child.pid && signal !== 0) shutdown.push([pid, signal]);
    return nativeKill(pid, signal);
  });
  const browser = await launchChrome("fake-chrome", {
    spawnChrome,
    timeoutMs: 1500,
    closeBrowser: async () => {
      shutdown.push("Browser.close");
      nativeKill(fake.calls[0].child.pid, "SIGKILL");
      return true;
    },
  });
  await browser.close();
  assert.deepEqual(shutdown, ["Browser.close"], "DevTools shutdown precedes and avoids process signals");
});

test("Chrome falls back to the browser PID when DevTools shutdown fails", async (t) => {
  const fake = fakeChrome(t, ["ready"]);
  const browser = await launchChrome("fake-chrome", {
    spawnChrome: fake.spawnChrome,
    timeoutMs: 1500,
    closeBrowser: async () => { throw new Error("DevTools connection closed"); },
  });
  await browser.close();
  assert.equal(fake.calls[0].child.signalCode, "SIGTERM");
  assert.equal(processAlive(fake.calls[0].child.pid), false);
});

test("Chrome bounds a stalled DevTools shutdown before falling back to the browser PID", async (t) => {
  const fake = fakeChrome(t, ["ready"]);
  const shutdown = [];
  const spawnChrome = (...args) => {
    const child = fake.spawnChrome(...args);
    const kill = child.kill.bind(child);
    child.kill = (signal) => {
      shutdown.push([child.pid, signal]);
      return kill(signal);
    };
    return child;
  };
  const browser = await launchChrome("fake-chrome", {
    spawnChrome,
    timeoutMs: 1500,
    stopTimeoutMs: 50,
    closeBrowser: () => {
      shutdown.push("Browser.close");
      return new Promise(() => {});
    },
  });
  await browser.close();
  assert.deepEqual(shutdown, [
    "Browser.close",
    [fake.calls[0].child.pid, "SIGTERM"],
  ], "a stalled DevTools request times out before PID fallback");
});

test("Chrome waits through an exiting process group's transient EPERM probe", { skip: process.platform === "win32" }, async (t) => {
  const fake = fakeChrome(t, ["ready"]);
  const browser = await launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500 });
  t.after(() => browser.close());
  let probes = 0;
  t.mock.method(process, "kill", (pid, signal) => {
    if (pid === -fake.calls[0].child.pid && signal === 0 && ++probes <= 2) {
      throw Object.assign(new Error("exiting group"), { code: "EPERM" });
    }
    return nativeKill(pid, signal);
  });
  await browser.close();
  assert.ok(probes >= 3, "EPERM is pending cleanup, never proof that the group is gone");
  assert.equal(processAlive(fake.calls[0].child.pid), false);
  assert.equal(existsSync(fake.calls[0].profile), false);
});

for (const scenario of ["ready-helper", "hang-helper"]) {
  test(`Chrome reaps a SIGTERM-resistant helper after its leader exits (${scenario})`, { skip: process.platform === "win32" }, async (t) => {
    let helperPid;
    const fake = fakeChrome(t, [scenario, "ready"], (calls) => {
      if (calls.length === 1) {
        assert.equal(processAlive(helperPid), false, "the owned helper must be gone before retry");
        assert.equal(existsSync(calls[0].profile), false);
      }
    });
    const spawnChrome = (...args) => {
      const child = fake.spawnChrome(...args);
      let stderr = "";
      child.stderr.on("data", (chunk) => {
        stderr += chunk;
        const match = stderr.match(/helper-pid:(\d+)\n/);
        if (match) helperPid = Number(match[1]);
      });
      return child;
    };
    const browser = await launchChrome("fake-chrome", { spawnChrome, timeoutMs: 1500, pollIntervalMs: 10 });
    t.after(() => browser.close());
    assert.ok(helperPid > 0, "fixture published its owned helper PID");
    await browser.close();
    assert.equal(processAlive(helperPid), false, "the owned helper must be gone when cleanup returns");
    assert.equal(existsSync(fake.calls[0].profile), false);
    assert.equal(fake.calls[0].child.signalCode, "SIGTERM", "the leader exited without SIGKILL");
    assert.equal(fake.calls.length, scenario === "hang-helper" ? 2 : 1);
  });
}

for (const scenario of ["partial", "http-late", "page-late"]) {
  test(`Chrome discovers a complete port file and ready page after ${scenario} publication without a stderr endpoint`, async (t) => {
    const fake = fakeChrome(t, [scenario]);
    const browser = await launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500, pollIntervalMs: 10 });
    t.after(() => browser.close());
    assert.match(browser.pageWsUrl, /^ws:\/\/127\.0\.0\.1:\d+\/devtools\/page\/fixture$/);
    assert.equal(fake.calls.length, 1, "readiness races must not relaunch a healthy child");
  });
}

for (const scenario of ["exit", "hang"]) {
  test(`Chrome retries ${scenario} once with a fresh profile after reaping the first process`, async (t) => {
    const fake = fakeChrome(t, [scenario, "ready"]);
    const browser = await launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500, pollIntervalMs: 10 });
    t.after(() => browser.close());
    assert.equal(fake.calls.length, 2);
    assert.notEqual(fake.calls[0].profile, fake.calls[1].profile);
    assert.ok(fake.calls[0].child.exitCode !== null || fake.calls[0].child.signalCode !== null);
    assert.equal(existsSync(fake.calls[0].profile), false);
    assert.match(browser.attempts[0].diagnostic, new RegExp(`fixture:${scenario}`));
    assert.ok(browser.startupMs > 0);
    for (const arg of ["--headless=new", "--no-first-run", "--no-sandbox", "--disable-background-networking", "--remote-debugging-port=0"]) {
      assert.ok(fake.calls[1].args.includes(arg), arg);
    }
    await browser.close();
    assert.equal(existsSync(fake.calls[1].profile), false);
  });
}

test("Chrome fails after two launches with per-attempt stderr, elapsed time and exit status, leaving no profile", async (t) => {
  const fake = fakeChrome(t, ["exit", "hang"]);
  await assert.rejects(launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500, pollIntervalMs: 10 }), (error) => {
    assert.match(error.message, /attempt 1.*code 17/s);
    assert.match(error.message, /attempt 2.*deadline/s);
    assert.match(error.message, /fixture:exit/);
    assert.match(error.message, /fixture:hang/);
    assert.match(error.message, /elapsed=\d+ms/);
    return true;
  });
  assert.equal(fake.calls.length, 2);
  assert.ok(fake.calls.every(({ profile, child }) => !existsSync(profile) && (child.exitCode !== null || child.signalCode !== null)));
});

test("Chrome handles spawn errors within the launch lifecycle", async () => {
  await assert.rejects(launchChrome("/missing/chromeflake-binary", { timeoutMs: 400 }), /ENOENT/);
});

test("an exited browser's detached stderr holder cannot hang teardown", async (t) => {
  const fake = fakeChrome(t, ["inherited-stderr"]);
  let holderPid;
  const spawnChrome = (...args) => {
    const child = fake.spawnChrome(...args);
    child.stderr.on("data", (chunk) => {
      const match = String(chunk).match(/holder-pid:(\d+)/);
      if (match) holderPid = Number(match[1]);
    });
    return child;
  };
  const browser = await launchChrome("fake-chrome", { spawnChrome, timeoutMs: 1500 });
  t.after(() => browser.close());
  const started = performance.now();
  await browser.close();
  assert.ok(performance.now() - started < 1000, "teardown waits for the browser, not detached stderr holders");
  assert.equal(existsSync(fake.calls[0].profile), false);
  assert.ok(holderPid > 0 && processAlive(holderPid), "fixture leaves stderr inherited while browser cleanup returns");
  for (let attempt = 0; attempt < 200 && processAlive(holderPid); attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.equal(processAlive(holderPid), false, "the detached stderr holder exits within its fixture bound");
});

test("a stalled HTTP probe shares the startup deadline and does not hang the launch", async (t) => {
  const fake = fakeChrome(t, ["http-hang", "ready"]);
  const browser = await launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500 });
  try {
    assert.equal(fake.calls.length, 2);
    assert.match(browser.attempts[0].diagnostic, /deadline/);
  } finally { await browser.close(); }
});

test("a browser that ignores SIGTERM is killed before the retry", async (t) => {
  const fake = fakeChrome(t, ["hang-ignore-term", "ready"]);
  const browser = await launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500 });
  try {
    assert.equal(fake.calls[0].child.signalCode, "SIGKILL");
    assert.equal(existsSync(fake.calls[0].profile), false);
  } finally { await browser.close(); }
});

test("Chrome waits through a transient EPERM group probe after SIGKILL before retrying", { skip: process.platform === "win32" }, async (t) => {
  let killed = false, injected = false;
  const shutdownSignals = [];
  const fake = fakeChrome(t, ["hang-ignore-term", "ready"], (calls) => {
    if (calls.length === 1) {
      assert.equal(injected, true, "the post-kill probe exercised EPERM");
      assert.throws(() => nativeKill(-calls[0].child.pid, 0), { code: "ESRCH" }, "the group must disappear before retry");
      assert.equal(existsSync(calls[0].profile), false);
    }
  });
  const spawnChrome = (...args) => {
    const child = fake.spawnChrome(...args);
    const kill = child.kill.bind(child);
    child.kill = (signal) => {
      shutdownSignals.push([child.pid, signal]);
      return kill(signal);
    };
    return child;
  };
  t.mock.method(process, "kill", (pid, signal) => {
    if (pid === -fake.calls[0]?.child.pid && signal !== 0) shutdownSignals.push([pid, signal]);
    if (pid === -fake.calls[0]?.child.pid) {
      if (signal === "SIGKILL") killed = true;
      if (signal === 0 && killed && !injected) {
        injected = true;
        throw Object.assign(new Error("group awaiting reap"), { code: "EPERM" });
      }
    }
    return nativeKill(pid, signal);
  });
  const browser = await launchChrome("fake-chrome", { spawnChrome, timeoutMs: 1500 });
  try {
    assert.equal(fake.calls.length, 2);
    assert.equal(fake.calls[0].child.signalCode, "SIGKILL");
    assert.deepEqual(shutdownSignals.slice(0, 2), [
      [fake.calls[0].child.pid, "SIGTERM"],
      [-fake.calls[0].child.pid, "SIGKILL"],
    ], "fallback signals the browser PID before its owned process group");
  } finally { await browser.close(); }
});

test("Chrome refuses retry when EPERM probes never establish group disappearance", { skip: process.platform === "win32" }, async (t) => {
  const fake = fakeChrome(t, ["exit", "ready"]);
  t.mock.method(process, "kill", (pid, signal) => {
    if (pid === -fake.calls[0]?.child.pid && signal === 0) {
      throw Object.assign(new Error("group remains unsignalable"), { code: "EPERM" });
    }
    return nativeKill(pid, signal);
  });
  await assert.rejects(launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500 }), /process tree did not exit after SIGKILL/);
  assert.equal(fake.calls.length, 1, "an unverified group never competes with a retry");
});

test("simultaneous browser launches use independent profiles and ports", async (t) => {
  const fake = fakeChrome(t, ["ready", "ready"]);
  const browsers = await Promise.all([0, 1].map(() => launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500 })));
  t.after(() => Promise.all(browsers.map((browser) => browser.close())));
  assert.notEqual(fake.calls[0].profile, fake.calls[1].profile);
  assert.notEqual(browsers[0].pageWsUrl, browsers[1].pageWsUrl);
});

test("fake Chrome after hooks leave no fixture process alive", async (t) => {
  const pids = [];
  const profiles = [];
  await t.test("starts fixtures without explicit browser cleanup", async (t) => {
    const fake = fakeChrome(t, ["ready", "hang-ignore-term"]);
    const first = await launchChrome("fake-chrome", { spawnChrome: fake.spawnChrome, timeoutMs: 1500 });
    pids.push(fake.calls[0].child.pid);
    profiles.push(fake.calls[0].profile);
    await first.close();
    const second = fake.spawnChrome("fake-chrome", [`--user-data-dir=${path.join(tmpdir(), `fixture-orphan-${process.pid}`)}`], {
      stdio: ["ignore", "ignore", "pipe"], detached: process.platform !== "win32",
    });
    pids.push(second.pid);
    profiles.push(fake.calls[1].profile);
  });
  assert.ok(pids.every((pid) => !processAlive(pid)), `fixture processes survived: ${pids.filter(processAlive).join(", ")}`);
  assert.ok(profiles.every((profile) => !existsSync(profile)), "fake Chrome profiles survive after-hook cleanup");
});

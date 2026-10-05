import { readdir } from "node:fs/promises";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const BROWSER_SHIM = "workspace-command-center-browser.test.mjs";
const LAUNCH_REGRESSIONS = "chrome-launch.test.mjs";

export function suiteBatches(files) {
  const suites = files.filter((file) => /\.test\.(?:js|mjs)$/.test(file)).sort();
  if (!suites.includes(BROWSER_SHIM)) throw new Error("Chrome browser shim is missing from the Node suites");
  const browsers = suites.filter((file) => file !== BROWSER_SHIM && /-browser\.test\.(?:js|mjs)$/.test(file));
  return [[BROWSER_SHIM], suites.filter((file) => file === LAUNCH_REGRESSIONS),
    ...browsers.map((file) => [file]),
    suites.filter((file) => ![BROWSER_SHIM, LAUNCH_REGRESSIONS, ...browsers].includes(file))];
}

async function main() {
  // Chrome's cold first launch must not compete with the bulk MCP test pool.
  // Keep the browser shim intact (its inventory test enforces all imports),
  // Run the timing-sensitive launch fixtures alone too, then run the rest
  // with Node's normal parallelism. No suite runs twice.
  for (const batch of suiteBatches(await readdir(path.join(ROOT, "test")))) {
    if (!batch.length) continue;
    const code = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, ["--test", ...batch.map((file) => path.join(ROOT, "test", file))], { cwd: ROOT, stdio: "inherit" });
      child.once("error", reject);
      child.once("close", (exitCode) => resolve(exitCode ?? 1));
    });
    if (code !== 0) { process.exitCode = code; return; }
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}

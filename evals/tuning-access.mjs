// Cooperative Node tuning integrity guard. Not an OS security sandbox.
import fs from "node:fs";
import promises from "node:fs/promises";
import child from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

let running = false;

export async function withTuningAccess(blockedPaths, work, allowedCommands = []) {
  if (running) throw new Error("overlapping tuning access guards are forbidden");
  running = true;
  const violations = [];
  const originals = [];
  const canonical = path => {
    const name = path instanceof URL ? fileURLToPath(path) : String(path);
    try { return fs.realpathSync(name); } catch { return resolve(name); }
  };
  const blocked = blockedPaths.map(canonical);
  const inodes = blocked.filter(p => fs.existsSync(p)).map(p => fs.statSync(p));
  const check = path => {
    if (typeof path === "number") throw new Error("untracked file descriptor forbidden during tuning");
    const target = canonical(path);
    let alias = blocked.includes(target);
    if (!alias && fs.existsSync(target)) {
      const stat = fs.statSync(target);
      alias = inodes.some(x => x.dev === stat.dev && x.ino === stat.ino);
    }
    if (alias) {
      violations.push("final_access");
      throw new Error("final_access: tuning cannot read final or raw mixed-source files");
    }
  };
  const patch = (object, name, verify) => {
    const original = object[name];
    originals.push(() => { object[name] = original; });
    object[name] = function (...args) { verify(...args); return original.apply(this, args); };
  };
  for (const name of ["readFileSync", "readFile", "openSync", "open", "createReadStream"])
    patch(fs, name, check);
  for (const name of ["readFile", "open"]) patch(promises, name, check);
  for (const name of ["spawnSync", "spawn", "exec", "execSync", "execFile", "execFileSync", "fork"])
    patch(child, name, (command, args) => {
      if (!allowedCommands.some(x => JSON.stringify(x) === JSON.stringify([command, args]))) {
        violations.push("unmonitored_execution");
        throw new Error("unmonitored_execution: tuning child process refused");
      }
    });
  syncBuiltinESMExports();
  try {
    const result = await work();
    if (violations.length) throw new Error("final_access: tuning audit failed after a caught violation");
    return result;
  } finally {
    for (const restore of originals.reverse()) restore();
    syncBuiltinESMExports();
    running = false;
  }
}

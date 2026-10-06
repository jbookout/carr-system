// Cooperative Node tuning integrity guard. Not an OS security sandbox.
import fs from "node:fs";
import promises from "node:fs/promises";
import child from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

let running = false;

export async function withTuningAccess(blockedPaths, work, allowedCommands = [], manifestPath = null) {
  if (running) throw new Error("overlapping tuning access guards are forbidden");
  running = true;
  const violations = [];
  const originals = [];
  let attempt = null, failed = true;
  const auditCommand = (command, args = []) => {
    const frontDoor = resolve(dirname(fileURLToPath(import.meta.url)), "rule-delivery/freeze_split.py");
    const run = child.spawnSync("python3", [frontDoor, command, manifestPath, ...args], { encoding: "utf8" });
    if (run.status !== 0) throw new Error(`tuning audit persistence failed: ${run.stderr}`);
    return JSON.parse(run.stdout);
  };
  let result, audit;
  try {
    if (manifestPath) attempt = auditCommand("audit-start");
    const canonical = path => {
      const name = path instanceof URL ? fileURLToPath(path) : String(path);
      try { return fs.realpathSync(name); } catch { return resolve(name); }
    };
    const blocked = blockedPaths.map(canonical);
    const inodes = blocked.filter(p => fs.existsSync(p)).map(p => fs.statSync(p));
    const check = path => {
      if (typeof path === "number" || (path && typeof path.fd === "number")) {
        violations.push({ event: "descriptor", reason: "untracked_descriptor" });
        throw new Error("untracked file descriptor forbidden during tuning");
      }
      const target = canonical(path);
      let alias = blocked.includes(target);
      if (!alias && fs.existsSync(target)) {
        const stat = fs.statSync(target);
        alias = inodes.some(x => x.dev === stat.dev && x.ino === stat.ino);
      }
      if (alias) {
        violations.push({ event: "open", reason: "final_access" });
        throw new Error("final_access: tuning cannot read final or raw mixed-source files");
      }
    };
    // Reject inherited final handles before FileHandle methods or synchronous
    // descriptor reads can bypass the path checks. No OS sandbox is claimed.
    const fdDir = fs.existsSync("/proc/self/fd") ? "/proc/self/fd" : "/dev/fd";
    for (const name of fs.readdirSync(fdDir)) {
      let stat;
      try { stat = fs.fstatSync(Number(name)); } catch { continue; }
      if (inodes.some(x => x.dev === stat.dev && x.ino === stat.ino)) {
        violations.push({ event: "inherited_descriptor", reason: "final_access" });
        throw new Error("final_access: inherited final or raw-source descriptor");
      }
    }
    const patch = (object, name, verify) => {
      const original = object[name];
      originals.push(() => { object[name] = original; });
      object[name] = function (...args) { verify(...args); return original.apply(this, args); };
    };
    for (const name of ["readFileSync", "readFile", "openSync", "open", "createReadStream"])
      patch(fs, name, check);
    for (const name of ["readSync", "read", "readvSync", "readv"]) patch(fs, name, check);
    for (const name of ["readFile", "open"]) patch(promises, name, check);
    for (const name of ["spawnSync", "spawn", "exec", "execSync", "execFile", "execFileSync", "fork"])
      patch(child, name, (command, args) => {
        if (!allowedCommands.some(x => JSON.stringify(x) === JSON.stringify([command, args]))) {
          violations.push({ event: "child_process", reason: "unmonitored_execution" });
          throw new Error("unmonitored_execution: tuning child process refused");
        }
      });
    syncBuiltinESMExports();
    result = await work();
    if (violations.length) throw new Error(`${violations.map(v => v.reason).join(",")}: tuning audit failed after a caught violation`);
    failed = false;
  } finally {
    for (const restore of originals.reverse()) restore();
    syncBuiltinESMExports();
    running = false;
    if (attempt) {
      attempt.violations = violations;
      audit = auditCommand("audit-finish", [JSON.stringify(attempt), ...(failed ? ["--failed"] : [])]);
    }
  }
  return manifestPath ? { result, audit } : result;
}

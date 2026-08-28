#!/usr/bin/env node
// Lease-bound Engineering Passport controller.  This is a local adapter, not
// an MCP verb: it owns the fixed carr_jobs connection, calls only the scoped
// engineering claim function, and gives the Codex child no database capability.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { Pool } from "@neondatabase/serverless";
import { runEngineeringWorker } from "../src/engineering-runtime.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const ADAPTER = path.join(REPO, "tools", "room-bridge", "engineering_dispatch_adapter.py");
const PYTHON = path.join(REPO, ".venv", "bin", "python");
// This is an execution identity, never a controller/operator option.  The
// adapter resolves and validates this exact desk before the envelope is sent.
const DESK = "engineering-codex";
const WORKER = "room-bridge-engineering-controller";

class ControllerError extends Error {
  constructor(payload) { super(JSON.stringify(payload)); this.payload = payload; }
}

function jobsDsn() {
  const value = process.env.CARR_DB_JOBS_URL;
  if (typeof value !== "string" || !value) throw new ControllerError({ error: "engineering_jobs_dsn_missing" });
  let parsed;
  try { parsed = new URL(value); } catch { throw new ControllerError({ error: "engineering_jobs_dsn_invalid" }); }
  if (!/^postgres(?:ql)?:$/.test(parsed.protocol) || decodeURIComponent(parsed.username) !== "carr_jobs")
    throw new ControllerError({ error: "engineering_jobs_dsn_not_carr_jobs" });
  return value;
}

function safeAdapterEnv() {
  // Deliberately constructed rather than redacted: a future credential name
  // cannot accidentally cross from the controller to a model process.
  const permitted = ["HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TERM"];
  return Object.fromEntries(permitted.filter(key => process.env[key]).map(key => [key, process.env[key]]));
}

function runAdapter(input) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [ADAPTER], { env: safeAdapterEnv(), stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.on("data", chunk => { stderr += chunk; });
    child.on("error", () => reject(new ControllerError({ error: "engineering_adapter_unavailable" })));
    child.on("close", code => {
      if (code !== 0) return reject(new ControllerError({ error: "engineering_adapter_refused", exit_code: code }));
      try {
        const parsed = JSON.parse(stdout);
        if (!parsed?.ok || !parsed.receipt || typeof parsed.receipt !== "object")
          throw new Error("invalid adapter response");
        resolve(parsed.receipt);
      } catch {
        reject(new ControllerError({ error: "engineering_adapter_response_invalid" }));
      }
    });
    child.stdin.end(JSON.stringify(input));
  });
}

function preflightDedicatedDesk() {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [ADAPTER, "--preflight"], {
      env: safeAdapterEnv(), stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.on("error", () => reject(new ControllerError({ error: "engineering_desk_preflight_unavailable" })));
    child.on("close", code => {
      if (code !== 0) return reject(new ControllerError({ error: "engineering_desk_preflight_refused" }));
      try {
        const row = JSON.parse(stdout);
        if (row?.ok !== true || row?.desk?.name !== DESK || row?.desk?.kind !== "codex-session") throw new Error("bad preflight");
        resolve();
      } catch {
        reject(new ControllerError({ error: "engineering_desk_preflight_invalid" }));
      }
    });
  });
}

async function main() {
  // Prove the exact unseated, model-pinned desk before opening the jobs pool.
  // A missing or altered desk therefore cannot claim a live lease.
  await preflightDedicatedDesk();
  const pool = new Pool({ connectionString: jobsDsn() });
  try {
    const client = await pool.connect();
    try {
      const result = await runEngineeringWorker({
        c: client, worker: WORKER, desk: DESK, ToolError: ControllerError,
        dispatchEnvelope: async (_desk, envelope, task) => runAdapter({
          desk: DESK, envelope, task,
          executor_slug: "codex",
        }),
      });
      // This is the operator readback: references and states only, never model
      // text, task content, or a credential.
      console.log(JSON.stringify({ ok: true, worker: WORKER, claimed: result.claimed,
        completed: result.completed, results: result.results }, null, 0));
    } finally {
      client.release();
    }
  } finally {
    await pool.end();
  }
}

main().catch(error => {
  const payload = error instanceof ControllerError ? error.payload : { error: "engineering_controller_failed" };
  console.error(JSON.stringify(payload));
  process.exitCode = 1;
});

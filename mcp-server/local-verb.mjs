// local-verb.mjs — run ONE registered verb against a database from this Mac.
//
// Why it exists: the Worker's deploy is a human tap, so a verb has to be
// provable BEFORE it is live. This imports the real `src/tools.js` registry —
// the same handler code the Worker runs, not a reimplementation — and calls it
// over a plain connection, so a rehearsal on a Neon branch exercises the actual
// verb rather than a description of it.
//
//   DATABASE_URL=<branch url> node local-verb.mjs <verb> '<json args>'
//
// IDENTITY, Phase 1 (2026-08-13, council-ordered, closes the caller-supplied-
// identity hole). This used to take the actor as a THIRD ARGV SLUG, defaulting
// to "joe" when omitted — any local caller could claim any identity, and every
// historical receipt written through this path is unverifiable because of it.
// There is no argv slug anymore. The actor is derived from ONE place:
// ~/.config/carr/local-actor.json, written once per machine by
// bin/set-local-actor.sh. Both reads and writes require it — see the
// resolveIdentity() block below for why reads are not exempted.
//
// THE HONEST LIMIT (stated again at set-local-actor.sh): this closes
// ACCIDENTAL and DEFAULT impersonation — a stray argv, a copy-pasted "joe"
// that should have been "dell". It does NOT stop a DELIBERATE local attacker:
// any process running as this Mac's own user can read or overwrite
// local-actor.json exactly as this script does. That boundary is the server
// side's job (the actor table on write, the OAuth-issued token on the real
// MCP path) — this file only makes the ordinary, unintentional case
// impossible, not the adversarial one.
//
// PRODUCTION IS REACHABLE, reads and writes alike (Joe's ruling 2026-08-09,
// loop #258 — the old CARR_LOCAL_VERB_ALLOW_PRODUCTION rail is retired and the
// variable is no longer read anywhere). A production write prints a warning
// naming the verb and the host; it is not blocked. The full reasoning sits at
// the check itself, below the TOOLS lookup.

import { Pool, neonConfig } from "@neondatabase/serverless";
import ws from "ws";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { TOOLS, ToolError, executeRegisteredTool } from "./src/tools.js";

// The `ws` package, NOT Node's built-in WebSocket: under Node 26 the native
// constructor makes the driver die with an unhandled ErrorEvent before any
// query runs (measured 2026-08-06, first real use of this harness). `ws` is
// already in node_modules and works.
neonConfig.webSocketConstructor = ws;

const cliArgs = process.argv.slice(2);
const [verb, rawArgs = "{}"] = cliArgs;
if (!verb || !process.env.DATABASE_URL) {
  console.error("usage: DATABASE_URL=... node local-verb.mjs <verb> '<json args>'");
  process.exit(2);
}
// The old third argv (actor slug) is GONE, not silently ignored: a caller
// still passing one is almost always stale muscle memory or an unfixed
// wrapper, and swallowing it quietly would hide exactly the impersonation
// path this change closes. Fail loud and say why.
if (cliArgs.length > 2) {
  console.error(
    `local-verb.mjs no longer takes an actor-slug argv (got extra arg "${cliArgs[2]}"). ` +
    "Identity is derived from ~/.config/carr/local-actor.json — see resolveIdentity() " +
    "below, or run bin/set-local-actor.sh <slug> if that file does not exist yet."
  );
  process.exit(2);
}

// ---------- identity: derive the actor from the machine, not the caller ----------

const IDENTITY_PATH = path.join(os.homedir(), ".config", "carr", "local-actor.json");
const SETUP_COMMAND = `bin/set-local-actor.sh <slug>  (run from the repo root; use "joe" on this Mac)`;

function resolveIdentity() {
  // Both reads AND writes require this file. The tempting shortcut — let a
  // read proceed with a loud warning and no resolved actor — was considered
  // and rejected: personal-scope reads (personal_to, personal-scope filters)
  // depend on WHICH human is asking just as much as a write does, so a
  // warn-and-guess read would leak or hide rows for the wrong person instead
  // of failing loudly. Requiring the file everywhere means the failure mode
  // is always "stop and run one command," never "ran, but as someone else."
  let raw;
  try {
    raw = fs.readFileSync(IDENTITY_PATH, "utf8");
  } catch (e) {
    if (e.code === "ENOENT") {
      console.error(
        `no local identity set — this Mac has never run set-local-actor.sh.\n` +
        `Run this once:\n\n    ${SETUP_COMMAND}\n`
      );
      process.exit(2);
    }
    console.error(`could not read ${IDENTITY_PATH}: ${e.message}`);
    process.exit(2);
  }
  let identity;
  try {
    identity = JSON.parse(raw);
  } catch (e) {
    console.error(`${IDENTITY_PATH} is not valid JSON (${e.message}). Re-run:\n\n    ${SETUP_COMMAND} --force\n`);
    process.exit(2);
  }
  const actorSlug = identity && identity.actor_slug;
  if (!actorSlug || typeof actorSlug !== "string") {
    console.error(`${IDENTITY_PATH} is missing "actor_slug". Re-run:\n\n    ${SETUP_COMMAND} --force\n`);
    process.exit(2);
  }
  return actorSlug;
}

const slug = resolveIdentity();
const VIA = "local-verb/identity-file";

const url = process.env.DATABASE_URL;
if (!/ep-|neon\.tech/.test(url)) { console.error("DATABASE_URL does not look like a Neon url"); process.exit(2); }
console.error(`local-verb identity -> ${slug} (via ${VIA}, from ${IDENTITY_PATH})`);
// Production's endpoint, MEASURED today rather than guessed:
//   neonctl connection-string production ... -> ep-restless-resonance-awbp35k3
// The first version of this rail named an endpoint that does not exist, so it
// would have refused nothing. A guard that cannot fire is worse than no guard,
// because it is trusted. Hence the second half: the target host is always
// printed, so a human reading the output can see what it actually connected to
// even if this constant ever goes stale (a recreated endpoint changes it).
const PRODUCTION_ENDPOINT = "ep-restless-resonance-awbp35k3";
const host = (url.match(/@([^/?]+)/) || [])[1] || "(unparsed)";
console.error(`local-verb -> ${host}`);

const tool = TOOLS[verb];
if (!tool) { console.error(`unknown verb ${verb}; known: ${Object.keys(TOOLS).join(", ")}`); process.exit(2); }

// THE PRODUCTION RAIL WAS RETIRED 2026-08-09 BY JOE'S RULING (loop #258).
// Its history, kept because the reasoning is the useful part: it originally
// refused EVERY verb against production, which blocked reads as harmless as
// `list-verbs` while protecting nothing. Narrowing it to writes (2026-08-08)
// fixed that half. Joe then ruled the write half open as well: a terminal may
// write production through `run.sh call`.
//
// The argument he ruled on, recorded so nobody reinstates the rail by reflex:
// the actor plumbing is IDENTICAL either way — local-verb resolves the actor
// slug against the actor table, refuses a human_only verb to a non-human, and
// every write still carries its idempotency key and tool_call row. The rail was
// never the thing making a write traceable; the verb layer is. What the rail
// actually bought was a pause before a mistyped verb at a prompt reached live
// records, and Joe judged that cost higher than its benefit for a solo operator
// who hits it while doing ordinary work.
//
// WHAT REPLACES IT is visibility, not obstruction. The target host is printed
// on every run, and a production WRITE announces itself with the verb named, so
// the human always knows which database is about to change and why. A warning
// that cannot block is honest about what it is; a guard that is always
// overridden is theatre, and theatre teaches people to stop reading.
if (tool.write && url.includes(PRODUCTION_ENDPOINT)) {
  console.error(`⚠ PRODUCTION WRITE — ${verb} is about to change live records on ${host}.`);
}

const args = JSON.parse(rawArgs);
const pool = new Pool({ connectionString: url });
const client = await pool.connect();
try {
  if (!tool.write) {
    // Through executeRegisteredTool, NOT tool.handler directly (loop 353,
    // 2026-08-13). That function's own comment already called itself "the one
    // choke point ... so the fix lands once instead of being re-implemented per
    // caller", and named local-verb.mjs among its callers — but this file was
    // in fact bypassing it, so the CLI silently missed both the argument type
    // coercion and the raw-DB-error translation the Worker path has. Rule
    // a8c55a47: a manual path and an automated path that do the same job must
    // be the same code.
    const out = await executeRegisteredTool(client, { slug, human: true, kind: "human", via: VIA }, verb, args);
    console.log(JSON.stringify(out, null, 2));
  } else {
    await client.query("begin");
    const a = await client.query("select id, kind from actor where slug=$1", [slug]);
    if (!a.rows.length) throw new Error(`no actor ${slug}`);
    const actor = { slug, human: a.rows[0].kind === "human", kind: a.rows[0].kind, id: a.rows[0].id, via: VIA };
    if (tool.humanOnly && !actor.human) throw new Error("human_only verb");
    const out = await executeRegisteredTool(client, actor, verb, args);
    await client.query("commit");
    console.log(JSON.stringify(out, null, 2));
  }
} catch (e) {
  await client.query("rollback").catch(() => {});
  if (e instanceof ToolError) { console.error("TOOL ERROR " + JSON.stringify(e.payload, null, 2)); process.exit(1); }
  console.error(e);
  process.exit(1);
} finally {
  client.release();
  await pool.end();
}

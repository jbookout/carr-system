// local-verb.mjs — run ONE registered verb against a database from this Mac.
//
// Why it exists: the Worker's deploy is a human tap, so a verb has to be
// provable BEFORE it is live. This imports the real `src/tools.js` registry —
// the same handler code the Worker runs, not a reimplementation — and calls it
// over a plain connection, so a rehearsal on a Neon branch exercises the actual
// verb rather than a description of it.
//
//   DATABASE_URL=<branch url> node local-verb.mjs <verb> '<json args>' [actor-slug]
//
// PRODUCTION IS REACHABLE, reads and writes alike (Joe's ruling 2026-08-09,
// loop #258 — the old CARR_LOCAL_VERB_ALLOW_PRODUCTION rail is retired and the
// variable is no longer read anywhere). A production write prints a warning
// naming the verb and the host; it is not blocked. The full reasoning sits at
// the check itself, below the TOOLS lookup.

import { Pool, neonConfig } from "@neondatabase/serverless";
import ws from "ws";
import { TOOLS, ToolError } from "./src/tools.js";

// The `ws` package, NOT Node's built-in WebSocket: under Node 26 the native
// constructor makes the driver die with an unhandled ErrorEvent before any
// query runs (measured 2026-08-06, first real use of this harness). `ws` is
// already in node_modules and works.
neonConfig.webSocketConstructor = ws;

const [verb, rawArgs = "{}", slug = "joe"] = process.argv.slice(2);
const url = process.env.DATABASE_URL;
if (!verb || !url) {
  console.error("usage: DATABASE_URL=... node local-verb.mjs <verb> '<json args>' [actor-slug]");
  process.exit(2);
}
if (!/ep-|neon\.tech/.test(url)) { console.error("DATABASE_URL does not look like a Neon url"); process.exit(2); }
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
    const out = await tool.handler(client, { slug, human: true, kind: "human" }, args);
    console.log(JSON.stringify(out, null, 2));
  } else {
    await client.query("begin");
    const a = await client.query("select id, kind from actor where slug=$1", [slug]);
    if (!a.rows.length) throw new Error(`no actor ${slug}`);
    const actor = { slug, human: a.rows[0].kind === "human", kind: a.rows[0].kind, id: a.rows[0].id };
    if (tool.humanOnly && !actor.human) throw new Error("human_only verb");
    const out = await tool.handler(client, actor, args);
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

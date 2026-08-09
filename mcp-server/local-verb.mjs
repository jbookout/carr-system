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
// SAFETY RAIL: a WRITE verb refuses to run against the production branch unless
// CARR_LOCAL_VERB_ALLOW_PRODUCTION=1 is set. Production writes are a human's
// tap; this tool exists for branch rehearsal and must not become a side door.
// READ verbs against production pass freely (narrowed 2026-08-08, loop #258) —
// the rail used to refuse them too, which protected nothing and made the
// override a habit rather than a decision.

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

// THE PRODUCTION RAIL, narrowed to WRITES on 2026-08-08 (loop #258, Joe's call).
// It used to sit above the TOOLS lookup and refuse every verb, which meant a
// read as harmless as `list-verbs` was blocked against production while
// protecting nothing — the MCP connector performs those same reads constantly.
// A guard that fires where there is no danger is not extra safety; it teaches
// the human to reach for the override out of habit, and an override reached for
// by habit is exactly the side door the rail exists to prevent.
//
// The WRITE posture is deliberately unchanged: production writes remain a
// human's tap, and this tool remains a rehearsal harness for them. Reads now
// pass; writes still refuse unless CARR_LOCAL_VERB_ALLOW_PRODUCTION=1.
if (tool.write && !process.env.CARR_LOCAL_VERB_ALLOW_PRODUCTION && url.includes(PRODUCTION_ENDPOINT)) {
  console.error(`refusing: ${verb} WRITES and that is the production endpoint. This tool is for branch rehearsal; production writes are a human's deliberate tap (set CARR_LOCAL_VERB_ALLOW_PRODUCTION=1 if you mean it).`);
  process.exit(2);
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

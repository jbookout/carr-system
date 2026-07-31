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
// SAFETY RAIL: it refuses to run against the production branch unless
// CARR_LOCAL_VERB_ALLOW_PRODUCTION=1 is set. Production writes are a human's
// tap; this tool exists for branch rehearsal and must not become a side door.

import { Pool, neonConfig } from "@neondatabase/serverless";
import { TOOLS, ToolError } from "./src/tools.js";

if (typeof WebSocket !== "undefined") neonConfig.webSocketConstructor = WebSocket;

const [verb, rawArgs = "{}", slug = "joe"] = process.argv.slice(2);
const url = process.env.DATABASE_URL;
if (!verb || !url) {
  console.error("usage: DATABASE_URL=... node local-verb.mjs <verb> '<json args>' [actor-slug]");
  process.exit(2);
}
if (!/ep-|neon\.tech/.test(url)) { console.error("DATABASE_URL does not look like a Neon url"); process.exit(2); }
if (!process.env.CARR_LOCAL_VERB_ALLOW_PRODUCTION) {
  // Production's endpoint host is the default branch's; a branch url carries its
  // own endpoint id, so a substring check on the known production endpoint is
  // enough to keep this off the live database by accident.
  if (url.includes("ep-lively-mountain") || url.includes("br-summer-breeze")) {
    console.error("refusing: that looks like production. Branch rehearsal only.");
    process.exit(2);
  }
}

const tool = TOOLS[verb];
if (!tool) { console.error(`unknown verb ${verb}; known: ${Object.keys(TOOLS).join(", ")}`); process.exit(2); }

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

import { acquirePostgresFixtureGroup } from "../../mcp-server/test/helpers/disposable-postgres.mjs";
// Synthetic SQL evaluation support, imported by tests. No production DSN or writes.
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, renameSync } from "node:fs";
import { createRequire } from "node:module";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";

const require = createRequire(new URL("../../mcp-server/package.json", import.meta.url));
const { Client } = require("pg");
export const fixturePath = new URL("./fixtures/bounded-record-questions.v1.json", import.meta.url);
export const baselinePath = new URL("./baselines/bounded-record-strict-fts.v1.json", import.meta.url);
export const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
export const fixtureDigest = createHash("sha256").update(readFileSync(fixturePath)).digest("hex");

function binary(name) {
  // Debian/Ubuntu install server binaries outside PATH. The unit runner has
  // a packaged server but only the gates runner explicitly installs version 17.
  const linuxRoot = "/usr/lib/postgresql";
  const installed = existsSync(linuxRoot) ? readdirSync(linuxRoot)
    .filter(version => /^\d+$/.test(version) && Number(version) >= 14)
    .sort((a,b) => Number(b)-Number(a)).map(version => join(linuxRoot,version,"bin")) : [];
  for (const dir of ["/opt/homebrew/opt/postgresql@17/bin", ...installed, ...(process.env.PATH || "").split(":")])
    if (existsSync(join(dir, name)) && existsSync(join(dir, "postgres"))) return join(dir, name);
  throw new Error(`PostgreSQL test prerequisite missing: ${name}`);
}
function command(file, args) {
  const r = spawnSync(file, args, { encoding: "utf8", timeout: 30000 });
  if (r.error || r.status !== 0) throw new Error(`${file}: ${r.error?.message || r.stderr}`);
}

// A socket-only disposable cluster. Never connects to a supplied URL.
export async function syntheticDatabase() {
  const dir = mkdtempSync(join(tmpdir(), "br-"));
  const data = join(dir, "pg");
  const ctl = binary("pg_ctl");
  const releaseBudget = await acquirePostgresFixtureGroup();
  let running = false;
  try {
    command(binary("initdb"), ["-D", data, "-U", "synthetic_owner", "-A", "trust", "--no-locale", "--encoding=UTF8"]);
    command(ctl, ["-D", data, "-l", join(dir, "server.log"), "-o", `-F -k ${dir} -c listen_addresses=''`, "-w", "start"]);
    running = true;
  } catch (error) {
    try {
      if (existsSync(join(data, "postmaster.pid"))) command(ctl, ["-D", data, "-w", "stop"]);
    } finally {
      await releaseBudget();
    }
    throw error;
  }
  const client = new Client({ host: dir, user: "synthetic_owner", database: "postgres" });
  const close = async () => {
    try {
      try { await client.end(); } finally {
        if (running) command(ctl, ["-D", data, "-w", "stop"]);
        const staged = join(tmpdir(), "_to_delete");
        mkdirSync(staged, { recursive: true });
        renameSync(dir, join(staged, dir.split("/").at(-1)));
      }
    } finally {
      await releaseBudget();
    }
  };
  try {
    await client.connect();
    // Columns match existing projections; fixture setup is confined to this
    // ephemeral cluster. Production migrations and the registry are untouched.
    await client.query(`
      create role carr_reader;
      create table actor(id uuid primary key, slug text);
      create function public.retrieval_visibility_actor_id(text) returns uuid
        language sql stable security definer as $$ select id from actor where slug=$1 $$;
      create table doctrine_document(id uuid primary key, slug text, title text,
        content_class text, visibility text, owner_actor_id uuid,
        title_search_vector tsvector generated always as (to_tsvector('english',title)) stored);
      create table doctrine_section(id uuid primary key, document_id uuid, section_key text,
        title text, status text, current_revision_id uuid, current_version bigint,
        title_search_vector tsvector generated always as (to_tsvector('english',title)) stored);
      create table doctrine_revision(id uuid primary key, section_id uuid, version bigint,
        plain_text text, content_hash text,
        search_vector tsvector generated always as (to_tsvector('english',plain_text)) stored);
      create table doctrine_edge(source_section_id uuid, target_section_id uuid, edge_type text,
        retired_by_revision_id uuid);
      create table memory_item(id uuid primary key, organization_tenant_id text, kind text,
        statement text, context text, scope text, owner_actor_id uuid, status text,
        promoted_by_actor_id uuid, promoted_at timestamptz, version bigint,
        search_vector tsvector generated always as (to_tsvector('english',coalesce(statement,'') || ' ' || coalesce(context,''))) stored);
      grant select on doctrine_document,doctrine_section,doctrine_revision,doctrine_edge,memory_item to carr_reader;
      grant execute on function retrieval_visibility_actor_id(text) to carr_reader;
    `);
    for (const [name, caller] of Object.entries(fixture.callers)) {
      if (caller.owner_actor_id) await client.query("insert into actor values ($1,$2)", [caller.owner_actor_id, caller.actor.sponsoring_human_slug]);
    }
    for (const [i, r] of fixture.records.entries()) {
      if (r.record_type === "doctrine") {
        // The doctrine schema has one server-owned tenant, no tenant column.
        if (r.organization_tenant_id !== "carr-internal") continue;
        const revision = `20000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`;
        await client.query("insert into doctrine_document(id,slug,title,content_class,visibility,owner_actor_id) values ($1,$2,$3,$4,$5,$6)",
          [r.record_id,r.doc_slug,"Synthetic handbook",r.content_class,r.visibility,r.owner_actor_id]);
        await client.query("insert into doctrine_section(id,document_id,section_key,title,status,current_revision_id,current_version) values ($1,$1,$2,$3,$4,$5,$6)",
          [r.record_id,r.section_key,r.title,r.status,revision,r.current_version]);
        await client.query("insert into doctrine_revision(id,section_id,version,plain_text,content_hash) values ($1,$2,$3,$4,$5)",
          [revision,r.record_id,r.version,r.body,r.content_hash]);
        if (r.superseded) await client.query("insert into doctrine_edge values ($1,$1,'SUPERSEDES',null)", [r.record_id]);
      } else {
        await client.query("insert into memory_item(id,organization_tenant_id,kind,statement,context,scope,owner_actor_id,status,promoted_by_actor_id,promoted_at,version) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
          [r.record_id,r.organization_tenant_id,r.content_class,r.body,r.title,r.scope,r.owner_actor_id,
            r.superseded ? "corrected" : r.status, r.promoted ? fixture.callers.amber.owner_actor_id : null,
            r.promoted ? "2026-01-01T00:00:00Z" : null,r.version]);
      }
    }
    await client.query("set role carr_reader");
    await client.query("set default_transaction_read_only=on");
    return { client, close };
  } catch (error) { await close(); throw error; }
}

// Frozen comparison lane: strict all-word FTS, with the eligibility intended
// for the new boundary. There was no unified retriever before this slice.
// This is a lexical reference, not a claim about live global-search recall.
export const BASELINE_SQL = `
  with records as (
    select s.id as record_id, rev.search_vector as vector
      from doctrine_section s join doctrine_document d on d.id=s.document_id
      join doctrine_revision rev on rev.id=s.current_revision_id and rev.section_id=s.id and rev.version=s.current_version
     where s.status='active' and d.content_class in ('playbook','sop','reference','rule')
       and (d.visibility='shared' or (d.visibility='personal' and d.owner_actor_id=$2::uuid))
       and not exists(select 1 from doctrine_edge e where e.target_section_id=s.id and e.edge_type='SUPERSEDES' and e.retired_by_revision_id is null)
    union all
    select m.id,m.search_vector from memory_item m
     where m.organization_tenant_id=$3 and m.status='promoted' and m.promoted_by_actor_id is not null and m.promoted_at is not null
       and (m.scope='shared' or (m.scope='personal' and m.owner_actor_id=$2::uuid))
  )
  select record_id from records where vector @@ plainto_tsquery('english',$1)
  order by ts_rank(vector,plainto_tsquery('english',$1)) desc,record_id limit 5`;

// Independent fixture oracle: do not import the production admission function.
// A regression in that function must not redefine what this eval accepts.
function forbiddenRecord(r, caller) {
  if (!r || !caller || r.organization_tenant_id !== "carr-internal") return true;
  if (!["shared", "personal"].includes(r.scope) || r.visibility !== r.scope) return true;
  if (r.scope === "personal" && (!caller.owner_actor_id || caller.owner_actor_id !== r.owner_actor_id)) return true;
  if (r.superseded !== false) return true;
  if (r.record_type === "doctrine") {
    const version = Number(r.version);
    return r.authority !== "governing" || !["playbook", "sop", "reference", "rule"].includes(r.content_class) ||
      r.status !== "active" || !r.revision_id || r.revision_id !== r.current_revision_id ||
      !Number.isInteger(version) || version < 1 || version !== Number(r.current_version);
  }
  if (r.record_type === "memory")
    return r.authority !== "context" || !["preference", "fact", "episodic", "procedural"].includes(r.content_class) ||
      r.status !== "promoted" || r.promoted !== true;
  return true;
}

export function measures(rows) {
  const positive = rows.filter(r => r.expected_ids.length);
  const recall = positive.reduce((sum,r) => sum + r.expected_ids.filter(id => r.ids.includes(id)).length / r.expected_ids.length,0) / positive.length;
  const leakage = rows.reduce((n,row) => n + row.ids.filter(id => {
    const r = fixture.records.find(r => r.record_id === id);
    return forbiddenRecord(r, fixture.callers[row.caller]);
  }).length,0);
  return { questions: rows.length, positive_questions: positive.length, recall_at_5: recall, out_of_scope_leakage: leakage };
}

export async function baselineRows(client) {
  const rows = [];
  for (const q of fixture.questions) {
    const result = await client.query(BASELINE_SQL,[q.question,fixture.callers[q.caller].owner_actor_id,"carr-internal"]);
    rows.push({ ...q, ids: result.rows.map(r => r.record_id) });
  }
  return rows;
}

export async function evaluate(client, search) {
  const rows = [];
  for (const q of fixture.questions) {
    const result = await search(client,fixture.callers[q.caller].actor,{ q: q.question, limit: 5 });
    rows.push({ ...q, ids: result.hits.map(r => r.record_id) });
  }
  return { rows, metrics: measures(rows) };
}

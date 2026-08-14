// Coverage and method ledger for systematic investigations (migration 0116).
//
// This module is deliberately model-agnostic. Deterministic code enumerates the
// surface and candidate set; model/runtime calls append assessment receipts;
// server-computed checkpoints reconcile every candidate before anything can be
// released. Nothing in this module executes a stored matcher, prompt, SQL
// fragment, or target verb.

import { personalScopeForActor } from "./identity.js";

const SHA256_RE = /^[0-9a-f]{64}$/;
const MATCHER_TYPES = new Set(["regex", "path", "metadata"]);
export const ASSESSMENT_OUTCOMES = Object.freeze([
  "pending", "validated", "rejected", "error", "skipped", "refused",
]);
const ASSESSMENT_OUTCOME_SET = new Set(ASSESSMENT_OUTCOMES);

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.keys(value).sort().reduce((out, key) => {
      if (value[key] !== undefined) out[key] = canonical(value[key]);
      return out;
    }, {});
  return value;
}

export async function canonicalDigest(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(canonical(value)));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
}

function onlyKeys(object, allowed) {
  return Object.keys(object || {}).filter(key => !allowed.has(key));
}

function validateGlobs(globs) {
  if (!Array.isArray(globs) || !globs.length || globs.length > 20)
    return { error: "matcher_globs_required", min: 1, max: 20 };
  for (const glob of globs) {
    if (typeof glob !== "string" || !glob.trim())
      return { error: "matcher_glob_invalid", glob };
    const value = glob.trim();
    if (value.startsWith("/") || value.includes("\\") || value.split("/").includes(".."))
      return { error: "matcher_glob_escapes_scope", glob: value };
    if (["*", "**", "**/*", "*.*", "**/*.*"].includes(value))
      return { error: "matcher_glob_catch_all", glob: value };
  }
  return null;
}

function unsafeRegex(pattern) {
  if (pattern.length > 512) return "pattern_too_long";
  if (/\\[1-9]/.test(pattern)) return "backreference";
  if (/\(\?<([=!])/.test(pattern)) return "lookbehind";
  if (/\{\s*(\d{4,})(?:\s*,\s*(\d*)?)?\s*\}/.test(pattern)) return "huge_repeat";
  // Conservative nested-repeat guard. It catches the common catastrophic
  // shapes without pretending to be a full regex complexity proof.
  if (/\((?:[^()]|\\.)*[+*](?:[^()]|\\.)*\)\s*(?:[+*]|\{)/.test(pattern))
    return "nested_repeat";
  return null;
}

export function validateMatcherDefinition(args) {
  if (!MATCHER_TYPES.has(args.matcher_type))
    return { error: "matcher_type_unknown", allowed: [...MATCHER_TYPES] };
  if (!args.matcher_key || typeof args.matcher_key !== "string")
    return { error: "matcher_key_required" };
  if (!(Number.isInteger(args.version) && args.version >= 1) &&
      !(typeof args.version === "string" && args.version.trim()))
    return { error: "matcher_version_invalid" };
  if (!args.spec || typeof args.spec !== "object" || Array.isArray(args.spec))
    return { error: "matcher_spec_invalid" };

  if (args.matcher_type === "regex") {
    const extras = onlyKeys(args.spec, new Set(["pattern", "flags", "file_globs"]));
    if (extras.length) return { error: "matcher_spec_unknown_fields", fields: extras };
    const globError = validateGlobs(args.spec.file_globs);
    if (globError) return globError;
    const pattern = args.spec.pattern;
    const flags = args.spec.flags || "";
    if (typeof pattern !== "string" || !pattern)
      return { error: "matcher_pattern_required" };
    const unsafe = unsafeRegex(pattern);
    if (unsafe) return { error: "matcher_regex_unsafe", reason: unsafe };
    if (/[^imsu]/.test(flags) || new Set(flags).size !== flags.length)
      return { error: "matcher_regex_flags_invalid", flags };
    let compiled;
    try { compiled = new RegExp(pattern, flags); }
    catch (error) { return { error: "matcher_regex_invalid", detail: String(error.message || error) }; }
    const examples = Array.isArray(args.examples) ? args.examples : [];
    if (examples.length < 2 || !examples.some(x => x && x.should_match === true) ||
        !examples.some(x => x && x.should_match === false))
      return { error: "matcher_examples_need_positive_and_negative" };
    for (const example of examples) {
      if (!example || typeof example.text !== "string" || typeof example.should_match !== "boolean")
        return { error: "matcher_example_invalid" };
      compiled.lastIndex = 0;
      if (compiled.test(example.text) !== example.should_match)
        return { error: "matcher_example_failed", text: example.text.slice(0, 120),
          expected: example.should_match };
    }
  } else if (args.matcher_type === "path") {
    const extras = onlyKeys(args.spec, new Set(["file_globs"]));
    if (extras.length) return { error: "matcher_spec_unknown_fields", fields: extras };
    const globError = validateGlobs(args.spec.file_globs);
    if (globError) return globError;
  } else {
    const extras = onlyKeys(args.spec, new Set(["field", "operator", "value"]));
    if (extras.length) return { error: "matcher_spec_unknown_fields", fields: extras };
    if (typeof args.spec.field !== "string" || !args.spec.field.trim())
      return { error: "matcher_metadata_field_required" };
    if (!["equals", "exists", "in", "contains"].includes(args.spec.operator))
      return { error: "matcher_metadata_operator_invalid" };
    if (args.spec.operator !== "exists" && args.spec.value === undefined)
      return { error: "matcher_metadata_value_required" };
  }
  return null;
}

export async function validateCandidateBatch(args) {
  const inventory = Array.isArray(args.inventory) ? args.inventory : [];
  const candidates = Array.isArray(args.candidates) ? args.candidates : [];
  if (args.declared_inventory_count !== inventory.length)
    return { error: "inventory_count_mismatch", declared: args.declared_inventory_count,
      received: inventory.length };
  if (args.declared_candidate_count !== candidates.length)
    return { error: "candidate_count_mismatch", declared: args.declared_candidate_count,
      received: candidates.length };
  if (inventory.length > 500 || candidates.length > 5000)
    return { error: "investigation_batch_too_large", inventory_max: 500, candidate_max: 5000 };

  const surfaces = new Set();
  for (const row of inventory) {
    if (!row || typeof row.surface_key !== "string" || !row.surface_key.trim())
      return { error: "inventory_surface_key_required" };
    if (surfaces.has(row.surface_key))
      return { error: "inventory_surface_duplicate", surface_key: row.surface_key };
    surfaces.add(row.surface_key);
    if (!Number.isInteger(row.item_count) || row.item_count < 0 ||
        !Number.isInteger(row.scanned_count) || row.scanned_count < 0 ||
        row.scanned_count > row.item_count)
      return { error: "inventory_counts_invalid", surface_key: row.surface_key };
    if (!Array.isArray(row.matcher_refs) || !Array.isArray(row.evidence_refs) ||
        !row.evidence_refs.length || !SHA256_RE.test(String(row.input_digest || "")))
      return { error: "inventory_evidence_incomplete", surface_key: row.surface_key };
  }

  const keys = new Set();
  const ordinals = [];
  for (const row of candidates) {
    if (!row || typeof row.candidate_key !== "string" || !row.candidate_key.trim())
      return { error: "candidate_key_required" };
    if (keys.has(row.candidate_key))
      return { error: "candidate_key_duplicate", candidate_key: row.candidate_key };
    keys.add(row.candidate_key);
    if (!surfaces.has(row.surface_key))
      return { error: "candidate_surface_not_in_inventory", candidate_key: row.candidate_key,
        surface_key: row.surface_key };
    if (!Number.isInteger(row.ordinal) || row.ordinal < 1)
      return { error: "candidate_ordinal_invalid", candidate_key: row.candidate_key };
    ordinals.push(row.ordinal);
    if (typeof row.subject_id !== "string" ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(row.subject_id))
      return { error: "candidate_subject_id_invalid", candidate_key: row.candidate_key };
    if (typeof row.matcher_key !== "string" || !row.matcher_key.trim() ||
        typeof row.matcher_version !== "string" || !row.matcher_version.trim())
      return { error: "candidate_matcher_required", candidate_key: row.candidate_key };
    if (!Array.isArray(row.evidence_refs) || !row.evidence_refs.length ||
        !SHA256_RE.test(String(row.input_digest || "")))
      return { error: "candidate_evidence_incomplete", candidate_key: row.candidate_key };
  }
  const expected = candidates.map((_, index) => index + 1);
  const actual = [...ordinals].sort((a, b) => a - b);
  if (JSON.stringify(actual) !== JSON.stringify(expected))
    return { error: "candidate_ordinals_not_contiguous", expected, received: actual };

  const computed = await canonicalDigest({ inventory, candidates });
  if (!SHA256_RE.test(String(args.batch_digest || "")) || args.batch_digest !== computed)
    return { error: "candidate_batch_digest_mismatch", computed, received: args.batch_digest || null };
  return null;
}

export function reconcileAssessmentRows(candidateCount, rows) {
  const counts = Object.fromEntries(["unattempted", ...ASSESSMENT_OUTCOMES].map(x => [x, 0]));
  const latest = new Map();
  for (const row of rows || []) {
    if (!ASSESSMENT_OUTCOME_SET.has(row.outcome))
      return { error: "assessment_outcome_unknown", outcome: row.outcome };
    // SQL returns rows newest-first. Keep the first row for each candidate.
    if (!latest.has(row.candidate_id)) latest.set(row.candidate_id, row.outcome);
  }
  counts.unattempted = Math.max(0, Number(candidateCount) - latest.size);
  for (const outcome of latest.values()) counts[outcome] += 1;
  const accounted = Object.values(counts).reduce((sum, value) => sum + value, 0);
  if (accounted !== Number(candidateCount))
    return { error: "assessment_counts_do_not_reconcile", candidate_count: Number(candidateCount),
      accounted, counts };
  const verdict = counts.unattempted || counts.pending ? "blocked"
    : counts.error || counts.skipped || counts.refused ? "degraded"
      : "complete";
  return { counts, accounted, verdict };
}

// Tool handlers are appended below the pure validation functions so the test
// suite can exercise the safety contract without a database.
export function investigationMethodTools({ withEnvelope, writeEvent, ToolError }) {
  const digestSchema = { type: "string", pattern: "^[0-9a-f]{64}$" };
  const idempotency = { idempotency_key: { type: "string" } };

  async function require0116(c) {
    const r = await c.query(
      `select to_regclass('public.investigation_surface') is not null as surfaces,
              to_regclass('public.investigation_method_policy') is not null as policies,
              to_regclass('public.investigation_candidate') is not null as candidates,
              to_regclass('public.investigation_wave_reservation') is not null as reservations,
              to_regclass('public.investigation_wave') is not null as waves,
              to_regclass('public.investigation_phase_checkpoint') is not null as checkpoints,
              to_regclass('public.investigation_checkpoint_candidate') is not null as members`);
    const present = r.rows[0];
    if (present.surfaces && present.policies && present.candidates && present.reservations &&
        present.waves && present.checkpoints && present.members) return;
    throw new ToolError({ error: "migration_not_applied",
      migration: "0116_investigation_method_ledger", present,
      hint: "apply migration 0116 before using investigation method verbs; nothing was written" });
  }

  async function ownedOpenRun(c, actor, runId, lock = false) {
    const r = await c.query(
      `select r.* from investigation_run r where r.id=$1 ${lock ? "for update" : ""}`,
      [runId]);
    if (!r.rows.length)
      throw new ToolError({ error: "investigation_not_found", run_id: runId });
    const scope = personalScopeForActor(actor);
    if (scope.status !== "personal")
      throw new ToolError({ error: "sponsored_owner_required",
        hint: "method work must remain attributable to a verified CARR sponsor" });
    const owner = await c.query(`select id from actor where slug=$1 and active`, [scope.sponsor]);
    if (!owner.rows.length || r.rows[0].owner_actor_id !== owner.rows[0].id)
      throw new ToolError({ error: "investigation_owner_only" });
    if (r.rows[0].status !== "open")
      throw new ToolError({ error: "investigation_closed", status: r.rows[0].status });
    return r.rows[0];
  }

  async function coverageState(c, runId) {
    const policyResult = await c.query(
      `select * from investigation_method_policy where run_id=$1`, [runId]);
    if (!policyResult.rows.length)
      throw new ToolError({ error: "investigation_method_policy_required", run_id: runId });
    const policy = policyResult.rows[0];
    const inventory = (await c.query(
      `select * from investigation_inventory where run_id=$1 order by surface_key`, [runId])).rows;
    const required = [...policy.required_surfaces].sort();
    const received = inventory.map(row => row.surface_key).sort();
    const missing = required.filter(key => !received.includes(key));
    const unexpected = received.filter(key => !required.includes(key));
    const below = inventory.filter(row => Number(row.item_count) > 0 &&
      Number(row.scanned_count) / Number(row.item_count) < Number(policy.min_coverage_ratio))
      .map(row => row.surface_key);
    const sensitive = inventory.filter(row => row.sensitive);
    const representative = inventory.filter(row => row.representative);
    const sensitiveComplete = !policy.require_sensitive_coverage ||
      (sensitive.length > 0 && sensitive.every(row => Number(row.scanned_count) === Number(row.item_count)));
    const representativeComplete = !policy.require_representative_coverage ||
      (representative.length > 0 && representative.every(row =>
        Number(row.scanned_count) === Number(row.item_count)));
    const itemCount = inventory.reduce((sum, row) => sum + Number(row.item_count), 0);
    const scannedCount = inventory.reduce((sum, row) => sum + Number(row.scanned_count), 0);
    const ratio = itemCount === 0 ? 1 : scannedCount / itemCount;
    const complete = !missing.length && !unexpected.length && !below.length &&
      sensitiveComplete && representativeComplete && ratio >= Number(policy.min_coverage_ratio);
    return { policy, inventory, required, received, missing, unexpected, below,
      sensitive_complete: sensitiveComplete, representative_complete: representativeComplete,
      item_count: itemCount, scanned_count: scannedCount, coverage_ratio: ratio, complete };
  }

  const inventoryRowSchema = {
    type: "object", additionalProperties: false, properties: {
      surface_key: { type: "string" }, item_count: { type: "integer", minimum: 0 },
      scanned_count: { type: "integer", minimum: 0 },
      matcher_refs: { type: "array", items: { type: "string" } },
      evidence_refs: { type: "array", minItems: 1, items: { type: "string" } },
      input_digest: digestSchema, representative: { type: "boolean" }, sensitive: { type: "boolean" },
    }, required: ["surface_key", "item_count", "scanned_count", "matcher_refs",
      "evidence_refs", "input_digest"]
  };
  const candidateRowSchema = {
    type: "object", additionalProperties: false, properties: {
      surface_key: { type: "string" }, candidate_key: { type: "string" },
      ordinal: { type: "integer", minimum: 1 },
      subject_type: { type: "string", enum: ["lead","client","vendor","party","deal",
        "campaign","platform","pillar","format","repo","commit"] },
      subject_id: { type: "string" }, evidence_refs: { type: "array", minItems: 1,
        items: { type: "string" } }, input_digest: digestSchema,
      matcher_key: { type: "string" }, matcher_version: { type: "string" },
    }, required: ["surface_key", "candidate_key", "ordinal", "subject_type", "subject_id",
      "evidence_refs", "input_digest", "matcher_key", "matcher_version"]
  };

  return {
    "list-investigation-surfaces": {
      description: "List versioned declarative surfaces and matchers available to deterministic investigation enumerators. These rows are data, never executable SQL or stored prompts.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c, _actor, args) => {
        await require0116(c);
        const rows = await c.query(
          `select s.*, coalesce((select jsonb_agg(jsonb_build_object(
             'id',m.id,'matcher_key',m.matcher_key,'version',m.version,'matcher_type',m.matcher_type,
             'spec',m.spec,'examples',m.examples) order by m.matcher_key,m.version)
             from investigation_matcher m where m.surface_key=s.surface_key), '[]'::jsonb) as matchers
             from investigation_surface s order by s.surface_key`, []);
        return { ok: true, count: rows.rows.length, surfaces: rows.rows };
      },
    },

    "register-investigation-surface": {
      write: true,
      description: "Register one stable inventory surface. The source is a declarative address; this verb cannot store or execute SQL, a prompt, or a target verb.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        surface_key: { type: "string" }, title: { type: "string" }, source_kind: { type: "string" },
        source_ref: { type: "string" }, inventory_contract_version: { type: "string" },
        candidate_key_version: { type: "string" }, release_routes: { type: "array",
          items: { type: "string", enum: ["record-finding"] } }, ...idempotency,
      }, required: ["surface_key", "title", "source_kind", "source_ref",
        "inventory_contract_version", "candidate_key_version", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "register-investigation-surface", args, async () => {
        await require0116(c);
        const routes = args.release_routes || ["record-finding"];
        if (!routes.length || routes.some(route => route !== "record-finding"))
          throw new ToolError({ error: "release_route_not_allowed", allowed: ["record-finding"] });
        const r = await c.query(
          `insert into investigation_surface
             (surface_key,title,source_kind,source_ref,inventory_contract_version,
              candidate_key_version,release_routes,created_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8) returning *`,
          [args.surface_key, args.title, args.source_kind, args.source_ref,
           args.inventory_contract_version, args.candidate_key_version, JSON.stringify(routes), actor.id]);
        await writeEvent(c, actor, "register-investigation-surface", "investigation_surface",
          r.rows[0].id, { field: "registry", new: r.rows[0],
            idempotency_key: args.idempotency_key });
        return { ok: true, surface: r.rows[0] };
      }),
    },

    "register-investigation-matcher": {
      write: true,
      description: "Register one bounded declarative matcher after safety validation and positive/negative examples. It is stored as data and is never executed by the record layer.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        surface_key: { type: "string" }, matcher_key: { type: "string" }, version: { type: ["integer", "string"] },
        matcher_type: { type: "string", enum: [...MATCHER_TYPES] }, spec: { type: "object" },
        examples: { type: "array" }, ...idempotency,
      }, required: ["surface_key", "matcher_key", "version", "matcher_type", "spec", "examples", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "register-investigation-matcher", args, async () => {
        await require0116(c);
        const invalid = validateMatcherDefinition(args);
        if (invalid) throw new ToolError(invalid);
        const r = await c.query(
          `insert into investigation_matcher
             (surface_key,matcher_key,version,matcher_type,spec,examples,created_by)
           values ($1,$2,$3,$4,$5,$6,$7) returning *`,
          [args.surface_key, args.matcher_key, String(args.version), args.matcher_type,
           JSON.stringify(args.spec), JSON.stringify(args.examples), actor.id]);
        await writeEvent(c, actor, "register-investigation-matcher", "investigation_matcher",
          r.rows[0].id, { field: "matcher", new: { matcher_id: r.rows[0].id,
            matcher_key: args.matcher_key, version: String(args.version) },
            idempotency_key: args.idempotency_key });
        return { ok: true, matcher: r.rows[0] };
      }),
    },

    "set-investigation-method": {
      write: true,
      description: "Freeze the required surfaces, coverage floor, and optional measured cost/duration budget for one open investigation. A changed policy requires a new run.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" }, required_surfaces: { type: "array", minItems: 1,
          items: { type: "string" } }, min_coverage_ratio: { type: "number", minimum: 0, maximum: 1 },
        require_sensitive_coverage: { type: "boolean" }, require_representative_coverage: { type: "boolean" },
        max_cost_amount: { type: ["number", "null"], minimum: 0 },
        cost_currency: { type: "string", pattern: "^[A-Z]{3}$" },
        max_duration_seconds: { type: ["integer", "null"], minimum: 1 }, ...idempotency,
      }, required: ["run_id", "required_surfaces", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "set-investigation-method", args, async () => {
        await require0116(c); await ownedOpenRun(c, actor, args.run_id, true);
        const required = [...new Set(args.required_surfaces)].sort();
        if (required.length !== args.required_surfaces.length)
          throw new ToolError({ error: "required_surface_duplicate" });
        const known = await c.query(
          `select surface_key from investigation_surface where surface_key=any($1::text[])`, [required]);
        const found = new Set(known.rows.map(row => row.surface_key));
        const missing = required.filter(key => !found.has(key));
        if (missing.length) throw new ToolError({ error: "required_surface_unknown", missing });
        const r = await c.query(
          `insert into investigation_method_policy
             (run_id,required_surfaces,min_coverage_ratio,require_sensitive_coverage,
              require_representative_coverage,max_cost_amount,cost_currency,max_duration_seconds,created_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9) returning *`,
          [args.run_id, JSON.stringify(required), args.min_coverage_ratio ?? 1,
           !!args.require_sensitive_coverage, !!args.require_representative_coverage,
           args.max_cost_amount ?? null, args.cost_currency || "USD",
           args.max_duration_seconds ?? null, actor.id]);
        await writeEvent(c, actor, "set-investigation-method", "investigation", args.run_id,
          { field: "method_policy", new: r.rows[0], idempotency_key: args.idempotency_key });
        return { ok: true, policy: r.rows[0] };
      }),
    },

    "record-investigation-reservation": {
      write: true,
      description: "Reserve estimated cost and duration before an external model call. The database counts live reservations plus completed spend and refuses work that would exceed the frozen run budget.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" }, phase: { type: "string", enum: ["inventory", "primary_assessment", "revalidation", "release"] },
        role: { type: "string", enum: ["primary", "revalidation"] }, context_key: { type: "string" },
        requested_provider: { type: "string" }, requested_model: { type: "string" },
        requested_effort: { type: ["string", "null"] }, prompt_contract_version: { type: "string" },
        prompt_sha256: digestSchema, input_digest: digestSchema,
        reserved_cost_amount: { type: "number", minimum: 0 }, cost_currency: { type: "string", pattern: "^[A-Z]{3}$" },
        reserved_duration_seconds: { type: "integer", minimum: 1 },
        source_checkpoint_id: { type: ["string", "null"] }, expires_at: { type: "string" },
        ...idempotency,
      }, required: ["run_id", "phase", "role", "context_key", "requested_provider",
        "requested_model", "prompt_contract_version", "prompt_sha256", "input_digest",
        "reserved_cost_amount", "cost_currency", "reserved_duration_seconds", "expires_at",
        "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-investigation-reservation", args, async () => {
        await require0116(c); await ownedOpenRun(c, actor, args.run_id, true);
        const expiry = new Date(args.expires_at);
        if (!Number.isFinite(expiry.getTime()) || expiry <= new Date())
          throw new ToolError({ error: "reservation_expiry_invalid" });
        if (args.phase !== "inventory") {
          const coverage = await coverageState(c, args.run_id);
          if (!coverage.complete) throw new ToolError({ error: "coverage_incomplete", coverage });
        }
        if (args.phase === "revalidation") {
          const primary = await c.query(
            `select * from investigation_phase_checkpoint where id=$1 and run_id=$2
              and phase='primary_assessment'`, [args.source_checkpoint_id, args.run_id]);
          if (!primary.rows.length || primary.rows[0].verdict === "blocked")
            throw new ToolError({ error: "nonblocked_primary_checkpoint_required" });
        } else if (args.source_checkpoint_id != null) {
          throw new ToolError({ error: "source_checkpoint_only_for_revalidation" });
        }
        const r = await c.query(
          `insert into investigation_wave_reservation
             (run_id,phase,role,actor_id,context_key,requested_provider,requested_model,
              requested_effort,prompt_contract_version,prompt_sha256,input_digest,
              reserved_cost_amount,cost_currency,reserved_duration_seconds,source_checkpoint_id,
              expires_at)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) returning *`,
          [args.run_id,args.phase,args.role,actor.id,args.context_key,args.requested_provider,
           args.requested_model,args.requested_effort||null,args.prompt_contract_version,
           args.prompt_sha256,args.input_digest,args.reserved_cost_amount,args.cost_currency,
           args.reserved_duration_seconds,args.source_checkpoint_id||null,args.expires_at]);
        await writeEvent(c, actor, "record-investigation-reservation", "investigation", args.run_id,
          { field: "wave_reservation", new: { reservation_id: r.rows[0].id, phase: args.phase,
            requested_model: args.requested_model, reserved_cost_amount: args.reserved_cost_amount,
            reserved_duration_seconds: args.reserved_duration_seconds }, idempotency_key: args.idempotency_key });
        return { ok: true, reservation: r.rows[0] };
      }),
    },

    "record-investigation-wave": {
      write: true,
      description: "Append one completed execution receipt against its pre-spend reservation. An overrun remains recorded and blocks later reservations; it is never erased to make a budget look green.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        reservation_id: { type: "string" }, run_id: { type: "string" },
        phase: { type: "string", enum: ["inventory", "primary_assessment", "revalidation", "release"] },
        role: { type: "string", enum: ["primary", "revalidation"] }, context_key: { type: "string" },
        requested_provider: { type: "string" }, requested_model: { type: "string" },
        requested_effort: { type: ["string", "null"] }, prompt_contract_version: { type: "string" },
        prompt_sha256: digestSchema, input_digest: digestSchema, output_digest: digestSchema,
        started_at: { type: "string" }, finished_at: { type: "string" },
        input_tokens: { type: ["integer", "null"], minimum: 0 }, output_tokens: { type: ["integer", "null"], minimum: 0 },
        cost_amount: { type: ["number", "null"], minimum: 0 }, cost_currency: { type: ["string", "null"], pattern: "^[A-Z]{3}$" },
        cost_source: { type: ["string", "null"] }, ...idempotency,
      }, required: ["reservation_id", "run_id", "phase", "role", "context_key", "requested_provider",
        "requested_model", "prompt_contract_version", "prompt_sha256", "input_digest",
        "output_digest", "started_at", "finished_at", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-investigation-wave", args, async () => {
        await require0116(c); await ownedOpenRun(c, actor, args.run_id, true);
        const start = new Date(args.started_at), finish = new Date(args.finished_at);
        if (!Number.isFinite(start.getTime()) || !Number.isFinite(finish.getTime()) || finish < start)
          throw new ToolError({ error: "wave_timestamps_invalid" });
        const r = await c.query(
          `insert into investigation_wave
             (reservation_id,run_id,phase,role,actor_id,context_key,requested_provider,requested_model,
              requested_effort,prompt_contract_version,prompt_sha256,input_digest,output_digest,
              started_at,finished_at,input_tokens,output_tokens,cost_amount,cost_currency,cost_source)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20) returning *`,
          [args.reservation_id,args.run_id, args.phase, args.role, actor.id, args.context_key,
           args.requested_provider, args.requested_model, args.requested_effort || null,
           args.prompt_contract_version, args.prompt_sha256, args.input_digest, args.output_digest,
           args.started_at, args.finished_at, args.input_tokens ?? null, args.output_tokens ?? null,
           args.cost_amount ?? null, args.cost_currency || null, args.cost_source || null]);
        const budget = await c.query(
          `select p.max_cost_amount,p.max_duration_seconds,
             coalesce(sum(w.cost_amount),0) as spent_cost,
             coalesce(sum(extract(epoch from (w.finished_at-w.started_at))),0) as spent_seconds
           from investigation_method_policy p left join investigation_wave w on w.run_id=p.run_id
          where p.run_id=$1 group by p.run_id,p.max_cost_amount,p.max_duration_seconds`, [args.run_id]);
        const b = budget.rows[0];
        const overBudget = (b.max_cost_amount != null && Number(b.spent_cost)>Number(b.max_cost_amount)) ||
          (b.max_duration_seconds != null && Number(b.spent_seconds)>Number(b.max_duration_seconds));
        await writeEvent(c, actor, "record-investigation-wave", "investigation", args.run_id,
          { field: "wave", new: { wave_id: r.rows[0].id, phase: args.phase, role: args.role,
            context_key: args.context_key, requested_model: args.requested_model,
            cost_amount: args.cost_amount ?? null, cost_currency: args.cost_currency || null,
            over_budget: overBudget },
            idempotency_key: args.idempotency_key });
        return { ok: true, wave: r.rows[0], over_budget: overBudget, budget: b };
      }),
    },

    "record-investigation-candidates": {
      write: true,
      description: "Atomically record the complete required-surface inventory and deterministic candidate universe, including zero-candidate surfaces. Declared counts and canonical digest must reconcile exactly.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" }, inventory_wave_id: { type: "string" },
        declared_inventory_count: { type: "integer", minimum: 1 },
        declared_candidate_count: { type: "integer", minimum: 0 }, batch_digest: digestSchema,
        inventory: { type: "array", minItems: 1, maxItems: 500, items: inventoryRowSchema },
        candidates: { type: "array", maxItems: 5000, items: candidateRowSchema }, ...idempotency,
      }, required: ["run_id", "inventory_wave_id", "declared_inventory_count",
        "declared_candidate_count", "batch_digest", "inventory", "candidates", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-investigation-candidates", args, async () => {
        await require0116(c); await ownedOpenRun(c, actor, args.run_id, true);
        const invalid = await validateCandidateBatch(args);
        if (invalid) throw new ToolError(invalid);
        const policy = await c.query(`select required_surfaces from investigation_method_policy where run_id=$1`, [args.run_id]);
        if (!policy.rows.length) throw new ToolError({ error: "investigation_method_policy_required" });
        const required = [...policy.rows[0].required_surfaces].sort();
        const received = args.inventory.map(row => row.surface_key).sort();
        if (JSON.stringify(required) !== JSON.stringify(received))
          throw new ToolError({ error: "inventory_surfaces_do_not_match_policy", required, received });
        const wave = await c.query(`select phase from investigation_wave where id=$1 and run_id=$2`,
          [args.inventory_wave_id, args.run_id]);
        if (!wave.rows.length || wave.rows[0].phase !== "inventory")
          throw new ToolError({ error: "inventory_wave_required" });
        for (const inventoryRow of args.inventory) {
          for (const ref of inventoryRow.matcher_refs) {
            const split = String(ref).lastIndexOf("@");
            if (split < 1) throw new ToolError({ error: "matcher_ref_invalid", matcher_ref: ref });
            const key = String(ref).slice(0, split), version = String(ref).slice(split + 1);
            const matched = await c.query(
              `select 1 from investigation_matcher where matcher_key=$1 and version=$2
                and surface_key=$3`, [key, version, inventoryRow.surface_key]);
            if (!matched.rows.length) throw new ToolError({ error: "matcher_ref_unknown",
              matcher_ref: ref, surface_key: inventoryRow.surface_key });
          }
        }
        for (const row of args.candidates) {
          if (row.matcher_key == null || row.matcher_version == null)
            throw new ToolError({ error: "candidate_matcher_required", candidate_key: row.candidate_key });
          const ref = `${row.matcher_key}@${row.matcher_version}`;
          const inventoryRow = args.inventory.find(item => item.surface_key === row.surface_key);
          if (!inventoryRow.matcher_refs.includes(ref))
            throw new ToolError({ error: "candidate_matcher_not_declared_for_surface",
              candidate_key: row.candidate_key, matcher_ref: ref });
        }
        for (const inventoryRow of args.inventory) {
          if (!inventoryRow.matcher_refs.length)
            throw new ToolError({ error: "surface_matcher_required", surface_key: inventoryRow.surface_key });
          const count = args.candidates.filter(row => row.surface_key === inventoryRow.surface_key).length;
          if (count > inventoryRow.scanned_count)
            throw new ToolError({ error: "surface_candidate_count_exceeds_scanned",
              surface_key: inventoryRow.surface_key, candidate_count: count,
              scanned_count: inventoryRow.scanned_count });
        }
        for (const row of args.inventory) await c.query(
          `insert into investigation_inventory
             (run_id,surface_key,item_count,scanned_count,matcher_refs,evidence_refs,input_digest,
              batch_digest,declared_inventory_count,declared_candidate_count,
              representative,sensitive,inventory_wave_id,created_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`,
          [args.run_id, row.surface_key, row.item_count, row.scanned_count,
           JSON.stringify(row.matcher_refs), JSON.stringify(row.evidence_refs), row.input_digest,
           args.batch_digest, args.declared_inventory_count, args.declared_candidate_count,
           !!row.representative, !!row.sensitive, args.inventory_wave_id, actor.id]);
        const created = [];
        for (const row of args.candidates) {
          const r = await c.query(
            `insert into investigation_candidate
               (run_id,surface_key,candidate_key,ordinal,subject_type,subject_id,evidence_refs,
                input_digest,inventory_wave_id,matcher_key,matcher_version,created_by)
             values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) returning *`,
            [args.run_id, row.surface_key, row.candidate_key, row.ordinal, row.subject_type,
             row.subject_id, JSON.stringify(row.evidence_refs), row.input_digest,
             args.inventory_wave_id, row.matcher_key || null, row.matcher_version || null, actor.id]);
          created.push(r.rows[0]);
        }
        const coverage = await coverageState(c, args.run_id);
        await writeEvent(c, actor, "record-investigation-candidates", "investigation", args.run_id,
          { field: "candidate_universe", new: { batch_digest: args.batch_digest,
            inventory_count: args.inventory.length, candidate_count: created.length,
            coverage_complete: coverage.complete }, idempotency_key: args.idempotency_key });
        return { ok: true, batch_digest: args.batch_digest, inventory_count: args.inventory.length,
          candidate_count: created.length, candidates: created, coverage };
      }),
    },

    "record-candidate-assessment": {
      write: true,
      description: "Append one explicit candidate outcome. Revalidation must use a distinct recorded context from the current primary assessment; skipped, error, and refused remain visible outcomes.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        candidate_id: { type: "string" }, wave_id: { type: "string" },
        role: { type: "string", enum: ["primary", "revalidation"] },
        outcome: { type: "string", enum: ASSESSMENT_OUTCOMES },
        evidence_refs: { type: "array", items: { type: "string" } },
        result_digest: { type: ["string", "null"], pattern: "^[0-9a-f]{64}$" },
        reason: { type: ["string", "null"] }, ...idempotency,
      }, required: ["candidate_id", "wave_id", "role", "outcome", "evidence_refs", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-candidate-assessment", args, async () => {
        await require0116(c);
        const candidate = await c.query(`select * from investigation_candidate where id=$1`, [args.candidate_id]);
        if (!candidate.rows.length) throw new ToolError({ error: "candidate_not_found" });
        await ownedOpenRun(c, actor, candidate.rows[0].run_id, true);
        if ((args.outcome === "pending") !== (args.result_digest == null))
          throw new ToolError({ error: "assessment_digest_outcome_mismatch" });
        if (args.outcome !== "pending" && !String(args.reason || "").trim())
          throw new ToolError({ error: "assessment_reason_required" });
        const wave = await c.query(
          `select w.*,r.source_checkpoint_id from investigation_wave w
             join investigation_wave_reservation r on r.id=w.reservation_id
            where w.id=$1 and w.run_id=$2 and w.actor_id=$3`,
          [args.wave_id, candidate.rows[0].run_id, actor.id]);
        if (!wave.rows.length) throw new ToolError({ error: "assessment_wave_not_owned_by_actor" });
        if (wave.rows[0].role !== args.role)
          throw new ToolError({ error: "assessment_role_does_not_match_wave" });
        if (args.role === "revalidation") {
          const primary = await c.query(
            `select cc.outcome,a.actor_id from investigation_checkpoint_candidate cc
               left join investigation_candidate_assessment a on a.id=cc.assessment_id
              where cc.checkpoint_id=$1 and cc.candidate_id=$2`,
            [wave.rows[0].source_checkpoint_id, args.candidate_id]);
          if (!primary.rows.length || primary.rows[0].outcome !== "validated")
            throw new ToolError({ error: "candidate_not_validated_in_pinned_primary_checkpoint" });
          if (primary.rows[0].actor_id === actor.id)
            throw new ToolError({ error: "revalidation_actor_not_independent" });
        }
        const r = await c.query(
          `insert into investigation_candidate_assessment
             (candidate_id,wave_id,role,outcome,evidence_refs,result_digest,reason,actor_id)
           values ($1,$2,$3,$4,$5,$6,$7,$8) returning *`,
          [args.candidate_id, args.wave_id, args.role, args.outcome,
           JSON.stringify(args.evidence_refs), args.result_digest || null, args.reason || null, actor.id]);
        await writeEvent(c, actor, "record-candidate-assessment", "investigation",
          candidate.rows[0].run_id, { field: "assessment", new: { assessment_id: r.rows[0].id,
            candidate_id: args.candidate_id, role: args.role, outcome: args.outcome },
            idempotency_key: args.idempotency_key });
        return { ok: true, assessment: r.rows[0] };
      }),
    },

    "investigation-coverage": {
      description: "Read exact required-surface coverage, candidate counts, latest assessments, budgets, and checkpoints for a resumable systematic investigation.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" },
      }, required: ["run_id"] },
      handler: async (c, _actor, args) => {
        await require0116(c);
        const coverage = await coverageState(c, args.run_id);
        const candidates = await c.query(
          `select c.*,p.outcome as primary_outcome,p.wave_id as primary_wave_id,
                  r.outcome as revalidation_outcome,r.wave_id as revalidation_wave_id
             from investigation_candidate c
             left join v_investigation_candidate_latest_assessment p
               on p.candidate_id=c.id and p.role='primary'
             left join v_investigation_candidate_latest_assessment r
               on r.candidate_id=c.id and r.role='revalidation'
            where c.run_id=$1 order by c.ordinal,c.id`, [args.run_id]);
        const checkpoints = await c.query(
          `select * from investigation_phase_checkpoint where run_id=$1 order by phase,sequence`, [args.run_id]);
        const waves = await c.query(
          `select id,reservation_id,phase,role,context_key,requested_provider,requested_model,requested_effort,
                  started_at,finished_at,input_tokens,output_tokens,cost_amount,cost_currency,cost_source
             from investigation_wave where run_id=$1 order by started_at,id`, [args.run_id]);
        const reservations = await c.query(
          `select r.*,w.id as completed_wave_id from investigation_wave_reservation r
             left join investigation_wave w on w.reservation_id=r.id
            where r.run_id=$1 order by r.created_at,r.id`, [args.run_id]);
        const spentCost = waves.rows.reduce((sum,row)=>sum+Number(row.cost_amount||0),0);
        const spentSeconds = waves.rows.reduce((sum,row)=>sum+
          (new Date(row.finished_at).getTime()-new Date(row.started_at).getTime())/1000,0);
        const overBudget = (coverage.policy.max_cost_amount != null &&
          spentCost>Number(coverage.policy.max_cost_amount)) ||
          (coverage.policy.max_duration_seconds != null &&
          spentSeconds>Number(coverage.policy.max_duration_seconds));
        return { ok: true, coverage, candidate_count: candidates.rows.length,
          candidates: candidates.rows, checkpoints: checkpoints.rows, reservations: reservations.rows,
          waves: waves.rows, budget: { spent_cost: spentCost, spent_seconds: spentSeconds,
            over_budget: overBudget } };
      },
    },

    "record-investigation-checkpoint": {
      write: true,
      description: "Append a server-computed conservation checkpoint. Every candidate is counted as unattempted, pending, validated, rejected, error, skipped, or refused; silence can never read as clean.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" }, phase: { type: "string", enum: ["primary_assessment", "revalidation"] },
        source_primary_checkpoint_id: { type: ["string", "null"] },
        idempotency_key: { type: "string" },
      }, required: ["run_id", "phase", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-investigation-checkpoint", args, async () => {
        await require0116(c); await ownedOpenRun(c, actor, args.run_id, true);
        const coverage = await coverageState(c, args.run_id);
        if (!coverage.complete) throw new ToolError({ error: "coverage_incomplete", coverage });
        const role = args.phase === "primary_assessment" ? "primary" : "revalidation";
        let candidateRows;
        if (args.phase === "primary_assessment") {
          if (args.source_primary_checkpoint_id != null)
            throw new ToolError({ error: "source_checkpoint_only_for_revalidation" });
          candidateRows = (await c.query(`select id,surface_key,candidate_key,input_digest
              from investigation_candidate where run_id=$1
              order by surface_key,candidate_key,id`, [args.run_id])).rows;
        } else {
          const source = await c.query(
            `select * from investigation_phase_checkpoint where id=$1 and run_id=$2
              and phase='primary_assessment'`, [args.source_primary_checkpoint_id, args.run_id]);
          if (!source.rows.length || source.rows[0].verdict === "blocked")
            throw new ToolError({ error: "nonblocked_primary_checkpoint_required" });
          candidateRows = (await c.query(
            `select c.id,c.surface_key,c.candidate_key,c.input_digest
               from investigation_checkpoint_candidate cc
               join investigation_candidate c on c.id=cc.candidate_id
              where cc.checkpoint_id=$1 and cc.outcome='validated'
              order by c.surface_key,c.candidate_key,c.id`, [args.source_primary_checkpoint_id])).rows;
        }
        const ids = candidateRows.map(row => row.id);
        const assessments = ids.length ? (await c.query(
          `select a.candidate_id,a.outcome,a.result_digest,a.wave_id,a.recorded_at,a.id
             from investigation_candidate_assessment a
             join investigation_wave w on w.id=a.wave_id
             join investigation_wave_reservation r on r.id=w.reservation_id
            where a.role=$1 and a.candidate_id=any($2::uuid[])
              and ($3::uuid is null or r.source_checkpoint_id=$3)
            order by a.recorded_at desc,a.id desc`,
          [role, ids, args.source_primary_checkpoint_id || null])).rows : [];
        const latestByCandidate = new Map();
        for (const row of assessments) if (!latestByCandidate.has(row.candidate_id))
          latestByCandidate.set(row.candidate_id, row);
        const latestRows = [...latestByCandidate.values()];
        const reconciled = reconcileAssessmentRows(candidateRows.length, latestRows);
        if (reconciled.error) throw new ToolError(reconciled);
        const candidateDigest = await canonicalDigest(candidateRows);
        const assessmentDigest = await canonicalDigest(latestRows);
        const prior = await c.query(
          `select coalesce(max(sequence),0)::int as sequence from investigation_phase_checkpoint
            where run_id=$1 and phase=$2`, [args.run_id, args.phase]);
        const sequence = Number(prior.rows[0].sequence) + 1;
        const x = reconciled.counts;
        const r = await c.query(
          `insert into investigation_phase_checkpoint
             (run_id,phase,source_checkpoint_id,sequence,candidate_set_digest,assessment_set_digest,total_count,
              unattempted_count,pending_count,validated_count,rejected_count,error_count,
              skipped_count,refused_count,verdict,created_by)
           values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) returning *`,
          [args.run_id, args.phase, args.source_primary_checkpoint_id || null, sequence,
           candidateDigest, assessmentDigest, candidateRows.length, x.unattempted, x.pending,
           x.validated, x.rejected, x.error, x.skipped, x.refused, reconciled.verdict, actor.id]);
        for (const candidate of candidateRows) {
          const latest = latestByCandidate.get(candidate.id);
          await c.query(
            `insert into investigation_checkpoint_candidate
               (checkpoint_id,candidate_id,assessment_id,outcome) values ($1,$2,$3,$4)`,
            [r.rows[0].id,candidate.id,latest?.id || null,latest?.outcome || "unattempted"]);
        }
        await writeEvent(c, actor, "record-investigation-checkpoint", "investigation", args.run_id,
          { field: "checkpoint", new: { checkpoint_id: r.rows[0].id, phase: args.phase,
            sequence, verdict: reconciled.verdict, counts: x }, idempotency_key: args.idempotency_key });
        return { ok: true, checkpoint: r.rows[0], counts: x };
      }),
    },

    "investigation-checkpoints": {
      description: "Read append-only phase checkpoints and release receipts for one investigation.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        run_id: { type: "string" },
      }, required: ["run_id"] },
      handler: async (c, _actor, args) => {
        await require0116(c);
        const checkpoints = await c.query(
          `select * from investigation_phase_checkpoint where run_id=$1 order by phase,sequence`, [args.run_id]);
        const releases = await c.query(
          `select r.* from investigation_candidate_release r
             join investigation_candidate c on c.id=r.candidate_id
            where c.run_id=$1 order by r.created_at,r.id`, [args.run_id]);
        return { ok: true, checkpoints: checkpoints.rows, releases: releases.rows };
      },
    },

    "release-validated-finding": {
      write: true,
      description: "Create and route one record-finding inside the same transaction, only after pinned primary and different-actor revalidation checkpoints both validate the exact candidate subject. No pre-validation or unrelated finding can be linked.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        candidate_id: { type: "string" }, checkpoint_id: { type: "string" },
        kind: { type: "string" }, value: { type: "object" }, found: { type: "boolean" },
        source: { type: "string" }, internal: { type: "boolean" },
        epistemic_status: { type: "string", enum: ["proposed","observed","reproduced",
          "accepted","disputed","superseded","inferred","source_backed","speculative"] },
        observed_at: { type: ["string", "null"] }, expires_on: { type: ["string", "null"] },
        idempotency_key: { type: "string" },
      }, required: ["candidate_id", "checkpoint_id", "kind", "source", "idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "release-validated-finding", args, async () => {
        await require0116(c);
        const candidate = await c.query(`select * from investigation_candidate where id=$1`, [args.candidate_id]);
        if (!candidate.rows.length) throw new ToolError({ error: "candidate_not_found" });
        await ownedOpenRun(c, actor, candidate.rows[0].run_id, true);
        const eligibility = await c.query(
          `select rv.source_checkpoint_id,pc.outcome as primary_outcome,
                  rc.outcome as revalidation_outcome,pa.actor_id as primary_actor,
                  ra.actor_id as revalidation_actor
             from investigation_phase_checkpoint rv
             left join investigation_checkpoint_candidate pc
               on pc.checkpoint_id=rv.source_checkpoint_id and pc.candidate_id=$1
             left join investigation_candidate_assessment pa on pa.id=pc.assessment_id
             left join investigation_checkpoint_candidate rc
               on rc.checkpoint_id=rv.id and rc.candidate_id=$1
             left join investigation_candidate_assessment ra on ra.id=rc.assessment_id
            where rv.id=$2 and rv.run_id=$3 and rv.phase='revalidation'
              and rv.verdict<>'blocked'`,
          [args.candidate_id,args.checkpoint_id,candidate.rows[0].run_id]);
        const eligible = eligibility.rows[0];
        if (!eligible || eligible.primary_outcome !== "validated" ||
            eligible.revalidation_outcome !== "validated")
          throw new ToolError({ error: "candidate_not_validated_in_pinned_checkpoints" });
        if (eligible.primary_actor === eligible.revalidation_actor)
          throw new ToolError({ error: "revalidation_actor_not_independent" });
        const src = String(args.source || "").trim();
        if (!src) throw new ToolError({ error: "source_required" });
        if (!args.internal) {
          const locator = /https?:\/\//i.test(src)
            || /\b(nppes|sunbiz|npi|bbb|linkedin|zoominfo|rocketreach|facebook|instagram|chamber|arxiv|github)\b/i.test(src)
            || /[\/#§@]|\bp\.?\s?\d|\bevent\b|\bthread\b|\bv_[a-z_]+/i.test(src);
          if (!locator || src.length < 12)
            throw new ToolError({ error: "source_not_a_locator" });
        }
        const found = args.found !== false;
        if (found && (!args.value || typeof args.value !== "object" || !Object.keys(args.value).length))
          throw new ToolError({ error: "value_required" });
        const value = {
          found, ...(found ? args.value : { searched_for: args.kind }),
          ...(args.internal ? { internal: true } : {}),
          epistemic_status: args.epistemic_status || (args.internal ? "inferred" : "source_backed"),
          validation_provenance: { candidate_id: args.candidate_id,
            primary_checkpoint_id: eligible.source_checkpoint_id,
            revalidation_checkpoint_id: args.checkpoint_id },
        };
        const finding = await c.query(
          `insert into record_flag
             (subject_type,subject_id,kind,value,source,observed_at,expires_on,created_by)
           values ($1,$2,$3,$4,$5,coalesce($6::timestamptz,now()),$7::date,$8)
           returning id,observed_at`,
          [candidate.rows[0].subject_type,candidate.rows[0].subject_id,args.kind,
           JSON.stringify(value),src,args.observed_at||null,args.expires_on||null,actor.id]);
        await writeEvent(c, actor, "record-finding", candidate.rows[0].subject_type,
          candidate.rows[0].subject_id, { occurred_at: args.observed_at||null, field: args.kind,
            new: { found,source:src,expires_on:args.expires_on||null,
              candidate_id:args.candidate_id,revalidation_checkpoint_id:args.checkpoint_id },
            agent_rationale: found ? null : "searched, nothing found",
            idempotency_key: args.idempotency_key });
        const r = await c.query(
          `insert into investigation_candidate_release
             (candidate_id,checkpoint_id,route_key,finding_id,created_by)
           values ($1,$2,'record-finding',$3,$4) returning *`,
          [args.candidate_id, args.checkpoint_id, finding.rows[0].id, actor.id]);
        await writeEvent(c, actor, "release-validated-finding", "investigation",
          candidate.rows[0].run_id, { field: "release", new: { release_id: r.rows[0].id,
            candidate_id: args.candidate_id, checkpoint_id: args.checkpoint_id,
            finding_id: finding.rows[0].id, route_key: "record-finding" },
            idempotency_key: args.idempotency_key });
        return { ok: true, release: r.rows[0], finding_id: finding.rows[0].id,
          observed_at: finding.rows[0].observed_at };
      }),
    },
  };
}

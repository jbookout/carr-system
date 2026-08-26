#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { TOOLS } from "../mcp-server/src/tools.js";

export const REGISTRY_VERSION = "scac-mutation-registry.v1";
const REPO_ROOT = fileURLToPath(new URL("../", import.meta.url));
export const DB_CATALOG_BASELINE = Object.freeze({
  projection_version: "scac-db-catalog-projection.v1",
  secdef_execute: { count: 205, digest: "sha256:ee70c43304aa499af73d52bdbd98c72c270f5ce2f2ba480b9a1e1c74e39a15cf" },
  relation_dml: { count: 284, digest: "sha256:3bb06a15f3f19914d476edd5a2c789e307b5298633c2d4d98c1a3e5c10359345" },
  column_dml: { count: 12, digest: "sha256:607e31d990653776243350d001ca465234e321349b05259751f8231ae3c2c44f" },
});
export const JOB_DEFINITION_BASELINE = Object.freeze({
  count: 26,
  digest: "sha256:25ca2c7ef68c71479add93e5b2b2e5cffaa320b3de0188d17407821567c81020",
});

export function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
}

export function sha256(value) {
  const bytes = typeof value === "string" ? value : JSON.stringify(canonicalize(value));
  return createHash("sha256").update(bytes).digest("hex");
}

function delegatesTo(name) {
  if (name === "stamp-touch") return ["log-activity"];
  if (name === "resolve-candidate") return ["log-activity", "new-deal", "patch-deal-field", "set-next-step"];
  if (name === "find-and-catch-up") return ["catch-me-up", "find"];
  if (name === "prepare-conversation") return ["find-and-catch-up", "who-do-we-know"];
  if (name === "morning-brief") return ["claim-card", "deal-room-board", "loop-board", "today-triage"];
  if (name === "call-verb") return ["*registered_operation"];
  return [];
}

export function mcpInventory(tools = TOOLS) {
  return Object.entries(tools).sort(([left], [right]) => left.localeCompare(right)).map(([name, tool]) => {
    const write = tool.write === true;
    const authorityOnly = tool.authorityOnly === true;
    const humanOnly = tool.humanOnly === true;
    return {
      ingress_key: `mcp-tool:${name}`,
      ingress_kind: "mcp_tool",
      operation: name,
      effect_class: name === "call-verb" ? "delegating" : write
        ? "administrative_mutation"
        : "audit_side_effect",
      source_locator: tool.registrySource,
      schema_digest: sha256(tool.inputSchema || {}),
      source_digest: sourceDigest(tool.registrySource),
      write,
      human_only: humanOnly,
      authority_only: authorityOnly,
      principal_mode: authorityOnly ? "server_verified_partner_authority" : humanOnly
        ? "server_verified_human_or_native_agent" : "authenticated_registered_principal",
      mutation_kind: write ? "scac.mutation.admin" : null,
      target_surface: "scac.surface.database",
      delegates_to: delegatesTo(name),
      request_shape: "closed_top_level_schema",
      idempotency_mode: write ? "operation_principal_manifest_bound" : "not_applicable",
      rollback_class: !write ? "append_only_audit" : "forward_fix_only",
      admission_class: "application_default_deny",
      owner_package: "11",
      implementation_state: "source_guarded_not_deployed",
      classification_authorizing: false,
    };
  });
}

const APPLICATION_INGRESSES = [
  ["worker-route:ingest", "worker_route", "mcp-server/src/index.js", "record_mutation"],
  ["worker-route:capture", "worker_route", "mcp-server/src/capture.js", "record_mutation"],
  ["worker-route:deal-room-turn", "worker_route", "mcp-server/src/index.js", "record_mutation"],
  ["worker-route:program6-routine", "worker_route", "mcp-server/src/program6-routine-controller.js", "record_mutation"],
  ["worker-route:oauth-kv", "worker_route", "mcp-server/src/google-oidc.js", "administrative_mutation"],
  ["worker-route:browser-challenge", "worker_route", "mcp-server/src/program6-browser-challenge.js", "record_mutation"],
  ["worker-sidewrite:failure-record", "worker_sidewrite", "mcp-server/src/trace.js", "audit_side_effect"],
  ["worker-sidewrite:tool-read-call", "worker_sidewrite", "mcp-server/src/mcp.js", "audit_side_effect"],
  ["worker-sidewrite:situation-retrieval", "worker_sidewrite", "mcp-server/src/situation-retrieval.js", "audit_side_effect"],
];

function sourceDigest(path) {
  return createHash("sha256").update(readFileSync(resolve(REPO_ROOT, path))).digest("hex");
}

const SCRIPT_SCAN_EXCLUDED_DIRS = new Set([
  ".git", ".claude", ".mypy_cache", "__pycache__", "node_modules",
]);

function walkFiles(relativeDir = "") {
  return readdirSync(resolve(REPO_ROOT, relativeDir || "."), { withFileTypes: true }).flatMap(entry => {
    if (entry.isDirectory() && SCRIPT_SCAN_EXCLUDED_DIRS.has(entry.name)) return [];
    const relative = relativeDir ? `${relativeDir}/${entry.name}` : entry.name;
    return entry.isDirectory() ? walkFiles(relative) : [relative];
  });
}

export function discoverScriptEntrypoints() {
  return walkFiles().filter(path => {
    const base = path.split("/").at(-1);
    if (base.startsWith("test-") || base.startsWith("test_") || path.includes("/tests/")) return false;
    if (/(?:^|[-_])selftest(?:[-_.]|$)/.test(base) || path.includes("/test/")) return false;
    const knownExtension = /\.(?:py|sh|applescript|mjs|js)$/.test(path);
    const details = statSync(resolve(REPO_ROOT, path));
    if (!details.isFile()) return false;
    const executable = (details.mode & 0o111) !== 0;
    if (!knownExtension && !executable) return false;
    const source = readFileSync(resolve(REPO_ROOT, path), "utf8");
    if (source.startsWith("#!")) return true;
    if (path.endsWith(".applescript")) return true;
    if (!/\.(?:py|mjs|js)$/.test(path)) return false;
    if (path.endsWith(".py"))
      return /if\s+__name__\s*==\s*["']__main__["']\s*:/.test(source);
    return /\.(?:mjs|js)$/.test(path) &&
      (source.includes("process.argv") || source.includes("import.meta.url ===") || source.includes("require.main === module"));
  }).sort();
}

export function nonMcpInventory() {
  const application = APPLICATION_INGRESSES.map(([ingress_key, ingress_kind, source_locator, effect_class]) => ({
    ingress_key, ingress_kind, operation: ingress_key.split(":").slice(1).join(":"), effect_class,
    source_locator, schema_digest: sourceDigest(source_locator), handler_digest: sourceDigest(source_locator),
    write: true, human_only: false, authority_only: effect_class === "administrative_mutation",
    principal_mode: "existing_entrypoint_specific_authentication", mutation_kind: effect_class === "administrative_mutation" ? "scac.mutation.admin" : "scac.mutation.business_record",
    target_surface: "scac.surface.database", delegates_to: [], request_shape: "entrypoint_specific_current_contract",
    idempotency_mode: "entrypoint_specific_registered_current_control", rollback_class: "forward_fix_only",
    admission_class: "registered_inventory_only", owner_package: "11",
    implementation_state: "inventoried_not_atomically_mediated", classification_authorizing: false,
  }));
  const scripts = discoverScriptEntrypoints().map(source_locator => {
    const breakGlass = new Set(["tools/db-tap.py", "tools/call-verb.py"]).has(source_locator);
    const genericDelegator = new Set(["run.sh", "mcp-server/local-verb.mjs"]).has(source_locator);
    const externalAdmin = !breakGlass && /(?:deploy|migrate|provision|rotate-credential|cleanup|cutoff|install-|sync-.*prod)/.test(source_locator);
    const ingress_kind = breakGlass ? "break_glass" : externalAdmin ? "external_admin" : "script_entrypoint";
    return {
    ingress_key: `${ingress_kind.replaceAll("_", "-")}:${source_locator}`, ingress_kind,
    operation: source_locator, effect_class: breakGlass ? "break_glass" : genericDelegator ? "delegating" : "administrative_mutation", source_locator,
    schema_digest: sourceDigest(source_locator), handler_digest: sourceDigest(source_locator),
    write: true, human_only: false, authority_only: true,
    principal_mode: "existing_script_specific_credential_boundary",
    mutation_kind: breakGlass ? "scac.mutation.break_glass" : "scac.mutation.admin",
    target_surface: "scac.surface.runtime",
    delegates_to: source_locator === "run.sh" ? ["*registered_script_entrypoint"]
      : source_locator === "mcp-server/local-verb.mjs" ? ["*registered_mcp_tool"] : [],
    request_shape: "fixed_script_cli_contract",
    idempotency_mode: "script_specific_registered_current_control", rollback_class: "forward_fix_only",
    admission_class: "registered_inventory_only", owner_package: "11",
    implementation_state: "inventoried_not_atomically_mediated", classification_authorizing: false,
  }});
  return [...application, ...scripts].sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function jobDefinitionInventory() {
  const manifest = JSON.parse(readFileSync(resolve(REPO_ROOT, "ops/config/control-plane-workflows.v1.json"), "utf8"));
  return manifest.workflows.map(workflow => {
    const execution = { ...workflow.execution };
    delete execution.kind;
    const projected = {
      ingress_key: `job-definition:${workflow.key}:${workflow.version}`,
      ingress_kind: "job_definition",
      key: workflow.key,
      version: workflow.version,
      enabled: workflow.enabled,
      risk: workflow.risk,
      owner_actor: workflow.inventory?.owner || "system",
      execution_kind: workflow.execution.kind,
      entrypoint: workflow.execution.entrypoint || workflow.execution.cognition_job,
      execution_contract: execution,
      inventory_contract: workflow.inventory || {},
      recurrence: workflow.recurrence,
      state_contract: workflow.state,
      routing_contract: workflow.routing,
      filtering_contract: workflow.filtering,
      validation_contract: workflow.validation,
      retry_policy: workflow.retry,
      deduplication: workflow.deduplication,
      completion_contract: workflow.completion,
      legacy_schedule: workflow.legacy_schedule,
    };
    return {
      ...projected,
      effect_class: "administrative_mutation",
      source_locator: `ops/config/control-plane-workflows.v1.json#${workflow.key}:v${workflow.version}`,
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "existing_control_plane_authority_sync",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [projected.entrypoint],
      request_shape: "closed_reviewed_workflow_contract",
      idempotency_mode: "job_key_version_and_schedule_bound",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  }).sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

function yamlSections(source, key) {
  const lines = source.split(/\r?\n/);
  const sections = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(new RegExp(`^(\\s*)${key}:`));
    if (!match) continue;
    const indent = match[1].length;
    const section = [lines[index]];
    for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
      const line = lines[cursor];
      if (line.trim() && !line.trimStart().startsWith("#") && line.search(/\S/) <= indent) break;
      section.push(line);
    }
    sections.push(section.join("\n"));
  }
  return sections;
}

function decodeXmlText(value) {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(Number.parseInt(hex, 16)))
    .replace(/&#([0-9]+);/g, (_, decimal) => String.fromCodePoint(Number.parseInt(decimal, 10)));
}

export function parsePlistXml(source) {
  const clean = source
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<\?xml[\s\S]*?\?>/g, "")
    .replace(/<!DOCTYPE[\s\S]*?>/g, "");
  const tokens = clean.match(/<[^>]+>|[^<]+/g) || [];
  let cursor = 0;
  const skipWhitespace = () => {
    while (cursor < tokens.length && !tokens[cursor].startsWith("<") && !tokens[cursor].trim()) cursor += 1;
  };
  const parseElement = () => {
    skipWhitespace();
    const opening = tokens[cursor++];
    const selfMatch = opening?.match(/^<([A-Za-z][A-Za-z0-9]*)(?:\s[^>]*)?\/>$/);
    if (selfMatch) {
      if (selfMatch[1] === "true") return true;
      if (selfMatch[1] === "false") return false;
      throw new Error(`unsupported self-closing plist element ${selfMatch[1]}`);
    }
    const openMatch = opening?.match(/^<([A-Za-z][A-Za-z0-9]*)(?:\s[^>]*)?>$/);
    if (!openMatch) throw new Error(`malformed plist token ${opening || "<eof>"}`);
    const tag = openMatch[1];
    if (tag === "plist") {
      const value = parseElement();
      skipWhitespace();
      if (tokens[cursor++] !== "</plist>") throw new Error("plist root is not closed");
      return value;
    }
    if (tag === "dict") {
      const value = {};
      while (true) {
        skipWhitespace();
        if (tokens[cursor] === "</dict>") { cursor += 1; return value; }
        if (!tokens[cursor]?.startsWith("<key")) throw new Error("plist dict key is missing");
        const key = parseElement();
        if (Object.hasOwn(value, key)) throw new Error(`duplicate plist key ${key}`);
        value[key] = parseElement();
      }
    }
    if (tag === "array") {
      const value = [];
      while (true) {
        skipWhitespace();
        if (tokens[cursor] === "</array>") { cursor += 1; return value; }
        value.push(parseElement());
      }
    }
    if (!["key", "string", "integer", "real", "date", "data"].includes(tag))
      throw new Error(`unsupported plist element ${tag}`);
    let raw = "";
    while (cursor < tokens.length && tokens[cursor] !== `</${tag}>`) raw += tokens[cursor++];
    if (tokens[cursor++] !== `</${tag}>`) throw new Error(`plist ${tag} is not closed`);
    const text = decodeXmlText(raw);
    if (tag === "integer") {
      if (!/^-?[0-9]+$/.test(text.trim())) throw new Error(`invalid plist integer ${text}`);
      return Number.parseInt(text.trim(), 10);
    }
    if (tag === "real") {
      const value = Number(text.trim());
      if (!Number.isFinite(value)) throw new Error(`invalid plist real ${text}`);
      return value;
    }
    return tag === "data" ? text.replace(/\s+/g, "") : text;
  };
  const value = parseElement();
  skipWhitespace();
  if (cursor !== tokens.length) throw new Error(`unexpected plist content ${tokens[cursor]}`);
  return value;
}

export function validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy) {
  const reviewedPaths = new Set(launchdPaths);
  if (reviewedPaths.size !== launchdPaths.length) throw new Error("duplicate launchd source path");
  const serviceKeys = new Set();
  for (const service of services.services || []) {
    if (serviceKeys.has(service.key)) throw new Error(`duplicate ops.service key ${service.key}`);
    serviceKeys.add(service.key);
  }
  const servicesByPlist = new Map();
  for (const service of services.services || []) {
    const serviceEnvironments = new Set();
    for (const environment of service.environments || []) {
      if (serviceEnvironments.has(environment.environment))
        throw new Error(`duplicate ops.service environment ${service.key}:${environment.environment}`);
      serviceEnvironments.add(environment.environment);
      const path = environment.deploy_mechanism;
      if (typeof path !== "string" || !path.startsWith("ops/launchd/") || !path.endsWith(".plist")) continue;
      if (typeof environment.environment !== "string" || !environment.environment)
        throw new Error(`launchd ops.service environment is missing ${service.key}:${path}`);
      const mappings = servicesByPlist.get(path) || [];
      const mapping = { service_key: service.key, environment: environment.environment };
      servicesByPlist.set(path, [...mappings, mapping]
        .sort((left, right) => `${left.service_key}:${left.environment}`.localeCompare(`${right.service_key}:${right.environment}`)));
    }
  }
  const missingServices = [...reviewedPaths].filter(path => !servicesByPlist.has(path)).sort();
  const orphanServices = [...servicesByPlist.keys()].filter(path => !reviewedPaths.has(path)).sort();
  if (missingServices.length || orphanServices.length)
    throw new Error(`launchd ops.service catalog closure mismatch missing=${missingServices.join(",")} orphan=${orphanServices.join(",")}`);

  const legacyByPlist = new Map();
  const legacySurfaceIds = new Set();
  for (const surface of legacy.surfaces || []) {
    if (surface.scheduler_kind !== "launchd") continue;
    if (legacySurfaceIds.has(surface.surface_id)) throw new Error(`duplicate launchd legacy surface ${surface.surface_id}`);
    legacySurfaceIds.add(surface.surface_id);
    if (!reviewedPaths.has(surface.repo_plist_relpath))
      throw new Error(`orphan launchd legacy path ${surface.repo_plist_relpath}`);
    if (legacyByPlist.has(surface.repo_plist_relpath))
      throw new Error(`duplicate launchd legacy path ${surface.repo_plist_relpath}`);
    legacyByPlist.set(surface.repo_plist_relpath, surface);
  }
  return { servicesByPlist, legacyByPlist };
}

export function assertLegacyLaunchdSource(surface, sourceLocator, plist) {
  if (surface.repo_plist_relpath !== sourceLocator || plist.Label !== surface.locator ||
      JSON.stringify(plist.ProgramArguments) !== JSON.stringify(surface.canonical_program_arguments) ||
      sha256(plist) !== surface.canonical_plist_fingerprint)
    throw new Error(`launchd legacy source mismatch ${surface.surface_id}`);
}

function launchdAuthorityMaps(launchdPaths) {
  const servicesPath = "ops/config/services.json";
  const legacyPath = "ops/config/control-plane-scheduler-cutover.v1.json";
  const services = JSON.parse(readFileSync(resolve(REPO_ROOT, servicesPath), "utf8"));
  const legacy = JSON.parse(readFileSync(resolve(REPO_ROOT, legacyPath), "utf8"));
  const { servicesByPlist, legacyByPlist } = validateLaunchdAuthorityCatalogs(launchdPaths, services, legacy);
  return {
    servicesByPlist,
    legacyByPlist,
    catalogDigests: {
      services: `sha256:${sourceDigest(servicesPath)}`,
      legacy_schedule_launchd: `sha256:${sourceDigest(legacyPath)}`,
    },
  };
}

export function workflowDefinitionInventory() {
  const github = walkFiles(".github/workflows").filter(path => /\.ya?ml$/.test(path)).sort().map(source_locator => {
    const source = readFileSync(resolve(REPO_ROOT, source_locator), "utf8");
    const runSource = yamlSections(source, "run").join("\n");
    const actionDelegates = [...source.matchAll(/^\s*-?\s*uses:\s*([^\s#]+)/gm)]
      .map(match => `github-action:${match[1]}`);
    const scriptDelegates = [...runSource.matchAll(/(?:^|[\s"'])(?:(?:\.\/)?((?:bin|tools|ops|pipelines|hooks)\/[A-Za-z0-9_./-]+))/gm)]
      .map(match => `script:${match[1]}`);
    const commandDelegates = [
      ...(runSource.includes("aws s3api put-object") ? ["shell:aws-s3api-put-object"] : []),
      ...(runSource.includes("gh pr merge") ? ["shell:gh-pr-merge"] : []),
    ];
    return {
      ingress_key: `github-workflow:${source_locator.split("/").at(-1)}`,
      ingress_kind: "workflow_entrypoint",
      operation: source_locator,
      effect_class: "administrative_mutation",
      source_locator,
      schema_digest: sourceDigest(source_locator),
      handler_digest: sourceDigest(source_locator),
      trigger_contract_digest: sha256(yamlSections(source, "on")),
      permissions_contract_digest: sha256(yamlSections(source, "permissions")),
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "github_actions_token_and_declared_workflow_permissions",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [...new Set([...actionDelegates, ...scriptDelegates, ...commandDelegates])].sort(),
      request_shape: "github_workflow_trigger_and_permissions_source_bound",
      idempotency_mode: "github_run_and_attempt_bound_current_control",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  });
  const scriptEntrypoints = new Set(discoverScriptEntrypoints());
  const launchdPaths = walkFiles("ops/launchd").filter(path => path.endsWith(".plist")).sort();
  const { servicesByPlist, legacyByPlist, catalogDigests } = launchdAuthorityMaps(launchdPaths);
  const launchd = launchdPaths.map(source_locator => {
    const source = readFileSync(resolve(REPO_ROOT, source_locator), "utf8");
    const plist = parsePlistXml(source);
    const programArguments = Array.isArray(plist.ProgramArguments) ? plist.ProgramArguments : [];
    const label = typeof plist.Label === "string" ? plist.Label : null;
    const delegates = programArguments.filter(argument => argument.includes("/")).map(argument => {
      let candidate = argument;
      if (candidate.startsWith("{{REPO}}/")) candidate = candidate.slice("{{REPO}}/".length);
      else if (candidate.startsWith("/Users/booko/carr-system/")) candidate = candidate.slice("/Users/booko/carr-system/".length);
      if (scriptEntrypoints.has(candidate)) return `script:${candidate}`;
      return argument.startsWith("/") ? `executable:${argument}` : `argument:${argument}`;
    });
    const triggerContract = Object.fromEntries(["KeepAlive", "RunAtLoad", "StartInterval", "StartCalendarInterval", "WatchPaths", "QueueDirectories"]
      .filter(key => Object.hasOwn(plist, key)).map(key => [key, plist[key]]));
    const environmentContract = plist.EnvironmentVariables || {};
    const serviceMappings = servicesByPlist.get(source_locator) || [];
    if (!serviceMappings.length) throw new Error(`launchd workflow lacks ops.service authority mapping: ${source_locator}`);
    const physicalAuthorityRefs = serviceMappings.map(mapping =>
      `ops.service_environment:${mapping.service_key}:${mapping.environment}`);
    const legacySurface = legacyByPlist.get(source_locator);
    if (legacySurface) {
      assertLegacyLaunchdSource(legacySurface, source_locator, plist);
      physicalAuthorityRefs.push(`ops.legacy_schedule_launchd_contract:${legacySurface.surface_id}`);
    }
    return {
      ingress_key: `launchd-workflow:${label || source_locator.split("/").at(-1)}`,
      ingress_kind: "workflow_entrypoint",
      operation: source_locator,
      effect_class: "administrative_mutation",
      source_locator,
      schema_digest: sourceDigest(source_locator),
      handler_digest: sourceDigest(source_locator),
      launchd_label: label,
      trigger_contract_digest: sha256(triggerContract),
      environment_contract_digest: sha256(environmentContract),
      program_arguments_digest: sha256(programArguments),
      write: true,
      human_only: false,
      authority_only: true,
      principal_mode: "launchd_user_session_and_declared_environment",
      mutation_kind: "scac.mutation.admin",
      target_surface: "scac.surface.runtime",
      delegates_to: [...new Set(delegates)].sort(),
      request_shape: "launchd_trigger_arguments_environment_source_bound",
      idempotency_mode: "delegated_entrypoint_current_control",
      rollback_class: "forward_fix_only",
      admission_class: "registered_inventory_only",
      physical_authority_refs: physicalAuthorityRefs.sort(),
      physical_authority_catalog_digests: catalogDigests,
      owner_package: "11",
      implementation_state: "inventoried_not_atomically_mediated",
      classification_authorizing: false,
    };
  });
  return [...github, ...launchd].sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function fullInventory(tools = TOOLS) {
  return [...mcpInventory(tools), ...nonMcpInventory(), ...jobDefinitionInventory(), ...workflowDefinitionInventory()]
    .sort((left, right) => left.ingress_key.localeCompare(right.ingress_key));
}

export function registryDigest(rows = fullInventory()) {
  return sha256({ schema_version: REGISTRY_VERSION, rows, db_catalog_baseline: DB_CATALOG_BASELINE });
}

export function renderRuntimeProjection(rows = fullInventory()) {
  const digest = registryDigest(rows);
  const projection = Object.fromEntries(rows.filter(row => row.ingress_kind === "mcp_tool").map(row => [row.operation, {
    ingress_key: row.ingress_key,
    source_locator: row.source_locator,
    source_digest: row.source_digest,
    schema_digest: row.schema_digest,
    write: row.write,
    human_only: row.human_only,
    authority_only: row.authority_only,
    delegates_to: row.delegates_to,
  }]));
  return `// GENERATED by ops/scac-mutation-inventory.mjs. Review changes; never hand-edit.\n` +
    `// This is a non-authorizing source/build guard. The sealed DB registry is SIEP-11's sole metadata authority; SIEP-18 owns atomic admission.\n` +
    `export const SCAC_MUTATION_REGISTRY_VERSION = ${JSON.stringify(REGISTRY_VERSION)};\n` +
    `export const SCAC_MUTATION_REGISTRY_DIGEST = ${JSON.stringify(digest)};\n` +
    `export const SCAC_MUTATION_DB_METADATA_AUTHORITY = true;\n` +
    `export const SCAC_MUTATION_RUNTIME_PROJECTION_AUTHORIZING = false;\n` +
    `export const SCAC_MUTATION_OPERATIONS = Object.freeze(${JSON.stringify(projection, null, 2)});\n`;
}

function sqlLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function catalogSeedSql() {
  const common = `'effect_class','administrative_mutation','owner_package','11',` +
    `'implementation_state','inventoried_not_atomically_mediated','classification_authorizing',false`;
  return `-- Exact database capability rows are projected from the just-built catalog.\n` +
`-- The sealed expected category digests are independently recomputed by the DB gate.\n` +
`with runtime_roles as (\n` +
`  select oid,rolname from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'\n` +
`), functions as (\n` +
`  select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner\n` +
`    from pg_proc p join pg_namespace n on n.oid=p.pronamespace\n` +
`   where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')\n` +
`), capabilities as (\n` +
`  select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f\n` +
`  cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl\n` +
`), observed as (\n` +
`  select jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute',\n` +
`    'ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,\n` +
`    'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),\n` +
`    'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row\n` +
`  from capabilities c left join pg_roles r on r.oid=c.grantee\n` +
`  where prosecdef and privilege_type='EXECUTE' and (grantee=0 or r.oid in(select oid from runtime_roles))\n` +
`), contracts as (\n` +
`  select row||jsonb_build_object(${common},'source_locator',row->>'signature') contract from observed\n` +
`)\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select 'scac-mutation-registry.v1',contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`  'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'),contract from contracts;\n\n` +
`with runtime_roles as (select oid,rolname from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'), capabilities as (\n` +
`  select n.nspname,c.relname,c.relkind,acl.grantee,acl.privilege_type,acl.is_grantable\n` +
`  from pg_class c join pg_namespace n on n.oid=c.relnamespace\n` +
`  cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl\n` +
`  where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')\n` +
`), observed as (\n` +
`  select jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),\n` +
`    'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),\n` +
`    'privilege',lower(privilege_type),'grantable',is_grantable) row\n` +
`  from capabilities c left join pg_roles r on r.oid=c.grantee\n` +
`  where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and (grantee=0 or r.oid in(select oid from runtime_roles))\n` +
`), contracts as (\n` +
`  select row||jsonb_build_object(${common},'source_locator',row->>'relation') contract from observed\n` +
`)\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select 'scac-mutation-registry.v1',contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`  'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'),contract from contracts;\n\n` +
`with runtime_roles as (select oid,rolname from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'), capabilities as (\n` +
`  select n.nspname,c.relname,c.relkind,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable\n` +
`  from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace\n` +
`  cross join lateral aclexplode(a.attacl) acl\n` +
`  where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0\n` +
`    and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')\n` +
`), observed as (\n` +
`  select jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),\n` +
`    'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,\n` +
`    'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row\n` +
`  from capabilities c left join pg_roles r on r.oid=c.grantee\n` +
`  where privilege_type in ('INSERT','UPDATE') and (grantee=0 or r.oid in(select oid from runtime_roles))\n` +
`), contracts as (\n` +
`  select row||jsonb_build_object(${common},'source_locator',(row->>'relation')||'.'||(row->>'column')) contract from observed\n` +
`)\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select 'scac-mutation-registry.v1',contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',contract->>'source_locator',\n` +
`  'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'),contract from contracts;\n\n`;
}

export function renderMigration(rows = fullInventory()) {
  const digest = registryDigest(rows);
  const dbCatalogCount = DB_CATALOG_BASELINE.secdef_execute.count +
    DB_CATALOG_BASELINE.relation_dml.count + DB_CATALOG_BASELINE.column_dml.count;
  const totalCount = rows.length + dbCatalogCount;
  const seed = JSON.stringify(rows.map(row => ({
    ...row,
    entry_digest: `sha256:${sha256(row)}`,
  })));
  return `-- SIEP-11 / SCAC-01: immutable mutation ingress registry.\n` +
    `-- Source/test implementation only. Applying this migration to Production remains Joe-gated.\n` +
    `-- GENERATED seed from ops/scac-mutation-inventory.mjs; review, never hand-edit rows.\n\n` +
`begin;\n\n` +
`create table ops.scac_mutation_registry_version (\n` +
`  registry_version text primary key check (registry_version='scac-mutation-registry.v1'),\n` +
`  program_key text not null check (program_key='carr-system-integrity-elimination-v1'),\n` +
`  package_key text not null check (package_key='11'),\n` +
`  charter_digest text not null check (charter_digest='sha256:473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c'),\n` +
`  registry_digest text not null unique check (registry_digest ~ '^sha256:[0-9a-f]{64}$'),\n` +
`  entry_count integer not null check (entry_count>0),\n` +
`  source_entry_count integer not null check (source_entry_count>0 and source_entry_count<=entry_count),\n` +
`  catalog_projection jsonb not null check (jsonb_typeof(catalog_projection)='object'),\n` +
`  entry_set_digest text check (entry_set_digest is null or entry_set_digest ~ '^sha256:[0-9a-f]{64}$'),\n` +
`  mcp_default_deny_source_guarded boolean not null,\n` +
`  db_metadata_authority boolean not null check (db_metadata_authority),\n` +
`  runtime_projection_authorizing boolean not null check (not runtime_projection_authorizing),\n` +
`  non_mcp_default_deny_operational boolean not null check (not non_mcp_default_deny_operational),\n` +
`  atomic_database_mediation_operational boolean not null check (not atomic_database_mediation_operational),\n` +
`  direct_database_grant_cutover boolean not null check (not direct_database_grant_cutover),\n` +
`  production_enforcement_active boolean not null check (not production_enforcement_active),\n` +
`  sealed_at timestamptz not null default now()\n` +
`);\n\n` +
`create table ops.scac_mutation_registry_entry (\n` +
`  registry_version text not null references ops.scac_mutation_registry_version(registry_version) on delete restrict,\n` +
`  ingress_key text not null check (ingress_key ~ '^[a-z][a-z0-9_-]+:' and ingress_key !~ E'[\\n\\r\\t]' and char_length(ingress_key)<=1000),\n` +
`  ingress_kind text not null check (ingress_kind in ('mcp_tool','worker_route','worker_sidewrite','db_function_acl','db_relation_acl','db_column_acl','job_definition','workflow_entrypoint','script_entrypoint','external_admin','break_glass')),\n` +
`  effect_class text not null check (effect_class in ('read_only','audit_side_effect','record_mutation','external_mutation','administrative_mutation','delegating','break_glass')),\n` +
`  source_locator text not null check (btrim(source_locator)<>'' and char_length(source_locator)<=500),\n` +
`  entry_digest text not null check (entry_digest ~ '^sha256:[0-9a-f]{64}$'),\n` +
`  contract jsonb not null check (jsonb_typeof(contract)='object'),\n` +
`  registered_at timestamptz not null default now(),\n` +
`  primary key (registry_version,ingress_key),\n` +
`  unique (registry_version,entry_digest),\n` +
`  check (contract->>'ingress_key'=ingress_key and contract->>'ingress_kind'=ingress_kind\n` +
`    and contract->>'effect_class'=effect_class and contract->>'source_locator'=source_locator\n` +
`    and contract->>'entry_digest' is null)\n` +
`);\n\n` +
`create or replace function ops.scac_canonical_json(p_value jsonb) returns text\n` +
`language plpgsql immutable strict set search_path=pg_catalog,ops as $$\n` +
`declare kind text:=jsonb_typeof(p_value); rendered text;\n` +
`begin\n` +
`  if kind='object' then\n` +
`    select '{'||coalesce(string_agg(to_jsonb(key)::text||':'||ops.scac_canonical_json(value),',' order by key),'')||'}' into rendered from jsonb_each(p_value);\n` +
`    return rendered;\n` +
`  elsif kind='array' then\n` +
`    select '['||coalesce(string_agg(ops.scac_canonical_json(value),',' order by ordinal),'')||']' into rendered from jsonb_array_elements(p_value) with ordinality as a(value,ordinal);\n` +
`    return rendered;\n` +
`  end if;\n` +
`  return p_value::text;\n` +
`end $$;\n\n` +
`create or replace function ops.scac_mutation_registry_append_only() returns trigger\n` +
`language plpgsql security definer set search_path=pg_catalog,ops as $$\n` +
`begin raise exception 'SCAC mutation registry is append-only and sealed'; end $$;\n` +
`insert into ops.scac_mutation_registry_version(registry_version,program_key,package_key,charter_digest,registry_digest,entry_count,source_entry_count,catalog_projection,mcp_default_deny_source_guarded,db_metadata_authority,runtime_projection_authorizing,non_mcp_default_deny_operational,atomic_database_mediation_operational,direct_database_grant_cutover,production_enforcement_active)\n` +
`values ('scac-mutation-registry.v1','carr-system-integrity-elimination-v1','11','sha256:473b7b1cd2ea975ba118f05406b35f4affdda0cb61f4487c252db129a882151c','sha256:${digest}',${totalCount},${rows.length},${sqlLiteral(JSON.stringify(DB_CATALOG_BASELINE))}::jsonb,true,true,false,false,false,false,false);\n\n` +
`with seed as (select value as contract from jsonb_array_elements(${sqlLiteral(seed)}::jsonb))\n` +
`insert into ops.scac_mutation_registry_entry(registry_version,ingress_key,ingress_kind,effect_class,source_locator,entry_digest,contract)\n` +
`select 'scac-mutation-registry.v1',contract->>'ingress_key',contract->>'ingress_kind',contract->>'effect_class',\n` +
`       contract->>'source_locator','sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract-'entry_digest'),'UTF8'),'sha256'),'hex'),contract-'entry_digest'\n` +
`from seed;\n\n` +
`create or replace function ops.scac_mutation_registration(p_expected_digest text,p_ingress_key text)\n` +
`returns jsonb language plpgsql stable security definer set search_path=pg_catalog,ops as $$\n` +
`declare v ops.scac_mutation_registry_version%rowtype; e ops.scac_mutation_registry_entry%rowtype; actual_count integer; actual_set_digest text; contract_digest_mismatch boolean;\n` +
`begin\n` +
`  select * into v from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1';\n` +
`  if v.registry_version is null then return jsonb_build_object('registered',false,'reason','registry_unavailable'); end if;\n` +
`  select count(*),'sha256:'||encode(public.digest(convert_to(coalesce(string_agg(entry_digest,',' order by ingress_key),''),'UTF8'),'sha256'),'hex'),\n` +
`         coalesce(bool_or(entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex')),false)\n` +
`    into actual_count,actual_set_digest,contract_digest_mismatch from ops.scac_mutation_registry_entry where registry_version=v.registry_version;\n` +
`  if actual_count<>v.entry_count or actual_set_digest is distinct from v.entry_set_digest or contract_digest_mismatch then\n` +
`    return jsonb_build_object('registered',false,'reason','registry_corrupt','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  if p_expected_digest is distinct from v.registry_digest then return jsonb_build_object('registered',false,'reason','digest_mismatch','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  if p_ingress_key is null or p_ingress_key !~ '^[a-z][a-z0-9_-]+:' or p_ingress_key ~ E'[\\n\\r\\t]' or char_length(p_ingress_key)>1000 then return jsonb_build_object('registered',false,'reason','malformed_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  select * into e from ops.scac_mutation_registry_entry where registry_version=v.registry_version and ingress_key=p_ingress_key;\n` +
`  if e.ingress_key is null then return jsonb_build_object('registered',false,'reason','unknown_ingress','registry_version',v.registry_version,'registry_digest',v.registry_digest); end if;\n` +
`  return jsonb_build_object('registered',true,'reason','registered_inventory','registry_version',v.registry_version,'registry_digest',v.registry_digest,\n` +
`    'ingress_key',e.ingress_key,'ingress_kind',e.ingress_kind,'effect_class',e.effect_class,'entry_digest',e.entry_digest,\n` +
`    'implementation_state',e.contract->>'implementation_state','atomic_database_mediation_operational',false);\n` +
`end $$;\n\n` +
`revoke all on ops.scac_mutation_registry_version,ops.scac_mutation_registry_entry from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`revoke all on function ops.scac_canonical_json(jsonb) from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`revoke all on function ops.scac_mutation_registry_append_only() from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`revoke all on function ops.scac_mutation_registration(text,text) from public,carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`grant execute on function ops.scac_mutation_registration(text,text) to carr_reader,carr_writer,carr_jobs,carr_authority;\n` +
`comment on function ops.scac_mutation_registration(text,text) is 'Read-only SIEP-11 registry lookup. Presence inventories current ingress only; it never grants SCAC authority or claims SIEP-18 atomic mediation.';\n\n` +
catalogSeedSql() +
`do $$ declare actual_count integer; actual_digest text; expected jsonb; category text; kind text;\n` +
`begin\n` +
`  for category,kind in values ('secdef_execute','db_function_acl'),('relation_dml','db_relation_acl'),('column_dml','db_column_acl') loop\n` +
`    select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(\n` +
`      contract-'effect_class'-'owner_package'-'implementation_state'-'classification_authorizing'-'source_locator' order by ingress_key),'[]'::jsonb)),'UTF8'),'sha256'),'hex')\n` +
`      into actual_count,actual_digest from ops.scac_mutation_registry_entry where ingress_kind=kind;\n` +
`    select catalog_projection->category into expected from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1';\n` +
`    if actual_count<>(expected->>'count')::integer or actual_digest<>expected->>'digest' then\n` +
`      raise exception 'SCAC database catalog category % drifted: count %, digest %',category,actual_count,actual_digest;\n` +
`    end if;\n` +
`  end loop;\n` +
`end $$;\n\n` +
`update ops.scac_mutation_registry_version v set entry_set_digest=(\n` +
`  select 'sha256:'||encode(public.digest(convert_to(string_agg(e.entry_digest,',' order by e.ingress_key),'UTF8'),'sha256'),'hex')\n` +
`  from ops.scac_mutation_registry_entry e where e.registry_version=v.registry_version\n` +
`) where registry_version='scac-mutation-registry.v1';\n\n` +
`do $$ begin\n` +
`  if (select count(*) from ops.scac_mutation_registry_entry where registry_version='scac-mutation-registry.v1')<>${totalCount}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='mcp_tool')<>184\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='job_definition')<>26\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='workflow_entrypoint')<>28\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='db_function_acl')<>${DB_CATALOG_BASELINE.secdef_execute.count}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='db_relation_acl')<>${DB_CATALOG_BASELINE.relation_dml.count}\n` +
`     or (select count(*) from ops.scac_mutation_registry_entry where ingress_kind='db_column_acl')<>${DB_CATALOG_BASELINE.column_dml.count}\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where contract->>'owner_package'<>'11' or (contract->>'classification_authorizing')::boolean)\n` +
`     or exists(select 1 from ops.scac_mutation_registry_entry where entry_digest is distinct from 'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(contract),'UTF8'),'sha256'),'hex'))\n` +
`     or (select entry_set_digest is null from ops.scac_mutation_registry_version where registry_version='scac-mutation-registry.v1') then\n` +
`    raise exception 'SCAC mutation registry seed is incomplete, drifted, or authority-expanding';\n` +
`  end if;\n` +
`end $$;\n` +
`alter table ops.scac_mutation_registry_version alter column entry_set_digest set not null;\n\n` +
`create trigger scac_mutation_registry_version_sealed before insert or update or delete on ops.scac_mutation_registry_version\n` +
`for each row execute function ops.scac_mutation_registry_append_only();\n` +
`create trigger scac_mutation_registry_entry_sealed before insert or update or delete on ops.scac_mutation_registry_entry\n` +
`for each row execute function ops.scac_mutation_registry_append_only();\n\n` +
`commit;\n`;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const rows = fullInventory();
  if (process.argv[2] === "--write-runtime") {
    const target = resolve(process.argv[3] || "mcp-server/src/scac-mutation-registry.generated.js");
    await writeFile(target, renderRuntimeProjection(rows));
    process.stdout.write(`${target}\n`);
  } else if (process.argv[2] === "--write-migration") {
    const target = resolve(process.argv[3] || "migrations/0330_siep11_mutation_registry.sql");
    await writeFile(target, renderMigration(rows));
    process.stdout.write(`${target}\n`);
  } else {
    process.stdout.write(`${JSON.stringify({ schema_version: REGISTRY_VERSION, digest: registryDigest(rows), rows }, null, 2)}\n`);
  }
}

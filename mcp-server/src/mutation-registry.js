import {
  SCAC_MUTATION_DB_METADATA_AUTHORITY,
  SCAC_MUTATION_OPERATIONS,
  SCAC_MUTATION_REGISTRY_DIGEST,
  SCAC_MUTATION_REGISTRY_VERSION,
  SCAC_MUTATION_RUNTIME_PROJECTION_AUTHORIZING,
// v49 is the runtime selector after V5-UX-B11 Meeting Mode. Its sealed
// catalog admits the eight meeting verbs and preserves earlier versions as
// history; v42-v48 registered no verb, so the selector stayed on v41 until now.
//
// Superseded note (B09): v41 was the runtime selector because it admitted the
// outcome-card read.
//
// Superseded note (WR-000119): v35 was the runtime selector because it installed the DISPATCH SPINE --
// TWO new security-definer WRITE ingresses and TWO new verbs in a NEW family
// file, plus TWO new relations whose grants move relation_dml, which is why
// mcp-server/src/tools.js is edited again and every mcp-tool row sourced from
// it re-digests with the selector.
//
// Superseded note (WR-000117): v34 was the selector because it installed the
// session-identity READ PAIR -- two new security-definer ingresses and two new
// verbs in a new family file, with no relation and no table grant at all.
//
// Superseded note (WR-000116): v33 was the selector because it installed the
// notification-preference pair in the already-registered notifications family
// file.
//
// Superseded note (WR-000114): v31 was the selector because it installed the three Doc
// conversation write doors -- create, share/revoke and rename/pin/archive --
// as new SECURITY DEFINER ingresses, and the selector must name the sealed
// version that admits them.
//
// Superseded note (WR-000111/112/113): v30 was the selector because those
// requests installed the producer
// cost ledger, the Doc conversation store and the R03 notification store.
// The line below is the ONE place the runtime version is chosen.
// Superseded note (WR-000110): v29 was the selector because that request
// installed the V5-F02
// program-controller seams: one new SECURITY DEFINER writer with two grantees,
// and an edit to engineering-runtime.js that re-digests every mcp-tool row
// registered from it. Older registries must continue to refuse the new shapes
// as a contract mismatch: a registry that has not sealed the change does not
// know it.
} from "./scac-mutation-registry.v49.generated.js";

export { SCAC_MUTATION_REGISTRY_DIGEST, SCAC_MUTATION_REGISTRY_VERSION };

export class MutationRegistryRefusal extends Error {
  constructor(error, operation) {
    super(error);
    this.name = "MutationRegistryRefusal";
    this.error = error;
    this.operation = operation;
  }
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]));
  return value;
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(typeof value === "string" ? value : JSON.stringify(canonicalize(value)));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

export function registeredOperation(name) {
  return typeof name === "string" ? SCAC_MUTATION_OPERATIONS[name] || null : null;
}

export function assertClosedTopLevel(name, tool, args) {
  if (!args || typeof args !== "object" || Array.isArray(args)) return;
  const allowed = new Set(Object.keys(tool?.inputSchema?.properties || {}));
  const extras = Object.keys(args).filter(key => !allowed.has(key)).sort();
  if (extras.length) {
    const refusal = new MutationRegistryRefusal("unregistered_operation_fields", name);
    refusal.fields = extras;
    throw refusal;
  }
}

export async function assertRegisteredOperation(name, tool, args) {
  const row = registeredOperation(name);
  if (!row) throw new MutationRegistryRefusal("unregistered_operation", name);
  const actual = {
    source_locator: tool?.registrySource,
    schema_digest: await sha256(tool?.inputSchema || {}),
    write: tool?.write === true,
    human_only: tool?.humanOnly === true,
    authority_only: tool?.authorityOnly === true,
  };
  for (const [key, value] of Object.entries(actual)) {
    if (row[key] !== value) throw new MutationRegistryRefusal("mutation_contract_mismatch", name);
  }
  assertClosedTopLevel(name, tool, args);
  return row;
}

export function mutationManifestIdentity() {
  return {
    registry_version: SCAC_MUTATION_REGISTRY_VERSION,
    registry_digest: SCAC_MUTATION_REGISTRY_DIGEST,
    db_metadata_authority: SCAC_MUTATION_DB_METADATA_AUTHORITY,
    runtime_projection_authorizing: SCAC_MUTATION_RUNTIME_PROJECTION_AUTHORIZING,
  };
}

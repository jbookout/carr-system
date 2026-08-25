// CARR-owned, read-only bootstrap contract for a local bot runtime.
// The brief describes a profile; it never turns that profile into authority.

import {
  authorizationClassForActor,
  organizationTenantForActor,
  personalScopeForActor,
} from "./identity.js";

const PROFILE_KEY = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
// Tools' central top-level authority guard owns canonical aliases such as
// actor, identity, audience, capabilities, action(s), and writes_records.
// These are Bot-Brief-specific names that never belong in its input either.
const BOT_BRIEF_AUTHORITY_FIELDS = new Set([
  "partner", "runtime", "device", "authority", "personal_brain_scope",
  "operational_profile", "session_capability_profile", "human_only_authority",
  "device_id", "profile_grants_authority", "brief_grants_authority",
]);

function canon(value) {
  if (Array.isArray(value)) return value.map(canon);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((out, key) => {
      if (value[key] !== undefined) out[key] = canon(value[key]);
      return out;
    }, {});
  }
  return value;
}

async function digest(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(canon(value)));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)]
    .map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function requestedPacks(args) {
  if (args.packs === undefined) return [];
  if (!Array.isArray(args.packs)) return null;
  if (args.packs.some(pack => typeof pack !== "string")) return null;
  return [...new Set(args.packs.map(pack => pack.trim().toLowerCase()).filter(Boolean))].sort();
}

// Hermes consumes the server-issued bundle as a bounded reference list. This
// helper is intentionally pure so the caller can attach it to the existing
// bot brief after admission; it never accepts or renders raw context bodies.
export function botBriefTools({ ToolError, assertNoCallerAuthorityFields }) {
  return {
    "bot-brief": {
      description: "Return the server-derived, versioned bootstrap brief for one named agent profile. The profile is presentation and routing only, never authority. This read contains no rule bodies, personal-rule bodies, secrets, or local paths; callers must use standing-context for rules and tools/list for capabilities.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: {
          profile_key: { type: "string", description: "Named profile from read-profiles." },
          packs: { type: "array", items: { type: "string" }, description: "Optional rule-delivery pack names to report." },
          work_request: { type: "string", pattern: "^WR-[0-9]{1,12}$" },
          activation_binding_id: { type: "string", pattern: "^ctx-[0-9a-f]{16}$" },
        },
        required: ["profile_key"],
      },
      handler: async (c, actor, args = {}) => {
        // Keep the central policy authoritative for canonical aliases. This
        // direct-handler call matters because tests and local callers can
        // bypass executeRegisteredTool's outer gate.
        if (typeof assertNoCallerAuthorityFields === "function")
          assertNoCallerAuthorityFields(args);
        const suppliedAuthority = [...BOT_BRIEF_AUTHORITY_FIELDS].filter(field =>
          Object.prototype.hasOwnProperty.call(args, field));
        if (suppliedAuthority.length)
          throw new ToolError({ error: "caller_authority_fields_refused",
            fields: suppliedAuthority.sort(),
            hint: "identity, authority, runtime and capability come from the authenticated server context" });
        const profileKey = String(args.profile_key || "");
        if (!PROFILE_KEY.test(profileKey))
          throw new ToolError({ error: "profile_key_invalid" });
        const packs = requestedPacks(args);
        if (packs === null)
          throw new ToolError({ error: "packs_invalid", hint: "packs must be an array of strings" });
        if ((args.work_request === undefined) !== (args.activation_binding_id === undefined))
          throw new ToolError({ error: "activation_binding_pair_required" });

        const profileResult = await c.query(
          `select profile_key, display_name, charter, current_model, current_desk,
                  sponsor_scope, status, version
             from agent_profile where profile_key=$1`, [profileKey]);
        if (!profileResult.rows.length)
          throw new ToolError({ error: "profile_not_found", profile_key: profileKey,
            hint: "read-profiles lists the named roster; new profiles are a migration" });
        const row = profileResult.rows[0];

        const generationResult = await c.query(
          "select generation from doctrine_meta where id=1");
        const policyResult = await c.query(
          "select mode from ops.rule_delivery_policy limit 1");
        const packResult = await c.query("select * from ops.rule_pack_index()");
        const generation = generationResult.rows[0]?.generation ?? null;
        const deliveryMode = policyResult.rows[0]?.mode ?? null;
        const knownPacks = new Set(packResult.rows
          .map(pack => pack?.pack).filter(pack => typeof pack === "string"));
        const unknownPacks = packs.filter(pack => !knownPacks.has(pack));

        // Identity is derived exclusively from the authenticated actor. No
        // identity-like field is accepted in the input schema or read from it.
        const scope = personalScopeForActor(actor);
        if (scope.status === "error")
          throw new ToolError({ error: scope.error,
            hint: "reconnect through the authenticated CARR runtime; do not supply a partner or sponsor in the tool arguments" });
        const identity = {
          organization_tenant_id: organizationTenantForActor(actor),
          sponsoring_human_slug: scope.status === "personal" ? scope.sponsor : null,
          personal_brain_scope: scope.status === "personal" ? `${scope.sponsor}-personal` : "none",
          runtime_principal: actor?.slug || null,
          authorization_class: actor?.authorization_class || authorizationClassForActor(actor),
          operational_profile: actor?.operational_profile || "full",
          human_only_authority: actor?.human === true,
        };
        let boundContext = null;
        if (args.work_request) {
          await c.query("select set_config('carr.organization_tenant_id',$1::text,true)", [identity.organization_tenant_id]);
          const assignment = await c.query("select ops.context_activation_brief_assignment($1::text,$2::text) as profile_key", [args.work_request, args.activation_binding_id]);
          if (assignment.rows[0]?.profile_key !== profileKey) throw new ToolError({ error: "activation_profile_binding_mismatch" });
          const rendered = await c.query("select ops.render_context_activation_for_brief($1::text,$2::text) as items", [args.work_request, args.activation_binding_id]);
          if (!Array.isArray(rendered.rows[0]?.items)) throw new ToolError({ error: "required_context_render_refused" });
          boundContext = { work_request: args.work_request, binding_id: args.activation_binding_id, ephemeral: true, items: rendered.rows[0].items.map((item) => {
            if (item.delivery_mode === "inline") return item;
            const { content, ...reference } = item;
            return item.delivery_mode === "on_demand_tool" ? { ...reference, retrieval_tool: "render-context-activation" } : reference;
          }) };
        }

        const profile = {
          key: row.profile_key,
          name: row.display_name,
          charter: row.charter,
          model: row.current_model ?? null,
          desk: row.current_desk ?? null,
          sponsor_scope: row.sponsor_scope,
          status: row.status,
          version: Number(row.version),
        };
        const definition = {
          profile, doctrine_generation: generation, rule_delivery_mode: deliveryMode,
          requested_packs: packs, unknown_packs: unknownPacks,
        };
        const definitionDigest = await digest(definition);
        const instanceDigest = await digest({ definition, identity });

        return {
          ok: true,
          brief_version: 1,
          definition_digest: definitionDigest,
          instance_digest: instanceDigest,
          profile,
          identity,
          boot_sources: { standing_context: "standing-context", read_profiles: "read-profiles" },
          local_markdown_authoritative: false,
          local_memory_authoritative: false,
          profile_grants_authority: false,
          brief_grants_authority: false,
          runtime_requirements: {
            skip_context_files: true,
            // Hermes' native memory remains enabled during the CARR
            // transition. It is useful context and a candidate-learning
            // source, never authority; CARR remains authoritative for
            // promoted memory and permissions.
            skip_memory: false,
            memory_mode: "native_non_authoritative",
            ephemeral_system_prompt: true,
          },
          tool_allowlist_source: "mcp tools/list",
          doctrine_generation: generation,
          rule_delivery_mode: deliveryMode,
          requested_packs: packs,
          unknown_packs: unknownPacks,
          runtime_registration: { status: "not_registered", authorized: false },
          bound_context: boundContext,
        };
      },
    },
  };
}

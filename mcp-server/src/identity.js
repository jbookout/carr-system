// CARR MCP server — identity → actor mapping. ONE place, and it is the gate.
//
// Stress-test addendum §I.10 (A10): auth is pinned to IDENTITIES, not consent.
// ALLOW_LIST below has exactly two entries — the two Google identities that
// can be issued a token at all — and by itself maps to exactly two actors
// (joe, dell). Adding an ENTRY (a partner authenticating with a different
// Google identity) is one line there. Adding an ACTOR is not a config change
// — it is a design change, and DISPLAY below is the hard stop that makes that
// true: only a slug present in DISPLAY can ever become a live actor, from
// either path below.
//
// A second, narrower path was added loop #227 (2026-08-06+): AGENT_CLIENT_NAMES
// can override which of DISPLAY's actors a write lands under, for a request
// that already passed ALLOW_LIST — see that map's own comment below for the
// full reasoning. It cannot mint a new actor on its own; codex/grok still had
// to earn their DISPLAY entries the same "design change" way joe/dell did.
//
// Both auth paths — the OAuth grant (props) and the legacy PARTNER_TOKENS
// bearer — resolve through this file, so they cannot drift apart.

import { AsyncLocalStorage } from "node:async_hooks";

import { closedCallable } from "./closed-callable.js";

/** Google identities permitted to be issued a token. Everything else is refused. */
export const ALLOW_LIST = Object.freeze({
  "joe.bookout.carr.us@gmail.com": "joe",
  "dell.mccraney.carr.us@gmail.com": "dell",
});

/** The only actor slugs this Worker will ever hand to a verb. */
const DISPLAY = Object.freeze({ joe: "Joe", dell: "Dell", codex: "Codex", claude: "Claude", grok: "Grok",
  "joe-local": "Joe (local)", "dell-local": "Dell (local)" });
const PARTNER_SLUGS = new Set(["joe", "dell"]);
const SERVER_MACHINE_IDENTITIES = Object.freeze({
  "smoke-probe": { marker: "probe", via: "probe-token" },
  "codex-reviewer": { marker: "review", via: "review-token" },
  "grok-reviewer": { marker: "review", via: "review-token" },
  // The R0 Hermes evaluation runtime (2026-08-16). Registered here for the
  // reason stated above: personalScopeForActor refuses any slug that is neither
  // a DISPLAY actor nor a registered machine identity, so an unregistered
  // Hermes token would fail every call with invalid_runtime_principal. Its
  // scope resolves to shared-only, exactly like the probe and reviewer seats.
  // Adding a second evaluation seat is a deliberate edit here, never something
  // a token can claim.
  // The CoS door is separate, but it is still the same registered Hermes
  // runtime. Accept only these two server-issued provenances.
  "hermes-pilot": { marker: "hermes", via: ["hermes-token", "hermes-cos-token"] },
});
// CARR has one internal tenant at launch. This is intentionally a server
// constant, not a claim accepted from an OAuth client, tool payload, or job.
export const ORGANIZATION_TENANT_ID = "carr-internal";

export function organizationTenantForActor(_actor) {
  return ORGANIZATION_TENANT_ID;
}

// ---------------------------------------------------------------------------
// Outside-model agent surfaces (loop #227). Codex CLI and Grok Build CLI are
// registered in this Worker's /mcp endpoint and authenticate through the SAME
// Google OAuth broker as every human connector (google-oidc.js) — there is no
// separate machine credential, so without this map every write they make
// lands under actor 'joe' or 'dell', indistinguishable from the human using
// Claude Code directly.
//
// THIS IS NOT AN IDENTITY GATE. ALLOW_LIST above still runs first and is
// unchanged: a request must already carry a verified, allow-listed Google
// identity before this map is even consulted (see google-oidc.js's
// handleCallback — humanSlug is resolved and checked BEFORE agentSlugForClient
// is called). This map only decides which actor slug a write LANDS UNDER once
// the human is already known — a miss here degrades to the authenticating
// human's own slug, never to an unauthenticated write and never to a wider
// grant than the human already holds.
//
// MATCHING IS EXACT, case/space-insensitive — same discipline as
// slugForEmail, deliberately not a substring or fuzzy match. The input is a
// self-declared `client_name` from the calling app's OAuth dynamic client
// registration (RFC 7591), read back via env.OAUTH_PROVIDER.lookupClient() in
// google-oidc.js. A self-declared label is exactly the kind of caller-
// supplied claim A10 (see file header) says identity must never be pinned to
// loosely — so it is trusted only for this narrow, reversible, attribution-
// only purpose, and only as an exact match against a curated list, the same
// posture ALLOW_LIST takes on email.
//
//   codex — OpenAI's Codex CLI. Dynamic-registration-only as of 2026-08 (no
//           pre-registered client id support: openai/codex#19154). The
//           literal client_name "Codex" is corroborated third-party (a Figma
//           MCP server allowlists DCR by exact client_name and names "Codex"
//           as one of two strings that pass, the other being "Claude Code")
//           but has NOT been observed directly against THIS Worker yet.
//   claude — Claude Code. This exact, approved client-name entry receives the
//           same attribution/sponsor split as Codex after Google identity has
//           already passed the allow-list.
//   grok  — Grok Build CLI (xAI). NOT YET ADDED. No public or first-party
//           source found (loop #227 research pass) confirming the exact
//           client_name string it sends during DCR, and guessing one would
//           be exactly the kind of fake match this file exists to refuse.
//           Add it the same one-line way once observed live — do not land a
//           guess and call it verified.
//
// CONFIRM ON FIRST REAL CONNECT (either tool): after Joe completes the OAuth
// flow from that CLI against the deployed Worker, read back the grant's
// client_id (KV key `client:<id>` under the OAUTH_KV binding, or a one-off
// script calling env.OAUTH_PROVIDER.lookupClient(clientId)) and diff its
// clientName against the entries below. A mismatch means the entry needs
// correcting, not the CLI.
const AGENT_CLIENT_NAMES = Object.freeze({
  "codex": "codex",
  "claude code": "claude",
  // "grok": "grok",   // add once the real client_name is confirmed live
});

/** Self-declared OAuth client_name -> outside-model agent actor slug, or null. */
export function agentSlugForClient(clientName) {
  if (typeof clientName !== "string") return null;
  return AGENT_CLIENT_NAMES[clientName.trim().toLowerCase()] || null;
}

/**
 * A self-declared OAuth client_name is attribution only. Native-agent
 * authority requires an exact client-id -> actor binding held in server
 * configuration. Unknown, malformed, or mismatched bindings refuse.
 *
 * SIEP-21 will replace this bootstrap binding with enrolled workload identity;
 * until then absence is deliberately non-authorizing.
 */
export function verifiedAgentSlugForClient(clientId, attributedSlug, rawBindings) {
  if (typeof clientId !== "string" || !clientId ||
      typeof attributedSlug !== "string" || !attributedSlug ||
      typeof rawBindings !== "string" || !rawBindings) return null;
  let bindings;
  try { bindings = JSON.parse(rawBindings); }
  catch { return null; }
  if (!bindings || typeof bindings !== "object" || Array.isArray(bindings)) return null;
  const bound = bindings[clientId];
  return (bound === attributedSlug && (bound === "codex" || bound === "claude")) ? bound : null;
}

export function isKnownActor(slug) {
  return typeof slug === "string" && Object.prototype.hasOwnProperty.call(DISPLAY, slug);
}

/** A CARR partner identity, as opposed to a runtime agent identity. */
export function isKnownPartner(slug) {
  return typeof slug === "string" && PARTNER_SLUGS.has(slug);
}

/** Verified Google email → actor slug, or null (refusal). Case/space tolerant. */
export function slugForEmail(email) {
  if (typeof email !== "string") return null;
  return ALLOW_LIST[email.trim().toLowerCase()] || null;
}

/** The grant props stored against a token. Read back as ctx.props on every API request. */
export function propsForSlug(slug, extra = {}) {
  if (!isKnownActor(slug)) throw new Error(`refusing to build props for unknown actor: ${slug}`);
  return { slug, display: DISPLAY[slug], human: true, ...extra };
}

/**
 * ctx.props → the actor object the verbs expect. Fails closed.
 *
 * MODULE-PRIVATE SINCE AMENDMENT 8 (PR 1013, fourth correction round), and the
 * reason is the whole amendment in one function: props are an ORDINARY OBJECT.
 * Anything able to type `{ slug: "joe", via: "oauth-google" }` could call the
 * exported form, so an exported form was a brander that took caller bytes. The
 * server reaches this through `authenticatedIdentity.connectionForGrant`, which
 * brands only when the caller also presents bytes this module read from the
 * server's own environment at initialisation — see `authenticateConnection`.
 */
function actorForGrantProps(props, currentNativeAgentBindings = null, serverWitness = null) {
  if (!props || !isKnownActor(props.slug)) return null;
  // via/client_id ride through to the write path (0037). They were already on the
  // grant props and this function was dropping them, so no row ever recorded which
  // surface made the write. Both are server-derived — a verb never accepts them —
  // which is the whole point: an attestation the caller controls proves nothing.
  // human_slug (loop #227): set only when props.slug was overridden to an outside-
  // model agent (codex/grok) — the verified human behind the grant, server-derived
  // at /callback the same way via/client_id are, never caller-supplied. null for an
  // ordinary joe/dell session (the actor already IS the human there).
  const human = props.human !== false;
  // A sponsor is a second, server-derived identity.  Direct human sessions
  // derive it from the verified actor below; an outside-model OAuth session
  // may carry it only when the callback wrote it into encrypted grant props.
  // Do not accept a conflicting or unknown value merely because it appeared in
  // props: that would make an old/corrupt grant a cross-brain selector.
  const candidateSponsor = props.sponsoring_human_slug || props.human_slug || null;
  const sponsoring_human_slug = !human && isKnownPartner(candidateSponsor)
    ? candidateSponsor : null;
  // Recompute native-agent authority from the CURRENT server-held client-id
  // map on every request.  The encrypted grant's historical boolean is not a
  // revocation mechanism: removing a compromised client id from configuration
  // must immediately make every already-issued grant non-authorizing.  This
  // also admits a pre-flag grant once its stored client_id is explicitly bound,
  // avoiding a forced reauthorization rollout window.
  const native_agent_verified = !human &&
    verifiedAgentSlugForClient(props.client_id, props.slug, currentNativeAgentBindings) === props.slug;
  // Codex continuity existed on this OAuth door before local continuity
  // credentials were split. Preserve that authorized path by deriving the
  // surface from the current server-held client-id binding. Claude OAuth is
  // deliberately not granted the Claude continuity surface: its native hook
  // uses the separately provisioned local credential.
  const continuity_surface = native_agent_verified && props.slug === "codex"
    ? "codex" : null;
  return authenticateConnection({ slug: props.slug, display: DISPLAY[props.slug], human,
           via: props.via || null, client_id: props.client_id || null,
           sponsoring_human_slug,
           ...(native_agent_verified ? { native_agent_verified: true } : {}),
           ...(continuity_surface ? { continuity_surface } : {}),
           // Compatibility alias for existing internal readers. New code must
           // use sponsoring_human_slug so runtime and sponsor never blur.
           human_slug: sponsoring_human_slug,
           sponsor_required: props.sponsor_required === true }, serverWitness);
}

/**
 * Resolve the personal-brain scope without trusting tool arguments, model
 * names, cwd, browser state, or a caller-provided partner value. The only
 * inputs are the authenticated actor constructed above and server-written
 * grant metadata.
 */
export function personalScopeForActor(actor) {
  // The probe/reviewer doors authenticate three narrow machine identities in
  // index.js, not DISPLAY actors. Accept only a registered slug with its exact
  // marker and token provenance; adding another reviewer is an explicit
  // security-relevant registration update, never an arbitrary token claim.
  const registration = actor && SERVER_MACHINE_IDENTITIES[actor.slug];
  const serverMachine = Boolean(registration && actor[registration.marker] === true &&
    (Array.isArray(registration.via) ? registration.via.includes(actor.via) :
      actor.via === registration.via));
  if (!actor || (!isKnownActor(actor.slug) && !serverMachine))
    return { status: "error", error: "invalid_runtime_principal" };

  // Joe/Dell's direct Google-authenticated session is both runtime and sponsor.
  if (actor.human === true && isKnownPartner(actor.slug)) {
    return { status: "personal", sponsor: actor.slug, source: "verified_human_actor" };
  }

  // An outside-model OAuth grant is expected to preserve its verified sponsor.
  if ((actor.sponsor_required === true ||
       (actor.human === false && actor.via === "oauth-google")) &&
      !isKnownPartner(actor.sponsoring_human_slug)) {
    return { status: "error", error: "missing_or_ambiguous_sponsor",
             source: "oauth_agent_grant" };
  }
  if (isKnownPartner(actor.sponsoring_human_slug)) {
    return { status: "personal", sponsor: actor.sponsoring_human_slug,
             source: "verified_grant_sponsor" };
  }

  // Agent, probe, and review tokens have no human sponsor unless the server
  // explicitly provisions one. Shared-only is truthful, and grants no human
  // authority or capability.
  return { status: "none", sponsor: null,
           source: serverMachine ? "server_machine_token" : "unsponsored_runtime" };
}

/**
 * Server-derived owners allowed for set-next-action. Every actor may keep a
 * ball in its own queue. Only the separately provisioned Hermes CoS door may
 * hand a ball to its verified sponsor; plain Hermes and human sessions remain
 * own-ball-only even when sponsor metadata is present.
 */
export function permittedActionOwnerSlugs(actor) {
  const owners = [];
  if (actor && typeof actor.slug === "string") owners.push(actor.slug);
  if (actor?.hermesCos !== true || actor?.via !== "hermes-cos-token") return owners;
  const scope = personalScopeForActor(actor);
  if (scope.status === "personal" && isKnownPartner(scope.sponsor) &&
      !owners.includes(scope.sponsor)) owners.push(scope.sponsor);
  return owners;
}

/**
 * The server-derived authority class. It is intentionally distinct from the
 * legacy request-side operational limiter (?profile=), which can only reduce
 * the verb surface and is not an authorization boundary.
 */
export function authorizationClassForActor(actor) {
  if (actor?.human === true && isKnownPartner(actor.slug)) return "verified_partner";
  if (actor?.probe === true) return "probe_agent";
  if (actor?.review === true) return "review_agent";
  if (actor?.sponsoring_human_slug && isKnownActor(actor.slug)) return "sponsored_agent";
  return "unsponsored_agent";
}

// The legacy interim auth (slugForLegacyToken, PARTNER_TOKENS) was retired
// 2026-08-03, on the schedule set when it was written: both partners' OAuth
// connectors are live and the bearer path had no traffic left. /mcp now
// authenticates one way only, through a provider-issued token.
//
// ...with ONE later exception, added for outside-model CLIs (loop #227/#239,
// 2026-08-09): agentActorForToken below. Read the note in index.js's
// agentActorFor for why it is not a PARTNER_TOKENS revival — the short version
// is that PARTNER_TOKENS authenticated as a HUMAN (and so carried the humanOnly
// verbs on a credential in a config file), while this resolves to the tool's own
// actor with human:false, which mcp.js's humanOnly gate refuses by construction.

// LOCAL_SPONSOR (Phase 1, 2026-08-13, decision 97e76a2f — closing the
// direct-database bypass in run.sh call / local-verb.mjs). A bearer-token
// agent slug that names a locked-profile, NON-HUMAN identity but should still
// resolve to a human's personal scope, unlike codex/grok above (deliberately
// unsponsored/shared-only per the loop #227 design). 'joe-local' is the
// machine credential local-verb.mjs now authenticates as when it talks to the
// deployed Worker instead of opening its own database connection — see that
// file's header for the full design. Adding an entry here is a design
// decision (a credential sitting in a 600 file gaining a personal brain), the
// same weight as adding to DISPLAY, never a routine config edit.
// 'dell-local' added 2026-08-18. Dell's Mac needs the same machine door Joe's
// has, and it needs TWO entries rather than one: without a DISPLAY row above,
// isKnownActor refuses the slug and the bearer is dead on arrival; without a
// row here, it authenticates and then resolves shared-only, so his unattended
// runs would read and write against no personal brain while looking healthy.
// The second failure is the one worth naming, because it is silent.
// A SEPARATE SLUG, never a second key sharing 'joe-local', for the reason this
// map exists at all: the sponsor is what decides whose personal scope a run
// carries, so one shared machine credential would make Dell's automation write
// as Joe. Same design weight as a DISPLAY entry, per the note above.
const LOCAL_SPONSOR = Object.freeze({ "joe-local": "joe", "dell-local": "dell" });

/**
 * AGENT_TOKENS (or a same-shape sibling map, e.g. LOCAL_TOKENS) bearer ->
 * outside-model agent actor, or null.
 *
 * The pure half of index.js's agentActorFor / localActorFor, split out so it
 * is testable: the Worker entrypoint imports from `cloudflare:` and cannot be
 * loaded by node --test, so any logic left in there is logic nothing can
 * prove before a deploy. Takes the raw Authorization header and the raw
 * token-map string rather than a Request and an env, for the same reason.
 *
 * `viaLabel` names which door matched, so a tool_call/tool_read_call row can
 * tell an outside-model CLI's grant apart from a local machine credential's —
 * both resolve through this one function, EXTENDED rather than duplicated
 * (the credential's per-door shape is a config difference, not a new profile-
 * matching code path).
 *
 * Fails closed on every path: no header, unparseable JSON, empty map, a token
 * that matches nothing, or a slug that is not a known actor.
 */
export function agentActorForToken(authorizationHeader, agentTokensRaw, viaLabel = "agent-token") {
  const token = String(authorizationHeader || "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  let tokens;
  try {
    tokens = JSON.parse(agentTokensRaw || "{}");
  } catch {
    return null;
  }
  if (!tokens || typeof tokens !== "object") return null;
  const slug = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!slug) return null;
  // DISPLAY is the same hard stop the OAuth path answers to: a typo'd or
  // renamed slug must never mint an identity this Worker does not recognise.
  if (!isKnownActor(slug)) return null;
  // Bearer-token agents have no verified human sponsor BY DEFAULT. They remain
  // shared-only rather than acquiring a brain through a model name or a
  // mutable config map — UNLESS the slug is explicitly listed in LOCAL_SPONSOR
  // above, which is how 'joe-local' resolves to Joe's personal scope while
  // codex/grok (absent from that map) stay unsponsored exactly as before.
  const sponsoring_human_slug = LOCAL_SPONSOR[slug] || null;
  const native_agent_verified = viaLabel === "local-token" && sponsoring_human_slug !== null;
  return authenticateConnection({ slug, display: `Agent (${slug})`, human: false, agent: true,
           via: viaLabel, client_id: null,
           sponsoring_human_slug, human_slug: sponsoring_human_slug, sponsor_required: false,
           ...(native_agent_verified ? { native_agent_verified: true } : {}) }, agentTokensRaw);
}

/**
 * Match a bearer against the two isolated continuity secret maps.
 *
 * Each map is sponsor -> token. The authenticated sponsor and selected map
 * jointly determine the runtime actor and continuity surface; no tool input,
 * client profile name, model id, or request parameter participates. A token
 * present in both maps is ambiguous and refuses instead of inheriting check
 * order as authority.
 */
export function continuityActorForTokenMaps(
  authorizationHeader, codexTokensRaw, claudeTokensRaw,
) {
  const token = String(authorizationHeader || "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  const candidates = [];
  for (const [surface, raw] of [
    ["codex", codexTokensRaw], ["claude", claudeTokensRaw],
  ]) {
    let tokens;
    try { tokens = JSON.parse(raw || "{}"); }
    catch { continue; }
    if (!tokens || typeof tokens !== "object" || Array.isArray(tokens)) continue;
    const sponsors = Object.keys(tokens).filter(
      sponsor => isKnownPartner(sponsor) && tokens[sponsor] && tokens[sponsor] === token,
    );
    if (sponsors.length === 1) candidates.push({ surface, sponsor: sponsors[0], raw });
    else if (sponsors.length > 1) return null;
  }
  if (candidates.length !== 1) return null;
  const { surface, sponsor, raw } = candidates[0];
  return authenticateConnection({
    slug: surface, display: DISPLAY[surface], human: false, agent: true,
    via: `${surface}-continuity-token`, client_id: null,
    sponsoring_human_slug: sponsor, human_slug: sponsor, sponsor_required: false,
    native_agent_verified: true, continuity_surface: surface,
  }, raw);
}

/**
 * HERMES_TOKENS bearer -> the R0 evaluation runtime's actor, or null.
 *
 * The pure half of index.js's hermesActorFor, split out for the same reason as
 * agentActorForToken above: the Worker entrypoint imports from `cloudflare:`
 * and cannot be loaded by node --test. A locked profile whose lock can only be
 * exercised by deploying is a lock nobody can prove.
 *
 * DELIBERATELY NOT A CALL INTO agentActorForToken. That function's contract is
 * an outside-model CLI: it sets agent:true, honours LOCAL_SPONSOR, and its
 * actors take profileFor(request) in dispatch, so they can pass ?profile= and
 * write. Every one of those is wrong here. This actor sets hermes:true, which
 * dispatch turns into the locked, write-empty `hermes` profile.
 *
 * Its personal brain comes from HERMES_SPONSOR below, its own named map rather
 * than LOCAL_SPONSOR, so sponsoring the Hermes pilot never silently sponsors a
 * CLI seat and the reverse holds too. A slug absent from that map stays
 * shared-only. Cross-brain reads refuse regardless of sponsorship, which the R0
 * suite asserts directly rather than inheriting.
 *
 * The slug is not checked against DISPLAY here, and it does not need to be:
 * personalScopeForActor refuses any slug that is neither a DISPLAY actor nor a
 * registered entry in SERVER_MACHINE_IDENTITIES, so registration is enforced
 * one layer down and a typo'd slug fails every call with
 * invalid_runtime_principal rather than acquiring a quiet identity. That is
 * the same shape the probe and reviewer doors rely on. Found by the R0 test
 * suite before deploy, which is the point of the split.
 *
 * Fails closed on every path: no header, unparseable JSON, a non-object map, an
 * empty map, or a token matching nothing.
 */
/**
 * HERMES_SPONSOR — which human's personal brain a Hermes runtime reads.
 *
 * Joe's ruling, 2026-08-16: "i actaully want it to know my personal rules. that
 * way it can take on tasks for me personally." The R0 evaluation shipped hours
 * earlier as shared-only, and a runtime that cannot see how he wants work done
 * is a runtime that cannot do his work — it would re-derive his preferences from
 * nothing on every task, which is the failure the taught-rule store exists to
 * end.
 *
 * WHAT THIS GRANTS, precisely: reads scoped to joe-personal. His 33 personal
 * rules, and his side of any read verb that splits by brain. It grants no write
 * (the hermes profile's write set stays empty), no humanOnly verb (human:false
 * is unchanged), and no access to Dell's brain — cross-brain refusal is enforced
 * by the same rule that refuses it for every other actor, and the R0 test suite
 * asserts it explicitly.
 *
 * WHY THIS IS SAFE UNDER THE BOUNDARY THAT ACTUALLY BINDS. Rule d7f74c93: the
 * question is never who may READ, it is who HOLDS A CREDENTIAL and who may ACT
 * unsupervised. Personal rules are instructions about how to work with Joe. A
 * runtime reading them gains judgment, never authority.
 *
 * A NAMED ENTRY, never a wildcard, for the reason LOCAL_SPONSOR states above: a
 * credential in a 600 file gaining a personal brain is a design decision. Dell's
 * side would be a separate, deliberate entry made by Dell's own ruling, and
 * nothing here reaches it.
 */
const HERMES_SPONSOR = Object.freeze({ "hermes-pilot": "joe" });

export function hermesActorForToken(authorizationHeader, hermesTokensRaw) {
  const token = String(authorizationHeader || "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  let tokens;
  try {
    tokens = JSON.parse(hermesTokensRaw || "{}");
  } catch {
    return null;
  }
  if (!tokens || typeof tokens !== "object") return null;
  const slug = Object.keys(tokens).find((s) => tokens[s] && tokens[s] === token);
  if (!slug) return null;
  const sponsoring_human_slug = HERMES_SPONSOR[slug] || null;
  return authenticateConnection({ slug, display: `Hermes (${slug})`, human: false, hermes: true,
           via: "hermes-token", client_id: null,
           sponsoring_human_slug, human_slug: sponsoring_human_slug, sponsor_required: false },
         hermesTokensRaw);
}

/** Try additive Hermes token maps without replacing or reading back the
 * primary Worker secret. */
export function hermesActorForTokenMaps(authorizationHeader, ...tokenMaps) {
  for (const raw of tokenMaps) {
    const actor = hermesActorForToken(authorizationHeader, raw);
    if (actor) return actor;
  }
  return null;
}

/**
 * HERMES_COS_TOKENS bearer -> the separately provisioned chief-of-staff door.
 * The Worker checks this map before the ordinary Hermes maps. The internal
 * marker is server-created and is the only selector for the CoS profile.
 */
export function hermesCosActorForToken(authorizationHeader, hermesCosTokensRaw) {
  const token = String(authorizationHeader || "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  let tokens;
  try {
    tokens = JSON.parse(hermesCosTokensRaw || "{}");
  } catch {
    return null;
  }
  if (!tokens || typeof tokens !== "object" || Array.isArray(tokens)) return null;
  // This is deliberately not a generic machine-token map.  The CoS grant is
  // bound to one registered runtime and one sponsor; accepting an arbitrary
  // key here would create the actor before the profile/owner gates can help.
  const slugs = Object.keys(tokens);
  if (slugs.length !== 1 || slugs[0] !== "hermes-pilot") return null;
  const slug = "hermes-pilot";
  if (!tokens[slug] || tokens[slug] !== token) return null;
  const sponsoring_human_slug = HERMES_SPONSOR[slug];
  if (sponsoring_human_slug !== "joe") return null;
  return authenticateConnection({ slug, display: `Hermes CoS (${slug})`, human: false,
           hermes: true, hermesCos: true, via: "hermes-cos-token", client_id: null,
           sponsoring_human_slug, human_slug: sponsoring_human_slug, sponsor_required: false },
         hermesCosTokensRaw);
}

// ---------------------------------------------------------------------------
// THE AUTHENTICATED CALL (2026-09-12, PR 1013 — THIRD correction round, under
// standing-rule AMENDMENT 8, logged 2026-09-13).
//
// WHY IT EXISTS. r7's identity rule says every receipt identity is DERIVED from
// authenticated execution context, and caller-supplied identity denies. A module
// that must not take an actor parameter — because a parameter is exactly the
// door a caller smuggles an identity through — then has no way to ask who is
// calling it. The Gate Zero producer is that module: its arity is zero by
// design, and the first review round found it reconstructing a reviewer identity
// out of a static declaration instead, so ANY process that imported it received
// a receipt signed `codex-reviewer` without ever authenticating.
//
// WHAT THE FIRST CORRECTION GOT WRONG. It shipped `runInAuthenticatedCall(actor,
// fn)` as a public export that took an ORDINARY OBJECT, so anything able to type
// `{ slug: "codex-reviewer", review: true, ... }` could be signed for.
//
// WHAT THE SECOND CORRECTION GOT WRONG, and what amendment 8 closes. The brand
// it added was a Symbol-keyed ENUMERABLE property, chosen precisely so it would
// survive the places the server re-spreads an actor. A property that survives
// the server's spread survives the FORGER'S spread too: the reviewer took a
// branded partner actor, wrote `{ ...actor }`, rewrote the copy's slug as
// `codex-reviewer`, put it through the real `executeRegisteredTool`, and was
// signed for as a review agent. Two public doors made that easier still —
// `reviewActorForToken` took the SERVER'S TOKEN MAP as a caller argument (so a
// caller could verify a bearer of their choosing against a map of their
// choosing), and `dispatchAuthenticatedCall` was an exported context entry.
//
// AMENDMENT 8, WHICH IS WHAT THIS SECTION NOW IS:
//
//   * An actor is authenticated IF AND ONLY IF it is a member of
//     AUTHENTICATED_ACTORS, a module-private WeakSet populated SOLELY by
//     `authenticateConnection` below, at the moment a credential is verified
//     against a map this module holds. THE BRAND IS OBJECT IDENTITY, NOT A
//     PROPERTY: there is nothing written on the object, so a copy, a spread or a
//     clone is a different object and is not authenticated. The forgery the
//     second round admitted has nowhere to stand.
//   * Because object identity is the brand, the server may no longer COPY an
//     actor in order to decorate it. Every server decoration site — mcp.js's
//     scoped actor, its actor-with-id and its trusted-principal handoff,
//     index.js's continuity-compat surface, tour-runtime's runtime actor — now
//     writes onto the authenticated object IN PLACE. Those objects are built per
//     request by the doors above and are reachable from nothing else.
//   * The fields an identity is derived FROM are pinned at the moment of
//     authentication, in AUTHENTICATED_CREDENTIAL, and are never read back off
//     the actor afterwards. In-place decoration therefore cannot rewrite who an
//     actor is or what class it holds either — not the server's, and not a
//     forger's. `{ slug }` and `{ review: true }` written onto a branded actor
//     change nothing the receipt says.
//   * THIS FILE EXPORTS NO FUNCTION THAT ENTERS AN IDENTITY CONTEXT AND NO
//     FUNCTION THAT MINTS A BRANDED ACTOR FROM CALLER-SUPPLIED BYTES. The
//     context entry is `enterAuthenticatedCall`, which is not exported: the
//     dispatch path reaches it only through the module-internal call the
//     per-actor dispatcher closes over, and that dispatcher is closed over a
//     context DERIVED here — pointing it somewhere else is not a thing a caller
//     can express.
//
// WHAT THE FOURTH CORRECTION CLOSES, and it is the last door. The third round
// took the review-token map off the caller's side of the door but left a
// `sealServerReviewTokens(raw)` on the export surface to get the server's map
// IN, on the theory that one-shot installation made first-caller-wins the
// server's race to win. It is not: a caller that reaches this module before the
// first /mcp request installs its own map, and the reviewer's probe did exactly
// that and was signed for as `review_agent`. One-shot state controls ORDER, not
// PROVENANCE. That function is gone, with no replacement under any name — the
// probe cannot even name something to call.
//
// THE CREDENTIALS ARE READ ONCE, AT MODULE INITIALISATION, FROM THE SERVER'S OWN
// ENVIRONMENT. `SERVER_ENVIRONMENT` below is `process.env` — which in this
// deployment is the wrangler secret store itself: wrangler populates
// `process.env` from the Worker's vars and secrets whenever `nodejs_compat` is
// on and the compatibility date is 2025-04-01 or later (see
// mcp-server/wrangler.toml: nodejs_compat, 2026-07-01), and it is readable at
// module scope, which `env` is not. There is no parameter, no setter and no
// ordering to win. A process that does not hold the secret authenticates nobody
// through this file, whenever it arrives.
//
// AND THAT SAME TEST IS WHAT BRANDS EVERY OTHER DOOR. The legacy doors still
// take their token map as an argument, because the server has always called them
// that way and their maps are its own — but branding no longer follows from a
// match. It follows from the BYTES THE MATCH WAS MADE AGAINST being bytes this
// module read from the server's environment at initialisation. A caller pairing
// a bearer of its choosing with a map of its choosing still gets the actor it
// always got, and that actor is NOT branded, so it derives no identity and
// obtains no receipt. This is the amendment applied without a legacy exception:
// the doors keep working, and none of them mints a receipt identity out of bytes
// a caller supplied.
//
// WHAT TRAVELS IS THE DERIVATION, NOT THE ACTOR. The store holds a frozen
// three-field `authenticated-receipt-identity.v1` — actor_id, session_ref,
// authority_class. The actor object itself is deliberately not stored: a
// consumer that could reach it could read the grant props, and nothing
// downstream needs more than these three.
//
// THE SESSION REF IS THE SERVER'S OWN CORRELATION ID, not a digest of whatever
// the call happened to be looking at. correlation.js stamps one per request and
// mcp.js decorates it onto the actor; it is the only per-call identifier in this
// system that no caller writes. No correlation id means no session, and no
// session means no identity — a run that cannot be told apart from another run
// is not one a receipt may name.
//
// THE DOOR IS NOT IN THE REF. A correlation id already identifies one call
// uniquely, so naming the provenance beside it would add nothing — and it would
// put `review-token` into a value the v5 privileged-word sweep closes over as a
// SUBSTRING. The via is still validated, because a call with no recognizable
// provenance is not one this derivation will speak for.
//
// FAIL-CLOSED AND NON-FATAL. A call whose identity cannot be derived runs with
// the context CLEARED rather than with a previous call's: the surfaces that read
// it refuse, and every verb that does not read it is unaffected.

const AUTHENTICATED_CALL = new AsyncLocalStorage();

/**
 * THE BRAND. Membership of a module-private WeakSet — object identity and
 * nothing else. There is no name to write down, no property to copy, and no
 * exported brander, because an exported brander is the forger this brand exists
 * to stop.
 */
const AUTHENTICATED_ACTORS = new WeakSet();

/**
 * What the credential SAID, frozen at the instant it was verified. Every field a
 * receipt identity is built from is read from here rather than from the live
 * actor, so decorating the actor later — however that happens — cannot move it.
 */
const AUTHENTICATED_CREDENTIAL = new WeakMap();

/**
 * THE SERVER'S OWN SECRET SOURCE, read ONCE, here, at module initialisation.
 *
 * `process.env` is the deployed Worker's secret store: with `nodejs_compat` and
 * a compatibility date of 2025-04-01 or later — this Worker is on 2026-07-01 —
 * wrangler populates it from the Worker's vars and secrets, and unlike a
 * request's `env` it is readable at module scope. In node (the suites, the
 * local verb client, the ops processes) it is the process environment, which is
 * the same statement: the environment the SERVER PROCESS was started with.
 *
 * Guarded, because a runtime without a `process` global must degrade to "this
 * module holds no credentials" rather than throw on import.
 */
function serverEnvironmentSecret(name) {
  try {
    const environment = typeof process === "undefined" ? null : process.env;
    const value = environment ? environment[name] : undefined;
    return typeof value === "string" && value.length > 0 ? value : null;
  } catch {
    return null;
  }
}

/**
 * EVERY CREDENTIAL BYTE STRING THIS SERVER HOLDS, frozen at initialisation.
 *
 * This is the whole provenance test. `authenticateConnection` brands an actor
 * only when the bytes its door verified the credential against are IN HERE —
 * so a caller that hands a door a token map of its own gets the actor that door
 * has always returned and no brand, whatever the map says. Membership is by
 * exact string, so a caller would have to already hold the secret to pass, at
 * which point it is not forging anything.
 *
 * The names are exactly the Worker secrets the request handler feeds the doors
 * in index.js, plus GOOGLE_CLIENT_SECRET, which is the OAuth grant path's
 * witness: that door has no token map of its own — a grant's props are an
 * ordinary object — so the server proves it is the server by presenting the
 * secret only the OAuth provider half of this Worker holds.
 */
const SERVER_CREDENTIAL_NAMES = Object.freeze([
  "REVIEW_TOKENS", "AGENT_TOKENS", "LOCAL_TOKENS",
  "HERMES_TOKENS", "HERMES_TOKENS_EXTRA", "HERMES_COS_TOKENS",
  "CODEX_CONTINUITY_TOKENS", "CLAUDE_CONTINUITY_TOKENS",
  "GOOGLE_CLIENT_SECRET",
]);

const SERVER_CREDENTIAL_BYTES = new Set(
  SERVER_CREDENTIAL_NAMES.map(serverEnvironmentSecret).filter(value => value !== null));

/** Are these the bytes this module read from the server's environment? */
function serverHeldCredential(bytes) {
  return typeof bytes === "string" && bytes.length > 0 && SERVER_CREDENTIAL_BYTES.has(bytes);
}

/**
 * The review council's token map and the exact bytes it was parsed from. Read
 * here and nowhere else; there is no function that installs one.
 */
const SERVER_REVIEW_TOKENS_RAW = serverEnvironmentSecret("REVIEW_TOKENS");
const SERVER_REVIEW_TOKENS = parsedTokenMap(SERVER_REVIEW_TOKENS_RAW);

/** The shapes a derived session ref is built from. Server-written, both of them. */
const CALL_VIA = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const CALL_CORRELATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * THE CONNECTION AUTHENTICATOR, and the ONLY writer of the brand.
 *
 * Called at every return in this file that turns a verified CREDENTIAL into an
 * actor — the OAuth grant's props, the agent bearer, the two continuity bearers,
 * the two Hermes bearers, and the review bearer below — and nowhere else. It is
 * not exported, and nothing exported is a synonym for it.
 *
 * `credentialBytes` is THE BYTES THE DOOR VERIFIED AGAINST: the token map it
 * matched a bearer in, or, for the grant door, the witness the server presented.
 * It is not a flag a caller can pass — every door supplies its own — and it is
 * checked against what this module read from the server's environment at
 * initialisation. Bytes from anywhere else brand nothing.
 *
 * Beyond provenance it brands only what it can also PIN: a slug, a recognizable
 * server-written provenance, and a personal scope this file will stand behind.
 * An actor it declines to brand is still returned unchanged and still works for
 * every verb; what it cannot do is obtain a receipt identity.
 */
function authenticateConnection(actor, credentialBytes) {
  if (actor === null || typeof actor !== "object") return actor;
  if (typeof actor.slug !== "string") return actor;
  if (!serverHeldCredential(credentialBytes)) return actor;
  const via = typeof actor.via === "string" ? actor.via.toLowerCase() : "";
  if (!CALL_VIA.test(via)) return actor;
  if (personalScopeForActor(actor).status === "error") return actor;
  AUTHENTICATED_ACTORS.add(actor);
  AUTHENTICATED_CREDENTIAL.set(actor, Object.freeze({
    slug: actor.slug, via, authority_class: authorizationClassForActor(actor),
  }));
  return actor;
}

/**
 * The `authenticated-receipt-identity.v1` for one server-established actor, or
 * null when this actor cannot be spoken for.
 *
 * THE FIRST QUESTION IS THE BRAND, asked as membership. An object this module's
 * connection authenticator did not brand answers null here whatever is written
 * on it — and so does a copy of one that it did.
 *
 * Then every field comes from the PINNED credential: the slug the door accepted
 * and the class derived for it at that instant. The only thing read off the live
 * actor is the server's correlation id, which names a call rather than an
 * authority, and which correlation.js — not any caller — writes.
 */
function deriveCallIdentity(actor) {
  if (actor === null || typeof actor !== "object") return null;
  if (!AUTHENTICATED_ACTORS.has(actor)) return null;
  const credential = AUTHENTICATED_CREDENTIAL.get(actor);
  if (credential === undefined) return null;
  const correlationId = typeof actor.correlation_id === "string"
    ? actor.correlation_id.toLowerCase() : "";
  if (!CALL_CORRELATION_ID.test(correlationId)) return null;
  return Object.freeze({
    actor_id: credential.slug,
    session_ref: `session:${correlationId}`,
    authority_class: credential.authority_class,
  });
}

/**
 * THE CONTEXT ENTRY, and it is NOT EXPORTED — amendment 8 in one line. The only
 * way to reach it is the dispatcher `dispatchFor` hands back, which is closed
 * over a context this file derived and cannot be aimed at another one.
 */
function enterAuthenticatedCall(context, fn) {
  return AUTHENTICATED_CALL.run(context, fn);
}

/**
 * THE WHOLE CONTEXT THE DISPATCH PATH ESTABLISHES, derived once, at the moment
 * the dispatcher is built, and frozen.
 *
 * TWO SEATS, NOT ONE, and the third review round is why. A verb call has a
 * CALLING seat — who is running this verb — and it has a CANDIDATE-BUILD seat,
 * the seat inside this same authenticated call in which the candidate under
 * evaluation is materialised and digested. The producer needs both, and the
 * correction it is answering is that the build seat used to be ASSEMBLED BY THE
 * READER: `buildContext()` took the caller's session ref and appended a suffix
 * to it at read time, which is string work done on the consumer's side of the
 * boundary wearing a session's clothes. It is derived HERE now, by the dispatch
 * path, at the same instant and from the same pinned credential as the calling
 * seat, and the readers below return what was established rather than build
 * anything.
 *
 * BOTH REFS STAND ON THE CORRELATION ID, which is the only per-call identifier
 * in this system that no caller writes. No authenticated call means no context
 * at all: `deriveCallIdentity` answers null, this answers null, and every reader
 * below answers null.
 */
function deriveDispatchContext(actor) {
  const call = deriveCallIdentity(actor);
  if (call === null) return null;
  return Object.freeze({
    call,
    build: Object.freeze({ session_ref: `${call.session_ref}:candidate-build` }),
  });
}

/** A slug -> secret map the SERVER holds, or null when there is nothing usable. */
function parsedTokenMap(raw) {
  let tokens;
  try {
    tokens = JSON.parse(raw || "{}");
  } catch {
    return null;
  }
  if (!tokens || typeof tokens !== "object" || Array.isArray(tokens)) return null;
  const entries = Object.entries(tokens).filter(([slug, secret]) =>
    typeof slug === "string" && slug.length > 0
    && typeof secret === "string" && secret.length > 0);
  return entries.length === 0 ? null : Object.freeze(Object.fromEntries(entries));
}

/**
 * THE SUBJECT MAKER'S REGISTRY — a commit's committer address to the registered
 * partner it names.
 *
 * A FROZEN TABLE AND AN EXACT MATCH, never a pattern and never a domain rule.
 * It is deliberately SEPARATE from ALLOW_LIST above: ALLOW_LIST says which
 * Google identity may be ISSUED A TOKEN, and a committer address authenticates
 * nothing at all — it is an attribution read out of a commit object. Mapping the
 * two through one table would make a git address an authentication surface.
 *
 * THE NOREPLY ADDRESSES ARE WHY THIS EXISTS. Every commit GitHub's own merge
 * path writes carries the author's `users.noreply.github.com` address as the
 * committer, so a table holding only the Gmail addresses cannot name the maker
 * of any merged head — which is exactly what the third review round found at
 * head 0849b987.
 *
 * Joe's numeric-id form is the one observed on this repository's heads. Dell's
 * is the legacy login form of the GitHub account observed authoring Dell's
 * commits here (`dellmccraneycarrus-gif`); if Dell's account is set to the
 * numeric-id form, that address is added HERE when it is first observed, never
 * guessed at — an invented id would map a stranger.
 *
 * `noreply@github.com` — GitHub's shared web-flow committer — is deliberately
 * absent: every web commit by every account carries it, so it names nobody.
 */
const COMMITTER_IDENTITIES = Object.freeze({
  "joe.bookout.carr.us@gmail.com": "joe",
  "64207374+jbookout@users.noreply.github.com": "joe",
  "dell.mccraney.carr.us@gmail.com": "dell",
  "dellmccraneycarrus-gif@users.noreply.github.com": "dell",
});

/**
 * REVIEW bearer -> the review council's machine actor, or null.
 *
 * A BEARER AND NOTHING ELSE, checked against the map this module read from the
 * server's own environment at initialisation. There is no second parameter, so
 * "a bearer I chose plus a token map I chose" is not a call that can be written
 * — and, since the fourth correction round, there is no installer either: the
 * one-shot seal that used to get the map in is deleted, because one-shot state
 * settles which caller is FIRST and says nothing about which caller is the
 * SERVER. A process whose environment holds no REVIEW_TOKENS authenticates
 * nobody here, at any point in its life.
 *
 * The fields are unchanged byte for byte from when this door lived in index.js,
 * including `review: true` — still the only thing that can put mcp.js's dispatch
 * into the locked `reviewer` profile. Registration is enforced one layer down:
 * `personalScopeForActor`, inside the connection authenticator, refuses any slug
 * that is neither a DISPLAY actor nor a registered SERVER_MACHINE_IDENTITIES
 * entry with this exact marker and provenance, so a sealed map naming an
 * unregistered slug brands nothing.
 */
const reviewActorForToken = closedCallable((authorizationHeader) => {
  if (SERVER_REVIEW_TOKENS === null) return null;
  const token = String(authorizationHeader || "").replace(/^Bearer\s+/i, "");
  if (!token) return null;
  const slug = Object.keys(SERVER_REVIEW_TOKENS).find(s => SERVER_REVIEW_TOKENS[s] === token);
  if (!slug) return null;
  return authenticateConnection({ slug, display: `Reviewer (${slug})`, human: false,
                                  review: true, via: "review-token", client_id: null },
                                SERVER_REVIEW_TOKENS_RAW);
});

/**
 * THE OAUTH GRANT DOOR: a decrypted grant's props -> the actor the verbs expect.
 *
 * `actorForGrantProps` is module-private because props are an ordinary object,
 * so an exported form is a brander that takes caller bytes — the exact shape
 * amendment 8 forbids. This is the server's way in, and what makes it the
 * SERVER'S is the third argument: the witness, which brands only when it is a
 * credential byte string this module read from the server's environment at
 * initialisation. index.js and mcp.js pass `env.GOOGLE_CLIENT_SECRET`, which is
 * the secret the OAuth provider half of this same Worker already holds and
 * which nothing outside the deployment can present.
 *
 * CALLED WITHOUT A WITNESS IT STILL WORKS, and returns exactly the actor it has
 * always returned — unbranded. That is deliberate: the Deal Room's session
 * surface builds actors from grant props and needs no receipt identity, and a
 * door that failed closed on the witness would turn a receipt-identity control
 * into an authentication outage.
 */
const connectionForGrant = closedCallable((props, currentNativeAgentBindings = null,
                                           serverWitness = null) =>
  actorForGrantProps(props, currentNativeAgentBindings, serverWitness));

/**
 * The dispatch path's door: the per-actor dispatcher for ONE verb call.
 *
 * NOT A CONTEXT ENTRY AND NOT A SETTER. It derives the identity HERE, from the
 * brand and the pinned credential, and hands back a closure that can run a
 * function inside that identity and no other. An actor this file did not brand
 * derives null, and the closure then enters a CLEARED context — which is the
 * fail-closed half: a nested call never inherits an outer call's identity.
 *
 * The one caller is tools.js's `executeRegisteredTool`. Adding a second is a
 * security-relevant change, not a convenience.
 */
const dispatchFor = closedCallable((actor) => {
  const context = deriveDispatchContext(actor);
  return closedCallable(fn => enterAuthenticatedCall(context, fn));
});

/**
 * The `authenticated-receipt-identity.v1` of the call this code is running
 * inside, or null when there is no such call. Takes no argument, so there is
 * nothing to supply, and returns a frozen three-field value rather than the
 * actor, so there is nothing to read back out of it either.
 */
const receiptIdentity = closedCallable(() => {
  const context = AUTHENTICATED_CALL.getStore();
  return context === undefined || context === null ? null : context.call;
});

/**
 * THE BUILD CONTEXT the dispatch path established, or null outside one.
 *
 * The candidate under review was built in some other session, and a receipt that
 * names its maker still has to name a SESSION — the third review round's second
 * finding was that the producer was manufacturing one out of the revision it
 * happened to be reading, which is caller-side string assembly wearing a
 * session's clothes. This derives it instead, from the one per-call identifier
 * no caller writes: the authenticated call's own session ref, narrowed to the
 * candidate-build seat within it. Outside an authenticated call there is no
 * build context, so a run that cannot authenticate cannot name a maker either.
 */
const buildContext = closedCallable(() => {
  const context = AUTHENTICATED_CALL.getStore();
  return context === undefined || context === null ? null : context.build;
});

/**
 * A commit committer address -> the registered partner it names, with the
 * authority class DERIVED for that partner, or null.
 *
 * NO ACTOR IS MINTED HERE, deliberately: a table lookup over an address read out
 * of a commit must never be able to produce a branded actor, or the git object
 * store would become an authentication surface. What comes back is an
 * attribution — a slug and a derived class — and nothing that can be dispatched.
 */
const committerIdentity = closedCallable((email) => {
  if (typeof email !== "string") return null;
  const slug = COMMITTER_IDENTITIES[email.trim().toLowerCase()] ?? null;
  if (slug === null || !isKnownPartner(slug)) return null;
  return Object.freeze({
    actor_id: slug,
    authority_class: authorizationClassForActor({ slug, human: true }),
  });
});

/**
 * THE ONE EXPORT THIS SECTION ADDS, frozen, so the surface can be ENUMERATED
 * rather than listed by hand: a test walks `Object.keys` of this object, and a
 * seventh member added here arrives holding amendment 2's shape or arrives red.
 *
 * Read the members' own notes for what each is. What is NOT here is the point:
 * no context entry, and no way to turn bytes into a branded actor.
 */
export const authenticatedIdentity = Object.freeze({
  connectionForGrant,
  reviewActorForToken,
  dispatchFor,
  receiptIdentity,
  buildContext,
  committerIdentity,
});

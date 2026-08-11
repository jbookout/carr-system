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

/** Google identities permitted to be issued a token. Everything else is refused. */
export const ALLOW_LIST = Object.freeze({
  "joe.bookout.carr.us@gmail.com": "joe",
  "dell.mccraney.carr.us@gmail.com": "dell",
});

/** The only actor slugs this Worker will ever hand to a verb. */
const DISPLAY = Object.freeze({ joe: "Joe", dell: "Dell", codex: "Codex", claude: "Claude", grok: "Grok" });
const PARTNER_SLUGS = new Set(["joe", "dell"]);
const SERVER_MACHINE_IDENTITIES = Object.freeze({
  "smoke-probe": { marker: "probe", via: "probe-token" },
  "codex-reviewer": { marker: "review", via: "review-token" },
  "grok-reviewer": { marker: "review", via: "review-token" },
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

/** ctx.props → the actor object the verbs expect. Fails closed. */
export function actorFromProps(props) {
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
  return { slug: props.slug, display: DISPLAY[props.slug], human,
           via: props.via || null, client_id: props.client_id || null,
           sponsoring_human_slug,
           // Compatibility alias for existing internal readers. New code must
           // use sponsoring_human_slug so runtime and sponsor never blur.
           human_slug: sponsoring_human_slug,
           sponsor_required: props.sponsor_required === true };
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
    actor.via === registration.via);
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

/**
 * AGENT_TOKENS bearer -> outside-model agent actor, or null.
 *
 * The pure half of index.js's agentActorFor, split out so it is testable: the
 * Worker entrypoint imports from `cloudflare:` and cannot be loaded by node
 * --test, so any logic left in there is logic nothing can prove before a
 * deploy. Takes the raw Authorization header and the raw AGENT_TOKENS string
 * rather than a Request and an env, for the same reason.
 *
 * Fails closed on every path: no header, unparseable JSON, empty map, a token
 * that matches nothing, or a slug that is not a known actor.
 */
export function agentActorForToken(authorizationHeader, agentTokensRaw) {
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
  // Bearer-token agents have no verified human sponsor. They remain shared-only
  // rather than acquiring a brain through a model name or a mutable config map.
  return { slug, display: `Agent (${slug})`, human: false, agent: true,
           via: "agent-token", client_id: null,
           sponsoring_human_slug: null, human_slug: null, sponsor_required: false };
}

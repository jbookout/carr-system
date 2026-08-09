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
const DISPLAY = Object.freeze({ joe: "Joe", dell: "Dell", codex: "Codex", grok: "Grok" });

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
  return { slug: props.slug, display: DISPLAY[props.slug], human: props.human !== false,
           via: props.via || null, client_id: props.client_id || null,
           human_slug: props.human_slug || null };
}

// The legacy interim auth (slugForLegacyToken, PARTNER_TOKENS) was retired
// 2026-08-03, on the schedule set when it was written: both partners' OAuth
// connectors are live and the bearer path had no traffic left. /mcp now
// authenticates one way only, through a provider-issued token.

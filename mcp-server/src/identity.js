// CARR MCP server — identity → actor mapping. ONE place, and it is the gate.
//
// Stress-test addendum §I.10 (A10): auth is pinned to IDENTITIES, not consent.
// The allow-list below has exactly two entries and maps to exactly two actors.
// Adding an ENTRY (a partner authenticating with a different Google identity)
// is one line here. Adding an ACTOR is not a config change — it is a design
// change, and DISPLAY below is the hard stop that makes that true.
//
// Both auth paths — the OAuth grant (props) and the legacy PARTNER_TOKENS
// bearer — resolve through this file, so they cannot drift apart.

/** Google identities permitted to be issued a token. Everything else is refused. */
export const ALLOW_LIST = Object.freeze({
  "joe.bookout.carr.us@gmail.com": "joe",
  "dell.mccraney.carr.us@gmail.com": "dell",
});

/** The only actor slugs this Worker will ever hand to a verb. */
const DISPLAY = Object.freeze({ joe: "Joe", dell: "Dell" });

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
  return { slug: props.slug, display: DISPLAY[props.slug], human: props.human !== false,
           via: props.via || null, client_id: props.client_id || null };
}

// ---------- legacy interim auth (PARTNER_TOKENS), retired after both phones verify ----------
//
// Migration coexistence: a request carrying a valid PARTNER_TOKENS string
// authenticates exactly as it does today. Retirement is deleting the secret and
// the resolveExternalToken option in index.js — this function goes with them.
// Semantics are unchanged from the pre-OAuth build: exact match, >= 32 chars.

export function slugForLegacyToken(token, env) {
  if (typeof token !== "string" || token.length < 32) return null;
  let map;
  try {
    map = JSON.parse(env.PARTNER_TOKENS || "{}");
  } catch {
    return null;
  }
  for (const [slug, t] of Object.entries(map)) {
    if (typeof t === "string" && t.length >= 32 && t === token && isKnownActor(slug)) return slug;
  }
  return null;
}

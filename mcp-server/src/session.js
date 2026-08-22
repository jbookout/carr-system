// session.js — minting an authenticated application session (migration 0257/0258)
//
// WHAT THIS IS FOR. 0257 gave the database an opaque, server-minted session
// identity and a trigger that refuses evidence naming a session that is not
// live, unexpired, unrevoked, and whose actor and tenant match the row. 0258
// named the credential permitted to mint one: carr_session_issuer, which the
// tool-execution path does not hold. This module is the only place that
// credential is used, and it is deliberately not imported by tools.js.
//
// THE RULE THAT SHAPES EVERY FUNCTION HERE: a session identity must be derived
// from something the SERVER already knows about the credential presented, never
// from anything the caller can choose. No function below takes an actor, a
// tenant, an authentication instant, or an expiry from a request body.
//
// WHICH DOORS MAY MINT, AND WHY THE REST MUST NOT. A session is only meaningful
// if the credential behind it has an issuance instant, an expiry, and a
// revocation state. Two doors have all three:
//
//   * the OAuth access token — issued with its own record and its own expiry
//   * the Deal Room cookie   — a KV record with createdAt, expiresAt, and a
//                              delete-based revocation on sign-out
//
// The bearer-token doors (probe, review, hermes, agent, local), /ingest, and
// /capture/claim authenticate against a STATIC SECRET MAP. A static secret has
// no issuance instant, no expiry and no revocation state, so a session minted
// for one would be a fiction dressed as evidence. Those doors leave the session
// null and their rows are permanently non-qualifying, which is exactly what
// 0257's legacy path means.
//
// The risk there is NOT that they break. It is that someone later reads "these
// doors produce no qualified evidence" as a defect and fixes it by minting on
// their behalf. That would satisfy every test in this repo and destroy the only
// property the substrate has.
//
// ON THE OAUTH DOOR: THE GRANT IS NOT THE SESSION. The grant's refresh lifetime
// is ninety days; an access token lives one hour. Minting once when the grant is
// issued would produce ONE identity carrying ONE frozen authentication instant
// for a quarter of a year — which satisfies the wording of "a durable record
// with an issuance instant" while making the instant meaningless. So the unit is
// the ACCESS TOKEN.
//
// The OAuth library gives no hook at token issuance and hands the API handler
// only ctx.props, discarding the token id and expiry it just computed. It does
// not need to give us a hook: everything required is recomputable from the
// request. The token is on the Authorization header; splitting it yields the
// user and grant ids; hashing it yields the same token id the library itself
// stores under; and that record carries expiresAt. All server-side, none of it
// caller-chosen.

/** 0257 caps a session at 30 days. Nothing here may exceed it, and a credential
 *  claiming a longer life is clamped rather than rejected: the clamp is the
 *  security property, and refusing would turn a long-lived cookie into an
 *  outage. */
export const SESSION_CAP_MS = 30 * 24 * 60 * 60 * 1000;

/** Pure. The session expires when the CREDENTIAL does, or at the cap, whichever
 *  is sooner. Returns NULL when the credential's expiry is unknown or already
 *  past, and the caller must then decline to mint.
 *
 *  AN EARLIER VERSION RETURNED THE CAP FOR UNKNOWN INPUT, and that was the
 *  inversion backwards: for a one-hour access token, thirty days is not a
 *  conservative fallback, it is effectively unbounded. It also turned every
 *  failed KV read into a thirty-day session, and a review used it to mint one
 *  for a static agent-token secret that merely happened to contain two colons.
 *  Not knowing when a credential dies is a reason to mint nothing. */
export function sessionExpiry(credentialExpiresAtMs, nowMs, capMs = SESSION_CAP_MS) {
  const claimed = Number(credentialExpiresAtMs);
  if (!Number.isFinite(claimed) || claimed <= nowMs) return null;
  return Math.min(claimed, nowMs + capMs);
}

/** Pure apart from the hash. Recovers the server-side identity of the access
 *  token on this request, in the exact form the OAuth library stores it under.
 *  Returns null for anything that is not the library's own token format —
 *  callers must treat null as "this door cannot mint", never as "mint a fresh
 *  one", or a malformed token would become a way to spawn unlimited sessions. */
export async function accessTokenIdentity(request, subtleCrypto = globalThis.crypto?.subtle) {
  const header = request?.headers?.get?.("authorization") || "";
  if (!header.toLowerCase().startsWith("bearer ")) return null;
  const token = header.slice(7);
  const parts = token.split(":");
  if (parts.length !== 3) return null;          // not the internal format
  const [userId, grantId] = parts;
  if (!userId || !grantId) return null;
  const digest = await subtleCrypto.digest("SHA-256", new TextEncoder().encode(token));
  const tokenId = [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("");
  return { userId, grantId, tokenId, kvKey: `token:${userId}:${grantId}:${tokenId}` };
}

/** The KV key under which THIS module remembers which application session it
 *  already minted for a given access token. Separate from the library's own
 *  key so nothing here can corrupt the provider's records. */
export function sessionMapKey(tokenId) {
  return `appsession:token:${tokenId}`;
}

/**
 * Mint one application session, or return the one already minted for this
 * credential.
 *
 * mintFn is (text, params) => Promise<rows>, and production supplies a client
 * built on the ISSUER connection string — never the writer. Injected rather
 * than imported so this is testable without a database and so the writer
 * credential is not even reachable from here.
 *
 * There is NO parameter for the authentication instant. 0257's mint function
 * takes the server clock, so backdating is unexpressible rather than merely
 * rejected, and this signature keeps that true one layer up.
 */
export async function mintApplicationSession(mintFn, fields) {
  const {
    id, actorSlug, organizationTenantId, sponsoringHumanSlug, via,
    authIssuer, authorizationClass, verifiedSubject, expiresAt,
  } = fields;
  // BY SLUG, NOT BY ID (migration 0259). The door has a slug and no actor id --
  // actor.id is not resolved until callTool, long after authentication -- and
  // the issuer credential deliberately holds no table privilege with which to
  // resolve one. 0259's SECURITY DEFINER wrapper does the lookup as its owner
  // and delegates to 0257's mint, so the door needs neither the id nor a read.
  const rows = await mintFn(
    `select ops.mint_application_session_for_slug($1,$2,$3,$4,$5,$6,$7,$8,$9) as id`,
    [id, actorSlug, organizationTenantId, sponsoringHumanSlug, via,
     authIssuer, authorizationClass, verifiedSubject, expiresAt]);
  const minted = rows?.[0]?.id ?? rows?.rows?.[0]?.id ?? null;
  if (!minted) throw new Error("mint_application_session returned no id");
  return minted;
}

/**
 * The OAuth door. Returns a session id, or null when this request cannot carry
 * one — and null is a normal outcome, not an error: the caller records legacy
 * evidence, exactly as it did before this module existed.
 *
 * FAILS OPEN INTO THE LEGACY PATH, DELIBERATELY, and this is the one judgement
 * here worth arguing with. If minting fails — KV unavailable, issuer credential
 * misconfigured — the request proceeds and its evidence is non-qualifying,
 * rather than the whole door returning 500. The alternative makes the session
 * substrate a new single point of failure for every authenticated write in the
 * system, which trades a real outage for a marginal gain: a row that is
 * non-qualifying is already treated as proving nothing.
 */
export async function sessionForAccessToken(request, env, actor, deps = {}) {
  const kv = deps.kv || env?.OAUTH_KV;
  const mintFn = deps.mintFn;
  const now = deps.now ? deps.now() : Date.now();
  // onFailure makes a downgrade OBSERVABLE. The first version swallowed every
  // failure into a bare `return null` with no log, no counter and no failure
  // record -- and that is how three production-fatal defects stayed invisible
  // through a full round of testing. Fail-open without a signal is not
  // fail-open, it is fail-undetectably, which is this substrate's worst case:
  // a fleet that looks authenticated and qualifies nothing.
  const onFailure = deps.onFailure || (() => {});
  if (!kv || !mintFn || !actor?.slug) {
    onFailure("session_mint_unavailable", {
      kv: Boolean(kv), mintFn: Boolean(mintFn), slug: actor?.slug || null });
    return null;
  }
  try {
    const identity = await accessTokenIdentity(request, deps.subtle);
    if (!identity) return null;      // not an OAuth token; this door cannot mint

    const mapKey = sessionMapKey(identity.tokenId);
    const existing = await kv.get(mapKey, { type: "json" });
    if (existing?.session_id) return existing.session_id;

    // THE TOKEN RECORD MUST EXIST, and this is a security check rather than a
    // lookup. It is the provider's own proof that this token was issued and
    // when it dies. Without it there is no credential expiry to inherit, and
    // an earlier version treated its absence as "unknown" and handed out the
    // full thirty-day cap -- which minted a long session for any bearer string
    // shaped like user:grant:secret, including static secrets from doors that
    // must never qualify.
    const tokenRecord = await kv.get(identity.kvKey, { type: "json" });
    if (!tokenRecord) {
      onFailure("session_token_record_absent", { tokenId: identity.tokenId });
      return null;
    }
    const expiresMs = sessionExpiry(Number(tokenRecord.expiresAt) * 1000, now);
    if (!expiresMs) {
      onFailure("session_credential_expiry_unusable", { tokenId: identity.tokenId });
      return null;
    }
    const expiresAt = new Date(expiresMs).toISOString();

    const id = deps.uuid ? deps.uuid() : crypto.randomUUID();
    const sessionId = await mintApplicationSession(mintFn, {
      id,
      actorSlug: actor.slug,
      organizationTenantId: actor.organization_tenant_id || "carr-internal",
      sponsoringHumanSlug: actor.sponsoring_human_slug || (actor.human ? actor.slug : null),
      via: actor.via || "oauth-google",
      authIssuer: "accounts.google.com",
      authorizationClass: actor.authorization_class || null,
      verifiedSubject: actor.slug,
      expiresAt,
    });

    const ttl = Math.max(1, Math.floor((expiresMs - now) / 1000));
    await kv.put(mapKey, JSON.stringify({ session_id: sessionId, minted_at: now }),
                 { expirationTtl: ttl });
    return sessionId;
  } catch (e) {
    // The request still proceeds and records legacy evidence: the alternative
    // makes this a single point of failure for every authenticated write. But
    // it is reported, so a door that has silently stopped qualifying is
    // discoverable rather than indistinguishable from one nobody used.
    onFailure("session_mint_failed", { error: String(e?.message || e).slice(0, 200) });
    return null;
  }
}

/**
 * The Deal Room cookie door.
 *
 * WHY THIS DOOR QUALIFIES AND THE BEARER DOORS DO NOT. Its KV record carries an
 * issuance instant (createdAt), an enforced expiry (expiresAt, under a hard
 * seven-day ceiling), and a real revocation: signing out deletes the key, and
 * the lookup deletes on expiry. That is all three properties a session identity
 * needs. A static secret map has none of them.
 *
 * ONE COOKIE SESSION IS ONE APPLICATION SESSION, and the mapping is stored IN
 * the cookie record rather than beside it. That is deliberate: the two then
 * share a lifetime by construction, so a revoked or expired cookie cannot leave
 * a mapping behind pointing at a session someone could still cite. Deleting the
 * record deletes the link in the same operation.
 *
 * Expiry is the cookie's ABSOLUTE end, not its idle end. The idle window slides
 * forward on every request, so binding to it would let a session that is
 * actively used never expire — which is the grant-not-a-session mistake in a
 * different costume. The absolute ceiling cannot slide.
 *
 * Returns { sessionId, changed }. `changed` tells the caller to persist the
 * record; it never writes KV itself, so the mint rides along with whatever
 * write the caller was already going to make rather than adding one.
 */
export async function sessionForDealRoomCookie(opts) {
  const { record, actor, absoluteEndMs, mintFn, now } = opts;
  const onFailure = opts.onFailure || (() => {});
  if (record?.application_session_id) {
    return { sessionId: record.application_session_id, changed: false };
  }
  if (!mintFn || !actor?.slug) {
    onFailure("session_mint_unavailable",
              { mintFn: Boolean(mintFn), slug: actor?.slug || null, door: "dealroom-cookie" });
    return { sessionId: null, changed: false };
  }
  try {
    const expiresMs = sessionExpiry(absoluteEndMs, now);
    if (!expiresMs) {
      onFailure("session_credential_expiry_unusable", { door: "dealroom-cookie" });
      return { sessionId: null, changed: false };
    }
    const sessionId = await mintApplicationSession(mintFn, {
      id: opts.uuid ? opts.uuid() : crypto.randomUUID(),
      actorSlug: actor.slug,
      organizationTenantId: actor.organization_tenant_id || "carr-internal",
      sponsoringHumanSlug: actor.sponsoring_human_slug || (actor.human ? actor.slug : null),
      via: "dealroom-cookie",
      authIssuer: "accounts.google.com",
      authorizationClass: actor.authorization_class || null,
      verifiedSubject: actor.slug,
      expiresAt: new Date(expiresMs).toISOString(),
    });
    return { sessionId, changed: true };
  } catch (e) {
    onFailure("session_mint_failed",
              { door: "dealroom-cookie", error: String(e?.message || e).slice(0, 200) });
    return { sessionId: null, changed: false };
  }
}

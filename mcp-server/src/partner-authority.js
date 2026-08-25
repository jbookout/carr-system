// Server-derived partner authority for interactive partners and the two native
// implementation agents they sponsor. Caller input never reaches this module:
// identity.js derives the actor and sponsor from verified session state.

import { authorizationClassForActor } from "./identity.js";

const PARTNER_AUTHORITY_AGENTS = new Set(["codex", "claude"]);
const PARTNER_SLUGS = new Set(["joe", "dell"]);

export function partnerAuthoritySlugForActor(actor) {
  if (actor?.human === true && PARTNER_SLUGS.has(actor.slug)) return actor.slug;
  if (actor?.human === false && PARTNER_AUTHORITY_AGENTS.has(actor.slug) &&
      PARTNER_SLUGS.has(actor.sponsoring_human_slug) &&
      authorizationClassForActor(actor) === "sponsored_agent")
    return actor.sponsoring_human_slug;
  return null;
}

export function canExercisePartnerAuthority(actor) {
  return partnerAuthoritySlugForActor(actor) !== null;
}

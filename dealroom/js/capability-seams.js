/**
 * Typed capability seams for backends V5-J101 depends on but that are not
 * built yet: V5-F01 (canonical record homes), V5-J102 (typed lifecycle),
 * V5-A01 (truthful health projection). Source: architecture_or_design
 * instruction for V5-J101 — "choose between typed client seams that show an
 * honest 'unavailable' state and mocks. Never ship mocks as live behaviour."
 *
 * TWO RULES a seam must follow, both violated in an earlier draft and fixed
 * after independent review of PR #1259 (a seam is a READ about availability,
 * never a WRITE that performs the thing it is checking for):
 *
 *   1. A capability that happens to share a NAME or a RELATED existing call
 *      with a not-yet-built slice is not that slice. getDeal() is the
 *      pre-existing WO-1 Deal Room read, not the V5-F01 canonical
 *      cross-entity record-home surface — its presence must never be read as
 *      F01 being available. recordHomeSeam is unconditionally unavailable
 *      until F01 actually ships a dedicated call.
 *   2. Checking "can this write happen" must never BE the write. lifecycleSeam
 *      only inspects whether client.transitionLifecycle exists as a function;
 *      it never invokes it. (healthSeam and, when F01 ships, recordHomeSeam,
 *      may safely perform their own real READS — reads have no side effect to
 *      leak — but no seam here may perform a write.)
 */

/** @typedef {{available:true, capability:string, detail:Object}|{available:false, capability:string, reason:string}} SeamResult */

function refused(capability, error, fallbackReason) {
  return { available: false, capability, reason: error?.payload?.hint || error?.message || fallbackReason };
}

/**
 * V5-F01: canonical, cross-entity record-home read. Not built. This always
 * answers unavailable, regardless of what the client otherwise implements —
 * deliberately NOT wired to getDeal(), which is a different, already-existing
 * WO-1 contract call and would misreport F01 as shipped.
 * @param {import('./client.js').DealRoomClient|null|undefined} _client unused until V5-F01 defines its own call
 * @param {string} _dealId unused until V5-F01 defines its own call
 * @returns {Promise<SeamResult>}
 */
export async function recordHomeSeam(_client, _dealId) {
  return { available: false, capability: 'V5-F01', reason: 'Canonical record-home read (V5-F01) is not deployed yet.' };
}

/**
 * V5-J102: typed lifecycle transitions with evidence/conflict handling.
 * Availability is a pure capability check — presence of the method — and
 * NEVER invokes it: a seam answering "is this available" must not itself
 * perform the write it is asking about.
 * @param {import('./client.js').DealRoomClient|null|undefined} client
 * @param {string} dealId
 * @returns {Promise<SeamResult>}
 */
export async function lifecycleSeam(client, dealId) {
  if (typeof client?.transitionLifecycle === 'function')
    return { available: true, capability: 'V5-J102', detail: { deal: dealId } };
  return {
    available: false, capability: 'V5-J102',
    reason: 'Typed lifecycle transitions are not deployed yet; only field-level writes exist today.',
  };
}

/**
 * V5-A01: the truthful health projection. Honors an already-implemented
 * client.getHealth() if one exists; a real read has no side effect to leak,
 * so — unlike lifecycleSeam — this may safely perform it. Otherwise
 * unavailable.
 * @param {import('./client.js').DealRoomClient|null|undefined} client
 * @returns {Promise<SeamResult>}
 */
export async function healthSeam(client) {
  if (typeof client?.getHealth === 'function') {
    try {
      const detail = await client.getHealth();
      return { available: true, capability: 'V5-A01', detail };
    } catch (error) {
      return refused('V5-A01', error, 'Health read failed.');
    }
  }
  return { available: false, capability: 'V5-A01', reason: 'The truthful health projection (V5-A01) is not deployed yet.' };
}

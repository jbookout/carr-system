/**
 * Typed capability seams for backends V5-J101 depends on but that are not
 * built yet: V5-F01 (canonical record homes), V5-J102 (typed lifecycle),
 * V5-A01 (truthful health projection). Source: architecture_or_design
 * instruction for V5-J101 — "choose between typed client seams that show an
 * honest 'unavailable' state and mocks. Never ship mocks as live behaviour."
 *
 * Each function below is the one place the workspace asks "is this capability
 * actually here." It calls the real client if the real client implements the
 * capability, and returns {available:false, capability, reason} — never a
 * fabricated success — when it does not. Nothing in this file invents data.
 */

/** @typedef {{available:true, capability:string, detail:Object}|{available:false, capability:string, reason:string}} SeamResult */

function refused(capability, error, fallbackReason) {
  return { available: false, capability, reason: error?.payload?.hint || error?.message || fallbackReason };
}

/**
 * V5-F01: canonical record-home read. Today's client already exposes
 * getDeal(id) against the WO-1 contract, so this seam is "available" whenever
 * that call answers; it stays honest if a future client drops or renames it.
 * @param {import('./client.js').DealRoomClient|null|undefined} client
 * @param {string} dealId
 * @returns {Promise<SeamResult>}
 */
export async function recordHomeSeam(client, dealId) {
  if (typeof client?.getDeal !== 'function')
    return { available: false, capability: 'V5-F01', reason: 'Canonical record-home read is not deployed yet.' };
  try {
    const detail = await client.getDeal(dealId);
    return { available: true, capability: 'V5-F01', detail };
  } catch (error) {
    return refused('V5-F01', error, 'Record-home read failed.');
  }
}

/**
 * V5-J102: typed lifecycle transitions with evidence/conflict handling. The
 * WO-1 client has no such call today — only the generic field patch — so
 * this seam is unavailable until V5-J102 ships, and says exactly that instead
 * of quietly downgrading to the generic patch and calling it the real thing.
 * @param {import('./client.js').DealRoomClient|null|undefined} client
 * @param {string} dealId
 * @returns {Promise<SeamResult>}
 */
export async function lifecycleSeam(client, dealId) {
  if (typeof client?.transitionLifecycle === 'function') {
    try {
      const detail = await client.transitionLifecycle({ deal: dealId });
      return { available: true, capability: 'V5-J102', detail };
    } catch (error) {
      return refused('V5-J102', error, 'Lifecycle transition failed.');
    }
  }
  return {
    available: false, capability: 'V5-J102',
    reason: 'Typed lifecycle transitions are not deployed yet; only field-level writes exist today.',
  };
}

/**
 * V5-A01: the truthful health projection. Honors an already-implemented
 * client.getHealth() if one exists; otherwise unavailable.
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

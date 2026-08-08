/**
 * Live client: same interface as fixture, against the WO-1 HTTP/verb surface.
 * This work order does not enable network: without a base URL it throws.
 * Wire shape is the contract the fixture already exercises.
 */
import { uuidv4 } from './uuid.js';

/**
 * @param {Object} [opts]
 * @param {string} [opts.baseUrl] e.g. https://dealroom.doctorcre.com/api
 * @param {string} [opts.selfActor]
 * @param {(path:string, init?:RequestInit)=>Promise<any>} [opts.fetchJson]
 */
export function createLiveClient(opts = {}) {
  const baseUrl = opts.baseUrl || null;
  const selfActor = opts.selfActor || 'joe';
  const fetchJson =
    opts.fetchJson ||
    (async (path, init = {}) => {
      if (!baseUrl) {
        throw new Error(
          'Live client has no baseUrl. Fixture mode is the default for this work order.',
        );
      }
      const url = path.startsWith('http') ? path : `${baseUrl.replace(/\/$/, '')}${path}`;
      const res = await fetch(url, {
        ...init,
        headers: {
          'content-type': 'application/json',
          ...(init.headers || {}),
        },
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`live ${init.method || 'GET'} ${path} → ${res.status} ${body}`);
      }
      if (res.status === 204) return null;
      return res.json();
    });

  async function write(verb, args) {
    const idempotency_key = args.idempotency_key || uuidv4();
    return fetchJson(`/verbs/${verb}`, {
      method: 'POST',
      body: JSON.stringify({ ...args, idempotency_key }),
    });
  }

  return {
    mode: /** @type {const} */ ('live'),
    selfActor,

    async getBoard() {
      // existing deal-board read verb
      return fetchJson('/verbs/deal-board', { method: 'POST', body: '{}' });
    },

    async getDeal(dealId) {
      return fetchJson('/verbs/deal-read', {
        method: 'POST',
        body: JSON.stringify({ deal: dealId }),
      });
    },

    async getChanges(cursor) {
      const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
      return fetchJson(`/pipeline/changes${q}`);
    },

    async presenceLease({ deal, field, idempotency_key }) {
      return write('presence-lease', { deal, field, idempotency_key });
    },

    async patchDealField(args) {
      return write('patch-deal-field', args);
    },

    async resolveConflict(args) {
      return write('resolve-conflict', args);
    },

    async addDealNote(args) {
      return write('add-deal-note', args);
    },

    async setNextStep(args) {
      return write('set-next-step', args);
    },

    async createDeal(args) {
      return write('create-deal', args);
    },
  };
}

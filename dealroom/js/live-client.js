/**
 * Live client: same interface as fixture, against the deployed Worker.
 * Verbs travel as MCP JSON-RPC tools/call over the cookie-authenticated /mcp
 * mount; the event cursor is plain GET /pipeline/changes. Both are same-origin
 * on dealroom.doctorcre.com, so no baseUrl is needed in production; one may be
 * passed for wrangler-dev testing.
 */
import { uuidv4 } from './uuid.js';

/**
 * @param {Object} [opts]
 * @param {string} [opts.baseUrl] same-origin by default; override for dev
 * @param {string} [opts.selfActor]
 * @param {(path:string, init?:RequestInit)=>Promise<Response>} [opts.fetchImpl]
 */
export function createLiveClient(opts = {}) {
  const baseUrl = (opts.baseUrl || '').replace(/\/$/, '');
  const selfActor = opts.selfActor || 'joe';
  const fetchImpl = opts.fetchImpl || ((path, init) => fetch(`${baseUrl}${path}`, init));
  let rpcId = 0;

  async function rpc(verb, args = {}) {
    const res = await fetchImpl('/mcp', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: ++rpcId,
        method: 'tools/call',
        params: { name: verb, arguments: args },
      }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`live ${verb} -> ${res.status} ${body.slice(0, 200)}`);
    }
    const envelope = await res.json();
    if (envelope.error) throw new Error(`live ${verb} rpc error: ${envelope.error.message}`);
    const payload = JSON.parse(envelope.result?.content?.[0]?.text ?? 'null');
    if (envelope.result?.isError) {
      const err = new Error(`live ${verb} refused: ${payload?.error || 'tool_error'}`);
      err.payload = payload;
      throw err;
    }
    return payload;
  }

  async function write(verb, args) {
    return rpc(verb, { ...args, idempotency_key: args.idempotency_key || uuidv4() });
  }

  return {
    mode: /** @type {const} */ ('live'),
    selfActor,

    async getBoard() {
      return rpc('deal-board', {});
    },

    async getDeal(dealId) {
      return rpc('get-deal-room', { deal: dealId });
    },

    async getChanges(cursor) {
      const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
      const res = await fetchImpl(`/pipeline/changes${q}`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`live changes -> ${res.status}`);
      return res.json();
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

    async createDeal() {
      // new-deal requires an existing client and is humanOnly with richer
      // fields than a jot carries; the jot lane's live path is a design
      // decision still owed to Joe. Fixture keeps the jot demo; live refuses
      // honestly instead of inventing a client record.
      throw new Error('Jot lane is not wired live yet: a deal needs a client on the record. Use the record layer, or jot in fixture mode.');
    },
  };
}

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
  let selfActor = opts.selfActor || null;
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

  // The record layer speaks phase SLUGS (deal_phase table); the board speaks
  // the display names the mockup ruled. Translate at the client boundary in
  // both directions so neither side ever sees the other's vocabulary.
  const PHASE_TO_UI = {
    pending: 'On Deck', research: 'Research', site_selection: 'Research',
    negotiation: 'Negotiation', legal: 'Legal', due_diligence: 'Diligence',
    closing: 'Closing', closed: 'Closed',
  };
  const UI_TO_PHASE = {
    'On Deck': 'pending', 'Research': 'research', 'Negotiation': 'negotiation',
    'Legal': 'legal', 'Diligence': 'due_diligence', 'Closing': 'closing',
    'Closed': 'closed',
  };
  const TYPE_TO_UI = {
    startup: 'Startup', relocation: 'Relocation', additional_office: '2nd Office',
    renewal: 'Renewal', expansion: 'Expansion', purchase: 'Purchase', other: 'Other',
  };
  // Chip text, built from the candidate's own fields. A proposal must read as
  // a question about a specific deal, and it must never look like something
  // that already happened.
  function confirmLabel(c) {
    const who = c.deal_name || c.payload?.deal || 'this deal';
    const p = c.payload || {};
    if (c.kind === 'phase_move') return `${who} → phase ${PHASE_TO_UI[p.value] || p.value}?`;
    if (c.kind === 'next_step') return `${who} → next step "${p.text || ''}"?`;
    if (c.kind === 'new_deal') return `New deal "${p.name || 'untitled'}" — create?`;
    if (c.kind === 'meeting_record') return 'File the meeting summary?';
    return `${who} — log ${p.kind || 'activity'}?`;
  }

  const dealToUi = (d) => ({
    ...d,
    phase: PHASE_TO_UI[d.phase] || d.phase,
    type: TYPE_TO_UI[d.type] || d.type,
    next_step: d.next_step || '',
  });

  const client = {
    mode: /** @type {const} */ ('live'),
    get selfActor() { return selfActor; },

    async getBoard(options = {}) {
      const board = await rpc('deal-room-board', {
        workspace: options.workspace || 'all',
        ...(options.account_client_id ? { account_client_id: options.account_client_id } : {}),
      });
      selfActor = board.actor || selfActor;
      return { ...board, deals: (board.deals || []).map(dealToUi) };
    },

    async getDeal(dealId) {
      const page = await rpc('get-deal-room', { deal: dealId });
      const { thread = [], critical_dates = [], events = [], deal_id, ...fields } = page;
      // Thread: the newest next_step IS the cell's current step; older
      // next_step rows are the archive the ruling requires ("supersede,
      // never erase"). Notes pass through.
      let currentSeen = false;
      const uiThread = [];
      let currentStep = null;
      for (const n of thread) {
        if (n.kind === 'next_step') {
          if (!currentSeen) { currentSeen = true; currentStep = n.text; continue; }
          uiThread.push({ ...n, kind: 'archived_step' });
        } else {
          uiThread.push(n);
        }
      }
      const history = events.map((e) => {
        for (const side of ['old_value', 'new_value']) {
          const v = e[side];
          if (v && typeof v === 'object' && e.field && e.field in v) e[side] = v[e.field];
        }
        const val = e.field === 'phase' && typeof e.new_value === 'string'
          ? (PHASE_TO_UI[e.new_value] || e.new_value) : e.new_value;
        const summary = e.field
          ? `${e.verb.replace(/-/g, ' ')} · ${e.field} → ${typeof val === 'string' ? val : JSON.stringify(val)}`
          : e.verb.replace(/-/g, ' ');
        return { ...e, summary };
      });
      return {
        deal: dealToUi({ id: deal_id, ...fields, next_step: currentStep || fields.next_step || '' }),
        thread: uiThread,
        critical_dates: critical_dates.map((cd) => ({ ...cd, label: cd.note || cd.kind, date: cd.due_on })),
        history,
        next_actions: page.next_actions || [],
        activities: page.activities || [],
        participants: page.participants || [],
        premises: page.premises || [],
        negotiation_rounds: page.negotiation_rounds || [],
        documents: page.documents || [],
      };
    },

    // The confirm strip, live: proposals distilled from a recorded call. The
    // label is built here from the candidate's own shape, never from a
    // server-supplied string, and every disposition goes through
    // resolve-candidate, which is the only thing that writes.
    async getPendingConfirms() {
      const { candidates = [] } = await rpc('capture-queue', {});
      return {
        proposals: candidates.map((c) => ({
          id: c.id,
          deal_id: c.payload?.deal || null,
          label: confirmLabel(c),
        })),
      };
    },

    async resolveConfirm({ proposal_id, accept, idempotency_key }) {
      const res = await write('resolve-candidate', {
        candidate_id: proposal_id,
        accept,
        idempotency_key,
      });
      return { status: 'ok', event: null, ref: res?.ref || null };
    },

    async getChanges(cursor) {
      const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
      const res = await fetchImpl(`/pipeline/changes${q}`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`live changes -> ${res.status}`);
      const data = await res.json();
      for (const e of data.events || []) {
        // The event log stores values wrapped as {field: value}; the app (and
        // the fixture) speak bare values. Unwrap, then translate phase slugs.
        for (const side of ['old_value', 'new_value']) {
          const v = e[side];
          if (v && typeof v === 'object' && e.field && e.field in v) e[side] = v[e.field];
        }
        if (e.field === 'phase' && typeof e.new_value === 'string') {
          e.new_value = PHASE_TO_UI[e.new_value] || e.new_value;
        }
      }
      return data;
    },

    async presenceLease({ deal, field, idempotency_key }) {
      return write('presence-lease', { deal, field, idempotency_key });
    },

    async patchDealField(args) {
      if (args.field === 'phase' && UI_TO_PHASE[args.value]) {
        args = { ...args, value: UI_TO_PHASE[args.value] };
      }
      const res = await write('patch-deal-field', args);
      if (res?.ok === false && res.conflict) {
        const c = res.conflict;
        return { status: 'conflict', conflict: {
          conflict_id: c.id,
          deal: c.deal_id,
          field: c.field,
          a: { actor: c.actor_a, value: c.value_a },
          b: { actor: c.actor_b, value: c.value_b },
        } };
      }
      return { status: 'ok', ...res };
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
      const res = await write('new-deal', {
        client: args.client,
        name: args.name,
        deal_type: args.deal_type || 'other',
        phase: UI_TO_PHASE[args.phase] || args.phase || 'pending',
        segment: args.segment || undefined,
        city: args.market || undefined,
        lane: args.lane || 'territory',
        reason: 'Created in the Deal Room',
        idempotency_key: args.idempotency_key,
      });
      await write('set-lead', { deal: res.deal_id, new_lead: selfActor });
      return { status: 'ok', deal_id: res.deal_id };
    },

    async startReview(args) { return write('start-deal-review', args); },
    async reviewDeal(args) { return write('review-deal', args); },
    async endReview(args) { return write('end-deal-review', args); },
    async setMarketAgent(args) { return write('set-market-agent', args); },
    async setNationalAccountOwner(args) { return write('set-national-account-owner', args); },
    async createNationalAccount(args) { return write('create-national-account', args); },
    async createNationalMarketDeal(args) { return write('create-national-market-deal', args); },
    async revertDealField(args) { return write('revert-deal-field', args); },
  };
  return client;
}

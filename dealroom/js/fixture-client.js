/**
 * Fixture client: full WO-1 contract against in-memory state seeded from
 * data/board-seed.json. Zero network. Live and fixture share one interface.
 */
import { uuidv4 } from './uuid.js';
import { PHASES } from './client.js';

const LEASE_TTL_MS = 3000;
const IDEM_TTL_MS = 60 * 60 * 1000;

/**
 * @param {Object} [opts]
 * @param {string} [opts.seedUrl]
 * @param {string} [opts.selfActor]
 */
export async function createFixtureClient(opts = {}) {
  const seedUrl = opts.seedUrl || new URL('../data/board-seed.json', import.meta.url).href;
  const seed = await fetch(seedUrl).then((r) => {
    if (!r.ok) throw new Error(`fixture seed failed: ${r.status}`);
    return r.json();
  });

  const selfActor = opts.selfActor || seed.actors?.self || 'joe';
  const partnerActor = seed.actors?.partner || 'dell';

  /** @type {Map<string, any>} */
  const deals = new Map(seed.deals.map((d) => [d.id, { ...d }]));
  // Keep the local demo representative of the production information model:
  // one national-account portfolio, many market deals, no duplicate deal rows.
  const fixtureAccountId = 'acct-musicologie';
  let fixtureAccountOwner = 'dell';
  [...deals.values()].slice(0, 5).forEach((d, index) => Object.assign(d, {
    workspace_kind: 'national_account',
    account_client_id: fixtureAccountId,
    account_client_ref: 'C-161',
    account_name: 'Musicologie',
    account_owner: fixtureAccountOwner,
    client_ref: `C-${131 + index}`,
    client_name: d.name,
    market_agent: index < 3 ? ['Alex Morgan','Taylor Reed','Jordan Lee'][index] : null,
  }));
  [...deals.values()].slice(5).forEach((d) => Object.assign(d, {
    workspace_kind: 'team', account_client_id: null, account_name: null,
    client_ref: d.client_ref || null, client_name: d.client_name || d.name,
  }));
  /** @type {Map<string, any[]>} */
  const threads = new Map(
    Object.entries(seed.threads || {}).map(([k, v]) => [k, v.map((x) => ({ ...x }))]),
  );
  /** @type {Map<string, any[]>} */
  const history = new Map(
    Object.entries(seed.history || {}).map(([k, v]) => [k, v.map((x) => ({ ...x }))]),
  );

  /** @type {any[]} events newest-last (append order); cursor is last id */
  const events = [...(seed.seed_events || [])].map((e) => ({ ...e }));
  events.sort((a, b) => a.recorded_at.localeCompare(b.recorded_at));

  /** @type {Map<string, {actor:string, deal_id:string, field:string, expires_at:string}>} */
  const leases = new Map(); // key: actor|deal|field

  /** @type {Map<string, any>} idempotency_key -> result */
  const idem = new Map();

  /** @type {Map<string, any>} open conflicts */
  const conflicts = new Map();

  /** @type {any[]} confirm proposals from call distill */
  let pendingConfirms = [];

  /** field event index: deal|field -> last event id */
  const lastFieldEvent = new Map();
  for (const e of events) {
    if (e.field) lastFieldEvent.set(`${e.subject_id}|${e.field}`, e.id);
  }

  let lastCallAt = seed.last_call_at || '2026-08-07T16:00:00Z';
  let asOf = seed.as_of || '2026-08-08';
  let seq = events.length + 1;
  let conflictSeq = 1;
  let noteSeq = 1;
  let histSeq = 1;
  let confirmSeq = 1;
  let reviewSeq = 1;
  const reviewSessions = new Map();

  function nowIso() {
    return new Date().toISOString();
  }

  function nextEventId() {
    return `e-${String(seq++).padStart(4, '0')}`;
  }

  function actorLabel(a) {
    if (a === 'joe') return 'Joe';
    if (a === 'dell') return 'Dell';
    return a;
  }

  function ensureThread(dealId) {
    if (!threads.has(dealId)) threads.set(dealId, []);
    return threads.get(dealId);
  }

  function ensureHistory(dealId) {
    if (!history.has(dealId)) history.set(dealId, []);
    return history.get(dealId);
  }

  function pushHistory(dealId, actor, summary, at) {
    const h = {
      id: `h-${histSeq++}`,
      actor,
      summary,
      recorded_at: at || nowIso(),
    };
    ensureHistory(dealId).unshift(h);
    return h;
  }

  function pushEvent(partial) {
    const e = {
      id: nextEventId(),
      recorded_at: nowIso(),
      actor: partial.actor || selfActor,
      verb: partial.verb,
      subject_type: partial.subject_type || 'deal',
      subject_id: partial.subject_id,
      field: partial.field ?? null,
      old_value: partial.old_value ?? null,
      new_value: partial.new_value ?? null,
    };
    events.push(e);
    if (e.field) lastFieldEvent.set(`${e.subject_id}|${e.field}`, e.id);
    return e;
  }

  function cursorOf(list) {
    if (!list.length) return '0';
    return list[list.length - 1].id;
  }

  function eventsAfter(cursor) {
    if (!cursor || cursor === '0' || cursor === null) return events.slice();
    const idx = events.findIndex((e) => e.id === cursor);
    if (idx < 0) return events.slice(); // unknown cursor: full replay (safe)
    return events.slice(idx + 1);
  }

  function pruneLeases() {
    const t = Date.now();
    for (const [k, v] of leases) {
      if (new Date(v.expires_at).getTime() <= t) leases.delete(k);
    }
  }

  function withIdem(key, fn) {
    if (!key) throw new Error('idempotency_key required');
    const hit = idem.get(key);
    if (hit && Date.now() - hit.at < IDEM_TTL_MS) return structuredClone(hit.result);
    const result = fn();
    idem.set(key, { at: Date.now(), result: structuredClone(result) });
    return result;
  }

  function getDealOrThrow(id) {
    const d = deals.get(id);
    if (!d) throw new Error(`unknown deal ${id}`);
    return d;
  }

  /**
   * Apply a field write. If base_event_id mismatches last known, open conflict.
   */
  function applyFieldWrite({ deal, field, value, base_event_id, actor, verb }) {
    const d = getDealOrThrow(deal);
    const key = `${deal}|${field}`;
    const last = lastFieldEvent.get(key) || null;
    const base = base_event_id === undefined ? null : base_event_id;
    // Optimistic concurrency: client's base must match last event for (deal, field).
    // null means "no event seen"; mismatches (including null vs an id) are conflicts.
    if (last !== base) {
      // conflict: a = server (current), b = incoming
      const conflict_id = `c-${conflictSeq++}`;
      const payload = {
        conflict_id,
        deal,
        field,
        a: { actor: 'server', value: field === 'attention' ? d.attention : d[field], event_id: last },
        b: { actor, value, event_id: null },
      };
      conflicts.set(conflict_id, { ...payload, pending_value: value, pending_actor: actor, verb });
      return { status: 'conflict', conflict: payload };
    }

    const mapField = field === 'next_step' ? 'next_step' : field;
    const old = d[mapField];
    if (field === 'attention') d.attention = !!value;
    else if (field === 'owner') d.owner = value || null;
    else if (field === 'phase') {
      if (!PHASES.includes(value)) throw new Error(`bad phase ${value}`);
      d.phase = value;
    } else if (field === 'next_date') d.next_date = value || null;
    else if (field === 'next_step') d.next_step = value || '';
    else throw new Error(`unknown field ${field}`);

    d.last_touch = nowIso().slice(0, 10);
    const e = pushEvent({
      actor,
      verb: verb || 'patch-deal-field',
      subject_id: deal,
      field,
      old_value: old ?? null,
      new_value: value ?? null,
    });
    pushHistory(deal, actor, `${field} ${old ?? '(empty)'} to ${value ?? '(empty)'}`, e.recorded_at);
    return { status: 'ok', event: e };
  }

  const client = {
    mode: /** @type {const} */ ('fixture'),
    selfActor,

    async getBoard() {
      const national = [...deals.values()].filter((d) => d.account_client_id === fixtureAccountId);
      return {
        actor: selfActor,
        deals: [...deals.values()].map((d) => ({ ...d })),
        accounts: [{ account_client_id: fixtureAccountId, account_client_ref: 'C-161',
          account_name: 'Musicologie', account_owner: fixtureAccountOwner, open_deals: national.length,
          attention_deals: national.filter((d) => d.attention).length,
          overdue_deals: 0, stale_deals: 2, last_review_at: lastCallAt }],
        open_session: [...reviewSessions.values()].find((s) => s.status === 'open') || null,
        as_of: asOf,
        last_call_at: lastCallAt,
      };
    },

    async getDeal(dealId) {
      const deal = { ...getDealOrThrow(dealId) };
      const thread = (threads.get(dealId) || [])
        .slice()
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at));
      const hist = (history.get(dealId) || [])
        .slice()
        .sort((a, b) => b.recorded_at.localeCompare(a.recorded_at));
      const critical_dates = [];
      if (deal.next_date) {
        critical_dates.push({
          label: deal.id === 'd14' ? 'Lease commencement' : 'Next date',
          date: deal.next_date,
        });
      }
      return { deal, thread, critical_dates, history: hist,
        next_actions: deal.next_step ? [{ id: `a-${deal.id}`, owner: deal.owner,
          description: deal.next_step, due_on: deal.next_date, status: 'open' }] : [],
        activities: hist.slice(0, 4).map((h) => ({ id: h.id, actor: h.actor,
          occurred_at: h.recorded_at, kind: 'note', summary: h.summary })),
        participants: [{ role: 'lead', name: actorLabel(deal.owner), actor: deal.owner }],
        premises: [], negotiation_rounds: [], documents: [] };
    },

    async getChanges(cursor) {
      pruneLeases();
      const fresh = eventsAfter(cursor);
      return {
        events: fresh.map((e) => ({ ...e })),
        presence: [...leases.values()].map((p) => ({ ...p })),
        capture_sessions: [],
        cursor: cursorOf(events),
      };
    },

    async presenceLease({ deal, field, idempotency_key }) {
      // lease is not a write to deal data; idempotency still accepted
      void idempotency_key;
      getDealOrThrow(deal);
      const expires = new Date(Date.now() + LEASE_TTL_MS).toISOString();
      const key = `${selfActor}|${deal}|${field}`;
      leases.set(key, { actor: selfActor, deal_id: deal, field, expires_at: expires });
      return { ok: true };
    },

    async patchDealField({ deal, field, value, base_event_id, idempotency_key }) {
      return withIdem(idempotency_key, () =>
        applyFieldWrite({
          deal,
          field,
          value,
          base_event_id: base_event_id ?? null,
          actor: selfActor,
          verb: 'patch-deal-field',
        }),
      );
    },

    async resolveConflict({ conflict_id, winner, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const c = conflicts.get(conflict_id);
        if (!c) throw new Error(`unknown conflict ${conflict_id}`);
        const d = getDealOrThrow(c.deal);
        const chosen = winner === 'a' ? c.a.value : c.pending_value;
        const actor = winner === 'a' ? selfActor : c.pending_actor;
        const old = d[c.field];
        if (c.field === 'attention') d.attention = !!chosen;
        else d[c.field] = chosen;
        d.last_touch = nowIso().slice(0, 10);
        const e = pushEvent({
          actor,
          verb: 'resolve-conflict',
          subject_id: c.deal,
          field: c.field,
          old_value: old ?? null,
          new_value: chosen ?? null,
        });
        pushHistory(c.deal, actor, `resolved conflict on ${c.field}`, e.recorded_at);
        conflicts.delete(conflict_id);
        return { status: 'ok', event: e };
      });
    },

    async addDealNote({ deal, text, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        getDealOrThrow(deal);
        const t = String(text || '').trim();
        if (!t) throw new Error('empty note');
        const entry = {
          id: `n-${noteSeq++}`,
          kind: 'note',
          actor: selfActor,
          text: t,
          recorded_at: nowIso(),
        };
        ensureThread(deal).unshift(entry);
        const e = pushEvent({
          actor: selfActor,
          verb: 'add-deal-note',
          subject_id: deal,
          field: null,
          old_value: null,
          new_value: t,
        });
        pushHistory(deal, selfActor, `added note`, e.recorded_at);
        return { status: 'ok', event: e };
      });
    },

    async setNextStep({ deal, text, next_date, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const d = getDealOrThrow(deal);
        const t = String(text || '').trim();
        if (!t) throw new Error('empty next step');
        const old = d.next_step;
        // archive old step into the thread with attribution
        if (old && old.trim()) {
          ensureThread(deal).unshift({
            id: `n-${noteSeq++}`,
            kind: 'archived_step',
            actor: selfActor,
            text: old,
            recorded_at: nowIso(),
          });
        }
        d.next_step = t;
        if (next_date !== undefined) d.next_date = next_date || null;
        d.last_touch = nowIso().slice(0, 10);
        const e = pushEvent({
          actor: selfActor,
          verb: 'set-next-step',
          subject_id: deal,
          field: 'next_step',
          old_value: old ?? null,
          new_value: t,
        });
        if (next_date !== undefined) {
          pushEvent({
            actor: selfActor,
            verb: 'patch-deal-field',
            subject_id: deal,
            field: 'next_date',
            old_value: null,
            new_value: next_date,
          });
        }
        pushHistory(deal, selfActor, `set next step to "${t}"`, e.recorded_at);
        return { status: 'ok', event: e };
      });
    },

    async createDeal({ name, client, deal_type, phase, segment, market, lane, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const n = String(name || '').trim();
        if (!n) throw new Error('empty name');
        const id = `d-new-${seq++}`;
        const d = {
          id,
          name: n,
          type: deal_type || 'Other',
          phase: phase || 'On Deck',
          owner: selfActor,
          attention: false,
          last_touch: nowIso().slice(0, 10),
          next_step: '',
          next_date: null,
          segment: segment || null,
          market: market || null,
          client_ref: client || 'C-demo',
          client_name: client || n,
          workspace_kind: lane === 'national' ? 'national_account' : 'team',
          account_client_id: lane === 'national' ? fixtureAccountId : null,
          account_name: lane === 'national' ? 'Musicologie' : null,
        };
        deals.set(id, d);
        const e = pushEvent({
          actor: selfActor,
          verb: 'create-deal',
          subject_id: id,
          field: null,
          old_value: null,
          new_value: n,
        });
        pushHistory(id, selfActor, 'created from the board', e.recorded_at);
        return { status: 'ok', event: e, deal: { ...d } };
      });
    },

    async startReview({ workspace_kind, account_client_id, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const session = { session_id: `review-${reviewSeq++}`, workspace_kind,
          account_client_id: account_client_id || null, started_at: nowIso(), status: 'open', items: [] };
        reviewSessions.set(session.session_id, session);
        return { ok: true, ...session };
      });
    },

    async reviewDeal({ session_id, deal, disposition, note, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const session = reviewSessions.get(session_id);
        if (!session || session.status !== 'open') throw new Error('review session is not open');
        session.items = session.items.filter((i) => i.deal !== deal);
        session.items.push({ deal, disposition, note: note || null });
        return { ok: true, session_id, deal_id: deal, disposition };
      });
    },

    async endReview({ session_id, status = 'completed', idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const session = reviewSessions.get(session_id);
        session.status = status;
        session.ended_at = nowIso();
        lastCallAt = session.ended_at;
        return { ok: true, session_id, status,
          reviewed: session.items.filter((i) => i.disposition === 'reviewed').length,
          skipped: session.items.filter((i) => i.disposition === 'skipped').length };
      });
    },

    async setMarketAgent({ deal, agent_name, market, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const d = getDealOrThrow(deal); d.market_agent = agent_name; if (market) d.market = market;
        return { ok: true, deal_id: deal, market_agent: agent_name };
      });
    },

    async setNationalAccountOwner({ account_client_id, owner, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        fixtureAccountOwner = owner;
        for (const d of deals.values()) if (d.account_client_id === account_client_id) d.account_owner = owner;
        return { ok: true, account_client_id, owner };
      });
    },

    async createNationalAccount({ name, owner, idempotency_key }) {
      return withIdem(idempotency_key, () => ({ ok: true,
        account_client_id: `acct-${name.toLowerCase().replace(/\W+/g,'-')}`,
        account_client_ref: 'C-demo', name, owner }));
    },

    async createNationalMarketDeal(args) {
      return client.createDeal({ name: args.deal_name, client: args.client_name,
        deal_type: args.deal_type || 'Startup', phase: args.phase || 'On Deck',
        segment: args.segment, market: args.market, lane: 'national',
        idempotency_key: args.idempotency_key });
    },

    async revertDealField({ event_id, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const event = events.find((e) => e.id === event_id);
        if (!event) throw new Error('event not found');
        const d = getDealOrThrow(event.subject_id); d[event.field] = event.old_value;
        return { ok: true, deal_id: d.id, field: event.field, new_value: event.old_value };
      });
    },

    async getPendingConfirms() {
      return { proposals: pendingConfirms.map((p) => ({ ...p })) };
    },

    async resolveConfirm({ proposal_id, accept, idempotency_key }) {
      return withIdem(idempotency_key, () => {
        const idx = pendingConfirms.findIndex((p) => p.id === proposal_id);
        if (idx < 0) return { status: 'ok', event: null };
        const p = pendingConfirms[idx];
        pendingConfirms.splice(idx, 1);
        if (!accept) {
          return {
            status: 'ok',
            event: pushEvent({
              actor: selfActor,
              verb: 'skip-confirm',
              subject_id: p.deal_id,
              field: null,
              old_value: null,
              new_value: p.id,
            }),
          };
        }
        // apply proposal via same write path (nothing auto-writes)
        if (p.verb === 'patch-deal-field') {
          return applyFieldWrite({
            deal: p.args.deal,
            field: p.args.field,
            value: p.args.value,
            base_event_id: lastFieldEvent.get(`${p.args.deal}|${p.args.field}`) || null,
            actor: selfActor,
            verb: 'patch-deal-field',
          });
        }
        if (p.verb === 'set-next-step') {
          const d = getDealOrThrow(p.args.deal);
          const old = d.next_step;
          if (old && old.trim()) {
            ensureThread(p.args.deal).unshift({
              id: `n-${noteSeq++}`,
              kind: 'archived_step',
              actor: selfActor,
              text: old,
              recorded_at: nowIso(),
            });
          }
          d.next_step = p.args.text;
          d.last_touch = nowIso().slice(0, 10);
          const e = pushEvent({
            actor: selfActor,
            verb: 'set-next-step',
            subject_id: p.args.deal,
            field: 'next_step',
            old_value: old,
            new_value: p.args.text,
          });
          pushHistory(p.args.deal, selfActor, `set next step to "${p.args.text}"`, e.recorded_at);
          return { status: 'ok', event: e };
        }
        if (p.verb === 'create-deal') {
          return client.createDeal({
            name: p.args.name,
            idempotency_key: uuidv4(),
          });
        }
        return { status: 'ok', event: null };
      });
    },

    /**
     * Fixture-only: partner presence + partner write + call distill proposals.
     * Wired through the same event log and presence map the UI polls.
     */
    async simulatePartnerCall() {
      const steps = [];
      const cottis = getDealOrThrow('d05');
      const petersen = getDealOrThrow('d20');

      // partner joins
      steps.push({
        at: 400,
        run: () => {
          const expires = new Date(Date.now() + 12000).toISOString();
          leases.set(`${partnerActor}|d05|next_step`, {
            actor: partnerActor,
            deal_id: 'd05',
            field: 'next_step',
            expires_at: expires,
          });
        },
      });

      // partner holds Cottis next_step
      steps.push({
        at: 1500,
        run: () => {
          const expires = new Date(Date.now() + 10000).toISOString();
          leases.set(`${partnerActor}|d05|next_step`, {
            actor: partnerActor,
            deal_id: 'd05',
            field: 'next_step',
            expires_at: expires,
          });
        },
      });

      // partner writes Cottis next step (live write, not confirm)
      steps.push({
        at: 3300,
        run: () => {
          const old = cottis.next_step;
          if (old && old.trim()) {
            ensureThread('d05').unshift({
              id: `n-${noteSeq++}`,
              kind: 'archived_step',
              actor: partnerActor,
              text: old,
              recorded_at: nowIso(),
            });
          }
          cottis.next_step = 'Signed! Commencement Oct 1';
          cottis.next_date = '2026-10-01';
          cottis.last_touch = nowIso().slice(0, 10);
          pushEvent({
            actor: partnerActor,
            verb: 'set-next-step',
            subject_id: 'd05',
            field: 'next_step',
            old_value: old,
            new_value: cottis.next_step,
          });
          pushEvent({
            actor: partnerActor,
            verb: 'patch-deal-field',
            subject_id: 'd05',
            field: 'next_date',
            old_value: '2026-08-09',
            new_value: '2026-10-01',
          });
          pushHistory('d05', partnerActor, 'set next step to "Signed! Commencement Oct 1"');
          leases.delete(`${partnerActor}|d05|next_step`);
        },
      });

      // partner presence on Petersen
      steps.push({
        at: 5000,
        run: () => {
          void petersen;
          const expires = new Date(Date.now() + 8000).toISOString();
          leases.set(`${partnerActor}|d20|next_step`, {
            actor: partnerActor,
            deal_id: 'd20',
            field: 'next_step',
            expires_at: expires,
          });
        },
      });

      // call ended: distill proposals (nothing writes without a tap)
      steps.push({
        at: 7200,
        run: () => {
          pendingConfirms = [
            {
              id: `p-${confirmSeq++}`,
              label: 'Cottis → phase Closed?',
              deal_id: 'd05',
              verb: 'patch-deal-field',
              args: { deal: 'd05', field: 'phase', value: 'Closed' },
            },
            {
              id: `p-${confirmSeq++}`,
              label: 'Tubbs → next step "drop the rate push"?',
              deal_id: 'd23',
              verb: 'set-next-step',
              args: { deal: 'd23', text: 'drop the rate push' },
            },
            {
              id: `p-${confirmSeq++}`,
              label: 'New deal "Dr. Amanda Reyes" - create?',
              deal_id: '',
              verb: 'create-deal',
              args: { name: 'Dr. Amanda Reyes' },
            },
          ];
        },
      });

      steps.push({
        at: 12500,
        run: () => {
          for (const k of [...leases.keys()]) {
            if (k.startsWith(`${partnerActor}|`)) leases.delete(k);
          }
        },
      });

      return { steps };
    },

    /** Test helper: force a conflict by writing without advancing base. */
    async _forceConflict(deal, field, valueA, valueB) {
      const key = `${deal}|${field}`;
      const base = lastFieldEvent.get(key) || null;
      applyFieldWrite({
        deal,
        field,
        value: valueA,
        base_event_id: base,
        actor: partnerActor,
        verb: 'patch-deal-field',
      });
      // second write with stale base
      return applyFieldWrite({
        deal,
        field,
        value: valueB,
        base_event_id: base,
        actor: selfActor,
        verb: 'patch-deal-field',
      });
    },

    _lastFieldEventId(deal, field) {
      return lastFieldEvent.get(`${deal}|${field}`) || null;
    },

    _setLastCallAt(iso) {
      lastCallAt = iso;
    },
  };

  return client;
}

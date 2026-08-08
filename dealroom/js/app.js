/**
 * Deal Room front-end — live wiring against the WO-1 client interface.
 * Design authority: design/mockups/dealroom-v2.html
 */
import { createClient, PHICON, PHASES, ACTOR_LABEL } from './client.js';
import { uuidv4 } from './uuid.js';

const POLL_MS = 1000;
const TODAY = new Date('2026-08-08T12:00:00'); // demo clock matches seed as_of

// ── state ────────────────────────────────────────────────────────────
const state = {
  client: null,
  deals: /** @type {Map<string, any>} */ (new Map()),
  lastCallAt: null,
  /** deal ids that have events after lastCallAt (Δ filter) */
  changedSinceCall: /** @type {Set<string>} */ (new Set()),
  /** deal|field -> last event id we saw */
  fieldBase: /** @type {Map<string, string>} */ (new Map()),
  cursor: null,
  presence: /** @type {any[]} */ ([]),
  filter: 'all',
  query: '',
  view: 'board', // board | deal
  openDealId: null,
  composerDealId: null,
  conflict: null, // {conflict, anchorEl}
  confirms: [],
  selfActor: 'joe',
  partnerHere: false,
  demoPlaying: false,
  openPhaseMenu: null,
};

// ── DOM refs ─────────────────────────────────────────────────────────
const $ = (sel, root = document) => root.querySelector(sel);
const rowsEl = () => $('#rows');
const whisperEl = () => $('#whisper');
const confirmsEl = () => $('#confirms');

function say(html) {
  const w = whisperEl();
  w.innerHTML = html;
  w.classList.add('show');
  clearTimeout(say._t);
  say._t = setTimeout(() => w.classList.remove('show'), 2600);
}

// ── dates / icons ────────────────────────────────────────────────────
function dueInfo(d) {
  if (!d) return null;
  const days = Math.round((new Date(d + 'T12:00:00') - TODAY) / 864e5);
  const label = new Date(d + 'T12:00:00').toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
  if (days < 0) return { cls: 'over', txt: `OVERDUE ${-days}d` };
  if (days === 0) return { cls: 'today', txt: 'TODAY' };
  if (days <= 2) return { cls: 'd2', txt: `${label} · ${days}d` };
  if (days <= 7) return { cls: 'd7', txt: `${label} · ${days}d` };
  if (days <= 14) return { cls: 'd14', txt: `${label} · ${days}d` };
  return { cls: '', txt: label };
}

function rowIcon(r) {
  const d = r.next_date
    ? Math.round((new Date(r.next_date + 'T12:00:00') - TODAY) / 864e5)
    : 99;
  if (r.attention || d < 0) return '⚠️';
  return PHICON[r.phase] || '○';
}

function ownerLetter(own) {
  if (own === 'joe') return 'J';
  if (own === 'dell') return 'D';
  return '–';
}

function actorName(a) {
  return ACTOR_LABEL[a] || a || 'someone';
}

// ── board load / poll ────────────────────────────────────────────────
async function loadBoard() {
  const board = await state.client.getBoard();
  state.deals = new Map(board.deals.map((d) => [d.id, d]));
  state.lastCallAt = board.last_call_at;
  // seed Δ from any events already after last call
  await pollOnce(true);
  render();
}

async function pollOnce(initial = false) {
  const res = await state.client.getChanges(state.cursor);
  state.cursor = res.cursor;
  state.presence = res.presence || [];

  // partner presence avatar
  const partner = state.selfActor === 'joe' ? 'dell' : 'joe';
  state.partnerHere = state.presence.some(
    (p) => p.actor === partner && new Date(p.expires_at) > new Date(),
  );
  const av = $('#partnerAv');
  if (av) av.classList.toggle('here', state.partnerHere);

  for (const e of res.events || []) {
    if (e.field) state.fieldBase.set(`${e.subject_id}|${e.field}`, e.id);
    if (e.subject_type === 'deal' && e.subject_id) {
      if (state.lastCallAt && e.recorded_at > state.lastCallAt) {
        state.changedSinceCall.add(e.subject_id);
      }
      // apply event to local deal map when we know the field
      const d = state.deals.get(e.subject_id);
      if (d && e.field && e.verb !== 'add-deal-note') {
        if (e.field === 'attention') d.attention = !!e.new_value;
        else if (e.field === 'owner') d.owner = e.new_value;
        else if (e.field === 'phase') d.phase = e.new_value;
        else if (e.field === 'next_date') d.next_date = e.new_value;
        else if (e.field === 'next_step') d.next_step = e.new_value;
      }
      if (e.verb === 'create-deal' && !state.deals.has(e.subject_id)) {
        // refresh board for new deals
        const board = await state.client.getBoard();
        state.deals = new Map(board.deals.map((x) => [x.id, x]));
      }
      // partner write whispers (not on initial replay of seed)
      if (!initial && e.actor !== state.selfActor && e.field === 'next_step') {
        const deal = state.deals.get(e.subject_id);
        say(
          `<b>${actorName(e.actor)}</b> updated ${deal ? deal.name : e.subject_id}${
            e.new_value ? '' : ''
          }`,
        );
      }
    }
  }

  // refresh confirms if fixture exposes them
  if (state.client.getPendingConfirms) {
    const { proposals } = await state.client.getPendingConfirms();
    state.confirms = proposals;
    renderConfirms();
  }

  // Never rebuild the DOM out from under an active edit: a quick note, the
  // composer, the deal-page note input, or a contenteditable cell all live
  // inside what render replaces, and the 1s poll would erase typed text.
  if (!userIsEditing()) {
    if (state.view === 'board') renderRowsOnly();
    else if (state.view === 'deal' && state.openDealId) await openDeal(state.openDealId, true);
  }
  applyPresenceFlags();
}

function userIsEditing() {
  const a = document.activeElement;
  if (!a || !a.closest) return false;
  return !!(
    a.closest('.qnbox') ||
    a.closest('.composer') ||
    a.id === 'dvnote' ||
    a.hasAttribute('contenteditable')
  );
}

function startPolling() {
  setInterval(() => {
    pollOnce().catch((err) => console.warn('poll failed', err));
  }, POLL_MS);
}

// ── render board ─────────────────────────────────────────────────────
function filteredDeals() {
  const q = state.query;
  return [...state.deals.values()].filter((r) => {
    if (state.filter === 'joe' && r.owner !== 'joe') return false;
    if (state.filter === 'dell' && r.owner !== 'dell') return false;
    if (state.filter === 'active' && ['Closed', 'On Deck'].includes(r.phase)) return false;
    if (state.filter === 'delta' && !state.changedSinceCall.has(r.id)) return false;
    if (q && !r.name.toLowerCase().includes(q)) return false;
    return true;
  });
}

function renderStats() {
  const all = [...state.deals.values()];
  const act = all.filter((r) => !['Closed', 'On Deck'].includes(r.phase)).length;
  const soon = all.filter((r) => {
    if (!r.next_date) return false;
    const d = Math.round((new Date(r.next_date + 'T12:00:00') - TODAY) / 864e5);
    return d >= 0 && d <= 7;
  }).length;
  const over = all.filter((r) => {
    if (!r.next_date) return false;
    return new Date(r.next_date + 'T12:00:00') < TODAY;
  }).length;
  const closing = all.filter((r) => r.phase === 'Closing').length;
  $('#stats').innerHTML = `
    <div class="tile"><b>${act}</b><span>Deals in motion</span></div>
    <div class="tile"><b>${closing}</b><span>At the key 🔑</span></div>
    <div class="tile"><b>${soon}</b><span>Dates this week</span></div>
    <div class="tile"><b>${over}</b><span>Overdue <em>· needs eyes</em></span></div>`;
}

function renderRowsOnly() {
  const el = rowsEl();
  if (!el) return;
  // preserve open composer focus if any
  const composerOpen = state.composerDealId;
  el.innerHTML = '';
  for (const r of filteredDeals()) {
    el.appendChild(buildRow(r));
  }
  if (state.filter !== 'delta') {
    el.appendChild(buildJotRow());
  }
  if (composerOpen) {
    const cell = el.querySelector(`tr[data-id="${composerOpen}"] .nextcell`);
    if (cell) openComposer(composerOpen, cell);
  }
  if (state.conflict) {
    // re-attach conflict UI if still open
    const { conflict } = state.conflict;
    const cell = el.querySelector(
      `tr[data-id="${conflict.deal}"] td[data-field="${conflict.field}"], tr[data-id="${conflict.deal}"] .nextcell`,
    );
    if (cell) showConflict(conflict, cell);
  }
  applyPresenceFlags();
  renderStats();
}

function render() {
  if (state.view === 'board') {
    $('#boardview').style.display = 'block';
    $('#dealview').style.display = 'none';
    renderRowsOnly();
  }
}

function buildRow(r) {
  const tr = document.createElement('tr');
  tr.dataset.id = r.id;
  const d = r.next_date
    ? Math.round((new Date(r.next_date + 'T12:00:00') - TODAY) / 864e5)
    : 99;
  if (r.attention) tr.classList.add('attn');
  if (d < 0) tr.classList.add('overdue');
  const di = dueInfo(r.next_date);
  tr.innerHTML = `
    <td class="deal">
      <span class="pic" data-attn="${r.id}" title="${r.phase}${r.attention ? ' · needs attention' : ''}">${rowIcon(r)}</span>
      <a data-open="${r.id}" href="#deal/${r.id}">${escapeHtml(r.name)}</a>
    </td>
    <td data-field="phase"><span class="ph" data-p="${escapeAttr(r.phase)}" data-phase="${r.id}">${escapeHtml(r.phase)}</span></td>
    <td style="color:var(--ink-2)">${escapeHtml(r.type)}</td>
    <td class="nextcell" data-field="next_step" data-deal="${r.id}">
      <span class="nexttext">${escapeHtml(r.next_step || '')}</span>
      <button type="button" class="qn" data-note="${r.id}" title="Quick note on ${escapeAttr(r.name)}" aria-label="Quick note">📝</button>
    </td>
    <td data-field="next_date">${di ? `<span class="due ${di.cls}">${di.txt}</span>` : `<span class="due">-</span>`}</td>
    <td data-field="owner"><span class="own ${r.owner || 'none'}" data-own="${r.id}">${ownerLetter(r.owner)}</span></td>`;
  return tr;
}

function buildJotRow() {
  const jot = document.createElement('tr');
  jot.className = 'jot';
  jot.innerHTML = `
    <td style="color:var(--ink-3)">
      <span class="pic" style="color:var(--orange)">+</span>
      <span class="jot-name" contenteditable="true" spellcheck="false" data-ghost>Jot a deal - name, and go</span>
    </td>
    <td><span class="ph" data-p="On Deck">On Deck</span></td>
    <td></td>
    <td style="color:var(--ink-3)">next step…</td>
    <td></td>
    <td><span class="own joe">J</span></td>`;
  const name = jot.querySelector('.jot-name');
  name.addEventListener('focus', () => {
    if (name.dataset.ghost !== undefined) {
      name.textContent = '';
      delete name.dataset.ghost;
    }
  });
  name.addEventListener('keydown', async (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      const n = name.textContent.trim();
      if (!n) return;
      const res = await state.client.createDeal({ name: n, idempotency_key: uuidv4() });
      if (res.deal) state.deals.set(res.deal.id, res.deal);
      else {
        const board = await state.client.getBoard();
        state.deals = new Map(board.deals.map((d) => [d.id, d]));
      }
      if (res.event) state.changedSinceCall.add(res.event.subject_id);
      say(`Created <b>${escapeHtml(n)}</b> on deck`);
      renderRowsOnly();
    }
  });
  return jot;
}

// ── presence flags on cells ──────────────────────────────────────────
function applyPresenceFlags() {
  document.querySelectorAll('.flagged').forEach((el) => {
    el.classList.remove('flagged', 'actor-joe', 'actor-dell');
    el.removeAttribute('data-flag');
  });
  const now = Date.now();
  for (const p of state.presence) {
    if (new Date(p.expires_at).getTime() <= now) continue;
    if (p.actor === state.selfActor) continue; // only show the other person
    const sel =
      state.view === 'deal'
        ? `#dealview .f[data-field="${cssEscape(p.field)}"]`
        : `tr[data-id="${cssEscape(p.deal_id)}"] td[data-field="${cssEscape(p.field)}"], tr[data-id="${cssEscape(p.deal_id)}"] .nextcell`;
    const cell = document.querySelector(sel);
    if (!cell) continue;
    cell.classList.add('flagged', `actor-${p.actor}`);
    cell.dataset.flag = actorName(p.actor);
  }
}

function cssEscape(s) {
  return String(s).replace(/"/g, '\\"');
}

// ── next-step composer (append-only; never overwrite-edit) ───────────
function openComposer(dealId, cell) {
  closeComposer();
  const r = state.deals.get(dealId);
  if (!r || !cell) return;
  state.composerDealId = dealId;
  cell.querySelector('.nexttext')?.classList.add('open');

  const box = document.createElement('div');
  box.className = 'composer';
  box.innerHTML = `
    <div class="cur">Current next step: <b>${escapeHtml(r.next_step || '(none)')}</b></div>
    <input type="text" aria-label="Next step or note" placeholder="Write the next step, or a note about it">
    <div class="acts">
      <button type="button" class="secondary" data-act="note">Add note</button>
      <button type="button" class="primary" data-act="step">Set as next step</button>
      <button type="button" class="ghost" data-act="cancel">Cancel</button>
    </div>`;
  cell.appendChild(box);
  const inp = box.querySelector('input');
  inp.focus();

  // presence lease while composer is open
  const lease = () =>
    state.client.presenceLease({
      deal: dealId,
      field: 'next_step',
      idempotency_key: uuidv4(),
    });
  lease();
  const leaseTimer = setInterval(lease, POLL_MS);
  box._leaseTimer = leaseTimer;

  const finish = () => {
    clearInterval(leaseTimer);
    closeComposer();
  };

  box.querySelector('[data-act="cancel"]').onclick = finish;
  box.querySelector('[data-act="note"]').onclick = async () => {
    const text = inp.value.trim();
    if (!text) return;
    await state.client.addDealNote({ deal: dealId, text, idempotency_key: uuidv4() });
    state.changedSinceCall.add(dealId);
    say(`Note on <b>${escapeHtml(r.name)}</b> - in the record, visible on the deal page`);
    finish();
  };
  box.querySelector('[data-act="step"]').onclick = async () => {
    const text = inp.value.trim();
    if (!text) return;
    const res = await state.client.setNextStep({
      deal: dealId,
      text,
      idempotency_key: uuidv4(),
    });
    r.next_step = text;
    state.changedSinceCall.add(dealId);
    if (res.event) state.fieldBase.set(`${dealId}|next_step`, res.event.id);
    say(`Next step set on <b>${escapeHtml(r.name)}</b> - prior step archived to the thread`);
    finish();
    renderRowsOnly();
  };
  inp.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') finish();
    // Enter saves (Joe's ruling, day 9 live proof): plain Enter commits the
    // next step. Shift+Enter adds a note instead of superseding the step.
    if (ev.key === 'Enter') {
      ev.preventDefault();
      box.querySelector(ev.shiftKey ? '[data-act="note"]' : '[data-act="step"]').click();
    }
  });
}

function closeComposer() {
  document.querySelectorAll('.composer').forEach((c) => {
    if (c._leaseTimer) clearInterval(c._leaseTimer);
    c.remove();
  });
  document.querySelectorAll('.nexttext.open').forEach((n) => n.classList.remove('open'));
  state.composerDealId = null;
}

// ── quick note (📝 inside next-step cell) ────────────────────────────
function quickNote(dealId, btn) {
  const r = state.deals.get(dealId);
  if (!r) return;
  const cell = btn.closest('.nextcell');
  // remove existing
  cell.querySelector('.qnbox')?.remove();
  const box = document.createElement('div');
  box.className = 'qnbox';
  box.innerHTML = `<input placeholder="Quick note on ${escapeAttr(r.name)} - Enter saves" aria-label="Quick note">`;
  cell.appendChild(box);
  const inp = box.querySelector('input');
  inp.focus();
  state.client.presenceLease({
    deal: dealId,
    field: 'next_step',
    idempotency_key: uuidv4(),
  });
  const close = () => box.remove();
  inp.addEventListener('keydown', async (ev) => {
    if (ev.key === 'Enter' && inp.value.trim()) {
      await state.client.addDealNote({
        deal: dealId,
        text: inp.value.trim(),
        idempotency_key: uuidv4(),
      });
      state.changedSinceCall.add(dealId);
      close();
      say(`Note on <b>${escapeHtml(r.name)}</b> - in the record, visible on the deal page`);
    }
    if (ev.key === 'Escape') close();
  });
  inp.addEventListener('blur', () => setTimeout(close, 150));
}

// ── field patches (phase, owner, attention) ──────────────────────────
async function patchField(dealId, field, value) {
  const base = state.fieldBase.get(`${dealId}|${field}`) || null;
  const res = await state.client.patchDealField({
    deal: dealId,
    field,
    value,
    base_event_id: base,
    idempotency_key: uuidv4(),
  });
  if (res.status === 'conflict') {
    const cell = document.querySelector(
      `tr[data-id="${dealId}"] td[data-field="${field}"], tr[data-id="${dealId}"] .nextcell`,
    );
    showConflict(res.conflict, cell);
    return;
  }
  if (res.event) {
    state.fieldBase.set(`${dealId}|${field}`, res.event.id);
    state.changedSinceCall.add(dealId);
  }
  const d = state.deals.get(dealId);
  if (d) {
    if (field === 'attention') d.attention = !!value;
    else d[field] = value;
  }
  renderRowsOnly();
}

function showConflict(conflict, anchor) {
  document.querySelectorAll('.conflict').forEach((c) => c.remove());
  state.conflict = { conflict, anchor };
  if (!anchor) return;
  const box = document.createElement('div');
  box.className = 'conflict';
  const labelA = formatVal(conflict.a.value);
  const labelB = formatVal(conflict.b.value);
  box.innerHTML = `
    <h4>Conflict on ${escapeHtml(conflict.field)}</h4>
    <div class="opts">
      <div class="opt"><span>A · ${escapeHtml(String(conflict.a.actor))}: <b>${escapeHtml(labelA)}</b></span>
        <button type="button" data-w="a">Keep A</button></div>
      <div class="opt"><span>B · ${escapeHtml(String(conflict.b.actor))}: <b>${escapeHtml(labelB)}</b></span>
        <button type="button" data-w="b">Keep B</button></div>
    </div>`;
  anchor.style.position = 'relative';
  anchor.appendChild(box);
  box.querySelectorAll('button').forEach((btn) => {
    btn.onclick = async () => {
      const winner = btn.dataset.w;
      const res = await state.client.resolveConflict({
        conflict_id: conflict.conflict_id,
        winner,
        idempotency_key: uuidv4(),
      });
      if (res.event) {
        state.fieldBase.set(`${conflict.deal}|${conflict.field}`, res.event.id);
        const d = state.deals.get(conflict.deal);
        if (d) {
          const v = winner === 'a' ? conflict.a.value : conflict.b.value;
          if (conflict.field === 'attention') d.attention = !!v;
          else d[conflict.field] = v;
        }
        state.changedSinceCall.add(conflict.deal);
      }
      state.conflict = null;
      box.remove();
      say('Conflict resolved - written as you');
      renderRowsOnly();
    };
  });
}

function formatVal(v) {
  if (v === null || v === undefined || v === '') return '(empty)';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return String(v);
}

// ── phase cycle / owner cycle ────────────────────────────────────────
function cyclePhase(dealId) {
  const d = state.deals.get(dealId);
  if (!d) return;
  const i = PHASES.indexOf(d.phase);
  const next = PHASES[(i + 1) % PHASES.length];
  patchField(dealId, 'phase', next).then(() =>
    say(`Phase to <b>${escapeHtml(next)}</b> on ${escapeHtml(d.name)}`),
  );
}

function cycleOwner(dealId) {
  const d = state.deals.get(dealId);
  if (!d) return;
  const order = ['joe', 'dell', null];
  const i = order.indexOf(d.owner);
  const next = order[(i + 1) % order.length];
  patchField(dealId, 'owner', next).then(() =>
    say(`Owner set on <b>${escapeHtml(d.name)}</b>`),
  );
}

// ── deal page ────────────────────────────────────────────────────────
async function openDeal(id, soft = false) {
  state.view = 'deal';
  state.openDealId = id;
  closeComposer();
  const detail = await state.client.getDeal(id);
  const r = detail.deal;
  state.deals.set(id, r);
  $('#boardview').style.display = 'none';
  const v = $('#dealview');
  v.style.display = 'block';
  const d = dueInfo(r.next_date);
  const threadHtml =
    detail.thread.length === 0
      ? '<div class="thread-empty">nothing yet</div>'
      : detail.thread
          .map(
            (n) =>
              `<div class="noterow"><span class="who">${escapeHtml(actorName(n.actor))}</span>${escapeHtml(n.text)}${
                n.kind === 'archived_step'
                  ? '<span class="kind">prior next step</span>'
                  : ''
              }</div>`,
          )
          .join('');
  const histHtml =
    detail.history.length === 0
      ? '<li>created from the board</li>'
      : detail.history
          .map(
            (h) =>
              `<li><b>${escapeHtml(actorName(h.actor))}</b> ${escapeHtml(h.summary)} <span style="color:var(--ink-3)">· ${escapeHtml(relTime(h.recorded_at))}</span></li>`,
          )
          .join('');
  const datesHtml =
    detail.critical_dates.length === 0
      ? '<div class="sub">none set</div>'
      : detail.critical_dates
          .map((cd) => {
            const di = dueInfo(cd.date);
            return `<div class="daterow"><span>${escapeHtml(cd.label)}</span><span class="due ${di ? di.cls : ''}">${di ? di.txt : cd.date || '-'}</span></div>`;
          })
          .join('');

  v.innerHTML = `
    <button type="button" class="back">← Back to the room</button>
    <div class="dv-id">
      <span class="big">${rowIcon(r)}</span>
      <h1>${escapeHtml(r.name)}</h1>
      <span class="ph" data-p="${escapeAttr(r.phase)}">${escapeHtml(r.phase)}</span>
      <span class="own ${r.owner || 'none'}">${ownerLetter(r.owner)}</span>
      ${d ? `<span class="due ${d.cls}">${d.txt}</span>` : ''}
    </div>
    <div class="brief">
      <div class="bcard">
        <h3>Next step</h3>
        <div class="main">${escapeHtml(r.next_step || '-')}</div>
        <div class="sub">owner: ${r.owner ? actorName(r.owner) : 'unassigned'} · last touch: ${escapeHtml(r.last_touch || '-')}</div>
      </div>
      <div class="bcard"><h3>Critical dates</h3>${datesHtml}</div>
      <div class="bcard"><h3>Latest notes</h3>
        ${
          detail.thread.filter((t) => t.kind === 'note').length
            ? detail.thread
                .filter((t) => t.kind === 'note')
                .slice(0, 2)
                .map(
                  (n) =>
                    `<div class="noterow"><span class="who">${escapeHtml(actorName(n.actor))}</span>${escapeHtml(n.text)}</div>`,
                )
                .join('')
            : '<div class="sub">no notes yet</div>'
        }
      </div>
    </div>
    <h2 class="sec">The facts</h2>
    <div class="fields">
      <div class="f" data-field="type"><label>Deal type</label><div>${escapeHtml(r.type)}</div></div>
      <div class="f" data-field="phase"><label>Phase</label><div>${escapeHtml(r.phase)}</div></div>
      <div class="f" data-field="segment"><label>Segment</label><div>${escapeHtml(r.segment || '-')}</div></div>
      <div class="f" data-field="market"><label>Market</label><div>${escapeHtml(r.market || '-')}</div></div>
    </div>
    <h2 class="sec">Next-step thread</h2>
    <p class="sub" style="font-size:12px;color:var(--ink-2);margin-bottom:6px">Append-only. Notes and superseded next steps live here; nothing is overwritten.</p>
    ${threadHtml}
    <div class="addnote">
      <input id="dvnote" placeholder="Add a note - or open the board cell to set the next step">
      <button type="button" id="dvsave">Save</button>
    </div>
    <h2 class="sec">History - every change, attributed</h2>
    <ul class="hist">${histHtml}</ul>`;

  v.querySelector('.back').onclick = () => {
    state.view = 'board';
    state.openDealId = null;
    v.style.display = 'none';
    $('#boardview').style.display = 'block';
    render();
    history.replaceState(null, '', '#');
  };
  v.querySelector('#dvsave').onclick = async () => {
    const i = v.querySelector('#dvnote');
    if (!i.value.trim()) return;
    await state.client.addDealNote({
      deal: id,
      text: i.value.trim(),
      idempotency_key: uuidv4(),
    });
    state.changedSinceCall.add(id);
    say(`Note saved on <b>${escapeHtml(r.name)}</b>`);
    await openDeal(id);
  };
  if (!soft) history.replaceState(null, '', `#deal/${id}`);
  applyPresenceFlags();
}

function relTime(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  const d = Math.round(ms / 864e5);
  if (Math.abs(d) < 1) {
    const h = Math.round(ms / 3600e3);
    if (Math.abs(h) < 1) return 'just now';
    return `${Math.abs(h)}h ago`;
  }
  return `${Math.abs(d)}d ago`;
}

// ── confirm strip ────────────────────────────────────────────────────
function renderConfirms() {
  const el = confirmsEl();
  if (!state.confirms.length) {
    el.classList.remove('show');
    el.innerHTML = '';
    return;
  }
  el.classList.add('show');
  el.innerHTML =
    `<span class="lbl">From your call · confirm or skip - nothing writes itself</span>` +
    state.confirms
      .map(
        (p) =>
          `<span class="cchip" data-pid="${escapeAttr(p.id)}">${escapeHtml(p.label)}<button type="button" class="y">Yes</button><button type="button" class="n">Skip</button></span>`,
      )
      .join('');
  el.querySelectorAll('.cchip button').forEach((btn) => {
    btn.onclick = async () => {
      const chip = btn.closest('.cchip');
      const pid = chip.dataset.pid;
      const yes = btn.classList.contains('y');
      await state.client.resolveConfirm({
        proposal_id: pid,
        accept: yes,
        idempotency_key: uuidv4(),
      });
      // refresh deals after accept
      const board = await state.client.getBoard();
      state.deals = new Map(board.deals.map((d) => [d.id, d]));
      state.confirms = state.confirms.filter((p) => p.id !== pid);
      say(yes ? 'Confirmed - written as you, reversible' : 'Skipped - nothing written');
      renderConfirms();
      renderRowsOnly();
    };
  });
}

// ── simulate partner call (fixture) ──────────────────────────────────
async function runDemo() {
  if (state.demoPlaying || !state.client.simulatePartnerCall) return;
  state.demoPlaying = true;
  const btn = $('#demoBtn');
  btn.classList.add('live');
  btn.textContent = '● Live - watch the room';
  const { steps } = await state.client.simulatePartnerCall();
  for (const s of steps) {
    setTimeout(async () => {
      s.run();
      // Pull fixture mutations through the same poll path the UI always uses.
      await pollOnce();
      if (s.at === 400) {
        say('<b>Dell</b> joined the room');
      }
      if (s.at === 1500) {
        const tr = rowsEl()?.querySelector('tr[data-id="d05"]');
        tr?.scrollIntoView({
          block: 'center',
          behavior: prefersReduced() ? 'auto' : 'smooth',
        });
      }
      if (s.at === 3300) {
        say('<b>Dell</b> updated Nikki Cottis - landlord signed');
      }
      if (s.at === 5000) {
        say('<b>Dell</b> is on Petersen - his name rides the cell, Excel-style');
      }
      if (s.at === 7200) {
        say('Call ended - the distiller heard three things · nothing writes without a tap');
      }
      if (s.at === 12500) {
        btn.classList.remove('live');
        btn.textContent = '▶ Simulate the call';
        state.demoPlaying = false;
      }
    }, s.at);
  }
}

function prefersReduced() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// ── events ───────────────────────────────────────────────────────────
function wireChrome() {
  rowsEl().addEventListener('click', (e) => {
    const attn = e.target.closest('[data-attn]');
    if (attn) {
      const id = attn.dataset.attn;
      const r = state.deals.get(id);
      if (!r) return;
      const next = !r.attention;
      patchField(id, 'attention', next).then(() =>
        say(
          `${next ? 'Flagged' : 'Cleared'} <b>${escapeHtml(r.name)}</b>${
            next ? ' ⚠️ needs attention' : ''
          } - Dell sees it in a second`,
        ),
      );
      return;
    }
    const open = e.target.closest('[data-open]');
    if (open) {
      e.preventDefault();
      openDeal(open.dataset.open);
      return;
    }
    const note = e.target.closest('.qn');
    if (note) {
      e.stopPropagation();
      quickNote(note.dataset.note, note);
      return;
    }
    const phase = e.target.closest('[data-phase]');
    if (phase) {
      cyclePhase(phase.dataset.phase);
      return;
    }
    const own = e.target.closest('[data-own]');
    if (own) {
      cycleOwner(own.dataset.own);
      return;
    }
    const next = e.target.closest('.nextcell');
    if (next && next.dataset.deal) {
      openComposer(next.dataset.deal, next);
    }
  });

  document.querySelectorAll('.chip').forEach((c) =>
    c.addEventListener('click', () => {
      document.querySelectorAll('.chip').forEach((x) => x.classList.remove('on'));
      c.classList.add('on');
      state.filter = c.dataset.f;
      renderRowsOnly();
      if (state.filter === 'delta') {
        say('Δ - only what moved since your last call, straight off the event log');
      }
    }),
  );

  $('#q').addEventListener('input', (e) => {
    state.query = e.target.value.toLowerCase();
    renderRowsOnly();
  });

  document.querySelectorAll('.vibe.lay').forEach((b) =>
    b.addEventListener('click', () => {
      document.querySelectorAll('.vibe.lay').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      document.body.classList.toggle('dense', b.dataset.lay === 'dense');
    }),
  );

  document.querySelectorAll('.vibe.col, .vibe.cvd').forEach((b) =>
    b.addEventListener('click', () => {
      if (b.classList.contains('cvd')) {
        b.classList.toggle('on');
        document.body.classList.toggle('cvd');
        return;
      }
      document.querySelectorAll('.vibe.col:not(.cvd)').forEach((x) => x.classList.remove('on'));
      b.classList.add('on');
      document.body.classList.toggle('night', b.dataset.col === 'night');
    }),
  );

  $('#mic')?.addEventListener('click', () => {
    const m = $('#mic');
    const on = m.classList.toggle('on');
    say(
      on
        ? 'Quill listening - this is one of its three doors (right-cmd · menu bar · here)'
        : 'Quill off',
    );
  });

  $('#demoBtn')?.addEventListener('click', () => runDemo());

  // deep link
  window.addEventListener('hashchange', () => {
    const m = location.hash.match(/^#deal\/(.+)$/);
    if (m) openDeal(m[1]);
    else if (state.view === 'deal') {
      state.view = 'board';
      $('#dealview').style.display = 'none';
      $('#boardview').style.display = 'block';
      render();
    }
  });
}

// ── utils ────────────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/'/g, '&#39;');
}

// ── boot ─────────────────────────────────────────────────────────────
async function boot() {
  const params = new URLSearchParams(location.search);
  // Production serves live by default; fixture stays the default for local
  // files and dev servers, and either can be forced with ?mode=.
  const requested = params.get('mode');
  const isProdHost = location.hostname === 'dealroom.doctorcre.com';
  const mode =
    requested === 'live' ? 'live'
    : requested === 'fixture' ? 'fixture'
    : isProdHost ? 'live'
    : 'fixture';
  state.client = await createClient(mode, {
    baseUrl: params.get('api') || undefined,
  });
  state.selfActor = state.client.selfActor;
  $('#modePill').textContent = state.client.mode === 'fixture' ? 'Fixture' : 'Live';
  // The call simulation is a fixture-mode design demo; the live room has real
  // calls (Joe's ruling: not on the production top bar).
  if (state.client.mode !== 'fixture') $('#demoBtn')?.remove();

  wireChrome();
  await loadBoard();
  startPolling();

  const m = location.hash.match(/^#deal\/(.+)$/);
  if (m) await openDeal(m[1]);
}

boot().catch((err) => {
  console.error(err);
  document.body.insertAdjacentHTML(
    'afterbegin',
    `<div style="background:#C0392B;color:#fff;padding:12px 16px;font:600 13px var(--body)">Deal Room failed to start: ${escapeHtml(err.message)}</div>`,
  );
});

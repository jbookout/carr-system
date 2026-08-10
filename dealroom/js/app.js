import { createClient, PHASES, PHICON, ACTOR_LABEL } from './client.js';
import { uuidv4 } from './uuid.js';

const POLL_MS = 1400;
const state = {
  client: null, selfActor: null, deals: new Map(), accounts: [], cursor: null,
  workspace: 'team', accountId: null, filter: 'all', query: '',
  changed: new Set(), fieldBase: new Map(), presence: [], captureSessions: [],
  confirms: [], review: null, pollTimer: null, undoEventId: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const today = () => new Date(new Date().toDateString());
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;',
}[char]));
const actorName = (slug) => ACTOR_LABEL[slug] || slug || 'Unassigned';
const account = () => state.accounts.find((item) => item.account_client_id === state.accountId) || null;

function daysFromNow(value) {
  if (!value) return null;
  return Math.round((new Date(`${value}T12:00:00`) - today()) / 864e5);
}

function dateLabel(value) {
  if (!value) return 'Not set';
  const days = daysFromNow(value);
  const date = new Date(`${value}T12:00:00`).toLocaleDateString('en-US', { month:'short', day:'numeric' });
  if (days < 0) return `${date} · ${Math.abs(days)}d overdue`;
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  return `${date} · ${days}d`;
}

function relative(value) {
  if (!value) return 'not captured';
  const days = Math.round((Date.now() - new Date(value).getTime()) / 864e5);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}

function isStale(deal) {
  if (!deal.last_touch) return true;
  return (Date.now() - new Date(`${deal.last_touch}T12:00:00`).getTime()) / 864e5 >= 14;
}

function reasonFor(deal) {
  const days = daysFromNow(deal.next_date);
  if (deal.attention) return 'Flagged for attention';
  if (days !== null && days < 0) return `Next date is ${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'} overdue`;
  if (!deal.next_step) return 'No next step';
  if (isStale(deal)) return deal.last_touch ? `Gone quiet ${relative(deal.last_touch)}` : 'No recent touch captured';
  if (!deal.market_agent && deal.workspace_kind === 'national_account') return 'Market agent unassigned';
  return `Ready for review · ${deal.phase}`;
}

function priority(deal) {
  const days = daysFromNow(deal.next_date);
  return (deal.attention ? 10000 : 0) + (days !== null && days < 0 ? 7000 + Math.abs(days) : 0)
    + (!deal.next_step ? 4000 : 0) + (isStale(deal) ? 2000 : 0)
    + (deal.workspace_kind === 'national_account' && !deal.market_agent ? 1000 : 0);
}

function showToast(message, undoEventId = null) {
  const el = $('#toast');
  state.undoEventId = undoEventId;
  el.innerHTML = `${esc(message)}${undoEventId ? '<button type="button" data-undo>Undo</button>' : ''}`;
  el.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.remove('show'), undoEventId ? 7000 : 3200);
}

function setSync(ok, label = null) {
  const el = $('#syncStatus');
  el.classList.toggle('offline', !ok);
  el.textContent = label || (ok ? 'Live' : 'Reconnecting');
}

async function loadHome() {
  const home = await state.client.getBoard({ workspace:'all' });
  state.selfActor = home.actor || state.client.selfActor || state.selfActor;
  state.deals = new Map((home.deals || []).map((deal) => [deal.id, {
    workspace_kind: deal.account_client_id ? 'national_account' : 'team', ...deal,
  }]));
  state.accounts = home.accounts || [];
  for (const deal of state.deals.values()) if (!deal.last_review_at) state.changed.add(deal.id);
  $('#selfAvatar').textContent = state.selfActor === 'dell' ? 'D' : state.selfActor === 'joe' ? 'J' : '?';
  $('#selfAvatar').setAttribute('aria-label', `Signed in as ${actorName(state.selfActor)}`);
  await pollOnce(true);
  render();
}

async function pollOnce(initial = false) {
  try {
    const result = await state.client.getChanges(state.cursor);
    state.cursor = result.cursor;
    state.presence = result.presence || [];
    state.captureSessions = result.capture_sessions || [];
    renderCaptureStatus();
    for (const event of result.events || []) {
      if (event.field) state.fieldBase.set(`${event.subject_id}|${event.field}`, event.id);
      const deal = state.deals.get(event.subject_id);
      if (!deal) continue;
      if (!deal.last_review_at || String(event.recorded_at) > String(deal.last_review_at)) state.changed.add(deal.id);
      if (event.field === 'attention') deal.attention = Boolean(event.new_value);
      if (event.field === 'phase') deal.phase = event.new_value;
      if (event.field === 'owner') deal.owner = event.new_value;
      if (event.field === 'next_date') deal.next_date = event.new_value;
      if (event.field === 'next_step') deal.next_step = event.new_value;
      if (!initial && event.actor === state.selfActor && ['phase','owner','attention','next_date'].includes(event.field)) {
        showToast(`${deal.name} updated`, event.id);
      } else if (!initial && event.actor !== state.selfActor && event.field) {
        showToast(`${actorName(event.actor)} updated ${deal.name}`);
      }
    }
    if (state.client.getPendingConfirms) {
      const pending = await state.client.getPendingConfirms();
      state.confirms = pending.proposals || [];
      renderConfirms();
    }
    setSync(true);
    if (!userIsEditing()) renderBoardOnly();
  } catch (error) {
    console.warn('Deal Room poll failed', error);
    setSync(false);
  }
}

function userIsEditing() {
  return Boolean(document.querySelector('dialog[open]') || document.activeElement?.matches('input,textarea,select'));
}

function workspaceDeals() {
  let deals = [...state.deals.values()];
  if (state.query) {
    const query = state.query.toLowerCase();
    deals = deals.filter((deal) => [deal.name,deal.client_name,deal.account_name,deal.market,
      deal.market_agent,deal.next_step,deal.segment].some((value) => String(value || '').toLowerCase().includes(query)));
  } else if (state.workspace === 'team') {
    deals = deals.filter((deal) => deal.workspace_kind === 'team');
  } else if (state.accountId) {
    deals = deals.filter((deal) => deal.account_client_id === state.accountId);
  } else {
    deals = [];
  }
  if (state.filter === 'mine') deals = deals.filter((deal) => deal.owner === state.selfActor);
  if (state.filter === 'attention') deals = deals.filter((deal) => deal.attention || (daysFromNow(deal.next_date) ?? 0) < 0);
  if (state.filter === 'stale') deals = deals.filter(isStale);
  if (state.filter === 'missing') deals = deals.filter((deal) => !deal.next_step);
  if (state.filter === 'delta') deals = deals.filter((deal) => state.changed.has(deal.id));
  return deals.sort((a,b) => priority(b) - priority(a) || a.name.localeCompare(b.name));
}

function render() {
  renderChrome();
  renderAccounts();
  renderBoardOnly();
}

function renderChrome() {
  $$('.workspace').forEach((button) => button.classList.toggle('on', button.dataset.workspace === state.workspace));
  const selected = account();
  const isAccountHome = state.workspace === 'national_account' && !state.accountId && !state.query;
  $('#accountBack').hidden = !state.accountId;
  $('#workspaceEyebrow').textContent = state.query ? 'Global Deal Room search'
    : state.workspace === 'team' ? 'Shared territory pipeline' : selected ? 'National account agenda' : 'Partner-owned portfolios';
  $('#workspaceTitle').textContent = state.query ? 'Search results' : state.workspace === 'team' ? 'Team Book'
    : selected?.account_name || 'National Accounts';
  $('#workspaceSubtitle').textContent = state.query ? 'Searching territory and every national account.'
    : state.workspace === 'team' ? 'The live agenda Joe and Dell work together.'
    : selected ? `${actorName(selected.account_owner)} owns the account; each market deal keeps its assigned agent and owner.`
    : 'One account can hold dozens of market-level transactions without crowding the territory agenda.';
  $('#addButton').textContent = state.workspace === 'national_account' ? (selected ? 'Add market deal' : 'Add national account') : 'Add deal';
  $('#ownerButton').hidden = !selected;
  $('#agendaButton').hidden = isAccountHome;
  $('#agentHeading').textContent = state.workspace === 'national_account' ? 'Market agent' : 'Owner';
  $('#accountGrid').hidden = !isAccountHome;
  $('#boardSection').hidden = isAccountHome;
}

function renderAccounts() {
  const grid = $('#accountGrid');
  grid.innerHTML = state.accounts.length ? state.accounts.map((item) => `
    <button type="button" class="account-card" data-account="${esc(item.account_client_id)}">
      <header><div><p class="eyebrow">${esc(item.account_client_ref || 'National account')}</p><h2>${esc(item.account_name)}</h2></div>
        <span class="account-owner" title="Owned by ${esc(actorName(item.account_owner))}">${item.account_owner === 'dell' ? 'D' : item.account_owner === 'joe' ? 'J' : '?'}</span></header>
      <div class="account-metrics"><div><b>${Number(item.open_deals || 0)}</b><span>Open deals</span></div>
        <div><b>${Number(item.attention_deals || 0)}</b><span>Attention</span></div>
        <div><b>${Number(item.stale_deals || 0)}</b><span>Gone quiet</span></div></div>
      <footer>Last account review: ${esc(relative(item.last_review_at))} · Open agenda →</footer>
    </button>`).join('') : '<div class="empty">No national accounts yet. Add the first portfolio when it is won.</div>';
}

function renderBoardOnly() {
  if ($('#boardSection').hidden) return;
  const deals = workspaceDeals();
  renderStats(deals);
  renderFocus(deals);
  const rows = $('#rows');
  rows.innerHTML = deals.map(rowHtml).join('');
  $('#emptyState').hidden = deals.length > 0;
  $('#emptyState').textContent = state.query ? 'No deal matches this search.' : 'No deals match this filter.';
  applyPresence();
}

function renderStats(deals) {
  const overdue = deals.filter((deal) => (daysFromNow(deal.next_date) ?? 0) < 0).length;
  const clarity = deals.filter((deal) => !deal.next_step || isStale(deal)).length;
  $('#stats').innerHTML = `
    <div class="stat"><strong>${deals.length}</strong><span>Open deals</span></div>
    <div class="stat"><strong>${deals.filter((deal) => deal.phase === 'Closing').length}</strong><span>At closing</span></div>
    <div class="stat risk"><strong>${overdue}</strong><span>Overdue dates</span></div>
    <div class="stat"><strong>${clarity}</strong><span>Need clarity</span></div>`;
}

function renderFocus(deals) {
  const items = deals.filter((deal) => priority(deal) > 0).slice(0, 4);
  $('#focusStrip').innerHTML = items.length ? '<span class="focus-label">Focus first</span>' + items.map((deal) => `
    <button type="button" class="focus-item" data-open-deal="${esc(deal.id)}"><b>${esc(deal.name)}</b><span>${esc(reasonFor(deal))}</span></button>`).join('') : '';
}

function phaseOptions(current) {
  return PHASES.map((phase) => `<option${phase === current ? ' selected' : ''}>${esc(phase)}</option>`).join('');
}

function rowHtml(deal) {
  const days = daysFromNow(deal.next_date);
  const classes = [deal.attention ? 'attention' : '', days !== null && days < 0 ? 'overdue' : ''].join(' ');
  const partnerPresence = state.presence.find((lease) => lease.deal_id === deal.id && lease.actor !== state.selfActor && new Date(lease.expires_at) > new Date());
  const meta = [deal.market, deal.type, state.query && deal.account_name ? deal.account_name : null,
    partnerPresence ? `${actorName(partnerPresence.actor)} is editing` : null].filter(Boolean);
  return `<tr class="${classes}" data-deal-id="${esc(deal.id)}">
    <td><div class="deal-cell"><button type="button" class="attention-button" data-attention="${esc(deal.id)}" aria-pressed="${Boolean(deal.attention)}" aria-label="${deal.attention ? 'Clear attention flag' : 'Flag for attention'} on ${esc(deal.name)}">${deal.attention ? '⚠' : (PHICON[deal.phase] || '○')}</button>
      <div><button type="button" class="deal-link" data-open-deal="${esc(deal.id)}">${esc(deal.name)}</button>
      <div class="deal-meta">${meta.map((item) => `<span>${esc(item)}</span>`).join('<span>·</span>')}</div></div></div></td>
    <td><select class="cell-select" data-phase="${esc(deal.id)}" aria-label="Phase for ${esc(deal.name)}">${phaseOptions(deal.phase)}</select></td>
    <td><button type="button" class="step-button ${deal.next_step ? '' : 'empty'}" data-next-step="${esc(deal.id)}">${esc(deal.next_step || 'Set the next step…')}</button></td>
    <td><span class="due ${days !== null && days < 0 ? 'over' : days !== null && days <= 3 ? 'soon' : ''}">${esc(dateLabel(deal.next_date))}</span></td>
    <td>${deal.workspace_kind === 'national_account'
      ? `<button type="button" class="agent-button" data-market-agent="${esc(deal.id)}">${esc(deal.market_agent || 'Assign agent…')}</button>`
      : `<select class="cell-select" data-owner="${esc(deal.id)}" aria-label="Owner for ${esc(deal.name)}"><option value="">Unassigned</option><option value="joe"${deal.owner === 'joe' ? ' selected' : ''}>Joe</option><option value="dell"${deal.owner === 'dell' ? ' selected' : ''}>Dell</option></select>`}</td>
    <td class="row-actions"><button type="button" class="row-menu" data-open-deal="${esc(deal.id)}" aria-label="Open ${esc(deal.name)} details">•••</button></td>
  </tr>`;
}

function applyPresence() {
  for (const lease of state.presence) {
    if (lease.actor === state.selfActor || new Date(lease.expires_at) <= new Date()) continue;
    const row = document.querySelector(`[data-deal-id="${CSS.escape(lease.deal_id)}"]`);
    if (row) row.title = `${actorName(lease.actor)} is editing ${lease.field}`;
  }
}

function renderCaptureStatus() {
  const active = state.captureSessions.find((session) => !['completed','failed','cancelled'].includes(session.state));
  const badge = $('#captureStatus');
  badge.hidden = !active;
  if (active) badge.textContent = `Capture: ${String(active.state).replaceAll('_',' ')}`;
}

function renderConfirms() {
  const dock = $('#confirmDock');
  dock.hidden = !state.confirms.length;
  dock.innerHTML = state.confirms.map((proposal) => `<span class="confirm-chip" data-proposal="${esc(proposal.id)}">${esc(proposal.label)}
    <button type="button" class="yes" data-confirm="yes">Confirm</button><button type="button" class="skip-confirm" data-confirm="no">Skip</button></span>`).join('');
}

async function patchField(dealId, field, value) {
  const result = await state.client.patchDealField({ deal:dealId, field, value,
    base_event_id:state.fieldBase.get(`${dealId}|${field}`) || null, idempotency_key:uuidv4() });
  if (result.status === 'conflict') return showConflict(result.conflict);
  const deal = state.deals.get(dealId);
  if (deal) deal[field] = value;
  state.changed.add(dealId);
  renderBoardOnly();
  showToast(`${deal?.name || 'Deal'} updated`);
}

function openForm({ eyebrow='Deal Room', title, submit='Save', body, onSubmit }) {
  const dialog = $('#formDialog');
  $('#dialogEyebrow').textContent = eyebrow;
  $('#dialogTitle').textContent = title;
  $('#dialogSubmit').textContent = submit;
  $('#dialogBody').innerHTML = body;
  $('#formError').hidden = true;
  $('#dialogForm').onsubmit = async (event) => {
    event.preventDefault();
    const button = $('#dialogSubmit');
    button.disabled = true;
    $('#formError').hidden = true;
    try {
      await onSubmit(new FormData(event.currentTarget));
      dialog.close();
    } catch (error) {
      const detail = error.payload?.hint || error.payload?.error || error.message;
      $('#formError').textContent = detail;
      $('#formError').hidden = false;
    } finally { button.disabled = false; }
  };
  dialog.showModal();
  setTimeout(() => $('input,textarea,select', dialog)?.focus(), 0);
}

function nextStepForm(dealId) {
  const deal = state.deals.get(dealId);
  openForm({ title:`Next step — ${deal.name}`, submit:'Set next step', body:`
    <div class="field"><label for="stepText">What happens next?</label><textarea id="stepText" name="text" required>${esc(deal.next_step || '')}</textarea><small>This becomes a real next action in today’s triage; the prior step stays in history.</small></div>
    <div class="field"><label for="stepDate">When?</label><input id="stepDate" name="next_date" type="date" value="${esc(deal.next_date || '')}"></div>`,
    onSubmit:async (data) => {
      const text = String(data.get('text') || '').trim();
      await state.client.setNextStep({ deal:dealId, text, next_date:data.get('next_date') || null, idempotency_key:uuidv4() });
      deal.next_step = text; deal.next_date = data.get('next_date') || null; state.changed.add(dealId);
      showToast(`Next step set on ${deal.name}`); renderBoardOnly();
    } });
}

function marketAgentForm(dealId) {
  const deal = state.deals.get(dealId);
  openForm({ title:`Market assignment — ${deal.name}`, submit:'Save assignment', body:`
    <div class="field"><label for="agentName">Assigned local agent</label><input id="agentName" name="agent_name" required value="${esc(deal.market_agent || '')}" placeholder="Agent’s full name"></div>
    <div class="field"><label for="agentMarket">Market</label><input id="agentMarket" name="market" value="${esc(deal.market || '')}" placeholder="City, state"></div>`,
    onSubmit:async (data) => {
      await state.client.setMarketAgent({ deal:dealId, agent_name:data.get('agent_name'), market:data.get('market') || null,
        source:'partner stated in Deal Room', idempotency_key:uuidv4() });
      deal.market_agent = data.get('agent_name'); if (data.get('market')) deal.market = data.get('market');
      renderBoardOnly(); showToast(`Market agent saved on ${deal.name}`);
    } });
}

function addTeamDealForm() {
  openForm({ title:'Add territory deal', submit:'Create deal', body:`
    <div class="field"><label for="clientRef">Existing client</label><input id="clientRef" name="client" required placeholder="C-127 or exact client name"><small>A deal always belongs to a client. This prevents free-floating or duplicate records.</small></div>
    <div class="field"><label for="dealName">Deal name</label><input id="dealName" name="name" required></div>
    <div class="field-row"><div class="field"><label for="dealType">Type</label><select id="dealType" name="deal_type"><option value="startup">Startup</option><option value="relocation">Relocation</option><option value="additional_office">Additional office</option><option value="renewal">Renewal</option><option value="expansion">Expansion</option><option value="purchase">Purchase</option><option value="other">Other</option></select></div>
    <div class="field"><label for="dealPhase">Phase</label><select id="dealPhase" name="phase">${phaseOptions('On Deck')}</select></div></div>
    <div class="field-row"><div class="field"><label for="dealMarket">Market</label><input id="dealMarket" name="market"></div><div class="field"><label for="dealSegment">Healthcare vertical</label><input id="dealSegment" name="segment" placeholder="Dental, Vet, DPC…"></div></div>`,
    onSubmit:async (data) => {
      const args = Object.fromEntries(data.entries());
      await state.client.createDeal({ ...args, lane:'territory', idempotency_key:uuidv4() });
      await loadHome(); showToast('Deal created in the Team Book');
    } });
}

function addAccountForm() {
  openForm({ eyebrow:'National accounts', title:'Add national account', submit:'Create account', body:`
    <div class="field"><label for="accountName">Brand / organization</label><input id="accountName" name="name" required><small>Creates one parent account. Market transactions will live under their own sub-clients.</small></div>
    <div class="field-row"><div class="field"><label for="accountOwner">Account owner</label><select id="accountOwner" name="owner"><option value="${esc(state.selfActor)}">${esc(actorName(state.selfActor))}</option><option value="${state.selfActor === 'joe' ? 'dell' : 'joe'}">${esc(actorName(state.selfActor === 'joe' ? 'dell' : 'joe'))}</option></select></div>
    <div class="field"><label for="accountVertical">Healthcare vertical</label><input id="accountVertical" name="vertical"></div></div>`,
    onSubmit:async (data) => { await state.client.createNationalAccount({ ...Object.fromEntries(data.entries()), idempotency_key:uuidv4() }); await loadHome(); showToast('National account created'); } });
}

function addMarketDealForm() {
  const selected = account();
  openForm({ eyebrow:selected.account_name, title:'Add market transaction', submit:'Create market deal', body:`
    <div class="field"><label for="marketClient">Franchisee / local client</label><input id="marketClient" name="client_name" required><small>Reuses the exact sub-client if it exists; otherwise creates one under ${esc(selected.account_name)}.</small></div>
    <div class="field"><label for="marketDealName">Deal name</label><input id="marketDealName" name="deal_name" required></div>
    <div class="field-row"><div class="field"><label for="marketCity">Market</label><input id="marketCity" name="market" required placeholder="City"></div><div class="field"><label for="marketState">State</label><input id="marketState" name="state" maxlength="2"></div></div>
    <div class="field-row"><div class="field"><label for="marketAgent">Assigned agent</label><input id="marketAgent" name="agent_name" placeholder="Leave blank if unknown"></div><div class="field"><label for="marketSegment">Healthcare vertical</label><input id="marketSegment" name="segment"></div></div>
    <input type="hidden" name="deal_type" value="startup"><input type="hidden" name="phase" value="pending">`,
    onSubmit:async (data) => { await state.client.createNationalMarketDeal({ account_client_id:selected.account_client_id,
      ...Object.fromEntries(data.entries()), idempotency_key:uuidv4() }); await loadHome(); showToast(`Market deal added to ${selected.account_name}`); } });
}

function accountOwnerForm() {
  const selected = account();
  openForm({ eyebrow:selected.account_name, title:'Change account owner', submit:'Save owner', body:`
    <div class="field"><label for="newAccountOwner">Accountable partner</label><select id="newAccountOwner" name="owner"><option value="joe"${selected.account_owner === 'joe' ? ' selected' : ''}>Joe</option><option value="dell"${selected.account_owner === 'dell' ? ' selected' : ''}>Dell</option></select><small>This changes portfolio accountability only. Individual market-deal owners stay untouched.</small></div>`,
    onSubmit:async (data) => { await state.client.setNationalAccountOwner({ account_client_id:selected.account_client_id,
      owner:data.get('owner'), idempotency_key:uuidv4() }); await loadHome(); showToast(`${selected.account_name} owner updated`); } });
}

function showConflict(conflict) {
  openForm({ eyebrow:'Two edits crossed', title:`Choose the value for ${conflict.field}`, submit:'Keep selected value', body:`
    <div class="field"><label><input type="radio" name="winner" value="a" checked> ${esc(actorName(conflict.a.actor))}: ${esc(conflict.a.value ?? '(empty)')}</label></div>
    <div class="field"><label><input type="radio" name="winner" value="b"> ${esc(actorName(conflict.b.actor))}: ${esc(conflict.b.value ?? '(empty)')}</label></div>`,
    onSubmit:async (data) => { await state.client.resolveConflict({ conflict_id:conflict.conflict_id, winner:data.get('winner'), idempotency_key:uuidv4() }); await loadHome(); showToast('Conflict resolved with both values preserved in history'); } });
}

function detailRows(items, renderer, empty='Nothing captured yet.') {
  return items?.length ? items.map(renderer).join('') : `<div class="detail-row">${esc(empty)}</div>`;
}

async function openDeal(dealId) {
  const detail = await state.client.getDeal(dealId);
  const deal = detail.deal;
  const html = `<header><div><p class="eyebrow">${esc(deal.account_name || deal.client_name || 'Deal')}</p><h2>${esc(deal.name)}</h2><p class="subhead">${esc(deal.phase)} · ${esc(deal.market || 'Market not captured')}</p></div><button type="button" class="icon-button" data-close-deal aria-label="Close details">×</button></header>
    <div class="deal-content"><div class="deal-summary">
      <div class="detail-card"><label>Next step</label><p>${esc(deal.next_step || 'Not set')}</p></div>
      <div class="detail-card"><label>Next date</label><p>${esc(dateLabel(deal.next_date))}</p></div>
      <div class="detail-card"><label>${deal.workspace_kind === 'national_account' ? 'Market agent' : 'Owner'}</label><p>${esc(deal.market_agent || actorName(deal.owner))}</p></div>
      <div class="detail-card"><label>Last touch</label><p>${esc(relative(deal.last_touch))}</p></div></div>
      <section class="detail-section"><h3>Open next actions</h3><div class="detail-list">${detailRows((detail.next_actions || []).filter((a) => a.status === 'open'), (a) => `<div class="detail-row"><b>${esc(a.description)}</b><small>${esc(actorName(a.owner))} · ${esc(dateLabel(a.due_on))}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Critical dates</h3><div class="detail-list">${detailRows(detail.critical_dates, (d) => `<div class="detail-row"><b>${esc(d.label || d.kind)}</b><small>${esc(dateLabel(d.date || d.due_on))} · source: ${esc(d.source || 'not captured')}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Premises</h3><div class="detail-list">${detailRows(detail.premises, (p) => `<div class="detail-row"><b>${esc(p.label)}</b><small>${esc([p.address,p.suite,p.city,p.state].filter(Boolean).join(' · '))}${p.area_amount ? ` · ${esc(p.area_amount)} ${esc(p.area_basis || 'SF')}` : ''}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Negotiation rounds</h3><div class="detail-list">${detailRows(detail.negotiation_rounds, (n) => `<div class="detail-row"><b>Round ${esc(n.round_no)} · ${esc(n.side)}</b><small>${esc(n.rate_amount ? `${n.rate_amount} ${n.rate_basis || ''}` : 'Rate not captured')} · ${esc(n.term_months ? `${n.term_months} months` : 'Term not captured')}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Participants</h3><div class="detail-list">${detailRows(detail.participants, (p) => `<div class="detail-row"><b>${esc(p.name)}</b><small>${esc(String(p.role).replaceAll('_',' '))}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Recent activity</h3><div class="detail-list">${detailRows(detail.activities, (a) => `<div class="detail-row"><b>${esc(a.summary)}</b><small>${esc(actorName(a.actor))} · ${esc(relative(a.occurred_at))} · ${esc(a.kind)}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Notes and prior next steps</h3><div class="detail-list">${detailRows(detail.thread, (n) => `<div class="detail-row">${esc(n.text)}<small>${esc(actorName(n.actor))} · ${esc(n.kind === 'archived_step' ? 'prior next step' : 'note')}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Documents</h3><div class="detail-list">${detailRows(detail.documents, (d) => `<div class="detail-row"><b>${esc(String(d.sent_status).replaceAll('_',' '))}</b><small>Prepared ${esc(relative(d.prepared_at))} · lint ${d.lint_passed ? 'passed' : 'not confirmed'} · leak check ${d.leak_check_passed ? 'passed' : 'not confirmed'}</small></div>`)}</div></section>
      <section class="detail-section"><h3>Change history</h3><div class="detail-list">${detailRows(detail.history, (h) => `<div class="detail-row">${esc(h.summary)}<small>${esc(actorName(h.actor))} · ${esc(relative(h.recorded_at))}</small></div>`)}</div></section>
    </div>`;
  $('#dealDetail').innerHTML = html;
  $('#dealDialog').showModal();
}

function agendaDeals() {
  return workspaceDeals().sort((a,b) => priority(b) - priority(a));
}

async function startAgenda() {
  const deals = agendaDeals();
  if (!deals.length) return showToast('No deals in this agenda');
  const result = await state.client.startReview({ workspace_kind:state.workspace,
    ...(state.accountId ? { account_client_id:state.accountId } : {}), idempotency_key:uuidv4() });
  state.review = { sessionId:result.session_id, deals, index:0, reviewed:0, skipped:0 };
  $('#agendaPanel').hidden = false;
  renderAgenda();
}

function renderAgenda() {
  const review = state.review;
  if (!review) return;
  const deal = review.deals[review.index];
  $('#agendaTitle').textContent = state.workspace === 'team' ? 'Team Book' : account()?.account_name || 'National account';
  $('#agendaProgress').textContent = `${Math.min(review.index + 1,review.deals.length)} of ${review.deals.length}`;
  $('#agendaMeter').max = review.deals.length;
  $('#agendaMeter').value = review.index;
  if (!deal) return finishAgenda();
  $('#agendaCard').innerHTML = `<article class="agenda-deal"><p class="eyebrow">${esc(deal.market || deal.client_name || '')}</p><h3>${esc(deal.name)}</h3>
    <span class="agenda-reason">${esc(reasonFor(deal))}</span>
    <div class="agenda-fact"><label>Phase</label><p>${esc(deal.phase)}</p></div>
    <div class="agenda-fact"><label>Next step</label><p>${esc(deal.next_step || 'Not set')}</p></div>
    <div class="agenda-fact"><label>Next date</label><p>${esc(dateLabel(deal.next_date))}</p></div>
    ${deal.workspace_kind === 'national_account' ? `<div class="agenda-fact"><label>Assigned market agent</label><p>${esc(deal.market_agent || 'Unassigned')}</p></div>` : ''}
    <button type="button" class="secondary" data-open-deal="${esc(deal.id)}">Open full deal</button>
    <button type="button" class="secondary" data-agenda-step="${esc(deal.id)}">Set next step</button></article>`;
}

async function advanceAgenda(disposition) {
  const review = state.review;
  const deal = review?.deals[review.index];
  if (!review || !deal) return;
  await state.client.reviewDeal({ session_id:review.sessionId, deal:deal.id, disposition, idempotency_key:uuidv4() });
  review[disposition === 'reviewed' ? 'reviewed' : 'skipped'] += 1;
  review.index += 1;
  renderAgenda();
}

async function finishAgenda(status = 'completed') {
  if (!state.review) return;
  const review = state.review;
  const result = await state.client.endReview({ session_id:review.sessionId, status, idempotency_key:uuidv4() });
  state.review = null;
  $('#agendaPanel').hidden = true;
  await loadHome();
  showToast(status === 'completed' ? `Agenda finished · ${result.reviewed ?? review.reviewed} deals reviewed` : 'Agenda closed without changing the review clock');
}

function wireEvents() {
  document.addEventListener('click', async (event) => {
    const workspace = event.target.closest('[data-workspace]');
    if (workspace) { state.workspace = workspace.dataset.workspace; state.accountId = null; state.filter = 'all'; state.query = ''; $('#search').value = ''; render(); return; }
    const accountButton = event.target.closest('[data-account]');
    if (accountButton) { state.workspace = 'national_account'; state.accountId = accountButton.dataset.account; render(); return; }
    const open = event.target.closest('[data-open-deal]'); if (open) { await openDeal(open.dataset.openDeal); return; }
    const attention = event.target.closest('[data-attention]'); if (attention) { const deal=state.deals.get(attention.dataset.attention); await patchField(deal.id,'attention',!deal.attention); return; }
    const step = event.target.closest('[data-next-step],[data-agenda-step]'); if (step) { nextStepForm(step.dataset.nextStep || step.dataset.agendaStep); return; }
    const agent = event.target.closest('[data-market-agent]'); if (agent) { marketAgentForm(agent.dataset.marketAgent); return; }
    const filter = event.target.closest('[data-filter]'); if (filter) { state.filter=filter.dataset.filter; $$('.filter').forEach((b)=>b.classList.toggle('on',b===filter)); renderBoardOnly(); return; }
    if (event.target.closest('[data-close-deal]')) { $('#dealDialog').close(); return; }
    if (event.target.closest('[data-undo]') && state.undoEventId) { await state.client.revertDealField({ event_id:state.undoEventId,idempotency_key:uuidv4() }); state.undoEventId=null; await loadHome(); showToast('Change undone'); return; }
    const confirm = event.target.closest('[data-confirm]'); if (confirm) { const chip=confirm.closest('[data-proposal]'); const yes=confirm.dataset.confirm==='yes'; await state.client.resolveConfirm({proposal_id:chip.dataset.proposal,accept:yes,idempotency_key:uuidv4()}); state.confirms=state.confirms.filter((p)=>p.id!==chip.dataset.proposal); renderConfirms(); if(yes)await loadHome(); showToast(yes?'Suggestion confirmed':'Suggestion skipped'); return; }
    if (event.target.closest('[data-dialog-cancel]')) { $('#formDialog').close(); return; }
  });

  document.addEventListener('change', async (event) => {
    if (event.target.matches('[data-phase]')) await patchField(event.target.dataset.phase,'phase',event.target.value);
    if (event.target.matches('[data-owner]')) await patchField(event.target.dataset.owner,'owner',event.target.value || null);
  });

  $('#search').addEventListener('input', (event) => { state.query=event.target.value.trim(); render(); });
  $('#accountBack').onclick = () => { state.accountId=null; render(); };
  $('#addButton').onclick = () => state.workspace === 'team' ? addTeamDealForm() : state.accountId ? addMarketDealForm() : addAccountForm();
  $('#ownerButton').onclick = accountOwnerForm;
  $('#agendaButton').onclick = startAgenda;
  $('#agendaReviewed').onclick = () => advanceAgenda('reviewed');
  $('#agendaSkip').onclick = () => advanceAgenda('skipped');
  $('#agendaEnd').onclick = () => finishAgenda('completed');
  $('#agendaClose').onclick = () => finishAgenda('abandoned');
  $('#themeButton').onclick = () => { document.body.classList.toggle('night'); localStorage.setItem('dealroom-theme',document.body.classList.contains('night')?'night':'light'); };
  window.addEventListener('online', () => { $('#offlineBanner').hidden=true; setSync(true); pollOnce(); });
  window.addEventListener('offline', () => { $('#offlineBanner').hidden=false; setSync(false,'Offline'); });
  document.addEventListener('keydown', (event) => { if (event.key==='/' && !event.target.matches('input,textarea,select')) { event.preventDefault(); $('#search').focus(); } });
}

async function boot() {
  if (localStorage.getItem('dealroom-theme') === 'night') document.body.classList.add('night');
  const params = new URLSearchParams(location.search);
  const requested = params.get('mode');
  const mode = requested === 'fixture' ? 'fixture' : requested === 'live' ? 'live'
    : location.hostname === 'dealroom.doctorcre.com' ? 'live' : 'fixture';
  state.client = await createClient(mode, { baseUrl:params.get('api') || undefined,
    selfActor:params.get('actor') || undefined });
  wireEvents();
  await loadHome();
  state.pollTimer = setInterval(() => pollOnce(), POLL_MS);
  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').catch(()=>{});
}

boot().catch((error) => {
  console.error(error);
  document.body.insertAdjacentHTML('afterbegin', `<div class="offline">Deal Room could not start: ${esc(error.message)}</div>`);
});

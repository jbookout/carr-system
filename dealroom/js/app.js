import { createClient, PHASES, PHICON, ACTOR_LABEL } from './client.js';
import { resolveDealroomBoot } from './boot-mode.js';
import { uuidv4 } from './uuid.js';
import { createPostCallClient } from './post-call-client.js';

const POLL_MS = 1400;
const CALL_MODE_URL = 'http://127.0.0.1:4682';
const CALL_MODE_HEADER = { 'X-CARR-Call-Mode': 'deal-room-v1' };
const state = {
  client: null, selfActor: null, deals: new Map(), accounts: [], cursor: null,
  workspace: 'team', accountId: null, filter: 'active', query: '',
  changed: new Set(), fieldBase: new Map(), presence: [], captureSessions: [],
  confirms: [], review: null, pollTimer: null, undoEventId: null,
  callMode: { state: 'idle' }, callModeTimer: null,
  postCallClient: null, postCallTimer: null,
  postCall: { status: 'idle', session: null, report: null, error: null,
    contextReady: false, draftErrors: new Map() },
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

function parkingReasonLabel(reason) {
  return ({
    prospect_never_active: 'Prospect never became active work',
    client_paused: 'Client paused activity',
    other: 'Not active right now',
  })[reason] || 'Parked';
}

function reasonFor(deal) {
  if (deal.operating_state === 'parked') return parkingReasonLabel(deal.parking_reason);
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
      if (event.field === 'operating_state') {
        const value = event.new_value || {};
        deal.operating_state = value.state || 'active';
        deal.parking_reason = value.reason || null;
        deal.parking_note = value.note || null;
      }
      if (!initial && event.actor === state.selfActor && ['phase','owner','attention','next_date','operating_state'].includes(event.field)) {
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
  if (state.filter === 'parked') deals = deals.filter((deal) => deal.operating_state === 'parked');
  else deals = deals.filter((deal) => (deal.operating_state || 'active') === 'active');
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
  $$('.filter').forEach((button) => button.classList.toggle('on', button.dataset.filter === state.filter));
  const selected = account();
  const isAccountHome = state.workspace === 'national_account' && !state.accountId && !state.query;
  $('#accountBack').hidden = !state.accountId;
  $('#workspaceEyebrow').textContent = state.query ? 'Global Deal Room search'
    : state.workspace === 'team' ? 'Shared territory pipeline' : selected ? 'National account agenda' : 'Partner-owned portfolios';
  $('#workspaceTitle').textContent = state.query ? 'Search results' : state.workspace === 'team' ? 'Team Book'
    : selected?.account_name || 'National Accounts';
  $('#workspaceSubtitle').textContent = state.query ? 'Searching work records across the territory and every national account.'
    : state.workspace === 'team' ? 'The active work Joe and Dell are moving now.'
    : selected ? `${actorName(selected.account_owner)} owns the account; each market deal keeps its assigned agent and owner.`
    : 'One account can hold dozens of market-level transactions without crowding the territory agenda.';
  const addLabel = state.workspace === 'national_account' ? (selected ? 'Add market deal' : 'Add national account') : 'Add work record';
  $('#stickyAddButton').textContent = `+ ${addLabel}`;
  $('#stickyAddButton').setAttribute('aria-label', addLabel);
  $('#ownerButton').hidden = !selected;
  $('#agendaButton').hidden = isAccountHome;
  const agentHeading = state.workspace === 'national_account' ? 'Market agent' : 'Owner';
  $('#agentHeading').textContent = agentHeading;
  $('#stickyAgentHeading').textContent = agentHeading;
  $('#accountGrid').hidden = !isAccountHome;
  $('#boardSection').hidden = isAccountHome;
}

function renderAccounts() {
  const grid = $('#accountGrid');
  grid.innerHTML = state.accounts.length ? state.accounts.map((item) => `
    <button type="button" class="account-card" data-account="${esc(item.account_client_id)}">
      <header><div><p class="eyebrow">${esc(item.account_client_ref || 'National account')}</p><h2>${esc(item.account_name)}</h2></div>
        <span class="account-owner" title="Owned by ${esc(actorName(item.account_owner))}">${item.account_owner === 'dell' ? 'D' : item.account_owner === 'joe' ? 'J' : '?'}</span></header>
      <div class="account-metrics"><div><b>${Number(item.open_deals || 0)}</b><span>Active work</span></div>
        <div><b>${Number(item.attention_deals || 0)}</b><span>Attention</span></div>
        <div><b>${Number(item.stale_deals || 0)}</b><span>Gone quiet</span></div></div>
      <footer>${Number(item.parked_deals || 0)} parked · Last account review: ${esc(relative(item.last_review_at))} · Open agenda →</footer>
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
  $('#emptyState').textContent = state.query ? 'No work record matches this search.'
    : state.filter === 'parked' ? 'No parked work records.' : 'No active work matches this filter.';
  applyPresence();
}

function renderStats(deals) {
  if (state.filter === 'parked') {
    $('#stats').innerHTML = `
      <div class="stat"><strong>${deals.length}</strong><span>Parked records</span></div>
      <div class="stat"><strong>${deals.filter((deal) => deal.parking_reason === 'prospect_never_active').length}</strong><span>Never activated</span></div>
      <div class="stat"><strong>${deals.filter((deal) => deal.parking_reason === 'client_paused').length}</strong><span>Client paused</span></div>
      <div class="stat"><strong>${deals.filter((deal) => deal.parking_reason === 'other').length}</strong><span>Other reason</span></div>`;
    return;
  }
  const overdue = deals.filter((deal) => (daysFromNow(deal.next_date) ?? 0) < 0).length;
  const clarity = deals.filter((deal) => !deal.next_step || isStale(deal)).length;
  $('#stats').innerHTML = `
    <div class="stat"><strong>${deals.length}</strong><span>Active work</span></div>
    <div class="stat"><strong>${deals.filter((deal) => deal.phase === 'Closing').length}</strong><span>At closing</span></div>
    <div class="stat risk"><strong>${overdue}</strong><span>Overdue dates</span></div>
    <div class="stat"><strong>${clarity}</strong><span>Need clarity</span></div>`;
}

function renderFocus(deals) {
  if (state.filter === 'parked') { $('#focusStrip').innerHTML = ''; return; }
  const items = deals.filter((deal) => priority(deal) > 0).slice(0, 4);
  $('#focusStrip').innerHTML = items.length ? '<span class="focus-label">Focus first</span>' + items.map((deal) => `
    <button type="button" class="focus-item" data-open-deal="${esc(deal.id)}"><b>${esc(deal.name)}</b><span>${esc(reasonFor(deal))}</span></button>`).join('') : '';
}

function phaseOptions(current) {
  return PHASES.map((phase) => `<option${phase === current ? ' selected' : ''}>${esc(phase)}</option>`).join('');
}

function rowHtml(deal) {
  const days = daysFromNow(deal.next_date);
  const parked = deal.operating_state === 'parked';
  const classes = [parked ? 'parked' : '', deal.attention ? 'attention' : '', days !== null && days < 0 ? 'overdue' : ''].join(' ');
  const partnerPresence = state.presence.find((lease) => lease.deal_id === deal.id && lease.actor !== state.selfActor && new Date(lease.expires_at) > new Date());
  const meta = [deal.market, deal.type, state.query && deal.account_name ? deal.account_name : null,
    parked ? parkingReasonLabel(deal.parking_reason) : null,
    partnerPresence ? `${actorName(partnerPresence.actor)} is editing` : null].filter(Boolean);
  return `<tr class="${classes}" data-deal-id="${esc(deal.id)}">
    <td><div class="deal-cell"><button type="button" class="attention-button" data-attention="${esc(deal.id)}" aria-pressed="${Boolean(deal.attention)}" aria-label="${deal.attention ? 'Clear attention flag' : 'Flag for attention'} on ${esc(deal.name)}"${parked ? ' disabled' : ''}>${deal.attention ? '⚠' : (PHICON[deal.phase] || '○')}</button>
      <div><button type="button" class="deal-link" data-open-deal="${esc(deal.id)}">${esc(deal.name)}</button>
      <div class="deal-meta">${meta.map((item) => `<span>${esc(item)}</span>`).join('<span>·</span>')}</div></div></div></td>
    <td><select class="cell-select" data-phase="${esc(deal.id)}" aria-label="Phase for ${esc(deal.name)}"${parked ? ' disabled' : ''}>${phaseOptions(deal.phase)}</select></td>
    <td><button type="button" class="step-button ${deal.next_step ? '' : 'empty'}" data-next-step="${esc(deal.id)}"${parked ? ' disabled' : ''}>${esc(deal.next_step || 'Set the next step…')}</button></td>
    <td><span class="due ${days !== null && days < 0 ? 'over' : days !== null && days <= 3 ? 'soon' : ''}">${esc(dateLabel(deal.next_date))}</span></td>
    <td>${deal.workspace_kind === 'national_account'
      ? `<button type="button" class="agent-button" data-market-agent="${esc(deal.id)}"${parked ? ' disabled' : ''}>${esc(deal.market_agent || 'Assign agent…')}</button>`
      : `<select class="cell-select" data-owner="${esc(deal.id)}" aria-label="Owner for ${esc(deal.name)}"${parked ? ' disabled' : ''}><option value="">Unassigned</option><option value="joe"${deal.owner === 'joe' ? ' selected' : ''}>Joe</option><option value="dell"${deal.owner === 'dell' ? ' selected' : ''}>Dell</option></select>`}</td>
    <td class="row-actions"><button type="button" class="park-button" data-operating-state="${parked ? 'active' : 'parked'}" data-deal="${esc(deal.id)}">${parked ? 'Restore' : 'Park'}</button><button type="button" class="row-menu" data-open-deal="${esc(deal.id)}" aria-label="Open ${esc(deal.name)} details">•••</button></td>
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
  const active = state.captureSessions.find((session) => !['done','completed','failed','cancelled'].includes(session.state));
  const badge = $('#captureStatus');
  badge.hidden = !active;
  if (active) badge.textContent = `Capture: ${String(active.state).replaceAll('_',' ')}`;
}

function elapsedTime(startedAt) {
  if (!startedAt) return '0:00';
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(startedAt)) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function callModeActive(snapshot = state.callMode) {
  return snapshot?.state === 'recording';
}

function renderCallMode() {
  const snapshot = state.callMode || { state: 'idle' };
  const recording = callModeActive(snapshot);
  const processing = ['transcribing', 'ready_to_extract', 'filed'].includes(snapshot.state);
  const stage = $('#callModeStage');
  if (!stage) return;
  stage.classList.toggle('recording', recording);
  stage.classList.toggle('processing', processing);
  $('#callModeStarts').hidden = recording || processing;
  $('#callModeConsentRow').hidden = recording || processing;
  $('#callModeStop').hidden = !recording;
  $('#callModeTimer').textContent = recording ? elapsedTime(snapshot.started_at) : ({
    idle: 'Ready', transcribing: 'Processing', ready_to_extract: 'Transcript ready', filed: 'Summary saved', state_unknown: 'Check Quill',
  }[snapshot.state] || 'Ready');
  $('#callModeState').textContent = recording ? 'Recording live' : ({
    transcribing: 'Quill is processing this call', ready_to_extract: 'Transcript ready for extraction',
    filed: 'Meeting summary saved', state_unknown: 'Recorder state needs attention',
  }[snapshot.state] || 'Ready to record');
  $('#callModeDetail').textContent = recording ? 'Quill is recording separate local and other-side audio tracks.'
    : processing ? 'The recording has stopped. Quill is preparing the local transcript for the review pipeline.'
      : 'Start a weekly deal call or another conversation. Quill keeps the local and other-side tracks separate.';
  const labels = snapshot.speaker_labels || {};
  const speakers = $('#callModeSpeakers');
  speakers.hidden = !labels.mic;
  speakers.textContent = labels.mic ? `${labels.mic} on microphone · ${labels.system || 'Other participant'} on system audio` : '';
  const toolbarButton = $('#callModeButton');
  toolbarButton.classList.toggle('recording', recording);
  toolbarButton.innerHTML = recording
    ? `<span aria-hidden="true">●</span> ${elapsedTime(snapshot.started_at)}`
    : '<span aria-hidden="true">✦</span> Call Mode';
  toolbarButton.setAttribute('aria-label', recording ? `Call Mode recording ${elapsedTime(snapshot.started_at)}` : 'Open Call Mode');
  renderPostCall();
}

function showCallModePermission() {
  const message = 'Chrome needs one-time Local Network Access permission to reach Quill on this Mac. Allow the prompt, then retry here. The standalone controller remains available if the local bridge itself needs checking.';
  const notice = $('#callModePermission');
  notice.textContent = message;
  notice.hidden = false;
}

async function callModeApi(path, body = null) {
  const options = body ? {
    method: 'POST', headers: { 'content-type': 'application/json', ...CALL_MODE_HEADER }, body: JSON.stringify(body), targetAddressSpace: 'loopback',
  } : { method: 'GET', targetAddressSpace: 'loopback' };
  let response;
  try {
    response = await fetch(`${CALL_MODE_URL}/api/${path}`, options);
  } catch (error) {
    showCallModePermission();
    throw new Error('Call Mode could not reach Quill locally.');
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Call Mode could not complete that action.');
  return payload;
}

function postCallItemStatus(item) {
  return item.candidate_status || item.status || (item.candidate_id ? 'pending' : 'needs_review');
}

function reportText(value) {
  return String(value || '')
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/giu, 'unmatched record')
    .replace(/\bP-\d+\b/giu, 'unmatched participant');
}

function postCallDealName(item) {
  return item.deal_name || state.deals.get(item.deal_id)?.name || 'Work record';
}

function taskCard(item, owner) {
  const status = postCallItemStatus(item);
  const pending = status === 'pending';
  const text = item.action || item.title || item.text || item.summary || 'Action needs review';
  return `<article class="post-call-card" data-post-call-item="${esc(item.candidate_id || '')}">
    <div class="post-call-card-head"><b>${esc(postCallDealName(item))}</b><span class="post-call-badge ${esc(status)}">${esc(status.replaceAll('_',' '))}</span></div>
    <p>${esc(reportText(text))}</p>${item.due_on ? `<small>Due ${esc(dateLabel(item.due_on))}</small>` : ''}
    ${pending ? `<div class="post-call-card-actions"><button type="button" class="primary" data-post-call-confirm="${esc(item.candidate_id)}" data-candidate-resolver="post_call">Confirm ${esc(owner)} task</button><button type="button" class="secondary" data-post-call-skip="${esc(item.candidate_id)}" data-candidate-resolver="post_call">Skip</button></div>` : ''}
  </article>`;
}

function dealUpdateCard(item) {
  const status = postCallItemStatus(item);
  const pending = status === 'pending';
  const summary = item.update || item.summary || item.text || item.action || 'Deal update needs review';
  return `<article class="post-call-card" data-post-call-item="${esc(item.candidate_id || '')}">
    <div class="post-call-card-head"><b>${esc(postCallDealName(item))}</b><span class="post-call-badge ${esc(status)}">${esc(status.replaceAll('_',' '))}</span></div>
    <p>${esc(reportText(summary))}</p>
    ${pending ? `<div class="post-call-card-actions"><button type="button" class="primary" data-post-call-confirm="${esc(item.candidate_id)}" data-candidate-resolver="${['assigned_action','email_draft'].includes(item.candidate_kind || item.kind) || item.candidate_table === 'capture_post_call_candidate' ? 'post_call' : 'legacy'}">Confirm update</button><button type="button" class="secondary" data-post-call-skip="${esc(item.candidate_id)}" data-candidate-resolver="${['assigned_action','email_draft'].includes(item.candidate_kind || item.kind) || item.candidate_table === 'capture_post_call_candidate' ? 'post_call' : 'legacy'}">Skip</button></div>` : ''}
  </article>`;
}

function questionCard(item) {
  const question = typeof item === 'string' ? item : (item.question || item.text || item.summary || 'Needs review');
  const options = Array.isArray(item?.options) ? item.options : [];
  return `<article class="post-call-card question"><p>${esc(reportText(question))}</p>${options.length ? `<div class="post-call-options" aria-label="Possible answers">${options.map((option) => `<span>${esc(reportText(typeof option === 'string' ? option : option.label || option.text))}</span>`).join('')}</div>` : ''}</article>`;
}

function draftCard(draft) {
  const status = draft.status || draft.candidate_status || 'pending';
  const created = ['created','already_created','draft_created'].includes(status) || draft.idempotent === true;
  const skipped = status === 'skipped';
  const awaitingReceipt = !draft.candidate_id && !created;
  const busyError = state.postCall.draftErrors.get(draft.draft_id);
  const recipient = draft.recipient_name || 'Recipient needs review';
  return `<article class="post-call-card vendor-draft" data-post-call-draft-card="${esc(draft.draft_id)}">
    <div class="post-call-card-head"><div><b>${esc(postCallDealName(draft))}</b><small>${esc(recipient)}${draft.recipient_email ? ` · ${esc(draft.recipient_email)}` : ''}</small></div><span class="post-call-badge ${esc(status)}">${esc(status.replaceAll('_',' '))}</span></div>
    <h5>${esc(reportText(draft.subject || 'Deal update'))}</h5><p class="draft-body">${esc(reportText(draft.body || ''))}</p>
    ${busyError ? `<p class="post-call-inline-error" role="alert">${esc(busyError)} You can retry safely.</p>` : ''}
    <div class="post-call-card-actions"><button type="button" class="primary create-draft" data-create-outlook-draft="${esc(draft.draft_id)}" data-draft-candidate="${esc(draft.candidate_id || '')}" data-draft-status="${esc(status)}" data-content-hash="${esc(draft.content_hash || '')}"${created || skipped || awaitingReceipt ? ' disabled' : ''}>${created ? 'Created in Outlook' : skipped ? 'Skipped' : awaitingReceipt ? 'Preparing draft…' : busyError ? 'Retry Outlook draft' : 'Create Outlook draft'}</button>${status === 'pending' && draft.candidate_id ? `<button type="button" class="secondary" data-post-call-skip="${esc(draft.candidate_id)}" data-candidate-resolver="post_call">Skip</button>` : ''}</div>
    <small class="human-gate">Creates a draft only. Joe or Dell reviews and sends it in Outlook.</small>
  </article>`;
}

function reportSection(title, items, renderItem, empty) {
  return `<section class="post-call-group"><h4>${esc(title)}</h4>${items.length ? `<div class="post-call-cards">${items.map(renderItem).join('')}</div>` : `<p class="post-call-empty">${esc(empty)}</p>`}</section>`;
}

function renderPostCall() {
  const panel = $('#postCallPanel');
  const post = state.postCall;
  panel.hidden = !post.session && post.status === 'idle';
  if (panel.hidden) return;
  const labels = {
    context_loading: 'Preparing the active weekly agenda…',
    context_ready: 'Agenda context ready. Recording continues locally.',
    awaiting_context: 'Preparing the active weekly agenda…',
    waiting_for_transcript: 'Recording stopped. Quill is transcribing locally…',
    distilling: 'Quill is distilling the weekly updates and next actions…',
    review_ready: 'Report ready for Joe and Dell to review.',
    filed: 'Post-call report filed. Outlook drafts still require a person to send them.',
    failed: 'The post-call report needs attention.',
  };
  $('#postCallStatus').innerHTML = `<span class="post-call-spinner" aria-hidden="true"></span><b>${esc(labels[post.status] || 'Post-call workflow ready.')}</b>${post.error ? `<small role="alert">${esc(post.error)}</small><button type="button" class="secondary" data-retry-call-context>Retry agenda context</button>` : ''}`;
  $('#postCallStatus').classList.toggle('failed', Boolean(post.error) || post.status === 'failed');
  const envelope = post.report || {};
  const core = envelope.report || {};
  const joe = Array.isArray(envelope.joe_tasks) ? envelope.joe_tasks : [];
  const dell = Array.isArray(envelope.dell_tasks) ? envelope.dell_tasks : [];
  const updates = Array.isArray(envelope.deal_updates) ? envelope.deal_updates : (Array.isArray(envelope.deals) ? envelope.deals : []);
  const questions = [...(Array.isArray(envelope.review_questions) ? envelope.review_questions : []),
    ...(Array.isArray(core.open_questions) ? core.open_questions : []),
    ...(Array.isArray(envelope.questions) ? envelope.questions : [])];
  const drafts = Array.isArray(envelope.draft_proposals) ? envelope.draft_proposals : (Array.isArray(envelope.drafts) ? envelope.drafts : []);
  const hasReport = post.status === 'review_ready' || post.status === 'filed' || joe.length || dell.length || updates.length || questions.length || drafts.length;
  $('#postCallReport').innerHTML = hasReport ? `${core.summary ? `<p class="post-call-summary">${esc(reportText(core.summary))}</p>` : ''}
    ${reportSection('Joe this week', joe, (item) => taskCard(item, 'Joe'), 'No Joe tasks were identified.')}
    ${reportSection('Dell this week', dell, (item) => taskCard(item, 'Dell'), 'No Dell tasks were identified.')}
    ${reportSection('Deal updates', updates, dealUpdateCard, 'No deal updates were identified.')}
    ${reportSection('Questions to resolve', questions, questionCard, 'No unresolved questions.')}
    ${reportSection('Vendor email drafts', drafts, draftCard, 'No vendor emails are needed from this call.')}` : '';
}

async function publishWeeklyCallContext(snapshot) {
  if (!snapshot?.session) throw new Error('Quill did not return a recording session.');
  if (!state.client.getCallContext) throw new Error('The exact call-context index is not available for this account.');
  const deals = agendaDeals();
  if (!deals.length) throw new Error('This weekly agenda has no active work records.');
  state.postCall = { ...state.postCall, status:'context_loading', session:snapshot.session,
    report:null, error:null, contextReady:false };
  renderPostCall();
  const exact = await state.client.getCallContext({ deal_ids:deals.map((deal) => deal.id) });
  if (!Array.isArray(exact?.deals)) throw new Error('The call-context index returned an invalid response.');
  const allowed = new Set(deals.map((deal) => deal.id));
  const active = exact.deals.filter((deal) => allowed.has(deal.id) && deal.operating_state === 'active');
  if (!active.length) throw new Error('The call-context index returned no active agenda work.');
  for (const deal of active) {
    if (!deal.id || !deal.name || !Array.isArray(deal.participants))
      throw new Error('The call-context index is missing exact deal or participant metadata.');
  }
  await state.postCallClient.publishCallContext({ session:snapshot.session,
    workspace_kind:state.workspace, ...(state.accountId ? { account_client_id:state.accountId } : {}),
    generated_at:new Date().toISOString(), deals:active });
  state.postCall = { ...state.postCall, status:'context_ready', contextReady:true, error:null };
  renderPostCall();
}

function stopPostCallPolling() {
  clearInterval(state.postCallTimer);
  state.postCallTimer = null;
}

async function refreshPostCall({ quiet = false } = {}) {
  if (!state.postCall.session) return;
  try {
    const payload = await state.postCallClient.getStatus(state.postCall.session);
    const rawStatus = (typeof payload.status === 'object' ? payload.status.state : payload.status) || payload.state || 'waiting_for_transcript';
    const status = ({ ready_review:'review_ready', blocked:'failed' })[rawStatus] || rawStatus;
    state.postCall = { ...state.postCall, status, report:payload.report || null, error:null };
    if (['review_ready','filed','failed'].includes(status)) stopPostCallPolling();
    renderPostCall();
  } catch (error) {
    state.postCall.error = error.message;
    renderPostCall();
    if (!quiet) showToast(error.message);
  }
}

function startPostCallPolling(session) {
  stopPostCallPolling();
  state.postCall.session = session;
  refreshPostCall({ quiet:true });
  state.postCallTimer = setInterval(() => refreshPostCall({ quiet:true }), 1600);
}

async function resolvePostCallCandidate(candidateId, accept, button) {
  if (!candidateId) return;
  button.disabled = true;
  try {
    if (button.dataset.candidateResolver === 'post_call') {
      await state.client.resolvePostCallCandidate({ candidate_id:candidateId, accept, idempotency_key:uuidv4() });
    } else {
      await state.client.resolveConfirm({ proposal_id:candidateId, accept, idempotency_key:uuidv4() });
    }
    await state.postCallClient.syncStatus(state.postCall.session);
    showToast(accept ? 'Post-call item confirmed.' : 'Post-call item skipped.');
    await refreshPostCall();
    if (accept) await loadHome();
  } catch (error) {
    showToast(error.message);
  } finally { button.disabled = false; }
}

async function createPostCallDraft(button) {
  const draftId = button.dataset.createOutlookDraft;
  const candidateId = button.dataset.draftCandidate;
  const status = button.dataset.draftStatus;
  button.disabled = true;
  state.postCall.draftErrors.delete(draftId);
  try {
    if (!['confirmed','created','already_created','draft_created'].includes(status)) {
      if (!candidateId) throw new Error('This email draft still needs a matched metadata candidate.');
      await state.client.resolvePostCallCandidate({ candidate_id:candidateId, accept:true, idempotency_key:uuidv4() });
    }
    await state.postCallClient.syncStatus(state.postCall.session);
    const created = await state.postCallClient.createOutlookDraft(
      state.postCall.session, draftId, button.dataset.contentHash,
    );
    await refreshPostCall();
    const report = state.postCall.report;
    for (const draft of report?.draft_proposals || report?.drafts || []) {
      if (draft.draft_id === draftId) {
        draft.status = created.idempotent ? 'already_created' : (created.status || 'created');
        draft.idempotent = Boolean(created.idempotent);
      }
    }
    renderPostCall();
    showToast('Outlook draft created. Nothing was sent.');
  } catch (error) {
    state.postCall.draftErrors.set(draftId, error.message);
    renderPostCall();
  } finally { button.disabled = false; }
}

async function refreshCallMode({ quiet = false } = {}) {
  try {
    state.callMode = await callModeApi('state');
    $('#callModePermission').hidden = true;
    renderCallMode();
    if (state.callMode.mode === 'weekly_deal_call' && state.callMode.session) {
      if (callModeActive(state.callMode) && state.postCall.session !== state.callMode.session) {
        state.postCall = { status:'idle', session:null, report:null, error:null,
          contextReady:false, draftErrors:new Map() };
        try { await publishWeeklyCallContext(state.callMode); }
        catch (error) {
          state.postCall = { ...state.postCall, status:'failed', session:state.callMode.session,
            error:error.message };
          renderPostCall();
        }
      } else if (!callModeActive(state.callMode) && state.postCall.session !== state.callMode.session) {
        startPostCallPolling(state.callMode.session);
      }
    }
  } catch (error) {
    if (!quiet) showToast(error.message);
  }
}

async function openCallMode() {
  $('#callModeDialog').showModal();
  renderCallMode();
  await refreshCallMode({ quiet: true });
}

async function startCallMode(mode) {
  if (!$('#callModeConsent').checked) {
    showToast('Confirm that everyone has been told before recording.');
    $('#callModeConsent').focus();
    return;
  }
  const button = document.querySelector(`[data-call-mode-start="${mode}"]`);
  if (button) button.disabled = true;
  try {
    state.callMode = await callModeApi('start', { mode, consent_confirmed: true });
    stopPostCallPolling();
    state.postCall = { status:'idle', session:null, report:null, error:null,
      contextReady:false, draftErrors:new Map() };
    renderCallMode();
    if (mode === 'weekly_deal_call') {
      try {
        await publishWeeklyCallContext(state.callMode);
      } catch (error) {
        state.postCall = { ...state.postCall, status:'failed',
          session:state.callMode.session || null, error:error.message };
        renderPostCall();
        showToast(`Recording started, but the weekly context needs attention: ${error.message}`);
      }
      try {
        await startAgenda();
        showToast('Weekly deal call is recording. The agenda is open.');
      } catch (error) {
        console.error('Could not start the weekly agenda', error);
        showToast('Weekly deal call is recording. The agenda could not open.');
      }
    } else {
      showToast('Call is recording.');
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function stopCallMode() {
  const button = $('#callModeStop');
  button.disabled = true;
  try {
    state.callMode = await callModeApi('stop', {});
    renderCallMode();
    const session = state.callMode.session || state.postCall.session;
    if (session && (state.callMode.mode === 'weekly_deal_call' || state.postCall.contextReady)) {
      state.postCall = { ...state.postCall, status:'waiting_for_transcript', session, error:null };
      renderPostCall();
      startPostCallPolling(session);
    }
    showToast('Recording stopped. Quill is processing the call.');
  } catch (error) {
    showToast(error.message);
  } finally { button.disabled = false; }
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

async function patchOperatingState(dealId, value) {
  const result = await state.client.patchDealField({ deal:dealId, field:'operating_state', value,
    base_event_id:state.fieldBase.get(`${dealId}|operating_state`) || null, idempotency_key:uuidv4() });
  if (result.status === 'conflict') return showConflict(result.conflict);
  const deal = state.deals.get(dealId);
  if (deal) {
    deal.operating_state = value.state;
    deal.parking_reason = value.state === 'parked' ? value.reason : null;
    deal.parking_note = value.state === 'parked' ? value.note || null : null;
    deal.parked_at = value.state === 'parked' ? new Date().toISOString() : null;
    deal.parked_by = value.state === 'parked' ? state.selfActor : null;
  }
  state.changed.add(dealId);
  if ($('#dealDialog').open) $('#dealDialog').close();
  render();
  showToast(value.state === 'parked'
    ? `${deal?.name || 'Work record'} parked`
    : `${deal?.name || 'Work record'} restored to active work`);
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

function parkDealForm(dealId) {
  const deal = state.deals.get(dealId);
  openForm({ eyebrow:'Active work', title:`Park — ${deal.name}`, submit:'Park record', body:`
    <p class="form-guidance">Parking removes this record from active counts, focus lists, and weekly agendas. Its Salesforce link, phase, history, and participants stay intact.</p>
    <div class="field"><label for="parkingReason">Why is this not active work?</label><select id="parkingReason" name="reason" required>
      <option value="prospect_never_active">Prospect never became active work</option>
      <option value="client_paused">Client paused activity</option>
      <option value="other">Other / not active right now</option>
    </select></div>
    <div class="field"><label for="parkingNote">Context (optional)</label><textarea id="parkingNote" name="note" maxlength="500" placeholder="What would help when this record becomes active again?"></textarea></div>`,
    onSubmit:async (data) => patchOperatingState(dealId, {
      state:'parked', reason:String(data.get('reason')), note:String(data.get('note') || '').trim() || null,
    }) });
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
  openForm({ title:'Add work record', submit:'Create work record', body:`
    <div class="field"><label for="clientRef">Existing client</label><input id="clientRef" name="client" required placeholder="C-127 or exact client name"><small>A work record always belongs to a client. This prevents free-floating or duplicate records.</small></div>
    <div class="field"><label for="dealName">Record name</label><input id="dealName" name="name" required></div>
    <div class="field-row"><div class="field"><label for="dealType">Type</label><select id="dealType" name="deal_type"><option value="startup">Startup</option><option value="relocation">Relocation</option><option value="additional_office">Additional office</option><option value="renewal">Renewal</option><option value="expansion">Expansion</option><option value="purchase">Purchase</option><option value="other">Other</option></select></div>
    <div class="field"><label for="dealPhase">Phase</label><select id="dealPhase" name="phase">${phaseOptions('On Deck')}</select></div></div>
    <div class="field-row"><div class="field"><label for="dealMarket">Market</label><input id="dealMarket" name="market"></div><div class="field"><label for="dealSegment">Healthcare vertical</label><input id="dealSegment" name="segment" placeholder="Dental, Vet, DPC…"></div></div>`,
    onSubmit:async (data) => {
      const args = Object.fromEntries(data.entries());
      await state.client.createDeal({ ...args, lane:'territory', idempotency_key:uuidv4() });
      await loadHome(); showToast('Work record created in the Team Book');
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
  const display = (value) => value && typeof value === 'object'
    ? value.state === 'parked' ? `Parked — ${parkingReasonLabel(value.reason)}` : 'Active work'
    : value ?? '(empty)';
  openForm({ eyebrow:'Two edits crossed', title:`Choose the value for ${conflict.field}`, submit:'Keep selected value', body:`
    <div class="field"><label><input type="radio" name="winner" value="a" checked> ${esc(actorName(conflict.a.actor))}: ${esc(display(conflict.a.value))}</label></div>
    <div class="field"><label><input type="radio" name="winner" value="b"> ${esc(actorName(conflict.b.actor))}: ${esc(display(conflict.b.value))}</label></div>`,
    onSubmit:async (data) => { await state.client.resolveConflict({ conflict_id:conflict.conflict_id, winner:data.get('winner'), idempotency_key:uuidv4() }); await loadHome(); showToast('Conflict resolved with both values preserved in history'); } });
}

function detailRows(items, renderer, empty='Nothing captured yet.') {
  return items?.length ? items.map(renderer).join('') : `<div class="detail-row">${esc(empty)}</div>`;
}

async function openDeal(dealId) {
  const detail = await state.client.getDeal(dealId);
  const deal = detail.deal;
  const parked = deal.operating_state === 'parked';
  const html = `<header><div><p class="eyebrow">${esc(deal.account_name || deal.client_name || 'Work record')}</p><h2>${esc(deal.name)}</h2><p class="subhead">${parked ? `${esc(parkingReasonLabel(deal.parking_reason))} · ` : ''}${esc(deal.phase)} · ${esc(deal.market || 'Market not captured')}</p></div><div class="detail-header-actions"><button type="button" class="park-button" data-operating-state="${parked ? 'active' : 'parked'}" data-deal="${esc(deal.id)}">${parked ? 'Restore to active' : 'Park'}</button><button type="button" class="icon-button" data-close-deal aria-label="Close details">×</button></div></header>
    <div class="deal-content">${parked ? `<div class="parking-banner"><b>${esc(parkingReasonLabel(deal.parking_reason))}</b>${deal.parking_note ? `<span>${esc(deal.parking_note)}</span>` : ''}<small>This record is outside active counts and weekly agendas.</small></div>` : ''}<div class="deal-summary">
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
  let deals = [...state.deals.values()].filter((deal) => (deal.operating_state || 'active') === 'active');
  if (state.workspace === 'team') deals = deals.filter((deal) => deal.workspace_kind === 'team');
  else if (state.accountId) deals = deals.filter((deal) => deal.account_client_id === state.accountId);
  else deals = [];
  return deals.sort((a,b) => priority(b) - priority(a) || a.name.localeCompare(b.name));
}

async function startAgenda() {
  const deals = agendaDeals();
  if (!deals.length) return showToast('No active work in this agenda');
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
    <button type="button" class="secondary" data-open-deal="${esc(deal.id)}">Open full record</button>
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
  showToast(status === 'completed' ? `Agenda finished · ${result.reviewed ?? review.reviewed} records reviewed` : 'Agenda closed without changing the review clock');
}

function wireEvents() {
  document.addEventListener('click', async (event) => {
    const workspace = event.target.closest('[data-workspace]');
    if (workspace) { state.workspace = workspace.dataset.workspace; state.accountId = null; state.filter = 'active'; state.query = ''; $('#search').value = ''; render(); return; }
    const accountButton = event.target.closest('[data-account]');
    if (accountButton) { state.workspace = 'national_account'; state.accountId = accountButton.dataset.account; render(); return; }
    const operating = event.target.closest('[data-operating-state]');
    if (operating) {
      const dealId = operating.dataset.deal;
      if (operating.dataset.operatingState === 'active') await patchOperatingState(dealId, { state:'active' });
      else { if ($('#dealDialog').open) $('#dealDialog').close(); parkDealForm(dealId); }
      return;
    }
    const open = event.target.closest('[data-open-deal]'); if (open) { await openDeal(open.dataset.openDeal); return; }
    const attention = event.target.closest('[data-attention]'); if (attention) { const deal=state.deals.get(attention.dataset.attention); await patchField(deal.id,'attention',!deal.attention); return; }
    const step = event.target.closest('[data-next-step],[data-agenda-step]'); if (step) { nextStepForm(step.dataset.nextStep || step.dataset.agendaStep); return; }
    const agent = event.target.closest('[data-market-agent]'); if (agent) { marketAgentForm(agent.dataset.marketAgent); return; }
    const filter = event.target.closest('[data-filter]'); if (filter) { state.filter=filter.dataset.filter; $$('.filter').forEach((b)=>b.classList.toggle('on',b===filter)); renderBoardOnly(); return; }
    if (event.target.closest('[data-close-deal]')) { $('#dealDialog').close(); return; }
    if (event.target.closest('[data-undo]') && state.undoEventId) { await state.client.revertDealField({ event_id:state.undoEventId,idempotency_key:uuidv4() }); state.undoEventId=null; await loadHome(); showToast('Change undone'); return; }
    const confirm = event.target.closest('[data-confirm]'); if (confirm) { const chip=confirm.closest('[data-proposal]'); const yes=confirm.dataset.confirm==='yes'; await state.client.resolveConfirm({proposal_id:chip.dataset.proposal,accept:yes,idempotency_key:uuidv4()}); state.confirms=state.confirms.filter((p)=>p.id!==chip.dataset.proposal); renderConfirms(); if(yes)await loadHome(); showToast(yes?'Suggestion confirmed':'Suggestion skipped'); return; }
    const postConfirm = event.target.closest('[data-post-call-confirm]'); if (postConfirm) { await resolvePostCallCandidate(postConfirm.dataset.postCallConfirm, true, postConfirm); return; }
    const postSkip = event.target.closest('[data-post-call-skip]'); if (postSkip) { await resolvePostCallCandidate(postSkip.dataset.postCallSkip, false, postSkip); return; }
    const createDraft = event.target.closest('[data-create-outlook-draft]'); if (createDraft) { await createPostCallDraft(createDraft); return; }
    const retryContext = event.target.closest('[data-retry-call-context]'); if (retryContext) {
      retryContext.disabled = true;
      try { await publishWeeklyCallContext(state.callMode); }
      catch (error) { state.postCall = { ...state.postCall, status:'failed', error:error.message }; renderPostCall(); }
      finally { retryContext.disabled = false; }
      return;
    }
    if (event.target.closest('[data-dialog-cancel]')) { $('#formDialog').close(); return; }
    const callStart = event.target.closest('[data-call-mode-start]'); if (callStart) { await startCallMode(callStart.dataset.callModeStart); return; }
    if (event.target.closest('#callModeClose')) { $('#callModeDialog').close(); return; }
  });

  document.addEventListener('change', async (event) => {
    if (event.target.matches('[data-phase]')) await patchField(event.target.dataset.phase,'phase',event.target.value);
    if (event.target.matches('[data-owner]')) await patchField(event.target.dataset.owner,'owner',event.target.value || null);
  });

  $('#search').addEventListener('input', (event) => { state.query=event.target.value.trim(); render(); });
  $('#accountBack').onclick = () => { state.accountId=null; render(); };
  const openAddForm = () => state.workspace === 'team' ? addTeamDealForm() : state.accountId ? addMarketDealForm() : addAccountForm();
  $('#stickyAddButton').onclick = openAddForm;
  $('#ownerButton').onclick = accountOwnerForm;
  $('#agendaButton').onclick = startAgenda;
  $('#callModeButton').onclick = openCallMode;
  $('#callModeStop').onclick = stopCallMode;
  $('#postCallRefresh').onclick = () => refreshPostCall();
  $('#agendaReviewed').onclick = () => advanceAgenda('reviewed');
  $('#agendaSkip').onclick = () => advanceAgenda('skipped');
  $('#agendaEnd').onclick = () => finishAgenda('completed');
  $('#agendaClose').onclick = () => finishAgenda('abandoned');
  $('#themeButton').onclick = () => { document.body.classList.toggle('night'); localStorage.setItem('dealroom-theme',document.body.classList.contains('night')?'night':'light'); };
  $('#colorAssistButton').onclick = () => {
    const enabled = !document.body.classList.contains('color-assist');
    document.body.classList.toggle('color-assist', enabled);
    const button = $('#colorAssistButton');
    button.setAttribute('aria-pressed', String(enabled));
    button.setAttribute('aria-label', `${enabled ? 'Turn off' : 'Turn on'} color-blind-friendly view`);
    localStorage.setItem('dealroom-color-assist', enabled ? 'on' : 'off');
    showToast(enabled ? 'Color-friendly view on · patterns and labels supplement color' : 'Color-friendly view off');
  };
  window.addEventListener('online', () => { $('#offlineBanner').hidden=true; setSync(true); pollOnce(); });
  window.addEventListener('offline', () => { $('#offlineBanner').hidden=false; setSync(false,'Offline'); });
  document.addEventListener('keydown', (event) => { if (event.key==='/' && !event.target.matches('input,textarea,select')) { event.preventDefault(); $('#search').focus(); } });
}

async function boot() {
  if (localStorage.getItem('dealroom-theme') === 'night') document.body.classList.add('night');
  if (localStorage.getItem('dealroom-color-assist') === 'on') {
    document.body.classList.add('color-assist');
    $('#colorAssistButton').setAttribute('aria-pressed', 'true');
    $('#colorAssistButton').setAttribute('aria-label', 'Turn off color-blind-friendly view');
  }
  const bootConfig = resolveDealroomBoot(location);
  state.client = await createClient(bootConfig.mode, bootConfig.options);
  state.postCallClient = createPostCallClient({ loopbackUrl:CALL_MODE_URL,
    postHeaders:CALL_MODE_HEADER });
  wireEvents();
  await loadHome();
  state.pollTimer = setInterval(() => pollOnce(), POLL_MS);
  state.callModeTimer = setInterval(() => {
    if (callModeActive()) renderCallMode();
  }, 250);
  if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('/sw.js').catch(()=>{});
}

boot().catch((error) => {
  console.error(error);
  document.body.insertAdjacentHTML('afterbegin', `<div class="offline">Deal Room could not start: ${esc(error.message)}</div>`);
});

/**
 * Doc — persistent, pinnable command panel (V5-J101).
 *
 * Self-mounting: a page adds `<script type="module" src="/js/doc-panel.js">`
 * and gets the toggle button plus panel with no markup changes of its own.
 * A host page that HAS a client and a current record calls
 * `registerDocPanelSource({ getClient, getContext })` once (see js/app.js);
 * until it does, the panel still opens/closes/keyboard-operates correctly
 * and reports every command as {status:'unavailable'} — an honest state, not
 * a broken one (see architecture_or_design guidance for V5-J101: never ship
 * mocks as live behaviour).
 *
 * Modality mirrors the record panel exactly (workspace-business.js): docked
 * and non-modal on a desktop, a real focus-trapped dialog with the rest of
 * the page made inert at phone width. docPanelModality/escapeShouldClose live
 * in doc-panel-model.js so that rule is unit-testable without a DOM.
 */

import { docPanelModality, escapeShouldClose, parseDocInput, formatReceiptLine } from './doc-panel-model.js';
import { runCommand, listCommands } from './commands.js';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const PIN_KEY = 'dealroom-doc-pinned';

let source = { getClient: () => null, getContext: () => ({}) };

/**
 * Called by a host page (app.js, workspace-business.js, …) once its client
 * and current-record context exist. Safe to call more than once; the last
 * registration wins, which matters when a page tears down and re-mounts.
 * @param {{getClient: () => any, getContext: () => {dealId?:string|null, nextDate?:string|null, label?:string}}} next
 */
export function registerDocPanelSource(next) {
  source = { getClient: next.getClient || source.getClient, getContext: next.getContext || source.getContext };
}

function readPinned() {
  try { return localStorage.getItem(PIN_KEY) === '1'; } catch { return false; }
}
function writePinned(value) {
  try { localStorage.setItem(PIN_KEY, value ? '1' : '0'); } catch { /* private mode / blocked storage: pin just won't persist */ }
}

function phoneWidthMatch() {
  return typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 767px)') : null;
}

function mount() {
  if (document.getElementById('docPanel')) return; // already mounted on this page

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.id = 'docPanelToggle';
  toggle.className = 'doc-toggle';
  toggle.setAttribute('aria-haspopup', 'dialog');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'docPanel');
  toggle.innerHTML = '<span aria-hidden="true">◐</span><span class="doc-toggle-label">Doc</span>';

  const panel = document.createElement('aside');
  panel.id = 'docPanel';
  panel.className = 'doc-panel';
  panel.hidden = true;
  panel.setAttribute('aria-labelledby', 'docPanelTitle');
  panel.innerHTML = `
    <div class="doc-panel-head">
      <div><p class="eyebrow">Dr. CRE</p><h2 id="docPanelTitle" tabindex="-1">Doc</h2></div>
      <div class="doc-panel-head-actions">
        <button type="button" id="docPanelPin" class="doc-icon-button" aria-pressed="false">Pin</button>
        <button type="button" id="docPanelClose" class="doc-icon-button" aria-label="Close Doc">Close</button>
      </div>
    </div>
    <p class="doc-panel-context" id="docPanelContext" aria-live="polite">Working on: nothing open yet.</p>
    <div class="doc-panel-log" id="docPanelLog" role="log" aria-live="polite" aria-label="Doc activity"></div>
    <form id="docPanelForm" class="doc-panel-form">
      <label for="docPanelInput" class="visually-hidden">Tell Doc what to do</label>
      <input id="docPanelInput" name="input" type="text" autocomplete="off" spellcheck="false"
             placeholder="Type a note, or /set_next_step …">
      <button type="submit" class="action primary-action">Send</button>
    </form>
    <p class="doc-panel-hint" id="docPanelHint">Same commands as the record panel — this runs the identical typed command, not a shortcut around it.</p>
  `;

  document.body.append(toggle, panel);

  const state = { open: false, pinned: readPinned() };
  const dom = {
    toggle, panel,
    title: panel.querySelector('#docPanelTitle'),
    context: panel.querySelector('#docPanelContext'),
    log: panel.querySelector('#docPanelLog'),
    form: panel.querySelector('#docPanelForm'),
    input: panel.querySelector('#docPanelInput'),
    pin: panel.querySelector('#docPanelPin'),
    close: panel.querySelector('#docPanelClose'),
  };

  const phoneQuery = phoneWidthMatch();
  const isModal = () => docPanelModality({ open: state.open, phoneWidth: Boolean(phoneQuery?.matches) }) === 'modal';

  function otherRegions() {
    return [...document.body.children].filter((el) => el !== toggle && el !== panel);
  }

  function applyModality() {
    const modal = isModal();
    panel.setAttribute('role', modal ? 'dialog' : 'complementary');
    if (modal) panel.setAttribute('aria-modal', 'true'); else panel.removeAttribute('aria-modal');
    for (const region of otherRegions()) {
      region.inert = modal && state.open;
      if (modal && state.open) region.setAttribute('aria-hidden', 'true'); else region.removeAttribute('aria-hidden');
    }
  }

  function open() {
    state.open = true;
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    applyModality();
    dom.title.focus();
    renderContext();
  }

  function close({ returnFocus = true } = {}) {
    state.open = false;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    applyModality();
    if (returnFocus) toggle.focus();
  }

  function togglePin() {
    state.pinned = !state.pinned;
    writePinned(state.pinned);
    dom.pin.setAttribute('aria-pressed', String(state.pinned));
    dom.pin.textContent = state.pinned ? 'Pinned' : 'Pin';
  }

  function renderContext() {
    const context = source.getContext() || {};
    dom.context.textContent = context.dealId
      ? `Working on: ${context.label || context.dealId}.`
      : 'Working on: nothing open yet. Commands that need a record will say so.';
  }

  function appendLog(text, tone) {
    const line = document.createElement('p');
    line.className = `doc-log-line doc-log-${tone}`;
    line.textContent = text;
    dom.log.append(line);
    dom.log.scrollTop = dom.log.scrollHeight;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    const raw = dom.input.value;
    if (!raw.trim()) return;
    const { command, text } = parseDocInput(raw);
    appendLog(`You: ${raw.trim()}`, 'you');
    dom.input.value = '';
    dom.input.disabled = true;
    try {
      const client = source.getClient();
      const context = source.getContext() || {};
      const receipt = await runCommand(client, command, text, context);
      const { text: line } = formatReceiptLine(receipt);
      appendLog(line, receipt.status);
    } finally {
      dom.input.disabled = false;
      dom.input.focus();
    }
  }

  function containFocus(event) {
    if (event.key !== 'Tab' || !isModal()) return;
    const stops = [...panel.querySelectorAll(FOCUSABLE)];
    if (stops.length === 0) return;
    const first = stops[0];
    const last = stops[stops.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !panel.contains(active))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault(); first.focus();
    }
  }

  toggle.addEventListener('click', () => (state.open ? close() : open()));
  dom.close.addEventListener('click', () => close());
  dom.pin.addEventListener('click', togglePin);
  dom.pin.setAttribute('aria-pressed', String(state.pinned));
  dom.pin.textContent = state.pinned ? 'Pinned' : 'Pin';
  dom.form.addEventListener('submit', handleSubmit);
  panel.addEventListener('keydown', containFocus);
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || !state.open) return;
    if (!escapeShouldClose({ pinned: state.pinned })) return;
    close();
  });
  phoneQuery?.addEventListener?.('change', applyModality);

  applyModality();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
}

export const __internal = { listCommands };

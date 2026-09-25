/**
 * Doc — persistent, pinnable command panel (V5-J101).
 *
 * The app persona is Dr. CRE ("Doc" is only the spoken nickname — CLAUDE.md).
 * The visible label stays "Doc"; the accessible name (what a screen reader
 * announces) is "Dr. CRE" on both the toggle button and the panel itself.
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
 * in doc-panel-model.js so that rule is unit-testable without a DOM; the
 * tab-stop decision reuses workspace-business-model.js's own panelTabTarget
 * rather than a second, drifting implementation of the same rule.
 *
 * THREE FIXES after independent review of PR #1259:
 *  - index.html's #dealDialog/#formDialog are native <dialog> elements shown
 *    with showModal(), which makes everything OUTSIDE the dialog's own
 *    subtree inert — including a Doc panel appended to document.body. A
 *    MutationObserver watches every <dialog>'s `open` attribute and
 *    reparents the toggle+panel into whichever one is currently open (there
 *    is at most one at a time in this app), and back to document.body when
 *    none is, so Doc stays reachable while a deal or form dialog is up.
 *  - The focus trap previously built its stop list from the panel's own
 *    focusable elements only, so Shift+Tab from the heading (a stop-less
 *    tabindex="-1" element) fell through to the browser's native previous
 *    element — the toggle button, which the inert sweep deliberately
 *    excluded so it stayed clickable. The toggle is no longer excluded from
 *    that sweep (the in-panel Close button is sufficient while modal) and
 *    panelTabTarget now owns the heading case exactly as it does for the
 *    record panel.
 *  - applyModality swept ALL of document.body's other children and forced
 *    each one's `inert`/`aria-hidden` on close, which could undo a `true`
 *    another panel (e.g. the record panel on business.html) had
 *    independently set on the SAME element for its own still-open modal.
 *    Each region's prior inert/aria-hidden state is now snapshotted before
 *    Doc changes it, and restored exactly — not forced false — when Doc
 *    stops covering it.
 */

import { docPanelModality, escapeShouldClose, parseDocInput, formatReceiptLine } from './doc-panel-model.js';
import { panelTabTarget } from './workspace-business-model.js';
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

function focusWithoutScrolling(element) {
  element?.focus?.({ preventScroll: true });
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
  // Accessible name is the persona, "Dr. CRE"; the visible label stays the
  // nickname, "Doc" (CLAUDE.md naming rule).
  toggle.setAttribute('aria-label', 'Dr. CRE');
  toggle.innerHTML = '<span aria-hidden="true">◐</span><span class="doc-toggle-label" aria-hidden="true">Doc</span>';

  const panel = document.createElement('aside');
  panel.id = 'docPanel';
  panel.className = 'doc-panel';
  panel.hidden = true;
  // aria-labelledby concatenates both nodes: accessible name becomes
  // "Dr. CRE Doc" — it CONTAINS the required persona name while the visible
  // heading stays the nickname alone.
  panel.setAttribute('aria-labelledby', 'docPanelEyebrow docPanelTitle');
  panel.innerHTML = `
    <div class="doc-panel-head">
      <div><p class="eyebrow" id="docPanelEyebrow">Dr. CRE</p><h2 id="docPanelTitle" tabindex="-1">Doc</h2></div>
      <div class="doc-panel-head-actions">
        <button type="button" id="docPanelPin" class="doc-icon-button" aria-pressed="false">Pin</button>
        <button type="button" id="docPanelClose" class="doc-icon-button" aria-label="Close Dr. CRE">Close</button>
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

  const state = { open: false, pinned: readPinned(), lastAttempt: null };
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

  // ------------------------------------------------------- top-layer host
  //
  // A native <dialog> shown with showModal() (index.html's #dealDialog and
  // #formDialog) makes everything outside ITS OWN subtree inert. The Doc
  // panel has to live inside whichever one is currently open to stay
  // reachable, and back in document.body the rest of the time.

  function openDialogHost() {
    const openDialog = document.querySelector('dialog[open]');
    return (openDialog && openDialog !== panel && !panel.contains(openDialog)) ? openDialog : document.body;
  }

  function relocate() {
    const host = openDialogHost();
    if (toggle.parentNode !== host) host.append(toggle, panel);
  }

  // Relocating alone is not enough: the set of "other regions" to inert is
  // relative to whatever element toggle/panel currently live in, so a host
  // change must always be followed by recomputing inert state against the
  // NEW host — otherwise closing a dialog while Doc is still open at phone
  // width would move Doc back to document.body without ever making body's
  // own children inert, breaking the trap.
  const dialogWatcher = typeof MutationObserver === 'function'
    ? new MutationObserver(() => { relocate(); applyModality(); })
    : null;
  dialogWatcher?.observe(document.documentElement, { attributes: true, attributeFilter: ['open'], subtree: true });

  // ------------------------------------------------------- inert background
  //
  // Snapshot each region's OWN prior inert/aria-hidden state the first time
  // Doc covers it, and restore exactly that — never a hardcoded false — when
  // Doc stops covering it, so Doc can never undo another panel's independent
  // modal state on a region they both happen to reach (e.g. <header
  // data-panel-background> on business.html, which workspace-business.js's
  // record panel also manages).
  const managedRegions = new WeakMap();

  function otherRegions() {
    // The toggle is NOT excluded here (an earlier draft excluded it so it
    // stayed clickable while modal, which is exactly what let Shift+Tab from
    // the heading escape the trap onto it). The in-panel Close button is
    // sufficient while modal, matching the record panel's own pattern of a
    // fully-inert background with no exception.
    const host = toggle.parentNode || document.body;
    return [...host.children].filter((el) => el !== panel);
  }

  function applyModality() {
    const modal = isModal();
    panel.setAttribute('role', modal ? 'dialog' : 'complementary');
    if (modal) panel.setAttribute('aria-modal', 'true'); else panel.removeAttribute('aria-modal');
    const shouldCover = modal && state.open;
    for (const region of otherRegions()) {
      if (shouldCover) {
        if (!managedRegions.has(region)) {
          managedRegions.set(region, { inert: region.inert, ariaHidden: region.getAttribute('aria-hidden') });
        }
        region.inert = true;
        region.setAttribute('aria-hidden', 'true');
      } else if (managedRegions.has(region)) {
        const prior = managedRegions.get(region);
        region.inert = prior.inert;
        if (prior.ariaHidden === null) region.removeAttribute('aria-hidden');
        else region.setAttribute('aria-hidden', prior.ariaHidden);
        managedRegions.delete(region);
      }
    }
  }

  function open() {
    relocate();
    state.open = true;
    panel.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    applyModality();
    focusWithoutScrolling(dom.title);
    renderContext();
  }

  function close({ returnFocus = true } = {}) {
    state.open = false;
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    applyModality();
    if (returnFocus) focusWithoutScrolling(toggle);
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

  function appendLog(text, tone, { retry = null } = {}) {
    const line = document.createElement('p');
    line.className = `doc-log-line doc-log-${tone}`;
    line.textContent = text;
    if (retry) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'doc-log-retry';
      button.textContent = 'Retry';
      button.addEventListener('click', retry);
      line.append(' ', button);
    }
    dom.log.append(line);
    dom.log.scrollTop = dom.log.scrollHeight;
  }

  /**
   * Runs one command attempt. `idempotencyKey`, when supplied, is a RETRY of
   * the exact same attempt and reuses that key so the server's own
   * idempotency_key dedupe (mcp-server/src/tools.js withEnvelope) applies —
   * a retry can never turn into a second write.
   */
  async function attempt(command, text, idempotencyKey) {
    const client = source.getClient();
    const context = source.getContext() || {};
    const receipt = await runCommand(client, command, text, context, idempotencyKey ? { idempotencyKey } : {});
    const { text: line } = formatReceiptLine(receipt);
    if (receipt.status === 'refused' || receipt.status === 'unavailable') {
      state.lastAttempt = { command, text, idempotencyKey: receipt.idempotencyKey };
      appendLog(line, receipt.status, {
        retry: () => attempt(command, text, state.lastAttempt.idempotencyKey),
      });
    } else {
      state.lastAttempt = null;
      appendLog(line, receipt.status);
    }
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
      await attempt(command, text, null);
    } finally {
      dom.input.disabled = false;
      dom.input.focus();
    }
  }

  function containFocus(event) {
    if (event.key !== 'Tab' || !isModal()) return;
    const stops = [...panel.querySelectorAll(FOCUSABLE)];
    const active = document.activeElement;
    const target = panelTabTarget({
      inside: panel.contains(active),
      stopIndex: stops.indexOf(active),
      stopCount: stops.length,
      shiftKey: event.shiftKey,
    });
    if (!target) return;
    event.preventDefault();
    if (target === 'title') return focusWithoutScrolling(dom.title);
    focusWithoutScrolling(target === 'first' ? stops[0] : stops[stops.length - 1]);
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

  relocate();
  applyModality();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
}

export const __internal = { listCommands };

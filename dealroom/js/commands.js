/**
 * Shared command registry (V5-J101).
 *
 * checkable_done for V5-J101 requires "the same command issued from the UI
 * and from the Doc yields an equivalent receipt"
 * (doctorcre-v5-astra-integration-review /
 * v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09).
 * The only way to GUARANTEE that structurally, rather than promise it in two
 * places that can drift, is one function both callers run through. A UI form
 * and the Doc composer both call `runCommand`; neither calls
 * client.setNextStep / client.addDealNote directly. Same inputs, same code
 * path, same receipt shape.
 *
 * A command here names and normalizes a write the DealRoomClient (client.js)
 * already implements — it is not a new backend and does not widen authority.
 * When the client does not implement the underlying call, `run` returns an
 * explicit {status:'unavailable', ...} receipt rather than faking success:
 * see capability-seams.js for the same rule applied to reads.
 */

import { uuidv4 } from './uuid.js';

/**
 * @typedef {Object} CommandReceipt
 * @property {'ok'|'conflict'|'refused'|'unavailable'} status
 * @property {string} command
 * @property {string|null} deal
 * @property {string} at ISO-8601
 * @property {Object} detail
 */

function unavailable(command, deal, reason) {
  return { status: 'unavailable', command, deal: deal ?? null, at: new Date().toISOString(), detail: { reason } };
}

function toReceipt(command, deal, result) {
  const at = new Date().toISOString();
  if (result && result.status === 'conflict')
    return { status: 'conflict', command, deal, at, detail: { conflict: result.conflict } };
  if (result && result.status === 'ok')
    return { status: 'ok', command, deal, at, detail: { event: result.event ?? null } };
  return { status: 'refused', command, deal, at, detail: { result: result ?? null } };
}

export const COMMANDS = {
  set_next_step: {
    label: 'Set next step',
    parse(input, context) {
      const text = String(input ?? '').trim();
      if (!text) return { error: 'Say what happens next.' };
      if (!context.dealId) return { error: 'Open a deal first — set next step needs one to act on.' };
      return { deal: context.dealId, text, next_date: context.nextDate ?? null };
    },
    async run(client, args) {
      if (typeof client?.setNextStep !== 'function')
        return unavailable('set_next_step', args.deal, 'V5-J102 typed lifecycle/record-write surface is not deployed yet.');
      const result = await client.setNextStep({
        deal: args.deal, text: args.text, next_date: args.next_date ?? null,
        idempotency_key: uuidv4(),
      });
      return toReceipt('set_next_step', args.deal, result);
    },
  },
  add_note: {
    label: 'Add note',
    parse(input, context) {
      const text = String(input ?? '').trim();
      if (!text) return { error: 'Write the note text.' };
      if (!context.dealId) return { error: 'Open a deal first — a note needs a record to attach to.' };
      return { deal: context.dealId, text };
    },
    async run(client, args) {
      if (typeof client?.addDealNote !== 'function')
        return unavailable('add_note', args.deal, 'V5-F01 canonical record-home write surface is not deployed yet.');
      const result = await client.addDealNote({
        deal: args.deal, text: args.text, idempotency_key: uuidv4(),
      });
      return toReceipt('add_note', args.deal, result);
    },
  },
};

/**
 * The one entry point every caller — UI form or Doc composer — must use.
 * @param {import('./client.js').DealRoomClient|null|undefined} client
 * @param {string} name a COMMANDS key
 * @param {string} input raw command text
 * @param {{dealId?:string|null, nextDate?:string|null}} [context]
 * @returns {Promise<CommandReceipt>}
 */
export async function runCommand(client, name, input, context = {}) {
  const at = new Date().toISOString();
  const command = COMMANDS[name];
  if (!command)
    return { status: 'refused', command: name, deal: context.dealId ?? null, at, detail: { reason: `Unknown command "${name}".` } };
  const parsed = command.parse(input, context);
  if (parsed.error)
    return { status: 'refused', command: name, deal: context.dealId ?? null, at, detail: { reason: parsed.error } };
  try {
    return await command.run(client, parsed);
  } catch (error) {
    return {
      status: 'refused', command: name, deal: parsed.deal ?? context.dealId ?? null, at,
      detail: { reason: error?.payload?.hint || error?.message || 'Command failed.' },
    };
  }
}

export function listCommands() {
  return Object.entries(COMMANDS).map(([name, c]) => ({ name, label: c.label }));
}

import { ToolError } from "./tool-error.js";

export function docOutcomeCardsProjection(facts, ErrorType = ToolError) {
  const states = new Set(["queued", "active", "waiting", "failed", "unknown", "verified"]);
  if (!facts || facts.ok !== true || facts.schema_version !== "doc-outcome-cards.v2" || !Array.isArray(facts.cards))
    throw new ErrorType({ error: facts?.reason_id || "doc_outcome_cards_unavailable" });
  for (const card of facts.cards) {
    if (!card || !states.has(card.routing_state) || card.session_entry?.auto_launch !== false || !Object.hasOwn(card, "native_task_id"))
      throw new ErrorType({ error: "doc_outcome_cards_invalid_projection" });
  }
  return { ok: true, schema_version: facts.schema_version, as_of: facts.as_of, correlation_version: facts.correlation_version,
    more: facts.more === true, next_cursor: facts.next_cursor ?? null, cards: facts.cards };
}

export function docOutcomeCardsTools() {
  return {
    "read-doc-outcome-cards": {
      discoveryOrder: 184,
      writerConnection: true,
      description: "Read actor- and tenant-scoped, page-atomic DoctorCRE outcome cards. The producer derives joins and availability; it never launches a native task.",
      inputSchema: { type: "object", additionalProperties: false, properties: { cursor: { type: "string", minLength: 1, maxLength: 1000 }, limit: { type: "integer", minimum: 1, maximum: 50 } }, required: [] },
      handler: async (c, _actor, args) => docOutcomeCardsProjection((await c.query("select ops.read_doc_outcome_cards_successor($1::text,$2::integer) as facts", [args.cursor ?? null, args.limit ?? null])).rows[0]?.facts, ToolError),
    },
  };
}

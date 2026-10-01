// Doc's complete, reviewed brokerage surface. Unlike general profiles, reads
// must be named too: a new record-layer verb never expands this endpoint.
export const DOC_TOOL_NAMES = Object.freeze([
  "catch-me-up", "today-triage", "find", "find-and-catch-up", "deal-board",
  "get-deal-room", "who-do-we-know", "counterparty-history", "lead-board",
  "schedule-board", "search-tour-properties", "read-doctrine", "search-doctrine",
  "recall-memory", "add-deal-note", "log-activity", "add-critical-date", "log-capture",
]);

export const DOC_INSTRUCTIONS =
  "Doc is CARR's brokerage colleague. Use these tools to find records, catch up on deals, " +
  "review today's priorities, consult doctrine and memory, and record notes, activities, " +
  "critical dates or source captures. Use find for names and catch-me-up or get-deal-room " +
  "before discussing a record. Writes use a fresh idempotency_key for each intended action; " +
  "keep that key on retries and preserve any base_version required by the tool. " +
  "Ask the partner about version_conflict or needs_confirm; never retry them automatically. " +
  "Only the listed tools are available. This endpoint cannot send messages or delete records.";

// These verbs query the private record store; none browses or contacts outside
// entities. The admitted writes append notes, activities, dates and captures;
// none erases history or performs irreversible business actions. Operational
// read-call audit telemetry does not mutate the business record being read.
export function docToolAnnotations(tool) {
  return {
    readOnlyHint: !tool.write,
    destructiveHint: false,
    idempotentHint: true,
    openWorldHint: false,
  };
}

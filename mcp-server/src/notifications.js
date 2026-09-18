// WR-000113 — the R03 notification verbs.
//
// LIBRARY ONLY: no shebang and no main-module construct (the SCAC
// inventory scans for those by substring, so they are described, never spelled).
//
// acknowledge-notification is a plain `write: true` routine-writer verb,
// matching the carr_writer EXECUTE grant on ops.acknowledge_notification.
// notification-feed declares `writerConnection: true` and NO write flag:
// ops.notification_feed_facts resolves the caller's own actor from the
// transaction-local setting, which only the writer path installs -- on the
// reader connection it would resolve nobody.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Pure shaper over ops.notification_feed_facts. */
export function notificationFeedProjection(facts, ToolError) {
  if (!facts || typeof facts !== "object" || facts.ok !== true) {
    throw new ToolError({ error: "notification_feed_unavailable" });
  }
  return {
    ok: true,
    unread_count: facts.unread_count ?? 0,
    notifications: (Array.isArray(facts.notifications) ? facts.notifications : []).map(row => ({
      id: row.id,
      severity: row.severity,
      reason: row.reason,
      subject_type: row.subject_type,
      subject_ref: row.subject_ref,
      deep_link: row.deep_link,
      created_at: row.created_at,
      read_at: row.read_at ?? null,
      delivery: (Array.isArray(row.delivery) ? row.delivery : [])
        .map(entry => ({ channel: entry.channel, state: entry.state })),
    })),
  };
}

export function notificationTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "acknowledge-notification": {
      write: true,
      description: "Mark one notification addressed to the acting actor as read. It writes ops.notification_read and nothing else: no notification row and no source task row moves.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        notification_id: { type: "string" },
      }, required: ["idempotency_key", "notification_id"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "acknowledge-notification", args, async () => {
          if (!UUID.test(String(args.notification_id || ""))) {
            throw new ToolError({ error: "notification_not_found" });
          }
          const r = await c.query(
            "select ops.acknowledge_notification($1::uuid,$2::uuid) as acknowledged",
            [args.notification_id, args.idempotency_key]);
          const result = r.rows[0]?.acknowledged;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "notification_not_found",
              notification_id: args.notification_id });
          }
          if (result.deduplicated !== true) {
            await writeEvent(c, actor, "acknowledge-notification", "notification",
              args.notification_id, {
                field: "read",
                new: { read_at: result.read_at },
                cause: "automation_job",
                idempotency_key: args.idempotency_key,
              });
          }
          return { ok: true, notification_id: args.notification_id,
            read_at: result.read_at, deduplicated: result.deduplicated === true };
        }),
    },

    "notification-feed": {
      writerConnection: true,
      description: "Read the acting actor's own notification feed and unread count. There is no recipient argument: the function resolves the caller itself.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        after: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 200 },
      }, required: [] },
      handler: async (c, _a, args) => {
        const r = await c.query(
          "select ops.notification_feed_facts($1::timestamptz,$2::integer) as facts",
          [args.after ?? null, args.limit ?? 50]);
        return notificationFeedProjection(r.rows[0]?.facts, ToolError);
      },
    },
  };
}

// WR-000112 — the Doc conversation verbs.
//
// LIBRARY ONLY: no shebang and no main-module construct (the SCAC
// inventory scans for those by substring, so they are described, never spelled). This file
// is a registrySource, so every verb below becomes a NEW inventoried ingress and
// the one registerTools line it costs in tools.js re-digests every mcp-tool row
// sourced from that file.
//
// THE CONNECTION FLAGS ARE PART OF THE CONTRACT. mcp.js decides the connection
// from three flags and nothing else, and a grant the connection cannot exercise
// is a run-time permission error that reads like a missing grant:
//   - ops.append_doc_conversation_turn is granted to carr_authority, so
//     add-doc-conversation-turn declares authorityOnly: true;
//   - ops.doc_conversation_facts is granted to carr_writer/carr_authority and
//     needs the acting-actor context, which only the writer path installs, so
//     read-doc-conversation declares writerConnection: true and no write flag.
// Fix the flag, never widen the grant.

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * The read verb's shaper. Pure: it takes the definer function's facts and
 * returns the projection, refusing anything that is not the shape it expects
 * rather than passing an unknown object through to a caller.
 */
export function docConversationProjection(facts, ToolError) {
  if (!facts || typeof facts !== "object" || facts.ok !== true) {
    throw new ToolError({ error: "doc_conversation_not_found" });
  }
  const identity = facts.identity || {};
  return {
    ok: true,
    identity: {
      id: identity.id,
      title: identity.title,
      visibility: identity.visibility,
      pinned_at: identity.pinned_at ?? null,
      archived_at: identity.archived_at ?? null,
      version: identity.version,
      created_by: identity.created_by,
    },
    turns: (Array.isArray(facts.turns) ? facts.turns : []).map(turn => ({
      sequence: turn.sequence,
      role: turn.role,
      body: turn.body,
      msg_id: turn.msg_id,
      origin_channel: turn.origin_channel,
      origin_actor: turn.origin_actor,
      at: turn.at,
    })),
    latest_sequence: facts.latest_sequence,
    more: facts.more === true,
    effective_grants: (Array.isArray(facts.effective_grants) ? facts.effective_grants : [])
      .map(grant => ({
        grantee_actor: grant.grantee_actor,
        granted_at: grant.granted_at,
        granted_by_actor: grant.granted_by_actor,
      })),
    visible_conversation_count: facts.visible_conversation_count ?? null,
  };
}

export function docConversationTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "add-doc-conversation-turn": {
      write: true,
      // Its append function is granted to carr_authority ONLY.
      authorityOnly: true,
      description: "Append one turn to a Doc conversation. Sequence, origin channel and origin actor are derived server-side; a caller may not name any of them.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        conversation_id: { type: "string" },
        role: { type: "string", enum: ["human", "assistant", "system"] },
        body: { type: "string" },
        msg_id: { type: "string" },
      }, required: ["idempotency_key", "conversation_id", "role", "body"] },
      handler: async (c, actor, args) =>
        withEnvelope(c, actor, "add-doc-conversation-turn", args, async () => {
          if (!UUID.test(String(args.conversation_id || ""))) {
            throw new ToolError({ error: "doc_conversation_id_invalid",
              hint: "conversation_id is the uuid ops.doc_conversation assigned" });
          }
          if (typeof args.body !== "string" || !args.body.trim() || args.body.length > 20000) {
            throw new ToolError({ error: "doc_conversation_body_invalid",
              hint: "a turn body is non-empty and at most 20000 characters" });
          }
          if (args.msg_id !== undefined && !UUID.test(String(args.msg_id))) {
            throw new ToolError({ error: "doc_conversation_msg_id_invalid" });
          }
          // Minted here when absent, exactly as add-room-turn mints one: the
          // transport identity is the server's to choose.
          const msgId = args.msg_id || crypto.randomUUID();
          const appended = await c.query(
            "select ops.append_doc_conversation_turn($1::uuid,$2::text,$3::text,$4::uuid,$5::uuid) as appended",
            [args.conversation_id, args.role, args.body, msgId, args.idempotency_key]);
          const result = appended.rows[0]?.appended;
          if (!result || result.ok !== true) {
            throw new ToolError({ error: result?.reason_id || "doc_conversation_append_refused",
              conversation_id: args.conversation_id, msg_id: msgId });
          }
          if (result.deduplicated !== true) {
            await writeEvent(c, actor, "add-doc-conversation-turn", "doc_conversation",
              args.conversation_id, {
                field: "turn",
                new: { sequence: result.sequence, role: args.role },
                cause: "automation_job",
                idempotency_key: args.idempotency_key,
              });
          }
          return { ok: true, deduplicated: result.deduplicated === true,
            conversation_id: args.conversation_id, sequence: result.sequence,
            msg_id: result.msg_id, origin_actor: result.origin_actor };
        }),
    },

    "read-doc-conversation": {
      // A read-only transaction on the identity that carries the acting-actor
      // context. On the reader connection that context is never installed.
      writerConnection: true,
      description: "Read one Doc conversation the acting actor may see: its identity, its turns in sequence order, and the effective access list.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        conversation_id: { type: "string" },
        after_sequence: { type: "integer", minimum: 0 },
        limit: { type: "integer", minimum: 1, maximum: 200 },
      }, required: ["conversation_id"] },
      handler: async (c, a, args) => {
        if (!UUID.test(String(args.conversation_id || ""))) {
          throw new ToolError({ error: "doc_conversation_not_found" });
        }
        const r = await c.query(
          "select ops.doc_conversation_facts($1::uuid,$2::text,$3::integer,$4::integer) as facts",
          [args.conversation_id, a.id, args.after_sequence ?? 0, args.limit ?? 200]);
        if (!r.rows[0]?.facts?.ok) throw new ToolError({ error: "doc_conversation_not_found" });
        return docConversationProjection(r.rows[0].facts, ToolError);
      },
    },
  };
}

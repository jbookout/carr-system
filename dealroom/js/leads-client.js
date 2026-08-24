import { uuidv4 } from "./uuid.js";

/** A deliberately small MCP client: this board reads the complete lead universe
 * and can make one bounded change, stage. Authentication remains the host's
 * same-origin cookie; there is no client-side identity or alternate endpoint. */
export function createLeadBoardClient(options = {}) {
  const fetchImpl = options.fetchImpl || ((path, init) => fetch(path, init));
  const uuid = options.uuid || uuidv4;
  let rpcId = 0;

  function typedError(payload, fallback = "The lead board request was refused.") {
    const error = new Error(payload?.message || payload?.hint || fallback);
    error.code = payload?.error || payload?.code || "tool_error";
    error.payload = payload || {};
    return error;
  }

  async function rpc(name, args = {}) {
    let response;
    try {
      response = await fetchImpl("/mcp", {
        method: "POST",
        credentials: "same-origin",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", id: ++rpcId, method: "tools/call", params: { name, arguments: args } }),
      });
    } catch (cause) {
      const error = new Error("The Lead Board could not reach the server.");
      error.code = "network_error";
      error.cause = cause;
      throw error;
    }
    const envelope = await response.json().catch(() => null);
    if (!response.ok) throw typedError(envelope, `The Lead Board request failed (${response.status}).`);
    if (envelope?.error) throw typedError(envelope.error, "The Lead Board request was refused.");
    const text = envelope?.result?.content?.find((item) => item.type === "text")?.text;
    let payload;
    try { payload = text ? JSON.parse(text) : null; }
    catch { throw typedError(null, "The Lead Board returned an unreadable response."); }
    if (envelope?.result?.isError || payload?.error || payload?.ok === false) throw typedError(payload);
    return payload;
  }

  return {
    getLeadBoard: () => rpc("lead-board"),
    moveLeadStage(lead, stage) {
      return rpc("update-lead", {
        lead: lead.registry_ref || lead.id,
        base_version: lead.base_version,
        fields: { stage },
        idempotency_key: uuid(),
      });
    },
  };
}

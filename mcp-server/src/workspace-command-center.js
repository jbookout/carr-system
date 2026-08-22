import { readDealRoomDeals } from "./dealroom-read-service.js";

export const DEAL_ATTENTION_PATH = "/api/v1/workspace/command-center/deal-attention";
export const DEAL_ATTENTION_DESTINATION = "/deals?workspace=team&filter=flagged&owner=me";

export async function readDealAttentionSummary({ client, actor, now = () => new Date() }) {
  const deals = await readDealRoomDeals(client, {
    workspace: "team",
    owner: actor.slug,
    operatingState: "active",
  });
  const ownedActive = deals.filter((deal) => deal.workspace_kind === "team" && deal.owner === actor.slug &&
    (deal.operating_state || "active") === "active");
  const observedAt = now();
  const flagged = ownedActive.filter((deal) => deal.attention === true).length;
  return {
    schema_version: "workspace-command-center-deal-attention/v1",
    state: flagged ? "attention" : "empty",
    actor: { slug: actor.slug },
    source: { kind: "canonical_view", ref: "v_deal_room_board" },
    observed_at: observedAt.toISOString(),
    // The projection has no event-wide freshness clock. The successful read
    // time is reported separately and must not be promoted into event truth.
    freshness: { status: "unknown", basis: "read_time_only" },
    summary: { owned_active: ownedActive.length, owned_flagged: flagged },
    destination: DEAL_ATTENTION_DESTINATION,
  };
}

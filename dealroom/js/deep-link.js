const EXACT_KEYS = ["filter", "owner", "workspace"];

export function resolveDealroomDeepLink(search) {
  const params = new URLSearchParams(search || "");
  if ([...params.keys()].sort().join(",") !== EXACT_KEYS.join(",")) return null;
  if (params.get("workspace") !== "team" || params.get("filter") !== "flagged" || params.get("owner") !== "me") return null;
  return { workspace: "team", filter: "flagged", owner: "me" };
}

export function dealMatchesDeepLink(deal, selfActor, selection) {
  if (!selection) return true;
  return deal.workspace_kind === "team" && (deal.operating_state || "active") === "active" &&
    deal.attention === true && deal.owner === selfActor;
}

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

const checks = {
  accessibility(context) {
    requireCondition(context.html.includes('class="skip-link"'), "skip link missing");
    requireCondition(context.css.includes("min-height: 44px"), "touch-target floor missing");
    requireCondition(context.css.includes("prefers-reduced-motion"), "reduced-motion contract missing");
  },
  aiBoundary(context) {
    requireCondition(context.html.includes("Doc cannot send, publish, spend, change ownership, move phases, or deploy"), "Doc authority boundary missing");
    requireCondition(!/method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i.test(context.app + context.client), "mutation transport present");
  },
  aiDisplacement(context) {
    const more = context.fixtures.get("more").states.normal;
    const cutover = more.destinations.find(item => Array.isArray(item.baselines));
    requireCondition(cutover?.text === "Can Joe or Dell now complete and understand this routine without opening Claude Code or Codex?", "exact cutover question missing");
    requireCondition(cutover.baselines.length === 5 && cutover.baselines.every(item => item.value === null && item.status === "Unknown — not yet measured"), "baseline values must remain unknown");
  },
  call(context) {
    const call = context.fixtures.get("call-review").states.normal.call;
    for (const group of ["facts", "decisions", "objectives_deliverables", "joe_actions", "dell_actions", "outside_party_needs", "draft_communications", "initiated_work_history"]) requireCondition(Array.isArray(call.sections[group]), `call section ${group} missing`);
    requireCondition(call.retention.purge_eligible === false, "retention must begin gated");
    requireCondition(context.app.includes("consent-confirmed") && context.app.includes("complete-call"), "call journey controls missing");
  },
  commandCenter(context) {
    const home = context.fixtures.get("command-center").states.normal;
    requireCondition(home.headline === `${home.items.length} decisions need Joe`, "headline count mismatch");
    requireCondition(home.items.every(item => item.owner === "Joe"), "partner-specific owner mismatch");
    requireCondition(!JSON.stringify(home.items).match(/lead outreach/i), "named lead outreach leaked to home");
  },
  deal(context) {
    const deal = context.fixtures.get("deal-room").states.normal;
    requireCondition(deal.parking_reasons.includes("Other"), "Other parking reason missing");
    requireCondition(Object.values(deal.record.active_pressure).every(Boolean), "active-pressure starting twin invalid");
    requireCondition(context.app.includes("Context is required when the parking reason is Other"), "Other context guard missing");
    requireCondition(context.app.includes("Confirm synthetic restore"), "restore control missing");
  },
  doc(context) {
    const request = context.fixtures.get("doc-request").states.normal.request;
    requireCondition(request.state === "draft", "request must start draft");
    requireCondition(context.app.includes("request-add-evidence") && context.app.includes("request-complete"), "evidence-gated lifecycle missing");
    requireCondition(!context.app.includes("request-state"), "unsafe lifecycle dropdown present");
  },
  externalBoundary(context) {
    requireCondition(!/method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i.test(context.app + context.client), "external mutation method present");
    requireCondition(context.app.includes("Sending is structurally absent"), "no-send statement missing");
  },
  fixtureContract(context) {
    for (const fixture of context.fixtures.values()) {
      requireCondition(fixture.synthetic === true, "non-synthetic fixture");
      requireCondition(Object.keys(fixture.states).length === 10, "fixture state count mismatch");
    }
  },
  lead(context) {
    requireCondition(context.fixtures.get("lead-board").states.normal.items.length > 0, "lead twin missing");
    requireCondition(context.app.includes("Named lead outreach reminders appear only here"), "lead ownership boundary missing");
  },
  marketing(context) {
    requireCondition(context.app.includes("Publishing, media spend, audience changes, and outbound action are structurally unavailable"), "marketing gate missing");
  },
  notification(context) {
    const notices = context.fixtures.get("notifications").states.normal.items;
    requireCondition(notices.every(item => item.destination), "notification deep link missing");
  },
  offline(context) {
    for (const fixture of context.fixtures.values()) requireCondition(fixture.states.offline.freshness.status === "offline", `${fixture.surface} offline truth missing`);
  },
  release(context) {
    requireCondition(context.server.includes('new Set(["GET", "HEAD", "OPTIONS"])'), "static method allow-list missing");
    requireCondition(context.server.includes("405"), "unsupported-method refusal missing");
  },
  security(context) {
    const fixtures = JSON.stringify([...context.fixtures.values()]);
    requireCondition(!/@[a-z0-9.-]+\.[a-z]{2,}/i.test(fixtures), "email-shaped fixture value");
    requireCondition(!/\b(?:sk|pk)_(?:live|test)_[a-z0-9]+\b/i.test(fixtures), "credential-shaped fixture value");
    requireCondition(context.html.includes("synthetic prototype"), "synthetic boundary missing");
  },
  surface(context) {
    requireCondition(context.fixtures.size === 9, "surface count mismatch");
    requireCondition(context.app.includes("surface-registry") || context.app.includes("registry destinations"), "surface registry/cutover disclosure missing");
  },
  tour(context) {
    const tour = context.fixtures.get("tour").states.normal.tour;
    requireCondition(tour.stops.every(stop => stop.marker === stop.order), "map/list parity mismatch");
    requireCondition(tour.stops.filter(stop => stop.locked).length >= 1, "locked appointment missing");
    for (const action of ["confirm-route", "capture-note", "capture-photo", "tour-offline", "resolve-tour-conflict", "tour-resume", "complete-tour-review"]) requireCondition(context.app.includes(action), `tour action ${action} missing`);
  }
};

function publicBoundary(context) {
  requireCondition(context.server.includes("const publicFiles = new Map"), "public asset allow-list missing");
  requireCondition(context.server.includes("const fixtureFiles = new Set"), "fixture allow-list missing");
  requireCondition(!context.server.includes("pathname.slice(1)"), "arbitrary workspace path serving remains");
}

function secretScan(context) {
  const fixtureText = JSON.stringify([...context.fixtures.values()]);
  requireCondition(!/\b(?:sk|pk)_(?:live|test)_[a-z0-9]+\b/i.test(fixtureText), "credential-shaped fixture value");
  requireCondition(!/"(?:password|access_token|refresh_token|client_secret)"\s*:/i.test(fixtureText), "secret-shaped fixture key");
}

const groups = {
  accessibility: ["A11Y-001", "RESPONSIVE-001"],
  aiBoundary: ["AI-AUTH-001", "AI-INJECT-001", "AI-LEAK-001", "AI-SAFE-001", "AI-TARGET-001", "DOC-PROP-001", "STATE-REFUSE-001"],
  aiDisplacement: ["AI-DISPLACEMENT-001"],
  call: ["CALL-CAPTURE-001", "CALL-CONSENT-001", "CALL-RET-001", "CALL-REVIEW-001", "CALL-REVIEW-002", "CALL-SPEAKER-001", "FLOW-WS-03"],
  commandCenter: ["CC-FLOW-001", "CC-NO-LEAD-001", "FLOW-WS-01", "OBS-ROUTE-001"],
  deal: ["DEAL-PARK-001", "DEAL-PARK-002", "DEAL-RESTORE-001", "DEAL-UX-001", "FLOW-WS-02"],
  doc: ["BRIDGE-SAFE-001", "DOC-ENG-001", "FLOW-WS-04"],
  externalBoundary: ["EXT-NO-PUBLISH-001", "EXT-NO-SEND-001"],
  fixtureContract: ["CONTRACT-WRITE-001"],
  lead: ["LEAD-CONVERT-001", "LEAD-TOUCH-001"],
  marketing: ["MKT-DECISION-001"],
  notification: ["NOTIFY-001"],
  offline: ["OFFLINE-TRUTH-001", "SYNC-CONFLICT-001"],
  release: ["AUTH-KV-001", "AUTH-RECOVERY-001", "RELEASE-GATE-001"],
  security: ["PRIV-REDACT-001", "PRIV-STERILE-001", "SEC-AUTH-001", "SEC-AUTHZ-001", "SEC-CSRF-001", "SEC-DEVICE-001", "SEC-PUBLIC-001", "SEC-REPLAY-001", "SEC-SECRET-001"],
  surface: ["SURFACE-PARITY-001", "SURFACE-WRITER-001"],
  tour: ["FLOW-WS-05", "TOUR-ROUTE-001", "TOUR-SYNC-001"]
};

export const testRegistry = new Map(Object.entries(groups).flatMap(([checkName, ids]) => ids.map(id => [id, checks[checkName]])));
export const futureGateIds = new Set([
  "FRONTDOOR-USAGE-001",
  "AI-INJECT-001", "AI-LEAK-001", "AI-TARGET-001", "CALL-SPEAKER-001", "CONTRACT-WRITE-001",
  "SURFACE-PARITY-001", "SURFACE-WRITER-001",
  "SEC-AUTH-001", "SEC-AUTHZ-001", "SEC-CSRF-001", "SEC-DEVICE-001", "SEC-REPLAY-001",
  "AUTH-KV-001", "AUTH-RECOVERY-001", "RELEASE-GATE-001"
]);
for (const id of futureGateIds) testRegistry.delete(id);
testRegistry.set("SEC-PUBLIC-001", publicBoundary);
testRegistry.set("SEC-SECRET-001", secretScan);

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { deriveJobPassports, jobPassportStatusLabel, parseJobPassportReceipt } from "../../dealroom/js/job-passport.js";

const fixture = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.observatory-projection.v1.json", import.meta.url)));
const portfolio = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/carr-evaluation-kernel.synthetic.v1.json", import.meta.url)));
const spatial = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.spatial-surface.v1.json", import.meta.url)));
const roomSource = fs.readFileSync(new URL("../../dealroom/js/room.js", import.meta.url), "utf8");
const roomCss = fs.readFileSync(new URL("../../dealroom/css/room.css", import.meta.url), "utf8");
const turn = (payload, seq = 1) => ({ seq: String(seq), kind: "receipt", at: "2026-08-24T12:00:06Z", body: JSON.stringify(payload) });
const wrap = (kind, payload) => ({ job_passport: { schema_version: "job-passport-wire.v1", kind, payload } });

test("Job Passport parses only strict typed wire wrappers", () => {
  assert.equal(parseJobPassportReceipt("not json").ok, false);
  assert.equal(parseJobPassportReceipt('{"job_passport":{"schema_version":"job-passport-wire.v1","kind":"nope","payload":{}}}').ok, false);
  assert.deepEqual(parseJobPassportReceipt(JSON.stringify(wrap("progress_event", { schema_version: "progress-event.v1" }))),
    { ok: true, kind: "progress_event", payload: { schema_version: "progress-event.v1" } });
});

test("the deterministic panel keeps profile identity separate from model staffing", () => {
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture))], { now: Date.parse("2026-08-24T12:00:10Z") });
  assert.equal(model.enabled, true);
  assert.equal(model.passports[0].attempt_lane.persistent_profile.display_label, "Doc");
  assert.equal(model.passports[0].attempt_lane.actual_staffing.model_id, "model:codex-synthetic");
  assert.equal(model.passports[0].status, "verified_complete");
  assert.equal(jobPassportStatusLabel(model.passports[0].status), "Independently verified complete");
});

test("newer canonical state wins, stale receipts do not overwrite it, and same-version conflicts stay visible", () => {
  const newer = structuredClone(fixture);
  newer.source_state.state_version = 2;
  newer.source_state.canonical_record_digest = "sha256:" + "9".repeat(64);
  newer.generated_at = "2026-08-24T12:00:08Z";
  const stale = structuredClone(fixture);
  const conflict = structuredClone(newer);
  conflict.source_state.canonical_record_digest = "sha256:" + "8".repeat(64);
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("observatory_projection", newer), 2),
    turn(wrap("observatory_projection", stale), 3), turn(wrap("observatory_projection", conflict), 4),
  ]);
  assert.equal(model.passports[0].source_state.state_version, 2);
  assert.equal(model.passports[0].status, "unknown_partial", "a conflicting same-version receipt cannot look certain");
  assert.ok(model.rejected.some((row) => row.reason === "stale_projection"));
  assert.ok(model.rejected.some((row) => row.reason === "same_version_conflict"));
});

test("status distinctions do not invent deviation from quiet or filesystem-only observation", () => {
  const cases = {
    aligned: ["active", [], "unknown"], deviation_candidate: ["active", [{ candidate_id: "candidate:one" }], "unknown"],
    blocked: ["blocked", [], "unknown"], quiet: ["quiet", [], "filesystem_only"], stale: ["stale", [], "stale_signal"],
    failed: ["failed", [], "unknown"], unknown_partial: ["unknown", [], "unknown"],
  };
  for (const [expected, [progress, candidates, uncertainty]] of Object.entries(cases)) {
    const value = structuredClone(fixture);
    value.state.progress = progress; value.observed_movement.progress_state = progress;
    value.observed_movement.deviation_candidates = candidates; value.observed_movement.uncertainty = uncertainty;
    if (expected === "failed") value.state.lifecycle = "failed";
    const result = deriveJobPassports([turn(wrap("observatory_projection", value))]);
    assert.equal(result.passports[0].status, expected, expected);
  }
});

test("typed facts count on the wire but cannot create a visual job from transcript-like data", () => {
  const model = deriveJobPassports([
    turn(wrap("execution_envelope", { schema_version: "execution-envelope.v1" })),
    turn(wrap("progress_event", { schema_version: "progress-event.v1" })),
    turn(wrap("attempt_receipt", { schema_version: "attempt-receipt.v1" })),
  ]);
  assert.equal(model.enabled, false);
  assert.deepEqual(model.typedCounts, { execution_envelope: 1, progress_event: 1, attempt_receipt: 1, observatory_projection: 0, evaluation_kernel: 0, eval_portfolio: 0, spatial_surface: 0 });
});

test("spatial Home Zone binds exact visual state, preserves list parity, and withholds stale/conflicting views", () => {
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("spatial_surface", spatial), 2)]);
  const view = model.passports[0].spatial_surface;
  assert.equal(view.home_zone.return_label, "Return to Job Passport Home");
  assert.deepEqual(view.list_order, view.nodes.map((node) => node.node_id));
  assert.equal(view.nodes.find((node) => node.node_type === "attempt_lane").resource_refs[0], "native:codex-thread-1");
  const stale = structuredClone(spatial); stale.canonical_binding.state_version = 0;
  const mismatch = structuredClone(spatial); mismatch.canonical_binding.source_projection_digest = "sha256:" + "0".repeat(64);
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("spatial_surface", stale), 2), turn(wrap("spatial_surface", mismatch), 3)]);
  assert.equal(withheld.passports[0].spatial_surface, null);
  assert.ok(withheld.rejected.some((row) => row.reason === "mismatched_spatial_surface"));
  assert.match(roomSource, /Job Passport Home Zone/);
  assert.match(roomSource, /Telemetry: unavailable/);
  assert.match(roomCss, /passport-spatial-list/);
});

test("eval ladder binds to the exact visual projection and keeps critical regression visible without a score", () => {
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("evaluation_kernel", portfolio), 2),
  ]);
  assert.equal(model.passports[0].eval_portfolio.portfolio_id, "portfolio:carr-synthetic-read-only");
  assert.deepEqual([...new Set(model.passports[0].eval_portfolio.cases.map((row) => row.rung))], ["smoke", "regression", "hill_climb", "launch"]);
  const candidate = model.passports[0].eval_portfolio.results.find((row) => row.result_id === "result:cheaper-candidate");
  assert.equal(candidate.dimension_results.find((row) => row.dimension_id === "visual_accessibility").direction_vs_baseline, "regressed");
  const mismatched = structuredClone(portfolio); mismatched.binding.projection_digest = "sha256:" + "0".repeat(64);
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("evaluation_kernel", mismatched), 2)]);
  assert.equal(withheld.passports[0].eval_portfolio, null);
  assert.ok(withheld.rejected.some((row) => row.reason === "mismatched_eval_portfolio"));
  assert.match(roomSource, /no aggregate score/);
  assert.match(roomCss, /passport-eval-matrix/);
});

test("a malformed projection digest is visibly withheld before it can paint", () => {
  const malformed = structuredClone(fixture);
  malformed.projection_digest = "not-a-digest";
  const model = deriveJobPassports([turn(wrap("observatory_projection", malformed))]);
  assert.equal(model.enabled, false);
  assert.deepEqual(model.rejected, [{ seq: 1, reason: "invalid_projection" }]);
});

test("regression guards preserve keyboard drilldown across a poll and a real mobile grid row", () => {
  assert.match(roomSource, /card\.dataset\.detailOpen = "true"/);
  assert.match(roomSource, /detail\.open = card\.dataset\.detailOpen === "true"/);
  assert.match(roomSource, /event\.key === "Enter" \|\| event\.key === " "/);
  assert.match(roomCss, /minmax\(280px, max-content\)/);
  assert.match(roomCss, /\.job-passport \{ overflow: visible; min-height: max-content; \}/);
});

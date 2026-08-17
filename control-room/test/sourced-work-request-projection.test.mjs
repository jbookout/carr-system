import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = relative => JSON.parse(fs.readFileSync(path.join(ROOT, relative), "utf8"));
const source = relative => fs.readFileSync(path.join(ROOT, relative), "utf8");
const UUID = /\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b/i;

test("sourced captured-request projection is a closed, versioned read contract", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.version, "1.0.0");
  assert.match(contract.status, /^phase1_/);
  assert.equal(contract.transport, "authenticated MCP-backed read, not implemented by this static prototype");
  assert.deepEqual(contract.lifecycle, {canonical_from: ["captured", "triaged", "ready"], projection_state: "queued"});
  assert.deepEqual(contract.output.required_fields, [
    "human_ref", "title", "desired_outcome", "acceptance_criteria", "source", "triage", "plan", "pending_outcome_feedback", "outcome_feedback", "outcome_feedback_history", "accepted_feedback_count", "shape", "state", "next_human_action", "actions"
  ]);
  assert.deepEqual(contract.output.actions, []);
  assert.deepEqual(contract.output.next_human_action.captured, {label: "Review and triage", effect: "none"});
});

test("triaged projection supplies only durable triage readback and another inert next step", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.output.triage.captured, null);
  assert.deepEqual(contract.output.triage.triaged, ["classification", "human_actor_slug", "triaged_at"]);
  assert.match(contract.output.triage.rule, /never an action authority/i);
  assert.deepEqual(contract.output.next_human_action, {
    captured: {label: "Review and triage", effect: "none"},
    triaged: {label: "Prepare scope and acceptance", effect: "none"},
    ready: {label: "Plan accepted", effect: "none"},
    ready_with_pending_feedback: {label: "Review outcome feedback", effect: "none"},
    ready_with_accepted_feedback: {label: "Outcome feedback accepted", effect: "none"}
  });
});

test("accepted outcome feedback is bounded history, never a completion or displacement claim", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.deepEqual(contract.output.outcome_feedback.outcome_values, ["criteria_met", "criteria_not_met", "inconclusive"]);
  assert.match(contract.output.outcome_feedback.rule, /never execution, completion, release, approval, or a state transition/i);
  assert.match(contract.output.outcome_feedback_history.rule, /last 20 human-accepted/i);
  assert.match(contract.output.outcome_feedback_history.rule, /pending proposals never appear/i);
  assert.match(contract.output.accepted_feedback_count, /not a completion count/i);
  assert.equal(contract.prohibitions.includes("interpret accepted outcome feedback as success, displacement, execution, completion, or a state transition"), true);
});

test("pending feedback is a distinct reload-safe proposal and never contaminates accepted history", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.output.pending_outcome_feedback.status, "pending_human_acceptance");
  assert.match(contract.output.pending_outcome_feedback.rule, /exact current ready Work Request version and accepted plan/i);
  assert.match(contract.output.pending_outcome_feedback.rule, /never appears in accepted history/i);
  assert.match(contract.output.pending_outcome_feedback.rule, /never claims acceptance, execution, completion, success, release, or displacement/i);
});

test("the safe card names only durable human references and source evidence", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.output.human_ref, "durable human-readable Work Request reference; never a database UUID");
  assert.equal(contract.output.source.required_fields.join(","), "label,freshness,provenance");
  assert.match(JSON.stringify(contract), /exact source label/i);
  assert.doesNotMatch(JSON.stringify(contract), UUID);
  assert.doesNotMatch(JSON.stringify(contract), /business_payload|raw_query|client_payload/i);
});

test("Review and triage is an inert navigation affordance, never an action route", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.deepEqual(contract.output.next_human_action.captured, {
    label: "Review and triage",
    effect: "none"
  });
  assert.deepEqual(contract.output.next_human_action.triaged, {
    label: "Prepare scope and acceptance",
    effect: "none"
  });
  assert.equal(contract.action_card, undefined, "the card shape has one next_human_action home");
  const controlRoom = source("public/js/app.js");
  assert.doesNotMatch(controlRoom, /method\s*:\s*["'](?:POST|PUT|PATCH|DELETE)["']/i);
  assert.doesNotMatch(controlRoom, /\/mcp\b/i);
  assert.doesNotMatch(controlRoom, /data-action=["'](?:dispatch|claim|approve|execute)/i);
  assert.match(JSON.stringify(contract.prohibitions), /POST, dispatch, claim, approve, or execute routes/);
});

test("the projection cannot derive state, completion, or controls from UI copy", () => {
  const contract = read("contracts/sourced-work-request-projection.v1.json");
  assert.equal(contract.prohibitions.includes("derive canonical state from a UI label"), true);
  assert.equal(contract.prohibitions.includes("claim completion before confirmed_closed"), true);
  assert.equal(contract.prohibitions.includes("invent action controls"), true);
  assert.equal(contract.prohibitions.includes("accept a client-supplied tenant, actor, or source reference"), true);
  assert.match(contract.integration_boundary, /authenticated server-side reader/i);
});

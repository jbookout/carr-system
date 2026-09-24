#!/usr/bin/env node
// recovery-matrix-evaluate.mjs — evaluate V5-F08 recovery evidence from a JSON file.
//
//   node mcp-server/bin/recovery-matrix-evaluate.mjs restore  <restore-exercise-receipt.json>
//   node mcp-server/bin/recovery-matrix-evaluate.mjs rpo      <pitr-restore-proof.json>
//   node mcp-server/bin/recovery-matrix-evaluate.mjs matrix   <matrix-evidence.json>
//   node mcp-server/bin/recovery-matrix-evaluate.mjs outbound <outbound-request.json>
//   node mcp-server/bin/recovery-matrix-evaluate.mjs degraded <dependency> [<dependency> ...]
//   node mcp-server/bin/recovery-matrix-evaluate.mjs policy
//
// `rpo` evaluates one RPO evidence block (bin/pitr-restore-proof.sh output) at
// the current instant. `matrix` supplies the repository's sealed business
// calendar (ops/config/business-calendar.us-federal.json) when the evidence
// does not carry one; the evaluator refuses any calendar but the pinned one.
//
// Read-only: it reads local files and prints the verdict. Exit 0 when the
// verdict is pass / reconciled, 1 when it is not, 2 when the evidence cannot be
// read at all (a contract violation, never a policy answer).

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  evaluateRestoreExercise,
  evaluateRecoveryMatrix,
  evaluateOutboundQueueRelease,
  v5DegradedModeProjection,
  v5RecoveryMatrixPolicyDigest,
} from "../src/recovery-matrix.v5.js";

const CALENDAR_PATH = fileURLToPath(new URL("../../ops/config/business-calendar.us-federal.json", import.meta.url));
const [cmd, ...rest] = process.argv.slice(2);
const read = path => JSON.parse(readFileSync(path, "utf8"));

try {
  let result;
  let passed;
  if (cmd === "restore") {
    result = evaluateRestoreExercise(read(rest[0]));
  } else if (cmd === "rpo") {
    const matrix = evaluateRecoveryMatrix({
      observed_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
      cells: { record_layer_rpo: read(rest[0]) },
    });
    result = { observed_at: matrix.observed_at, policy_digest: matrix.policy_digest, record_layer_rpo: matrix.cells.record_layer_rpo };
    passed = result.record_layer_rpo.state === "pass";
  } else if (cmd === "matrix") {
    const evidence = read(rest[0]);
    if (evidence.business_calendar === undefined) evidence.business_calendar = read(CALENDAR_PATH);
    result = evaluateRecoveryMatrix(evidence);
  } else if (cmd === "outbound") {
    result = evaluateOutboundQueueRelease(read(rest[0]));
  } else if (cmd === "degraded") {
    result = v5DegradedModeProjection(rest);
  } else if (cmd === "policy") {
    result = { policy_digest: v5RecoveryMatrixPolicyDigest() };
  } else {
    process.stderr.write("usage: recovery-matrix-evaluate.mjs restore|rpo|matrix|outbound <file.json> | degraded <dep>... | policy\n");
    process.exit(2);
  }
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  if (passed === undefined) passed = !["fail", "hold"].includes(result.decision);
  process.exit(passed ? 0 : 1);
} catch (error) {
  process.stderr.write(`recovery-matrix-evaluate: ${error.code ?? "error"}: ${error.message}\n`);
  process.exit(2);
}

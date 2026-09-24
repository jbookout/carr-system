#!/usr/bin/env node
// recovery-matrix-evaluate.mjs — evaluate V5-F08 recovery evidence from a JSON file.
//
//   node mcp-server/bin/recovery-matrix-evaluate.mjs restore  <restore-exercise-receipt.json>
//   node mcp-server/bin/recovery-matrix-evaluate.mjs matrix   <matrix-evidence.json>
//   node mcp-server/bin/recovery-matrix-evaluate.mjs outbound <outbound-request.json>
//   node mcp-server/bin/recovery-matrix-evaluate.mjs degraded <dependency> [<dependency> ...]
//   node mcp-server/bin/recovery-matrix-evaluate.mjs policy
//
// Read-only: it reads one local file and prints the verdict. Exit 0 when the
// verdict is pass / reconciled, 1 when it is not, 2 when the evidence cannot be
// read at all (a contract violation, never a policy answer).

import { readFileSync } from "node:fs";
import {
  evaluateRestoreExercise,
  evaluateRecoveryMatrix,
  evaluateOutboundQueueRelease,
  v5DegradedModeProjection,
  v5RecoveryMatrixPolicyDigest,
} from "../src/recovery-matrix.v5.js";

const [cmd, ...rest] = process.argv.slice(2);
const read = path => JSON.parse(readFileSync(path, "utf8"));

try {
  let result;
  if (cmd === "restore") result = evaluateRestoreExercise(read(rest[0]));
  else if (cmd === "matrix") result = evaluateRecoveryMatrix(read(rest[0]));
  else if (cmd === "outbound") result = evaluateOutboundQueueRelease(read(rest[0]));
  else if (cmd === "degraded") result = v5DegradedModeProjection(rest);
  else if (cmd === "policy") result = { policy_digest: v5RecoveryMatrixPolicyDigest() };
  else {
    process.stderr.write("usage: recovery-matrix-evaluate.mjs restore|matrix|outbound <file.json> | degraded <dep>... | policy\n");
    process.exit(2);
  }
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
  process.exit(["fail", "hold"].includes(result.decision) ? 1 : 0);
} catch (error) {
  process.stderr.write(`recovery-matrix-evaluate: ${error.code ?? "error"}: ${error.message}\n`);
  process.exit(2);
}

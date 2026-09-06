#!/usr/bin/env node
// Minimal Claude-facing MCP proxy for the three Claude continuity verbs.
// The bearer stays in the existing 0600 token file and never enters Claude's
// JSON configuration, argv, transcript, or tracked source.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

import { selectLocalClientCredential, tokenFileSecurityIssue } from "./local-client-auth.mjs";

const ALLOWED = new Set(["claude-checkpoint", "claude-read-recovery", "claude-record-event"]);
const TOKEN_FILE = process.env.CARR_MCP_ENV || path.join(os.homedir(), ".config/carr/mcp-tokens.env");
const URL = process.env.CARR_MCP_URL || "https://api.doctorcre.com/mcp";
const MAX_MESSAGE_BYTES = 2_000_000;

function credential() {
  const stat = fs.lstatSync(TOKEN_FILE);
  const issue = tokenFileSecurityIssue({
    mode: stat.mode, uid: stat.uid, isFile: stat.isFile(),
    isSymbolicLink: stat.isSymbolicLink(),
  }, process.getuid?.());
  if (issue) throw new Error(`refusing MCP token file: ${issue}`);
  const selected = selectLocalClientCredential(
    { ...process.env, CARR_MCP_CLIENT_PROFILE: "claude-continuity" },
    fs.readFileSync(TOKEN_FILE, "utf8"),
  );
  if (!selected.token) throw new Error(`missing ${selected.tokenVariable} in secure token file`);
  return selected.token;
}

async function forward(message, token) {
  if (message.method === "tools/call" && !ALLOWED.has(message.params?.name))
    return { jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "not_in_claude_continuity_profile" } };
  const response = await fetch(URL, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify(message),
  });
  if (!response.ok) throw new Error(`Worker returned HTTP ${response.status}`);
  if (message.id === undefined) return null;
  const result = await response.json();
  if (message.method === "initialize" && result?.result) {
    result.result.serverInfo = { name: "carr-continuity", version: "1" };
    result.result.instructions =
      "Claude continuity only: record semantic milestones with claude-checkpoint using the " +
      "native activation binding and compare-and-swap expected_version. Never infer completion " +
      "from telemetry and never replay pending external effects.";
  }
  if (message.method === "tools/list" && Array.isArray(result?.result?.tools))
    result.result.tools = result.result.tools.filter(tool => ALLOWED.has(tool?.name));
  return result;
}

const token = credential();
const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of input) {
  if (!line.trim()) continue;
  let message;
  try {
    if (Buffer.byteLength(line) > MAX_MESSAGE_BYTES) throw new Error("MCP message too large");
    message = JSON.parse(line);
    const response = await forward(message, token);
    if (response) process.stdout.write(JSON.stringify(response) + "\n");
  } catch (error) {
    if (message?.id !== undefined)
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: message.id,
        error: { code: -32603, message: "continuity_proxy_failure" } }) + "\n");
    else
      process.stderr.write("carr-continuity proxy: refused malformed or oversized input\n");
  }
}

import { readFileSync, lstatSync, mkdirSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { selectLocalClientCredential, tokenFileSecurityIssue } from '../mcp-server/local-client-auth.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export async function consumeRuntimeErrors(control, callVerb) {
  const plan = await control('/_runtime-errors/plan', {});
  if (!plan.ok || !Array.isArray(plan.operations) || typeof plan.health !== 'string') throw new Error('invalid_runtime_error_plan');
  for (const operation of plan.operations) {
    if (!['add-loop', 'update-loop', 'close-loop'].includes(operation.verb)) throw new Error('invalid_runtime_error_operation');
    let args = { ...operation.args, idempotency_key: operation.key };
    if (operation.verb !== 'add-loop') {
      const { loop } = await callVerb('read-loop', { loop_id: args.loop_id });
      if (!loop || loop.loop_id !== args.loop_id || !Number.isInteger(loop.version) || loop.version < 1) throw new Error('runtime_error_loop_unreadable');
      const prepared = await control('/_runtime-errors/prepare', { fingerprint: operation.fingerprint, key: operation.key, base_version: loop.version });
      if (!prepared.ok || !prepared.operation) throw new Error('runtime_error_operation_unprepared');
      args = { ...prepared.operation.args, idempotency_key: operation.key };
    }
    const result = await callVerb(operation.verb, args);
    if (result.error === 'version_conflict') {
      await control('/_runtime-errors/refresh', { fingerprint: operation.fingerprint, key: operation.key });
      throw new Error('runtime_error_version_conflict_replan');
    }
    if (!result.ok || !result.loop_id) throw new Error('runtime_error_loop_write_failed');
    const ack = await control('/_runtime-errors/ack', { fingerprint: operation.fingerprint, key: operation.key, loop_id: result.loop_id });
    if (!ack.ok) throw new Error('runtime_error_ack_refused');
  }
  return plan.operations.length ? (await control('/_runtime-errors/plan', {})).health : plan.health;
}

function credential() {
  const path = process.env.CARR_MCP_ENV || resolve(homedir(), '.config/carr/mcp-tokens.env');
  const stat = lstatSync(path);
  const issue = tokenFileSecurityIssue({ isFile: stat.isFile(), isSymbolicLink: stat.isSymbolicLink(), mode: stat.mode, uid: stat.uid }, process.getuid?.());
  if (issue) throw new Error('runtime_error_token_file_refused');
  const selected = selectLocalClientCredential({ ...process.env, CARR_MCP_CLIENT_PROFILE: 'local' }, readFileSync(path, 'utf8'));
  if (!selected.token) throw new Error('runtime_error_token_unavailable');
  return selected.token;
}

export async function callRuntimeVerb(verb, args, root = ROOT) {
  const options = { cwd: root, encoding: 'utf8', timeout: 30000, stdio: ['ignore', 'pipe', 'pipe'], env: { PATH: process.env.PATH, HOME: homedir(), CARR_MCP_CLIENT_PROFILE: 'local', ...(process.env.CARR_MCP_URL ? { CARR_MCP_URL: process.env.CARR_MCP_URL } : {}), ...(process.env.CARR_MCP_ENV ? { CARR_MCP_ENV: process.env.CARR_MCP_ENV } : {}) } };
  try { return JSON.parse(execFileSync(resolve(root, 'run.sh'), ['call', verb, JSON.stringify(args)], options)); }
  catch (error) {
    for (const output of [error.stderr, error.stdout]) {
      const text = String(output || '');
      const start = text.indexOf('{');
      if (start < 0) continue;
      try {
        const result = JSON.parse(text.slice(start));
        if (result && typeof result === 'object' && typeof result.error === 'string') return result;
      } catch {}
    }
    throw new Error('runtime_error_verb_unavailable');
  }
}

async function main() {
  const token = credential();
  const origin = new URL(process.env.CARR_MCP_URL || 'https://api.doctorcre.com/mcp').origin;
  const control = async (path, body) => {
    const response = await fetch(origin + path, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify(body), signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error('runtime_error_control_unavailable');
    const result = await response.json();
    if (result.error) throw new Error('runtime_error_control_refused');
    return result;
  };

  const health = await consumeRuntimeErrors(control, callRuntimeVerb);
  mkdirSync(resolve(ROOT, 'out'), { recursive: true });
  writeFileSync(resolve(ROOT, 'out/runtime-error-health.json'), JSON.stringify({ checked_at: new Date().toISOString(), health }) + '\n');
  console.log(health);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { await main(); } catch { console.error('UNAVAILABLE runtime errors · owner orchestrator · repair capture/consumer connectivity · verify a captured fixture becomes one loop · auto-clear after 24h quiet on a newer release'); process.exitCode = 1; }
}

// The Python evidence gate consumes declaration facts without loading the Worker.
import { readFileSync, writeFileSync } from 'node:fs';
import { TOOLS } from '../mcp-server/src/tools.js';
import { UNREGISTERED_WRITE_PREFIXES } from '../mcp-server/src/tool-registry.js';
const path = new URL('./config/verb-completion-facts.generated.json', import.meta.url);
export function renderCompletionFacts() {
  return JSON.stringify({ schema: 'verb-completion-facts/v1',
    unknown_write_prefixes: UNREGISTERED_WRITE_PREFIXES,
    verbs: Object.fromEntries(Object.entries(TOOLS).map(([name, tool]) =>
      [name, { completionClass: tool.verbFacts.completionClass }])) }, null, 2) + '\n';
}
if (process.argv[2] === '--check') {
  if (readFileSync(path, 'utf8') !== renderCompletionFacts())
    throw new Error('completion facts drifted; run node ops/verb-completion-facts.mjs --write');
} else if (process.argv[2] === '--write') writeFileSync(path, renderCompletionFacts());

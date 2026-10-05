// A stale connector may name an operation newer than this checkout. Preserve
// the existing conservative family policy only for unknown declarations.
export const UNREGISTERED_WRITE_PREFIXES = Object.freeze([
  'accept', 'activate', 'add', 'admit', 'amend', 'append', 'approve', 'assign', 'attach', 'attest', 'begin', 'change', 'claim', 'close',
  'complete', 'confirm', 'create', 'deactivate', 'decide', 'decline', 'detach', 'end', 'link',
  'disable', 'log', 'measure', 'merge', 'new', 'patch', 'prepare', 'promote', 'propose',
  'reassign', 'record', 'register', 'release', 'resolve', 'restore', 'retire',
  'revert', 'revoke', 'score', 'seal', 'set', 'stamp', 'start', 'teach', 'triage', 'update', 'write',
]);

function freezeContract(value, seen = new Set()) {
  if ((!value || !['object', 'function'].includes(typeof value)) || seen.has(value)) return value;
  seen.add(value);
  for (const key of Reflect.ownKeys(value)) freezeContract(value[key], seen);
  return Object.freeze(value);
}

export function createToolRegistry() {
  const tools = {};
  function registerTools(additions, source) {
    const duplicates = Object.keys(additions).filter(name => Object.hasOwn(tools, name)).sort();
    if (duplicates.length) throw new Error(`duplicate tool registration from ${source}: ${duplicates.join(',')}`);
    if (!/^mcp-server\/src\/[a-z0-9.-]+\.js$/.test(source)) throw new Error(`invalid tool source: ${source}`);
    for (const tool of Object.values(additions)) {
      const writerClass = !tool.write && !tool.writerConnection ? 'reader' : tool.authorityOnly ? 'authority'
        : tool.writerConnection && !tool.write ? 'writer_read_only' : 'writer';
      const facts = { writerClass, serialization: tool.serialization || 'none',
        completionClass: tool.completionClass || (tool.write ? 'write' : 'read') };
      if (!['none', 'idempotency-key'].includes(facts.serialization) || !['read', 'write'].includes(facts.completionClass))
        throw new Error('invalid verb facts');
      Object.defineProperties(tool, {
        registrySource: { value: source, enumerable: false },
        verbFacts: { value: facts, enumerable: false },
      });
      freezeContract(tool);
    }
    Object.assign(tools, additions);
  }
  return { tools, registerTools };
}

const registry = createToolRegistry();
export const TOOLS = registry.tools;
export const registerTools = registry.registerTools;

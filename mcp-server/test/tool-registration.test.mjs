import test from 'node:test';
import assert from 'node:assert/strict';
import { createToolRegistry } from '../src/tool-registry.js';

test('declarations supply immutable writer, serialization and completion facts', () => {
  const { tools, registerTools } = createToolRegistry();
  registerTools({ 'synthetic-write': { write: true, serialization: 'idempotency-key' },
    'synthetic-read': { writerConnection: true },
    'synthetic-authority': { write: true, authorityOnly: true },
    'synthetic-evidence': { completionClass: 'write' } }, 'mcp-server/src/synthetic.js');
  assert.deepEqual(tools['synthetic-write'].verbFacts, { writerClass: 'writer', serialization: 'idempotency-key', completionClass: 'write' });
  assert.equal(tools['synthetic-read'].verbFacts.writerClass, 'writer_read_only');
  assert.equal(tools['synthetic-read'].verbFacts.completionClass, 'read');
  assert.equal(tools['synthetic-authority'].verbFacts.writerClass, 'authority');
  assert.equal(tools['synthetic-evidence'].verbFacts.completionClass, 'write');
  assert.equal(tools['synthetic-read'].registrySource, 'mcp-server/src/synthetic.js');
  assert.equal(Object.isFrozen(tools['synthetic-write'].verbFacts), true);
  assert.throws(() => registerTools({ 'synthetic-write': {} }, 'mcp-server/src/other.js'), /duplicate tool registration/);
  assert.deepEqual(Object.keys(tools).sort(), ['synthetic-authority','synthetic-evidence','synthetic-read','synthetic-write']);
});

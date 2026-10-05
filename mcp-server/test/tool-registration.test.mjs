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

test('runtime declarations register from domain sources, including former inline verbs', async () => {
  const { TOOLS } = await import('../src/tools.js');
  const { leadTools } = await import('../src/lead-tools.js');
  const declarations = leadTools();
  assert.equal(declarations['update-lead'].write, true);
  assert.equal(typeof declarations['update-lead'].handler, 'function');
  for (const [name, tool] of Object.entries(TOOLS)) {
    assert.notEqual(tool.registrySource, 'mcp-server/src/tools.js', name);
    assert.equal(Object.isFrozen(tool), true, name);
  }
});

test('registration preserves declared discovery order across domain batches', () => {
  const { tools, registerTools } = createToolRegistry();
  registerTools({ later: { discoveryOrder: 1 } }, 'mcp-server/src/later.js');
  registerTools({ earlier: { discoveryOrder: 0 } }, 'mcp-server/src/earlier.js');
  registerTools({ appended: {} }, 'mcp-server/src/appended.js');
  assert.deepEqual(Object.keys(tools), ['earlier', 'later', 'appended']);
});

test('moved declarations retain runtime admission and reject their former locator', async () => {
  const { TOOLS } = await import('../src/tools.js');
  const { assertRegisteredOperation } = await import('../src/mutation-registry.js');
  const { frozenInventory } = await import('../../ops/scac-mutation-inventory.mjs');
  for (const row of frozenInventory('scac-mutation-registry.v112').filter(row => row.ingress_kind === 'mcp_tool' && row.source_locator === 'mcp-server/src/tools.js')) {
    const name = row.operation, tool = TOOLS[name];
    const admitted = await assertRegisteredOperation(name, tool, {});
    assert.equal(admitted.source_locator, tool.registrySource, name);
    assert.equal(admitted.write, row.write, name);
    assert.equal(admitted.human_only, row.human_only, name);
    assert.equal(admitted.authority_only, row.authority_only, name);
    assert.equal(admitted.schema_digest, row.schema_digest, name);
    await assert.rejects(() => assertRegisteredOperation(name, { ...tool, registrySource: row.source_locator }, {}), error => error.error === 'mutation_contract_mismatch');
  }
});

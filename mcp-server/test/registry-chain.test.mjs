import test from 'node:test';
import assert from 'node:assert/strict';
import { registryChain, appendSuccessor } from '../../ops/registry-chain.mjs';

test('append owns the successor seal, runtime projection and atomic migration pair', () => {
  const before = structuredClone(registryChain);
  const current = before.versions.at(-1);
  const rows = [{ ingress_key: 'mcp-tool:example', ingress_kind: 'mcp_tool', operation: 'example',
    source_locator: 'example.js', source_digest: 'a'.repeat(64), schema_digest: 'b'.repeat(64),
    write: false, human_only: false, authority_only: false, delegates_to: [] }];
  const catalog = { ...current.catalog, secdef_execute: { count: 2, digest: 'sha256:' + 'c'.repeat(64) },
    relation_dml: { count: 3, digest: 'sha256:' + 'd'.repeat(64) }, column_dml: { count: 0, digest: 'sha256:' + 'e'.repeat(64) } };
  const result = appendSuccessor({ rows, domainMigration: { filename: '0900_example.sql', sql: 'select 1;' }, catalog,
    entrySetDigest: 'sha256:' + 'f'.repeat(64), chain: before });
  assert.equal(result.current.number, current.number + 1);
  assert.equal(result.current.predecessor, current.version);
  assert.equal(result.current.source_count, 1);
  assert.equal(result.current.entry_count, 6);
  assert.deepEqual(result.current.atomic_pair, ['0900_example.sql', '0901_example_scac_successor.sql']);
  assert.match(result.sql, /scac_mutation_registry_v113_seal_available/);
  assert.match(result.sql, /Successor dependency drifted: 0900_example.sql/);
  assert.match(result.runtime, /mcp-tool:example/);
  assert.match(result.runtime, new RegExp(result.current.digest.slice(7)));
  assert.deepEqual(result.chain.versions.slice(0, -1), before.versions);
  assert.deepEqual(before, registryChain, 'append does not mutate historical pins');
});

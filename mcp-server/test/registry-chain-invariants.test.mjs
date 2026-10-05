import test from 'node:test';
import assert from 'node:assert/strict';
import {registryChain} from '../../ops/registry-chain.mjs';
import {checkRegistryChain} from '../../ops/registry-chain-check.mjs';

test('the entire chain preserves continuity, digests, counts and atomic ordering', () => {
  const result = checkRegistryChain();
  assert.equal(result.versions, registryChain.versions.length);
  assert.equal(result.current, registryChain.versions.at(-1).version);
});
for (const [name, mutate, reason] of [
  ['gap', chain => chain.versions.splice(20,1), /continuity/],
  ['predecessor', chain => chain.versions[20].predecessor=chain.versions[0].version, /continuity/],
  ['digest', chain => chain.versions[20].digest='sha256:'+'0'.repeat(64), /digest/],
  ['count', chain => chain.versions[20].entry_count++, /count/],
  ['pair order', chain => chain.atomic_groups.at(-1).reverse(), /atomic.*order/],
  ['history rewrite', chain => chain.versions[0].commit='0'.repeat(40), /history preservation/],
]) test(`a ${name} mutation is refused by the common invariant`, () => {
  const chain = structuredClone(registryChain);
  mutate(chain);
  assert.throws(() => checkRegistryChain({chain, before:registryChain}), reason);
});
test('changed historical migration bytes are refused', () => {
  assert.throws(() => checkRegistryChain({readMigration: () => 'select changed;'}), /migration pin/);
});

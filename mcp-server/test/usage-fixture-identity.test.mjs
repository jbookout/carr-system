import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('usage authentication fixtures contain only reserved synthetic email identities', () => {
  const source = readFileSync(new URL('./usage-signals-web.test.mjs', import.meta.url), 'utf8');
  const identities = source.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+/g) || [];
  assert.ok(identities.length > 0, 'authentication fixture exercises an email identity');
  assert.ok(identities.every(identity => identity.endsWith('@example.test')), 'fixture identities must use the reserved example.test domain');
});

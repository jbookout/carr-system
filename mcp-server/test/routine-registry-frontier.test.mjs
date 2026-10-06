import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assertRegisteredOperation } from '../src/mutation-registry.js';
import { TOOLS } from '../src/tools.js';
import { frozenInventory, CURRENT_REGISTRY_VERSION } from '../../ops/scac-mutation-inventory.mjs';

test('routine new-lead estimated score schema is admitted by the current runtime projection', async () => {
  await assertRegisteredOperation('new-lead', TOOLS['new-lead'], {});
});

test('routine installation and retired legacy job versions have current sealed inventory rows', () => {
  const rows = frozenInventory(CURRENT_REGISTRY_VERSION);
  assert.equal(rows.find(row => row.ingress_key === 'external-admin:bin/install-routines.sh')?.write, true);
  const jobs = rows.filter(row => row.ingress_kind === 'job_definition' && row.key === 'npi-sweep-weekly');
  assert.equal(jobs.length, 1);
  assert.equal(jobs[0].version, 2);
  assert.equal(jobs[0].enabled, false);
});

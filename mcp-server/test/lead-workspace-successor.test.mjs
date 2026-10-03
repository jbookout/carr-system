import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { SCAC_MUTATION_REGISTRY_VERSION, registeredOperation } from '../src/mutation-registry.js';

test('Leads follows the shipped Jev-cap frontier without replacing its contracts or migration numbers', async () => {
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v105');
  for (const name of ['find-rule', 'teach', 'ask-jev', 'claim-lead', 'link-lead-client', 'update-lead', 'lead-board'])
    assert.ok(registeredOperation(name), `${name} must survive the integration`);
  assert.equal(registeredOperation('confirm-merge').human_only, true);
  const names = (await readdir(new URL('../../migrations/', import.meta.url))).filter(name => /^\d+_.*\.sql$/.test(name));
  for (const number of ['0773', '0783', '0784'])
    assert.equal(names.filter(name => name.startsWith(`${number}_`)).length, 1, 'new Leads migration numbers must be unique');
  const sql = await readFile(new URL('../../migrations/0784_leads_scac_successor.sql', import.meta.url), 'utf8');
  assert.match(sql, /0773_jev_cap_scac_successor.sql/);
  assert.match(sql, /0783_lead_archived_stage.sql/);
  assert.match(sql, /scac-mutation-registry\.v104/);
  assert.match(sql, /scac-mutation-registry\.v105/);
  assert.match(sql, /scac_mutation_registration_v104\('sha256:[0-9a-f]{64}','mcp-tool:update-lead'\)/);
});

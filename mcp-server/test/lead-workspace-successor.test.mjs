import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { SCAC_MUTATION_REGISTRY_VERSION, registeredOperation } from '../src/mutation-registry.js';

test('Leads follows the shipped relationship frontier without replacing its contracts or migration numbers', async () => {
  assert.ok(Number(SCAC_MUTATION_REGISTRY_VERSION.split('.v').at(-1)) >= 112);
  for (const name of ['find-rule', 'teach', 'ask-jev', 'advance-leads', 'approve-lead-move', 'record-lead-contact', 'claim-lead', 'link-lead-client', 'update-lead', 'lead-board', 'read-doc-activity', 'unfinished-work'])
    assert.ok(registeredOperation(name), `${name} must survive the integration`);
  assert.equal(registeredOperation('confirm-merge').human_only, true);
  const names = (await readdir(new URL('../../migrations/', import.meta.url))).filter(name => /^\d+_.*\.sql$/.test(name));
  for (const number of ['0825', '0827', '0843', '0844', '0845', '0846'])
    assert.equal(names.filter(name => name.startsWith(`${number}_`)).length, 1, 'new Leads migration numbers must be unique');
  const sorted = names.sort();
  assert.ok(sorted.indexOf('0844_automation_undo_scac_successor.sql') < sorted.indexOf('0845_lead_archived_stage.sql') &&
    sorted.indexOf('0845_lead_archived_stage.sql') < sorted.indexOf('0846_leads_scac_successor.sql'),
    'Leads migrations must sort after the predecessor they pin, or the applied ledger stops being a prefix');
  const sql = await readFile(new URL('../../migrations/0846_leads_scac_successor.sql', import.meta.url), 'utf8');
  assert.match(sql, /0844_automation_undo_scac_successor.sql/);
  assert.match(sql, /0845_lead_archived_stage.sql/);
  assert.match(sql, /scac-mutation-registry\.v111/);
  assert.match(sql, /scac-mutation-registry\.v112/);
  assert.match(sql, /scac_mutation_registration_v111\('sha256:[0-9a-f]{64}','mcp-tool:codex-read-recovery'\)/);
});

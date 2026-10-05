import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { SCAC_MUTATION_REGISTRY_VERSION, registeredOperation } from '../src/mutation-registry.js';

test('unapplied Leads migrations extend the main migration ledger', async () => {
  const names = await readdir(new URL('../../migrations/', import.meta.url));
  const archived = names.find(name => /^\d+_lead_archived_stage\.sql$/.test(name));
  const successor = names.find(name => /^\d+_leads_scac_successor\.sql$/.test(name));
  const mainTail = names.filter(name => /^\d+_.*\.sql$/.test(name) &&
    name !== archived && name !== successor).sort().at(-1);
  const sorted = names.sort();
  assert.ok(mainTail, 'the merged main ledger tail must exist');
  assert.ok(sorted.indexOf(archived) > sorted.indexOf(mainTail),
    'Archived must apply after the already delivered main ledger');
  assert.ok(sorted.indexOf(successor) > sorted.indexOf(archived),
    'the Leads seal must follow its Archived vocabulary');
});

test('Leads follows the shipped relationship frontier without replacing its contracts or migration numbers', async () => {
  assert.equal(SCAC_MUTATION_REGISTRY_VERSION, 'scac-mutation-registry.v110');
  for (const name of ['find-rule', 'teach', 'ask-jev', 'advance-leads', 'approve-lead-move', 'record-lead-contact', 'claim-lead', 'link-lead-client', 'update-lead', 'lead-board', 'read-doc-activity', 'unfinished-work'])
    assert.ok(registeredOperation(name), `${name} must survive the integration`);
  assert.equal(registeredOperation('confirm-merge').human_only, true);
  const names = (await readdir(new URL('../../migrations/', import.meta.url))).filter(name => /^\d+_.*\.sql$/.test(name));
  for (const number of ['0825', '0827', '0843', '0844'])
    assert.equal(names.filter(name => name.startsWith(`${number}_`)).length, 1, 'new Leads migration numbers must be unique');
  const sorted = names.sort();
  assert.ok(sorted.indexOf('0840_relationship_scac_successor.sql') < sorted.indexOf('0843_lead_archived_stage.sql') &&
    sorted.indexOf('0843_lead_archived_stage.sql') < sorted.indexOf('0844_leads_scac_successor.sql'),
    'Leads migrations must sort after the predecessor they pin, or the applied ledger stops being a prefix');
  const sql = await readFile(new URL('../../migrations/0844_leads_scac_successor.sql', import.meta.url), 'utf8');
  assert.match(sql, /0840_relationship_scac_successor.sql/);
  assert.match(sql, /0843_lead_archived_stage.sql/);
  assert.match(sql, /scac-mutation-registry\.v109/);
  assert.match(sql, /scac-mutation-registry\.v110/);
  assert.match(sql, /scac_mutation_registration_v109\('sha256:[0-9a-f]{64}','mcp-tool:codex-read-recovery'\)/);
});

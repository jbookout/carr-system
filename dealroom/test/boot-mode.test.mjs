import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { deploymentIdentity, resolveDealroomBoot } from '../js/boot-mode.js';

test('reviewed DoctorCRE hosts boot as live and identify the production surface', () => {
  const boot = resolveDealroomBoot({ hostname: 'app.doctorcre.com', search: '' });
  assert.equal(boot.mode, 'live');
  assert.deepEqual(deploymentIdentity(boot.mode), {
    label: 'LIVE · DoctorCRE',
    detail: 'Connected to the live DoctorCRE Deal Room',
    mode: 'live',
  });
});

test('the legacy Deal Room hostname stays outside the live boot allowlist', () => {
  const boot = resolveDealroomBoot({ hostname: 'dealroom.doctorcre.com', search: '' });
  assert.equal(boot.mode, 'fixture');
});

test('local and unknown hosts stay explicitly fixture-only', () => {
  for (const location of [
    { hostname: 'localhost', search: '' },
    { hostname: 'preview.example.test', search: '?mode=live' },
  ]) {
    const boot = resolveDealroomBoot(location);
    assert.equal(boot.mode, 'fixture');
    assert.match(deploymentIdentity(boot.mode).label, /LOCAL FIXTURE/);
    assert.match(deploymentIdentity(boot.mode).detail, /not production records/);
  }
});

test('the shell does not call fixture connectivity live', async () => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  const app = await readFile(new URL('../js/app.js', import.meta.url), 'utf8');
  assert.match(html, /id="deploymentBadge"/);
  assert.match(html, /Checking connection/);
  assert.match(app, /Fixture ready/);
  assert.match(app, /Live sync/);
});

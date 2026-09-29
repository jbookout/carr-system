import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

const repo = resolve(import.meta.dirname, '../..');
const migrations = join(repo, 'migrations');
const migrationName = () => readdirSync(migrations).find((name) =>
  /^\d{4}_national_account_classification_correction\.sql$/.test(name));

const fixture = `
create table actor (id uuid primary key, slug text not null);
create table party (id uuid primary key, kind text not null, name text not null);
create table client (id uuid primary key, roster_ref text not null, party_id uuid not null,
  client_type text, acquisition_source text, merged_into uuid, updated_by uuid,
  parent_account_id uuid);
create table national_account_owner (account_client_id uuid primary key,
  owner_actor_id uuid not null, set_by uuid not null);
create table deal (id uuid primary key, client_id uuid not null);
create table deal_review_session (account_client_id uuid);
create table event (occurred_at timestamptz, recorded_at timestamptz, actor_id uuid,
  verb text, subject_type text, subject_id uuid, field text, old_value jsonb,
  new_value jsonb, cause text not null check (cause in
    ('human_stated','human_correction','ingest_email','ingest_calendar',
     'ingest_webhook','import_migration','import_salesforce','automation_job',
     'learning_job','system')), human_quote text, agent_rationale text);
create view v_client_account as select id as client_id,
  parent_account_id as account_client_id,
  parent_account_id is not null as is_sub_client from client;
insert into actor values
 ('00000000-0000-0000-0000-000000000001','system'),
 ('00000000-0000-0000-0000-000000000002','joe');
insert into party values
 ('00000000-0000-0000-0000-000000000011','org','Musicologie'),
 ('00000000-0000-0000-0000-000000000012','org','Operation Dental'),
 ('00000000-0000-0000-0000-000000000013','org','Kain Capital'),
 ('00000000-0000-0000-0000-000000000014','org','Unrelated Client');
insert into client (id,roster_ref,party_id,client_type,acquisition_source,updated_by) values
 ('9c323aa5-b9c0-45e7-b958-ef1ee3738660','C-161','00000000-0000-0000-0000-000000000011','national_account',null,'00000000-0000-0000-0000-000000000002'),
 ('8550b702-e6fb-4bea-8b3e-1fec5b9499bd','C-204','00000000-0000-0000-0000-000000000012','national_account','national_account','00000000-0000-0000-0000-000000000002'),
 ('2f7364a7-8167-421d-8492-79815307fba3','C-205','00000000-0000-0000-0000-000000000013','national_account','national_account','00000000-0000-0000-0000-000000000002'),
 ('00000000-0000-0000-0000-000000000015','C-206','00000000-0000-0000-0000-000000000014',null,null,'00000000-0000-0000-0000-000000000002');
insert into national_account_owner values
 ('9c323aa5-b9c0-45e7-b958-ef1ee3738660','00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002'),
 ('8550b702-e6fb-4bea-8b3e-1fec5b9499bd','00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002'),
 ('2f7364a7-8167-421d-8492-79815307fba3','00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002');
`;

test('the reviewed correction leaves Musicologie as the only national account and fails closed on linked work', (t) => {
  const postgresPath = spawnSync('which', ['postgres'], { encoding: 'utf8' }).stdout?.trim();
  if (!postgresPath) return t.skip('local PostgreSQL server unavailable');
  const pgBin = dirname(realpathSync(postgresPath));
  const bin = (name) => join(pgBin, name);
  if (!['initdb', 'pg_ctl', 'psql'].every((name) => existsSync(bin(name))))
    return t.skip('matching local PostgreSQL tools unavailable');

  const name = migrationName();
  assert.ok(name, 'the correction migration must exist');
  const migration = join(migrations, name);
  assert.doesNotMatch(readFileSync(migration, 'utf8'), /^\s*(begin|commit)\s*;/im,
    'the migration runner owns the transaction');
  const dir = mkdtempSync('/private/tmp/carr-na-test-');
  const data = join(dir, 'data');
  const socket = join(dir, 'socket');
  const init = spawnSync(bin('initdb'), ['-D', data, '-A', 'trust', '-U', 'postgres'], { encoding: 'utf8' });
  if (init.status !== 0) { rmSync(dir, { recursive: true, force: true }); return t.skip(init.stderr.trim()); }
  mkdirSync(socket);
  const port = String(54000 + Math.floor(Math.random() * 1000));
  const env = { ...process.env, PGHOST: socket, PGPORT: port, PGUSER: 'postgres', PGDATABASE: 'postgres' };
  const log = join(dir, 'postgres.log');
  const server = spawnSync(bin('pg_ctl'), ['-D', data, '-l', log, '-o', `-F -k ${socket} -p ${port}`, '-w', 'start'], { encoding: 'utf8' });
  assert.equal(server.status, 0, `${server.stderr}\n${readFileSync(log, 'utf8')}`);
  t.after(() => {
    spawnSync(bin('pg_ctl'), ['-D', data, '-m', 'immediate', '-w', 'stop'], { env });
    rmSync(dir, { recursive: true, force: true });
  });
  const sql = (statement, expectSuccess = true) => {
    const result = spawnSync(bin('psql'), ['-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', statement],
      { env, encoding: 'utf8' });
    assert.equal(result.status === 0, expectSuccess, result.stderr || result.stdout);
    return result.stdout.trim();
  };
  const file = (expectSuccess = true) => {
    const result = spawnSync(bin('psql'), ['-X', '-v', 'ON_ERROR_STOP=1', '-c', 'begin', '-f', migration, '-c', 'commit'],
      { env, encoding: 'utf8' });
    assert.equal(result.status === 0, expectSuccess, result.stderr || result.stdout);
  };

  sql(fixture);
  file();
  assert.equal(sql("select string_agg(roster_ref, ',' order by roster_ref) from client where client_type='national_account'"), 'C-161');
  assert.equal(sql('select count(*) from national_account_owner'), '1');
  assert.equal(sql("select count(*) from event where field='client_type' and subject_type='client'"), '2');
  assert.equal(sql("select count(*) from event where cause='human_correction' and human_quote like '%only national account%'"), '2');
  assert.equal(sql("select count(*) from client where roster_ref='C-206' and client_type is null"), '1');

  sql('drop schema public cascade; create schema public;');
  sql(fixture);
  sql(`insert into client (id,roster_ref,party_id,client_type,updated_by,parent_account_id)
    values ('00000000-0000-0000-0000-000000000016','C-207','00000000-0000-0000-0000-000000000014',null,
      '00000000-0000-0000-0000-000000000002','8550b702-e6fb-4bea-8b3e-1fec5b9499bd')`);
  file(false);
  assert.equal(sql("select count(*) from client where client_type='national_account'"), '3');
  assert.equal(sql('select count(*) from event'), '0');
});

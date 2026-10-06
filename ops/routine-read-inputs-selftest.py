#!/usr/bin/env python3
"""Prove routine read grants in a private Unix-socket PostgreSQL cluster."""
import importlib.util
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import postgres_fixture_group

BOOTSTRAP = """
create role carr_jobs;
create role routine_unrelated;
create table party (id uuid, ref text, name text, contact_state text, merged_into uuid,
 deleted_at timestamptz, title text,email text,phone text,cell text,city text,county text,
 state text,npi text,specialty text,version int,org_id uuid);
create table vendor (id uuid,party_id uuid,category_slug text,verticals text[],version int,merged_into uuid);
create table vendor_category(slug text,label text,sort int);
create table record_flag(subject_type text,subject_id uuid,kind text,expires_on date);
create table tool_call(idempotency_key text,verb text,response jsonb);
create table v_control_plane_enrichment_queue(priority int,subject_type text,subject_id uuid,reverification_due text);
create table v_ref_index(subject_type text,subject_id uuid,ref text,party_id uuid,merged boolean);
insert into party(id,ref,name,contact_state,city,state,version)
 values('00000000-0000-4000-8000-000000000001','P-0001','Fixture Person','active','Pensacola','FL',4),
 ('00000000-0000-4000-8000-000000000002','P-0002','Fixture Blocked','do_not_contact','Mobile','AL',2);
insert into vendor values('00000000-0000-4000-8000-000000000011','00000000-0000-4000-8000-000000000001',null,null,2,null);
insert into vendor_category values('cpa','CPA',1);
insert into v_control_plane_enrichment_queue values
 (2,'vendor','00000000-0000-4000-8000-000000000011','not_recorded'),
 (1,'party','00000000-0000-4000-8000-000000000001','expired'),
 (3,'party','00000000-0000-4000-8000-000000000002','expired');
insert into v_ref_index values('vendor','00000000-0000-4000-8000-000000000011','V-CPA-001','00000000-0000-4000-8000-000000000001',false);
insert into tool_call values('receipt-1','update-party-contact','{"ok":true}'),
 ('unrelated-secret-result','mint-auth-token','{"credential":"not-readable"}');
"""


def main():
    migration = ROOT / "migrations/0847_routine_read_inputs.sql"
    assert migration.exists(), "routine grant migration does not exist"
    spec = importlib.util.spec_from_file_location("routine_local_pg", ROOT / "ops/local-pg-ci.py")
    pg = importlib.util.module_from_spec(spec); sys.modules[spec.name] = pg; spec.loader.exec_module(pg)
    bins, env = pg.find_postgres_binaries(), pg.scrub_cloud_environment(os.environ)
    with postgres_fixture_group():
        root = Path(tempfile.mkdtemp(prefix="carr-routine-read-proof-"))
        data, socket = root / "data", root / "socket"
        socket.mkdir()
        started = False
        def run(args, **kwargs):
            return subprocess.run([str(x) for x in args], env=env, capture_output=True, text=True, timeout=60, **kwargs)
        def sql(statement, success=True):
            result = run([bins.psql,'-X','-qAt','-v','ON_ERROR_STOP=1','-h',socket,'-U','fixture','-d','postgres'],input=statement)
            assert (result.returncode == 0) is success, result.stderr
            return result.stdout.strip()
        try:
            run([bins.initdb,'-D',data,'-A','trust','-U','fixture'],check=True)
            run([bins.pg_ctl,'-D',data,'-l',root/'pg.log','-o',f"-k {socket} -c listen_addresses=''",'-w','start'],check=True)
            started = True
            sql(BOOTSTRAP)
            sql("set role carr_jobs; select * from party;",success=False)
            sql(migration.read_text())
            result = json.loads(sql("set role carr_jobs; select coalesce(jsonb_agg(to_jsonb(x)),'[]') from v_routine_contact_inputs x;"))
            assert len(result) == 1 and result[0]['ref'] == 'P-0001', result
            assert result[0]['priority'] == 1 and result[0]['party_version'] == 4, result
            contact_spec = importlib.util.spec_from_file_location("proof_contacts", ROOT / 'tools/routines/contact_enrichment.py')
            contact = importlib.util.module_from_spec(contact_spec); contact_spec.loader.exec_module(contact)
            class ReadContext:
                fixture = None
                now = dt.datetime.now(dt.timezone.utc)
                def query(self, statement, params=()):
                    assert not params
                    return json.loads(sql("set role carr_jobs; select coalesce(jsonb_agg(to_jsonb(x)),'[]') from (" + statement + ") x;"))
            plan = contact.prepare(ReadContext())
            assert plan['selected'] == 1 and plan['inputs']['categories'] == [{'slug':'cpa','label':'CPA'}], plan
            assert sql("set role carr_jobs; select version from v_routine_contact_party where id='00000000-0000-4000-8000-000000000001';") == '4'
            assert sql("set role carr_jobs; select version from v_routine_contact_vendor where id='00000000-0000-4000-8000-000000000011';") == '2'
            assert sql("set role carr_jobs; select slug from v_routine_vendor_category;") == 'cpa'
            assert sql("set role carr_jobs; select count(*) from v_routine_effect_receipts;") == '1'
            sql("set role carr_jobs; select * from tool_call;",success=False)
            sql("set role routine_unrelated; select * from v_routine_contact_inputs;",success=False)
            sql("set role carr_jobs; update v_routine_contact_party set version=99;",success=False)
            sql("insert into record_flag values('party','00000000-0000-4000-8000-000000000001','contact_enrichment_attempt',current_date+30);")
            assert sql("set role carr_jobs; select count(*) from v_routine_contact_inputs;") == '0'
            sql("""insert into party(id,ref,name,contact_state,version)
                select ('00000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,
                       'P-'||i,'Fixture '||i,'active',1 from generate_series(100,159) i;
                insert into v_control_plane_enrichment_queue
                select i,'party',('00000000-0000-4000-8000-'||lpad(i::text,12,'0'))::uuid,'not_recorded'
                  from generate_series(100,159) i;""")
            assert sql("set role carr_jobs; select count(*) from v_routine_contact_inputs;") == '40'
            print('PASS jobs contact prepare, hydration, priority/dedup/cap40, exclusions, cooldown, versions, categories and receipt scope; base tables and mutations denied')
        finally:
            if started:
                run([bins.pg_ctl,'-D',data,'-m','immediate','-w','stop'],check=True)
            quarantine = ROOT / '_to_delete'; quarantine.mkdir(exist_ok=True)
            shutil.move(str(root), str(quarantine / root.name))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

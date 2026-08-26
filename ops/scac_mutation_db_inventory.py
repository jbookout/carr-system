"""Canonical read-only SIEP-11 projection of DB mutation capability surfaces."""

from __future__ import annotations

import hashlib
import json
from typing import Any


SECDEF_EXECUTE_SQL = r"""
with runtime_roles as (
  select oid,rolname from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'
), functions as (
  select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,
         p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner
    from pg_proc p join pg_namespace n on n.oid=p.pronamespace
   where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')
), capabilities as (
  select f.*,acl.grantee,acl.privilege_type,acl.is_grantable
    from functions f
    cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl
)
select jsonb_build_object(
  'ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute',
  'ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')',
  'security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,
  'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),
  'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable)
from capabilities c left join pg_roles r on r.oid=c.grantee
where prosecdef and privilege_type='EXECUTE'
  and (grantee=0 or r.oid in (select oid from runtime_roles))
order by nspname,proname,args,coalesce(r.rolname,'public')
"""

RELATION_DML_SQL = r"""
with runtime_roles as (
  select oid,rolname from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'
), capabilities as (
  select n.nspname,c.relname,c.relkind,acl.grantee,acl.privilege_type,acl.is_grantable
    from pg_class c join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
   where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')
)
select jsonb_build_object(
  'ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),
  'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,
  'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable)
from capabilities c left join pg_roles r on r.oid=c.grantee
where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')
  and (grantee=0 or r.oid in (select oid from runtime_roles))
order by nspname,relname,coalesce(r.rolname,'public'),lower(privilege_type)
"""

COLUMN_DML_SQL = r"""
with runtime_roles as (
  select oid,rolname from pg_roles where rolname ~ '^carr_' and rolname<>'carr_ci'
), capabilities as (
  select n.nspname,c.relname,c.relkind,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable
    from pg_attribute a join pg_class c on c.oid=a.attrelid
    join pg_namespace n on n.oid=c.relnamespace
    cross join lateral aclexplode(a.attacl) acl
   where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0
     and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')
)
select jsonb_build_object(
  'ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),
  'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,
  'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable)
from capabilities c left join pg_roles r on r.oid=c.grantee
where privilege_type in ('INSERT','UPDATE')
  and (grantee=0 or r.oid in (select oid from runtime_roles))
order by nspname,relname,attname,coalesce(r.rolname,'public'),lower(privilege_type)
"""

JOB_DEFINITIONS_SQL = r"""
select jsonb_build_object(
  'ingress_key','job-definition:'||key||':'||version,'ingress_kind','job_definition',
  'key',key,'version',version,'enabled',enabled,'risk',risk,'owner_actor',owner_actor,
  'execution_kind',execution_kind,'entrypoint',coalesce(execution_contract->>'entrypoint',execution_contract->>'cognition_job'),
  'execution_contract',execution_contract,'inventory_contract',inventory_contract,'recurrence',recurrence,
  'state_contract',state_contract,'routing_contract',routing_contract,'filtering_contract',filtering_contract,
  'validation_contract',validation_contract,'retry_policy',retry_policy,'deduplication',deduplication,
  'completion_contract',completion_contract,'legacy_schedule',legacy_schedule)
from ops.job_definition order by key,version
"""

QUERIES = {
    "secdef_execute": SECDEF_EXECUTE_SQL,
    "relation_dml": RELATION_DML_SQL,
    "column_dml": COLUMN_DML_SQL,
    "job_definitions": JOB_DEFINITIONS_SQL,
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def project(cur: Any) -> dict[str, list[dict[str, Any]]]:
    return {name: [row[0] for row in cur.execute(query)] for name, query in QUERIES.items()}


def summarize(projection: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    categories = {name: {"count": len(rows), "digest": digest(rows)}
                  for name, rows in projection.items()}
    combined_rows = sorted(
        [row for rows in projection.values() for row in rows],
        key=lambda row: row["ingress_key"],
    )
    return {"categories": categories, "combined": {"count": len(combined_rows), "digest": digest(combined_rows)}}

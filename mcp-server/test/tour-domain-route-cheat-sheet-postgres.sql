\set ON_ERROR_STOP on
-- Disposable typed lifecycle proof for 0429; every fixture is rolled back.
begin;
do $owner$
begin
  insert into ops.tour_rights_receipt(id,organization_tenant_id,provider,sku,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status)
  values('40000000-0000-4000-8000-000000000010','tour-slice4-proof','route-proof','sku','route-policy',1,'sha256:'||repeat('a',64),'https://example.invalid','2026-08-27','owner','proof','["*"]','["route_planning"]','2026-08-27','active');
  insert into ops.tour_property(id,organization_tenant_id,property_status) values
  ('40000000-0000-4000-8000-000000000001','tour-slice4-proof','active'),
  ('40000000-0000-4000-8000-000000000002','tour-slice4-proof','active'),
  ('40000000-0000-4000-8000-000000000003','tour-slice4-proof','active');
end $owner$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; r1 uuid; a uuid; h uuid;
begin
 t:=ops.create_tour_domain('tour-slice4-proof','typed','work','opaque','proof','{}','{}');
 select id into r1 from ops.tour_route_version where tour_id=t and route_version=1;
 a:=ops.append_tour_route_stop('tour-slice4-proof',r1,'40000000-0000-4000-8000-000000000001',1,'A','active','2026-08-28T14:00:00Z','2026-08-28T14:30:00Z',true,20,5,'approved','sha256:'||repeat('1',64));
 h:=ops.append_tour_route_stop('tour-slice4-proof',r1,'40000000-0000-4000-8000-000000000002',null,null,'held',null,null,false,0,0,'unknown','sha256:'||repeat('2',64));
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r1,null,a,'added');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r1,null,h,'added');
end $writer$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-proof';
do $authority$
declare r1 uuid; begin
 select id into r1 from ops.tour_route_version where organization_tenant_id='tour-slice4-proof' and route_version=1;
 perform ops.accept_tour_route_version('tour-slice4-proof',r1,0,'sha256:'||repeat('3',64));
end $authority$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; r1 uuid; abandoned_r2 uuid; oa uuid; oh uuid; replacement uuid;
begin
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 select id into r1 from ops.tour_route_version where tour_id=t and route_version=1;
 abandoned_r2:=ops.append_tour_route_version('tour-slice4-proof',t,2,r1,'{}','{}','manual',null,null,'{}',null,1,null);
 if abandoned_r2 is null then raise exception 'abandoned route draft was not retained'; end if;
 replacement:=ops.append_tour_route_stop('tour-slice4-proof',abandoned_r2,'40000000-0000-4000-8000-000000000003',1,'A','active',null,null,false,10,5,'approved','sha256:'||repeat('4',64));
 select id into oa from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000001';
 select id into oh from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000002';
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,abandoned_r2,oa,null,'removed');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,abandoned_r2,oh,null,'held');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,abandoned_r2,null,replacement,'added');
end $writer$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-proof';
do $authority$
declare r2 uuid;
begin
 select id into r2 from ops.tour_route_version where organization_tenant_id='tour-slice4-proof' and route_version=2;
 begin
   perform ops.accept_tour_route_version('tour-slice4-proof',r2,1,'sha256:'||repeat('4',64));
   raise exception 'expected locked appointment preservation refusal';
 exception when raise_exception then
   if sqlerrm<>'route acceptance must preserve every locked appointment window, dwell, and buffer' then raise; end if;
 end;
end $authority$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; r1 uuid; r3 uuid; oa uuid; oh uuid; na uuid; nh uuid; n uuid;
begin
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 select id into r1 from ops.tour_route_version where tour_id=t and route_version=1;
 r3:=ops.append_tour_route_version('tour-slice4-proof',t,3,r1,'{}','{}','provider','route-proof','40000000-0000-4000-8000-000000000010','{}','sha256:'||repeat('4',64),1,'route-policy');
 na:=ops.append_tour_route_stop('tour-slice4-proof',r3,'40000000-0000-4000-8000-000000000001',2,'B','active','2026-08-28T14:00:00Z','2026-08-28T14:30:00Z',true,20,5,'approved','sha256:'||repeat('5',64));
 nh:=ops.append_tour_route_stop('tour-slice4-proof',r3,'40000000-0000-4000-8000-000000000002',null,null,'held',null,null,false,0,0,'unknown','sha256:'||repeat('6',64));
 n:=ops.append_tour_route_stop('tour-slice4-proof',r3,'40000000-0000-4000-8000-000000000003',1,'A','active',null,null,false,10,5,'approved','sha256:'||repeat('7',64));
 select id into oa from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000001';
 select id into oh from ops.tour_route_stop where route_version_id=r1 and property_id='40000000-0000-4000-8000-000000000002';
 begin
   perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,r3,oa,n,'reordered');
   raise exception 'expected cross-property reorder refusal';
 exception when raise_exception then
   if sqlerrm<>'route transition property identity mismatch' then raise; end if;
 end;
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,r3,oa,na,'reordered');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',r1,r3,oh,nh,'held');
 perform ops.append_tour_route_stop_transition('tour-slice4-proof',null,r3,null,n,'added');
 perform ops.append_tour_cheat_sheet_revision('tour-slice4-proof',t,'{"internal":"contact"}',0);
end $writer$;
set local session authorization carr_authority;
set local carr.acting_actor_slug='tour-proof';
do $authority$
declare r3 uuid; t uuid; begin
 select id into r3 from ops.tour_route_version where organization_tenant_id='tour-slice4-proof' and route_version=3;
 perform ops.accept_tour_route_version('tour-slice4-proof',r3,1,'sha256:'||repeat('8',64));
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 if (select count(*) from ops.tour_property_membership where tour_id=t and route_version=3)<>2 then raise exception 'held/excluded stop entered canonical membership'; end if;
end $authority$;
set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-proof';
do $writer$
declare t uuid; rev uuid; begin
 select id into t from ops.tour where organization_tenant_id='tour-slice4-proof';
 select id into rev from ops.tour_cheat_sheet_revision where tour_id=t and revision_number=1;
 perform ops.restore_tour_cheat_sheet_revision('tour-slice4-proof',t,rev,1);
 begin update ops.tour_route_stop set route_label='X' where organization_tenant_id='tour-slice4-proof'; raise exception 'expected raw DML refusal'; exception when insufficient_privilege then null; end;
end $writer$;
reset session authorization;
do $owner$
begin
 begin update ops.tour_route_stop set route_label='X' where organization_tenant_id='tour-slice4-proof'; raise exception 'expected append-only refusal'; exception when raise_exception then if sqlerrm<>'tour_route_stop is append-only' then raise; end if; end;
 if has_table_privilege('carr_writer','ops.tour_route_stop','insert') then raise exception 'raw DML granted'; end if;
 if has_function_privilege('public','ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text)','execute') or has_function_privilege('carr_reader','ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text)','execute') or has_function_privilege('carr_jobs','ops.append_tour_route_version(text,uuid,integer,uuid,jsonb,jsonb,text,text,uuid,jsonb,text,integer,text)','execute') then raise exception 'revised version seam leaked'; end if;
 if has_function_privilege('public','ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamptz,timestamptz,boolean,integer,integer,text,text)','execute') or has_function_privilege('carr_reader','ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamptz,timestamptz,boolean,integer,integer,text,text)','execute') or has_function_privilege('carr_jobs','ops.append_tour_route_stop(text,uuid,uuid,integer,text,text,timestamptz,timestamptz,boolean,integer,integer,text,text)','execute') then raise exception 'revised stop seam leaked'; end if;
 if exists(select 1 from ops.tour_public_projection_fact where organization_tenant_id='tour-slice4-proof') then raise exception 'public noninterference failed'; end if;
end $owner$;
rollback;

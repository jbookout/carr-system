\set ON_ERROR_STOP on
-- Disposable least-privilege proof for 0403. All rows are rolled back.
begin;

insert into ops.tour_property(id,organization_tenant_id,property_status) values
('a1000000-0000-4000-8000-000000000001','tour-delivery-proof','active'),
('a1000000-0000-4000-8000-000000000002','tour-delivery-proof','active');
insert into ops.tour(id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,subject_type,subject_id,subject_bound_at)
values('a2000000-0000-4000-8000-000000000001','tour-delivery-proof','Delivery proof','draft',1,'proof-v1','work','proof',now());
insert into ops.tour_route_version(id,organization_tenant_id,tour_id,route_version,start_point,end_point,routing_source,routing_request,created_by_actor_id)
values('a3000000-0000-4000-8000-000000000001','tour-delivery-proof','a2000000-0000-4000-8000-000000000001',1,'{}','{}','manual','{}','tour-delivery-proof');
insert into ops.tour_route_stop(id,organization_tenant_id,route_version_id,property_id,route_sequence,route_label,stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id)
values
('a4000000-0000-4000-8000-000000000001','tour-delivery-proof','a3000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000001',1,'A','active','2026-08-28 14:00:00+00','2026-08-28 14:30:00+00',true,30,10,'approved','sha256:'||repeat('d',64),'tour-delivery-proof'),
('a4000000-0000-4000-8000-000000000002','tour-delivery-proof','a3000000-0000-4000-8000-000000000001','a1000000-0000-4000-8000-000000000002',2,'B','active','2026-08-28 15:00:00+00','2026-08-28 15:30:00+00',true,30,10,'approved','sha256:'||repeat('e',64),'tour-delivery-proof');
insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest)
values('tour-delivery-proof','a2000000-0000-4000-8000-000000000001','a3000000-0000-4000-8000-000000000001',0,'tour-delivery-proof','sha256:'||repeat('c',64));

set local session authorization carr_writer;
set local carr.acting_actor_slug='tour-delivery-proof';
do $writer$
declare v_version uuid; v_cart jsonb; v_search jsonb; v_projection_meta jsonb;
begin
  begin
    perform ops.prepare_tour_route_version(
      'tour-delivery-proof','a2000000-0000-4000-8000-000000000001',
      'a3000000-0000-4000-8000-000000000001',1,
      '["a4000000-0000-4000-8000-000000000002","a4000000-0000-4000-8000-000000000001"]');
    raise exception 'expected locked appointment order refusal';
  exception when raise_exception then
    if sqlerrm<>'tour route preparation violates locked appointment order' then raise; end if;
  end;
  v_version:=ops.append_tour_selection_cart_version(
    'tour-delivery-proof','a2000000-0000-4000-8000-000000000001',null,
    '["a1000000-0000-4000-8000-000000000001","a1000000-0000-4000-8000-000000000002"]',0,
    'sha256:'||repeat('a',64));
  if v_version is null then raise exception 'selection cart version was not created'; end if;
  v_cart:=ops.read_tour_selection_cart('tour-delivery-proof','a2000000-0000-4000-8000-000000000001','tour-delivery-proof');
  if v_cart->>'selection_version'<>'1' or jsonb_array_length(v_cart->'property_ids')<>2 then raise exception 'selection cart projection is incomplete'; end if;
  begin
    perform ops.append_tour_selection_cart_version(
      'tour-delivery-proof','a2000000-0000-4000-8000-000000000001',null,'[]',0,'sha256:'||repeat('b',64));
    raise exception 'expected stale cart refusal';
  exception when raise_exception then
    if sqlerrm<>'tour selection refuses stale version' then raise; end if;
  end;
  v_search:=ops.search_tour_properties('tour-delivery-proof','tour-delivery-proof','{"query":null,"counties":[],"property_types":[],"min_square_feet":null,"max_square_feet":null,"availability":[],"entrance_verified":null,"public_projection_ready":null,"photos_available":null,"sort":"updated_desc","cursor":null,"limit":25}');
  if jsonb_typeof(v_search->'items')<>'array' then raise exception 'search did not return a bounded item array'; end if;
  v_projection_meta:=ops.read_tour_projection_creation_metadata('tour-delivery-proof','a2000000-0000-4000-8000-000000000001','a3000000-0000-4000-8000-000000000001','tour-delivery-proof');
  if v_projection_meta->>'route_version'<>'1' or v_projection_meta->>'projection_version'<>'1' then raise exception 'projection creation metadata is incomplete'; end if;
end $writer$;

reset session authorization;
do $owner$
begin
  begin
    update ops.tour_selection_cart_version set selection_version=2 where organization_tenant_id='tour-delivery-proof';
    raise exception 'expected append-only refusal';
  exception when raise_exception then
    if sqlerrm<>'tour_selection_cart_version is append-only' then raise; end if;
  end;
  if has_table_privilege('carr_writer','ops.tour_selection_cart_version','INSERT')
     or has_table_privilege('carr_authority','ops.tour_share_session','INSERT')
     or has_table_privilege('carr_reader','ops.tour_pdf_render_job','SELECT') then
    raise exception 'raw Tour delivery table privileges leaked';
  end if;
  if not has_function_privilege('carr_writer','ops.append_tour_selection_cart_version(text,uuid,uuid,jsonb,integer,text)','EXECUTE')
     or not has_function_privilege('carr_authority','ops.issue_tour_share_grant(text,uuid,text,jsonb,timestamp with time zone,text,text)','EXECUTE')
     or not has_function_privilege('carr_writer','ops.read_tour_projection_creation_metadata(text,uuid,uuid,text)','EXECUTE')
     or not has_function_privilege('carr_writer','ops.read_tour_projection_seal_candidates(text,uuid,text)','EXECUTE')
     or not has_function_privilege('carr_authority','ops.record_tour_pdf_render_result(text,uuid,text,text,text,text,integer,integer,integer,text,text)','EXECUTE')
     or not has_function_privilege('carr_writer','ops.exchange_tour_share_token(text,text,timestamp with time zone,text)','EXECUTE')
     or has_function_privilege('carr_reader','ops.exchange_tour_share_token(text,text,timestamp with time zone,text)','EXECUTE') then
    raise exception 'Tour delivery function grants are incorrect';
  end if;
end $owner$;

rollback;

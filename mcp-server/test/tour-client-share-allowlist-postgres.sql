\set ON_ERROR_STOP on
-- Disposable proof for 0591 (V5-J303): the client field allowlist is enforced
-- by the database, default deny. Every row is rolled back.
--
-- Negative cases proven here:
--   * the seal refuses a projection that selects an access note, a caveat or
--     a photo fact, and a direct insert of such a fact is refused too;
--   * a clean seal then shares; the client packet and the PDF render packet
--     carry no access note, owner contact, caveat, client (tour) name,
--     appointment time or internal uuid, and every stop key is allowlisted;
--   * a projection that ALREADY holds now-internal facts (sealed before 0591,
--     simulated here with triggers suspended) still shows none of them.
begin;

insert into ops.tour_property(id,organization_tenant_id,property_status,created_at) values
('b1000000-0000-4000-8000-000000000001','tour-client-share-proof','active',now()-interval '30 days');
insert into ops.tour_rights_receipt(id,organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status)
values('b5000000-0000-4000-8000-000000000001','tour-client-share-proof','share-proof','share-policy',1,'sha256:'||repeat('1',64),'https://example.invalid/share',now()-interval '1 year','proof','share proof','["*"]','["source_intake","canonical_fact","client_public_display"]',now()-interval '1 year','active');
insert into ops.tour_source_evidence(id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,retrieval_status,content_digest,rights_receipt_id,data_classification,rights_provider,rights_policy_key)
values('b6000000-0000-4000-8000-000000000001','tour-client-share-proof','proof:share','direct_source',now()-interval '31 days','read','sha256:'||repeat('2',64),'b5000000-0000-4000-8000-000000000001','public','share-proof','share-policy');
insert into ops.tour_field_assertion(id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,effective_to,confidence,data_classification,review_state,created_at) values
('b9000000-0000-4000-8000-000000000001','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','display.name','"Bayside Medical Plaza"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000002','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','display.address','"100 Bayside Way"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000003','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','suite','"Suite 210"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000004','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','property_type','"medical_office"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000005','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','size','{"value":4200,"unit":"SF"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000006','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','asking_economics','{"value":24,"currency":"USD","period":"NNN"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000007','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','availability','"available now"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000008','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','parking','"4 per 1000"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
-- Internal material recorded as reviewed public-shape facts: the old list
-- would have let every one of these be sealed for a client.
('b9000000-0000-4000-8000-000000000009','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','access','"Gate code 4411; call owner Bob Landlord 251-555-0100"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000010','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','caveat','"Internal note: client is tight on budget"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000011','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','photos','[{"asset_ref":"asset:public:ownerphotoabcdefgh","caption":"Owner Bob Landlord walkthrough"}]','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days');
insert into ops.tour(id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,subject_type,subject_id,subject_bound_at)
values('b2000000-0000-4000-8000-000000000001','tour-client-share-proof','Acme Pediatrics Client','draft',1,'proof-v1','work','proof',now());
insert into ops.tour_route_version(id,organization_tenant_id,tour_id,route_version,start_point,end_point,routing_source,routing_request,created_by_actor_id)
values('b3000000-0000-4000-8000-000000000001','tour-client-share-proof','b2000000-0000-4000-8000-000000000001',1,'{}','{}','manual','{}','tour-client-share-proof');
insert into ops.tour_route_stop(id,organization_tenant_id,route_version_id,property_id,route_sequence,route_label,stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id)
values('b4000000-0000-4000-8000-000000000001','tour-client-share-proof','b3000000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001',1,'A','active','2031-03-04 14:00:00+00','2031-03-04 14:30:00+00',true,30,10,'approved','sha256:'||repeat('d',64),'tour-client-share-proof');
insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest)
values('tour-client-share-proof','b2000000-0000-4000-8000-000000000001','b3000000-0000-4000-8000-000000000001',0,'tour-client-share-proof','sha256:'||repeat('c',64));
insert into ops.tour_property_membership(id,organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest,selected_at)
values('bd000000-0000-4000-8000-000000000001','tour-client-share-proof','b2000000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001',1,1,'A','sha256:'||repeat('d',64),now()-interval '1 hour');

-- Internal text typed INTO allowed fields (review of #1242): reviewed, public,
-- allowed keys -- only the VALUE is wrong.
insert into ops.tour_field_assertion(id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,effective_to,confidence,data_classification,review_state,created_at) values
('b9000000-0000-4000-8000-000000000021','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','parking','"4 per 1000. Owner Bob Landlord 251-555-0100"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000022','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','availability','"Now - email bob@landlord.example"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000023','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','suite','"Suite 210, Gate code 4411"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000024','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','size','{"value":4200,"unit":"SF","label":"owner cell 251-555-0100"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000025','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','property_type','"Internal note: client is tight on budget"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000026','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','display.address','"100 Bayside Way (lockbox on rear door)"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000027','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','asking_economics','{"value":24,"currency":"USD","period":"NNN see https://landlord.example"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days');

-- A second tour whose stop label is free text, not a short marker.
insert into ops.tour(id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,subject_type,subject_id,subject_bound_at)
values('b2000000-0000-4000-8000-000000000002','tour-client-share-proof','Label proof','draft',1,'proof-v1','work','proof',now());
insert into ops.tour_route_version(id,organization_tenant_id,tour_id,route_version,start_point,end_point,routing_source,routing_request,created_by_actor_id)
values('b3000000-0000-4000-8000-000000000002','tour-client-share-proof','b2000000-0000-4000-8000-000000000002',1,'{}','{}','manual','{}','tour-client-share-proof');
insert into ops.tour_route_stop(id,organization_tenant_id,route_version_id,property_id,route_sequence,route_label,stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id)
values('b4000000-0000-4000-8000-000000000002','tour-client-share-proof','b3000000-0000-4000-8000-000000000002','b1000000-0000-4000-8000-000000000001',1,'Stop 1','active',null,null,false,30,10,'approved','sha256:'||repeat('d',64),'tour-client-share-proof');
insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest)
values('tour-client-share-proof','b2000000-0000-4000-8000-000000000002','b3000000-0000-4000-8000-000000000002',0,'tour-client-share-proof','sha256:'||repeat('c',64));
insert into ops.tour_property_membership(id,organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest,selected_at)
values('bd000000-0000-4000-8000-000000000002','tour-client-share-proof','b2000000-0000-4000-8000-000000000002','b1000000-0000-4000-8000-000000000001',1,1,'Stop 1','sha256:'||repeat('d',64),now()-interval '1 hour');

do $client_allowlist$
declare
  v_digest text; v_share uuid; v_packet jsonb; v_render jsonb; v_text text; v_key text;
  v_allowed_stop_keys text[] := array['property_ref','route_sequence','route_label','name','address','suite','property_type','size','asking_economics','availability','parking'];
  v_forbidden text[] := array['Gate code','Bob Landlord','251-555-0100','tight on budget','Acme Pediatrics','ownerphoto','2031-03-04',
    'b1000000-0000-4000-8000-000000000001','b2000000-0000-4000-8000-000000000001','ac100000-0000-4000-8000-000000000001',
    'b9000000-0000-4000-8000-00000000000','b5000000-0000-4000-8000-000000000001','b6000000-0000-4000-8000-000000000001'];
  v_needle text;
  v_all_client_facts text := '{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000001","display_field_key":"display.name"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000002","display_field_key":"display.address"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000003","display_field_key":"suite"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000004","display_field_key":"property_type"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000005","display_field_key":"size"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000006","display_field_key":"asking_economics"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000007","display_field_key":"availability"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000008","display_field_key":"parking"}';
  v_internal record;
begin
  -- The allowlist is exactly the eight fields on today's Tour PDF.
  if ops.tour_client_field_keys() is distinct from array['display.name','display.address','suite','property_type','size','asking_economics','availability','parking']::text[] then
    raise exception 'client field allowlist drifted from the ruling';
  end if;
  -- `is distinct from false`, not plain truthiness: a NULL answer (unknown
  -- key, NULL key) must be a definite deny, never a three-valued maybe.
  if ops.tour_client_field_allowed('access') is distinct from false or ops.tour_client_field_allowed('caveat') is distinct from false
     or ops.tour_client_field_allowed(null) is distinct from false
     or ops.tour_client_field_allowed('brand_new_field') is distinct from false then
    raise exception 'client field allowlist is not default-deny';
  end if;
  -- 0591's value-safe fix: an ordinary size or economics value with no
  -- min/max range is safe (0427 returned NULL for it), and an inverted range
  -- is still refused.
  if ops.tour_public_value_safe('size','{"value":4200,"unit":"SF"}') is distinct from true
     or ops.tour_public_value_safe('asking_economics','{"value":24,"currency":"USD","period":"NNN"}') is distinct from true
     or ops.tour_public_value_safe('size','{"min":10,"max":5}') is distinct from false then
    raise exception 'tour_public_value_safe min/max check is not two-valued';
  end if;

  -- The client VALUE rule (review of #1242): internal text inside an allowed
  -- field is not client-safe; ordinary values are.
  foreach v_needle in array array[
    '4 per 1000. Owner Bob 251-555-0100','Call (251) 555-0100','Owner cell 2515550100','Ask for 555-0100',
    'UK owner +44 20 7946 0958','Email bob@landlord.example','See https://landlord.example/x','See www.landlord-portal.com',
    'Leasing via landlordportal.com','Gate code 4411','Door code 1234','Key code 9021','Lockbox on rear gate','Disarm the alarm',
    'Keypad by the dock','Entry PIN 5555','Internal note: tight budget','Confidential','Broker-only pricing','Not for client eyes',
    repeat('A',121), E'Available\nnow', '', '   '] loop
    if ops.tour_client_text_safe(v_needle) then raise exception 'client value rule admitted %', v_needle; end if;
    if ops.tour_public_value_safe('parking',to_jsonb(v_needle)) then raise exception 'value-safe admitted parking %', v_needle; end if;
  end loop;
  if ops.tour_client_text_safe(null) then raise exception 'client value rule admitted null'; end if;
  foreach v_needle in array array['Bayside Medical Plaza','100 Bayside Way, Pensacola, FL 32502','1250 E 9 Mile Rd, Pensacola, FL 32514-1234',
    'Suite 210','Suites 100-120','medical_office','available now','Available 2026-10-01','4 per 1000','4.5/1,000 SF',
    'Surface lot, 120 spaces','Q1 2027',repeat('A',120)] loop
    if not ops.tour_client_text_safe(v_needle) then raise exception 'client value rule refused ordinary %', v_needle; end if;
  end loop;
  if ops.tour_public_value_safe('size','{"value":4200,"unit":"SF","label":"owner cell 251-555-0100"}')
     or ops.tour_public_value_safe('asking_economics','{"value":24,"currency":"USD","period":"NNN gate code 4411"}')
     or ops.tour_public_value_safe('size',jsonb_build_object('value',4200,'unit',repeat('U',121))) then
    raise exception 'value-safe admitted internal text inside a size/economics part';
  end if;

  -- Seal refusal: each smuggled value, in place of the clean fact for the same
  -- allowed key, inside an otherwise complete client set.
  for v_internal in select * from (values
    ('ac100000-0000-4000-8000-000000000021'::uuid,'b9000000-0000-4000-8000-000000000021','parking'),
    ('ac100000-0000-4000-8000-000000000022'::uuid,'b9000000-0000-4000-8000-000000000022','availability'),
    ('ac100000-0000-4000-8000-000000000023'::uuid,'b9000000-0000-4000-8000-000000000023','suite'),
    ('ac100000-0000-4000-8000-000000000024'::uuid,'b9000000-0000-4000-8000-000000000024','size'),
    ('ac100000-0000-4000-8000-000000000025'::uuid,'b9000000-0000-4000-8000-000000000025','property_type'),
    ('ac100000-0000-4000-8000-000000000026'::uuid,'b9000000-0000-4000-8000-000000000026','display.address'),
    ('ac100000-0000-4000-8000-000000000027'::uuid,'b9000000-0000-4000-8000-000000000027','asking_economics')) x(projection_id,assertion_id,field_key)
  loop
    insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
    values(v_internal.projection_id,'tour-client-share-proof','b2000000-0000-4000-8000-000000000001',
      (select coalesce(max(projection_version),0)+1 from ops.tour_public_projection where organization_tenant_id='tour-client-share-proof' and tour_id='b2000000-0000-4000-8000-000000000001'),
      1,now(),true,'sha256:'||repeat('0',64),'draft');
    begin
      perform ops.seal_tour_public_projection('tour-client-share-proof',v_internal.projection_id,
        (select jsonb_agg(case when e->>'display_field_key'=v_internal.field_key
                                then jsonb_set(e,'{field_assertion_id}',to_jsonb(v_internal.assertion_id)) else e end)
           from jsonb_array_elements(('['||v_all_client_facts||']')::jsonb) e),
        'tour-client-share-proof','sha256:'||repeat('6',64));
      raise exception 'seal admitted internal text inside allowed field %', v_internal.field_key;
    exception when raise_exception then
      if sqlerrm<>'projection fact lacks current public assertion, rights, or safe value' then raise; end if;
    end;
  end loop;

  -- Seal refusal: a stop whose label is free text rather than a short marker.
  insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
  values('ac100000-0000-4000-8000-000000000031','tour-client-share-proof','b2000000-0000-4000-8000-000000000002',1,1,now(),true,'sha256:'||repeat('0',64),'draft');
  begin
    perform ops.seal_tour_public_projection('tour-client-share-proof','ac100000-0000-4000-8000-000000000031',
      ('['||v_all_client_facts||']')::jsonb,'tour-client-share-proof','sha256:'||repeat('6',64));
    raise exception 'seal admitted a free-text stop label';
  exception when raise_exception then
    if sqlerrm<>'projection stop marker is not a client-safe route label' then raise; end if;
  end;

  -- Seal refusal: each now-internal field, alongside a complete client set.
  for v_internal in select * from (values
    ('ac100000-0000-4000-8000-000000000011'::uuid,'b9000000-0000-4000-8000-000000000009','access'),
    ('ac100000-0000-4000-8000-000000000012'::uuid,'b9000000-0000-4000-8000-000000000010','caveat'),
    ('ac100000-0000-4000-8000-000000000013'::uuid,'b9000000-0000-4000-8000-000000000011','photos')) x(projection_id,assertion_id,field_key)
  loop
    insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
    values(v_internal.projection_id,'tour-client-share-proof','b2000000-0000-4000-8000-000000000001',
      (select coalesce(max(projection_version),0)+1 from ops.tour_public_projection where organization_tenant_id='tour-client-share-proof' and tour_id='b2000000-0000-4000-8000-000000000001'),
      1,now(),true,'sha256:'||repeat('0',64),'draft');
    begin
      perform ops.seal_tour_public_projection('tour-client-share-proof',v_internal.projection_id,
        ('['||v_all_client_facts||',{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"'||v_internal.assertion_id||'","display_field_key":"'||v_internal.field_key||'"}]')::jsonb,
        'tour-client-share-proof','sha256:'||repeat('6',64));
      raise exception 'seal admitted internal field %', v_internal.field_key;
    exception when raise_exception then
      if sqlerrm<>'projection fact field is not client-allowlisted' then raise; end if;
    end;
    -- And no side door: a direct fact insert is refused the same way.
    begin
      insert into ops.tour_public_projection_fact(organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key)
      values('tour-client-share-proof',v_internal.projection_id,'b1000000-0000-4000-8000-000000000001',v_internal.assertion_id::uuid,1,v_internal.field_key);
      raise exception 'direct insert admitted internal field %', v_internal.field_key;
    exception when raise_exception then
      if sqlerrm<>'projection fact field is not client-allowlisted' then raise; end if;
    end;
  end loop;

  -- A clean seal of exactly the client fields succeeds and shares.
  insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
  values('ac100000-0000-4000-8000-000000000001','tour-client-share-proof','b2000000-0000-4000-8000-000000000001',
    (select coalesce(max(projection_version),0)+1 from ops.tour_public_projection where organization_tenant_id='tour-client-share-proof' and tour_id='b2000000-0000-4000-8000-000000000001'),
    1,now(),true,'sha256:'||repeat('0',64),'draft');
  v_digest:=ops.seal_tour_public_projection('tour-client-share-proof','ac100000-0000-4000-8000-000000000001',
    ('['||v_all_client_facts||']')::jsonb,'tour-client-share-proof','sha256:'||repeat('6',64));
  if v_digest !~ '^sha256:[a-f0-9]{64}$' then raise exception 'client projection did not seal'; end if;

  -- Legacy: a projection sealed before 0591 may already hold internal facts.
  -- Simulate it by appending them with triggers suspended, then prove the
  -- client and render reads still show none of them.
  set local session_replication_role = replica;
  insert into ops.tour_public_projection_fact(organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values
  ('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001','b9000000-0000-4000-8000-000000000009',1,'access'),
  ('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001','b9000000-0000-4000-8000-000000000010',1,'caveat'),
  ('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001','b9000000-0000-4000-8000-000000000011',1,'photos');
  set local session_replication_role = origin;

  v_share:=ops.issue_tour_share_grant('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','sha256:'||repeat('a',64),'["view_packet"]',now()+interval '1 day','sha256:'||repeat('b',64),'tour-client-share-proof');
  perform ops.exchange_tour_share_token('sha256:'||repeat('a',64),'sha256:'||repeat('e',64),now()+interval '1 day','sha256:'||repeat('f',64));
  v_packet:=ops.read_tour_share_packet('sha256:'||repeat('e',64));
  v_render:=ops.read_tour_packet_for_render('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','tour-client-share-proof');
  if v_packet is null or jsonb_array_length(v_packet->'stops')<>1 then raise exception 'client packet did not read back one stop'; end if;
  if v_render is null or jsonb_array_length(v_render->'packet'->'properties')<>1 then raise exception 'render packet did not read back one property'; end if;

  -- Positive: every allowlisted field arrives.
  if v_packet->'stops'->0->>'name'<>'Bayside Medical Plaza' or v_packet->'stops'->0->>'address'<>'100 Bayside Way'
     or v_packet->'stops'->0->>'suite'<>'Suite 210' or v_packet->'stops'->0->>'property_type'<>'medical_office'
     or v_packet->'stops'->0->'size'->>'value'<>'4200' or v_packet->'stops'->0->'asking_economics'->>'value'<>'24'
     or v_packet->'stops'->0->>'availability'<>'available now' or v_packet->'stops'->0->>'parking'<>'4 per 1000' then
    raise exception 'client packet dropped an allowlisted field: %', v_packet;
  end if;

  -- Negative: only allowlisted stop keys, and no internal material anywhere.
  for v_key in select jsonb_object_keys(v_packet->'stops'->0) union select jsonb_object_keys(v_render->'packet'->'properties'->0) loop
    if not (v_key = any(v_allowed_stop_keys)) then raise exception 'client stop carries non-allowlisted key %', v_key; end if;
  end loop;
  v_text := v_packet::text || v_render::text;
  foreach v_needle in array v_forbidden loop
    if strpos(v_text, v_needle) > 0 then raise exception 'internal material reached a client read: %', v_needle; end if;
  end loop;
  if v_packet->'caveat' is distinct from 'null'::jsonb or v_render->'packet'->'caveat' is distinct from 'null'::jsonb then
    raise exception 'packet-level caveat must stay an explicit null';
  end if;

  -- Legacy VALUE leak: a projection sealed before this rule may already hold
  -- an allowed-key fact whose text is internal. Seal a clean projection
  -- without parking, append the smuggled parking fact with triggers
  -- suspended, and prove both client reads drop it (the value-safety join).
  insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
  values('ac100000-0000-4000-8000-000000000002','tour-client-share-proof','b2000000-0000-4000-8000-000000000001',
    (select coalesce(max(projection_version),0)+1 from ops.tour_public_projection where organization_tenant_id='tour-client-share-proof' and tour_id='b2000000-0000-4000-8000-000000000001'),
    1,now(),true,'sha256:'||repeat('0',64),'draft');
  perform ops.seal_tour_public_projection('tour-client-share-proof','ac100000-0000-4000-8000-000000000002',
    (select jsonb_agg(e) from jsonb_array_elements(('['||v_all_client_facts||']')::jsonb) e where e->>'display_field_key'<>'parking'),
    'tour-client-share-proof','sha256:'||repeat('6',64));
  set local session_replication_role = replica;
  insert into ops.tour_public_projection_fact(organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values
  ('tour-client-share-proof','ac100000-0000-4000-8000-000000000002','b1000000-0000-4000-8000-000000000001','b9000000-0000-4000-8000-000000000021',1,'parking');
  set local session_replication_role = origin;
  perform ops.issue_tour_share_grant('tour-client-share-proof','ac100000-0000-4000-8000-000000000002','sha256:'||repeat('7',64),'["view_packet"]',now()+interval '1 day','sha256:'||repeat('b',64),'tour-client-share-proof');
  perform ops.exchange_tour_share_token('sha256:'||repeat('7',64),'sha256:'||repeat('8',64),now()+interval '1 day','sha256:'||repeat('9',64));
  v_packet:=ops.read_tour_share_packet('sha256:'||repeat('8',64));
  v_render:=ops.read_tour_packet_for_render('tour-client-share-proof','ac100000-0000-4000-8000-000000000002','tour-client-share-proof');
  if v_packet is null or jsonb_array_length(v_packet->'stops')<>1 or v_packet->'stops'->0->>'name'<>'Bayside Medical Plaza' then
    raise exception 'legacy value projection did not read back its safe stop: %', v_packet;
  end if;
  if v_packet->'stops'->0->>'parking' is not null or v_render->'packet'->'properties'->0->>'parking' is not null then
    raise exception 'unsafe legacy parking value reached a client read';
  end if;
  v_text := v_packet::text || v_render::text;
  foreach v_needle in array v_forbidden loop
    if strpos(v_text, v_needle) > 0 then raise exception 'internal text inside an allowed field reached a client read: %', v_needle; end if;
  end loop;

  -- Legacy STOP LABEL: a projection sealed before the marker rule may sit on
  -- a membership whose label is free text. Seal the label-proof tour with the
  -- new client-allowlist trigger suspended (as a pre-0591 seal was), and prove
  -- neither client read emits the free-text label.
  alter table ops.tour_public_projection_fact disable trigger tour_projection_fact_client_allowlist;
  perform ops.seal_tour_public_projection('tour-client-share-proof','ac100000-0000-4000-8000-000000000031',
    ('['||v_all_client_facts||']')::jsonb,'tour-client-share-proof','sha256:'||repeat('6',64));
  alter table ops.tour_public_projection_fact enable trigger tour_projection_fact_client_allowlist;
  perform ops.issue_tour_share_grant('tour-client-share-proof','ac100000-0000-4000-8000-000000000031','sha256:'||repeat('4',64),'["view_packet"]',now()+interval '1 day','sha256:'||repeat('b',64),'tour-client-share-proof');
  perform ops.exchange_tour_share_token('sha256:'||repeat('4',64),'sha256:'||repeat('5',64),now()+interval '1 day','sha256:'||repeat('3',64));
  v_packet:=ops.read_tour_share_packet('sha256:'||repeat('5',64));
  v_render:=ops.read_tour_packet_for_render('tour-client-share-proof','ac100000-0000-4000-8000-000000000031','tour-client-share-proof');
  if v_packet is null or jsonb_array_length(v_packet->'stops')<>1 or v_render is null then
    raise exception 'legacy label projection did not read back: %', v_packet;
  end if;
  if v_packet->'stops'->0->>'route_label' is not null or v_render->'packet'->'properties'->0->>'route_label' is not null
     or strpos(v_packet::text || v_render::text, 'Stop 1') > 0 then
    raise exception 'a free-text stop label reached a client read';
  end if;
end $client_allowlist$;

rollback;

\set ON_ERROR_STOP on
-- Disposable proof for 0591 (V5-J303): the client field allowlist and the
-- client value rule are enforced by the database, default deny. Every row is
-- rolled back.
--
-- Proven here:
--   * the shared corpus (test/fixtures/tour-client-text-corpus.json): every
--     ordinary CRE string passes ops.tour_client_text_violation(), every
--     smuggled string is refused under its named rule -- the node test runs
--     the same strings through the JavaScript rule;
--   * the seal refuses an access note, a caveat or a photo fact (and a direct
--     insert of one), internal text typed into any allowed field -- including
--     a size value stored as text -- naming the field and the rule, and a stop
--     whose label is free text;
--   * route acceptance's table refuses a free-text stop label;
--   * a clean seal shares; the list, the PDF render read and the map all carry
--     only allowlisted material;
--   * legacy parity: a projection holding a now-internal fact, an unsafe value
--     or a free-text label (sealed before 0591, simulated with triggers
--     suspended) is refused by the list, the PDF render read and the map
--     alike, and all three come back once it is clean again.
begin;

insert into ops.tour_property(id,organization_tenant_id,property_status,created_at) values
('b1000000-0000-4000-8000-000000000001','tour-client-share-proof','active',now()-interval '30 days');
insert into ops.tour_rights_receipt(id,organization_tenant_id,provider,policy_key,receipt_version,receipt_digest,terms_url,reviewed_at,reviewer,intended_use,allowed_field_classes,allowed_use_classes,effective_at,status)
values('b5000000-0000-4000-8000-000000000001','tour-client-share-proof','share-proof','share-policy',1,'sha256:'||repeat('1',64),'https://example.invalid/share',now()-interval '1 year','proof','share proof','["*"]','["source_intake","canonical_fact","client_public_display"]',now()-interval '1 year','active');
insert into ops.tour_source_evidence(id,organization_tenant_id,stable_locator,evidence_class,retrieved_at,retrieval_status,content_digest,rights_receipt_id,data_classification,rights_provider,rights_policy_key)
values('b6000000-0000-4000-8000-000000000001','tour-client-share-proof','proof:share','direct_source',now()-interval '31 days','read','sha256:'||repeat('2',64),'b5000000-0000-4000-8000-000000000001','public','share-proof','share-policy');
insert into ops.tour_field_assertion(id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,effective_to,confidence,data_classification,review_state,created_at) values
('b9000000-0000-4000-8000-000000000001','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','display.name','"Westgate Pines Medical Center"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000002','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','display.address','"6 Medical Park Dr. Mobile, AL 36608"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000003','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','suite','"Suites 101-1050"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000004','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','property_type','"Class A medical office"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000005','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','size','{"value":"4,200","unit":"RSF","label":"2 suites @ 1,200 SF"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000006','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','asking_economics','{"value":28.5,"currency":"USD","period":"SF/yr NNN"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000007','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','availability','"Available 03-15-2027"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000008','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','parking','"Keypad entry lobby; 4/1,000 surface"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
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
insert into ops.tour_property_coordinate_candidate(id,organization_tenant_id,property_id,coordinate_role,latitude,longitude,precision_class,source_evidence_id,rights_receipt_id,provider,observed_at,review_state)
values('be000000-0000-4000-8000-000000000001','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','entrance',30.421000,-87.216000,'entrance','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',null,now()-interval '2 days','reviewed');

-- Internal text typed INTO allowed fields (review of #1242): reviewed, public,
-- allowed keys -- only the VALUE is wrong.
insert into ops.tour_field_assertion(id,organization_tenant_id,property_id,field_key,value,source_evidence_id,rights_receipt_id,observed_at,effective_from,effective_to,confidence,data_classification,review_state,created_at) values
('b9000000-0000-4000-8000-000000000021','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','parking','"4 per 1000. Owner Bob Landlord 251-555-0100"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000022','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','availability','"Now - email bob@landlord.example"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000023','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','suite','"Suite 210, Gate code 4411"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000024','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','size','{"value":4200,"unit":"SF","label":"owner cell 251-555-0100"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000025','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','property_type','"Internal note: client is tight on budget"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000026','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','display.address','"100 Bayside Way (lockbox on rear door)"','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
('b9000000-0000-4000-8000-000000000027','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','asking_economics','{"value":24,"currency":"USD","period":"NNN see https://landlord.example"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days'),
-- a numeric part stored as TEXT still goes through the text rule
('b9000000-0000-4000-8000-000000000028','tour-client-share-proof','b1000000-0000-4000-8000-000000000001','size','{"value":"251 555 01 00","unit":"SF"}','b6000000-0000-4000-8000-000000000001','b5000000-0000-4000-8000-000000000001',now()-interval '20 days',now()-interval '20 days',null,'high','public','reviewed',now()-interval '20 days');

-- A second tour whose stop label is free text, not a short marker: a label
-- accepted before 0591 (the acceptance guard is suspended to plant it).
insert into ops.tour(id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,subject_type,subject_id,subject_bound_at)
values('b2000000-0000-4000-8000-000000000002','tour-client-share-proof','Label proof','draft',1,'proof-v1','work','proof',now());
insert into ops.tour_route_version(id,organization_tenant_id,tour_id,route_version,start_point,end_point,routing_source,routing_request,created_by_actor_id)
values('b3000000-0000-4000-8000-000000000002','tour-client-share-proof','b2000000-0000-4000-8000-000000000002',1,'{}','{}','manual','{}','tour-client-share-proof');
insert into ops.tour_route_stop(id,organization_tenant_id,route_version_id,property_id,route_sequence,route_label,stop_state,appointment_start,appointment_end,locked_appointment,dwell_minutes,buffer_minutes,access_coordinate_status,assertion_set_digest,created_by_actor_id)
values('b4000000-0000-4000-8000-000000000002','tour-client-share-proof','b3000000-0000-4000-8000-000000000002','b1000000-0000-4000-8000-000000000001',1,'Stop 1','active',null,null,false,30,10,'approved','sha256:'||repeat('d',64),'tour-client-share-proof');
insert into ops.tour_route_version_acceptance(organization_tenant_id,tour_id,route_version_id,expected_prior_route_version,accepted_by_actor_id,acceptance_digest)
values('tour-client-share-proof','b2000000-0000-4000-8000-000000000002','b3000000-0000-4000-8000-000000000002',0,'tour-client-share-proof','sha256:'||repeat('c',64));
set local session_replication_role = replica;
insert into ops.tour_property_membership(id,organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest,selected_at)
values('bd000000-0000-4000-8000-000000000002','tour-client-share-proof','b2000000-0000-4000-8000-000000000002','b1000000-0000-4000-8000-000000000001',1,1,'Stop 1','sha256:'||repeat('d',64),now()-interval '1 hour');
set local session_replication_role = origin;

set local carr.verified_human_actor_slug='joe';
do $client_allowlist$
declare
  v_actor_id text; v_digest text; v_share uuid; v_packet jsonb; v_render jsonb; v_map jsonb; v_text text; v_key text;
  v_allowed_stop_keys text[] := array['property_ref','route_sequence','route_label','name','address','suite','property_type','size','asking_economics','availability','parking'];
  v_forbidden text[] := array['Gate code','Bob Landlord','251-555-0100','251 555 01 00','tight on budget','Acme Pediatrics','ownerphoto','2031-03-04','Stop 1',
    'b1000000-0000-4000-8000-000000000001','b2000000-0000-4000-8000-000000000001','ac100000-0000-4000-8000-000000000001',
    'b9000000-0000-4000-8000-00000000000','b5000000-0000-4000-8000-000000000001','b6000000-0000-4000-8000-000000000001'];
  v_needle text;
  v_all_client_facts text := '{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000001","display_field_key":"display.name"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000002","display_field_key":"display.address"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000003","display_field_key":"suite"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000004","display_field_key":"property_type"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000005","display_field_key":"size"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000006","display_field_key":"asking_economics"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000007","display_field_key":"availability"},{"property_id":"b1000000-0000-4000-8000-000000000001","field_assertion_id":"b9000000-0000-4000-8000-000000000008","display_field_key":"parking"}';
  v_internal record; v_case record; v_legacy record;
begin
  select id::text into strict v_actor_id from public.actor where slug='joe' and active and kind='human';

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

  -- The shared corpus: ordinary CRE text passes ...
  foreach v_needle in array array[
    'Suite 200-B','4,200 RSF','$28.50/SF/yr NNN','4/1,000 surface; 12 covered',
    'Available 01/15/2027','Dr. Smith Medical Plaza','Bldg 3, Ste 101','4,200 RSF @ $28.50/SF',
    '2 suites @ 1,200 SF','Westgate Pines Medical Center','Sentry Pinnacle Plaza','Key Pinnacle Professional Park',
    'Gentry Pines Office Park','Northgate Pinecrest Medical','Cedar Key Pines','Alarm-monitored building',
    'Fire alarm system upgraded 2025','Keypad entry lobby','Access pinned to ramp','Pinnacle Medical Plaza',
    'Key West Medical Plaza','Suites 100-1200','Suites 101-1050','Suites 100-120',
    'Ste 250-1200','4200-5000 SF','36602-1234','6 Medical Park Dr. Mobile, AL 36608',
    '1 Infirmary Dr.','St. Mary''s Plaza','U.S. Hwy 98 frontage','Hwy 98.Co Rd 30 corner',
    'Available Q1 2027','$24.00-$26.50/SF/yr','$1,850,000 sale','Parcel 12-34-56-7890-000',
    'Parking 5.0/1,000 +40 overflow 2026','Unit 1234.5678','Ste. 101','Available 3.15.2027',
    'Available 03-15-2027','Available 2026-10-01','Suite 1501 & 1502','Class A medical office',
    'MOB / ASC','Free rent 3 mo; TI $45/SF','Available 30 days'' notice','Ascension Sacred Heart campus',
    'Bayside Medical Plaza','100 Bayside Way, Pensacola, FL 32502','1250 E 9 Mile Rd, Pensacola, FL 32514-1234','Suite 210',
    'medical_office','Medical office','available now','4 per 1000',
    '4.5/1,000 SF','Surface lot, 120 spaces','Shell condition','Q1 2027',
    'Covered parking, gated garage','Key location on I-10','100-1200 SF',U&'Suite 100, Building B, Baptist Medical Park \2013 Nine Mile',
    'Providence Hospital campus (Mobile)',U&'Thomas Hospital \2013 Fairhope','ASC-ready, 2 ORs, 1 procedure room',U&'Medical condo \2013 2,150 RSF \00b1 for sale',
    '$22.00/SF NNN + $6.50 CAM','NNN est. $7.25/SF (2026)','Gross lease: $3,500/mo','Pad site 1.25 ac',
    U&'3,000 \2013 12,000 SF divisible','Hwy 59 & I-10 interchange, Loxley','2.5 ac, zoned B-2','Flood Zone X',
    'Available 60 days after lease execution','Ample parking 5/1,000 + 10 ADA','Parking: 6.0/1,000 RSF (surface); 45 reserved',U&'Suites 101\2013104 contiguous',
    'Suite 200 (2nd floor)','1st floor: 4,200 RSF; 2nd floor: 3,100 RSF',U&'Bldg 200, Ste 210\2013212',U&'Available: 10/1/2026 \2013 12/31/2026',
    'Asking $2,450,000 ($285/SF)','Year built 1998, renovated 2021','Total 18,500 SF; 4 suites','Units 1-4 (4 x 1,250 SF)',
    '$18.00 - $19.50/SF NNN (yr 1) (3% escalations)','Available 2026 (251 spaces)','Pensacola 32504 (850) area','Hwy 90.US 29 corner',
    'Key card entry','Gated lot, card access','Suites 120 - 125 & 130 - 135','Space 4 (2nd fl) 2,500 - 3,100 SF',
    'Parking: 200 (1 per 250 SF)','1,200 SF (Suite 101) 2,400 SF (Suite 102)','4 exam rooms, 1 lab, 2 offices','Dialysis / infusion ready',
    'Suite 1100-1150','Ste. 305 & 306','2 Mile Rd & Hwy 29, Cantonment','Available 11.2026',
    'Suites 250 - 2500 SF','Rooms 101-1205','Room 120-1250 SF','Suite 300, 1,500-3,000 SF',
    'Ste 250 5000 SF','Suite 251-5550','251 Government St, Mobile, AL 36602','Available 07.01.2027',
    'Parking 4/1,000; 250 total; 55 covered','2700 Airport Blvd Ste 101','1901 N 12th Ave Ste 100, Pensacola, FL 32503',U&'Suites 300, 301 \2013 305 (2,100 \2013 6,450 RSF)',
    'Parking: 1:250','$25/SF full service (FSG)',U&'Suite\00a0210',U&'\00a0Bayside Medical Plaza\00a0',
    U&'4,200\202fRSF',U&'Suites\2009101\2013104','Renovated 2021 - 2026 (12 suites)','Available 2026 - 2027 (18 mo)',
    '4 bldgs (1985 - 2019) 120,000 SF','Renovated 2021-2026 (12 suites)','4,200 RSF @ $28.50','Suites 201-204, 1200 SF',
    '1,250,000 SF campus','Built 2019/2020, 1,200 SF',U&'\ff22\ff41\ff59\ff53\ff49\ff44\ff45 Medical Plaza','Rate @ market',
    'Available 2026 - 2027 12 mo leases','Sizes 1500 (300) 2000 SF','Dept. Health offices nearby','Baldwin Co. Care Center',
    U&'\201cClass A\201d medical office \2022 2 stories',U&'Caf\00e9 on site; se\00f1or center adjacent',U&'Lot 150\00d7300 ft',U&'Built 1998\2026 renovated 2021',
    U&'\00a7 4.2 zoning; 72\00b0 lobby','Suite 200 (Unit 2), 1,200 SF','Time: 10:30 am tours','Ratio 4:1,000',
    'Rent $24 x 12 mo',U&'Suites 101\2010104',U&'Available 2026\20112027',U&'Suites 250\20102500 SF',
    'Suite 200','Door 3','Gate 2 parking','garage 250 spaces',
    'Zip code 36602','ZIP Code: 32502','Door 12 entrance','Gate 45 lot',
    'Code-compliant 2021 build-out',U&'Bldg 2\2011B, 1,200 SF','Rooms 301, 302 (1200 SF)','Northgate Code Compliance Center',
    'Eastgate 1200 SF',
    repeat('A',120),U&'\00a0'||repeat('A',120)||U&'\00a0','A'||repeat(U&'\00a0',10)||repeat('A',118)] loop
    if ops.tour_client_text_violation(v_needle) is not null then
      raise exception 'client value rule refused ordinary % (%)', v_needle, ops.tour_client_text_violation(v_needle);
    end if;
    if ops.tour_public_value_safe('parking',to_jsonb(v_needle)) is distinct from true then raise exception 'value-safe refused ordinary %', v_needle; end if;
  end loop;
  -- The documented residuals (digits spelled out or swapped for look-alike
  -- letters, "Suite 555-1234") are still ALLOWED, exactly as in the
  -- JavaScript rule; the human review before a seal is their control.
  foreach v_needle in array array[
    'Owner Bob: two five one 555 0100','Call five five five zero one hundred','251-555-O1OO','25l-555-0l00','251-555-0I00','251-555-01OO','Two51 555 0100','Suite 555-1234',U&'Suite 555 \2013 1234','Suite 555-1234 / Unit 555-9876','Tenant is tight on budget this quarter'] loop
    if ops.tour_client_text_violation(v_needle) is not null then
      raise exception 'documented residual % changed (%)', v_needle, ops.tour_client_text_violation(v_needle);
    end if;
  end loop;
  -- Normalization is the text a client reads: NFKC, space runs collapsed,
  -- ends trimmed.
  if ops.tour_client_text_normalize(U&'\ff22\ff41\ff59\ff53\ff49\ff44\ff45\3000Medical\00a0\00a0Plaza ') <> 'Bayside Medical Plaza'
     or ops.tour_client_text_normalize(U&'\ff12\ff15\ff11\ff0d555') <> '251-555' then
    raise exception 'client text normalization drifted';
  end if;
  -- ... and contact, access and internal-note text is refused under its rule.
  for v_case in select * from (values
    ('4 per 1000. Owner Bob 251-555-0100','phone'),
    ('Call (251) 555-0100 before visiting','phone'),
    ('Owner cell 2515550100','phone'),
    ('Owner 251-5550100','phone'),
    ('Owner (251)5550100','phone'),
    ('Owner 251 555 01 00','phone'),
    ('Call +1 (251) 555-0100','phone'),
    ('2901234567','phone'),
    ('Ask for 555-0100 at the desk','local_phone'),
    ('UK owner +44 20 7946 0958','international_phone'),
    ('Email bob@landlord.example for access','email'),
    ('Owner: bob @ landlord.com','email'),
    ('Details at https://landlord.example/private','url'),
    ('See www.landlord-portal.com','url'),
    ('Leasing via landlordportal.com','url'),
    ('Gate code 4411','access_code'),
    ('Door code is 1234#','access_code'),
    ('Key code 9021 on the side door','access_code'),
    ('Keycode-free entry','access_code'),
    ('Alarm code 2231','access_code'),
    ('Keypad code 7788 at dock','access_code'),
    ('Entry PIN 5555','access_code'),
    ('Garage Combination parking','access_code'),
    ('Lockbox on the rear gate','lockbox'),
    ('Lock box left of the entrance','lockbox'),
    ('Internal note: client is tight on budget','internal_note'),
    ('Confidential - do not share with tenant','internal_note'),
    ('Confidential (tenant NDA)','internal_note'),
    ('Broker-only pricing','internal_note'),
    ('Not for client eyes','internal_note'),
    ('Owner 251 - 555 - 0100','phone'),
    ('Call 251   555   0100','phone'),
    ('251 . 555 . 0100','phone'),
    (U&'Call 251\00a0555\00a00100','phone'),
    (U&'Call 251\2009555\20090100','phone'),
    (U&'Call 251\202f555\202f0100','phone'),
    (U&'Call 251\200b555\200b0100','character:U+200B at position 9'),
    (U&'Call 251 \2013 555 \2013 0100','phone'),
    ('011 44 20 7946 0958','international_phone'),
    ('+ 44 20 7946 0958','international_phone'),
    ('bob@landlord_co.fl','email'),
    ('cell 555  -  0100','local_phone'),
    (U&'cell 555\00a0-\00a00100','local_phone'),
    ('Unit 555-0100','local_phone'),
    ('Ste #555.0100','local_phone'),
    ('Suite 555-0100, Suite 555-0199','local_phone'),
    ('Ste 555.1234','local_phone'),
    (U&'Owner 251\00a0555\00a00100','phone'),
    (U&'251\2009555\20090100','phone'),
    (U&'251\202f555\202f0100','phone'),
    (U&'251\2013555\20130100','phone'),
    (U&'251\2014555\20130100','phone'),
    (U&'251 \2013 555 \2014 0100','phone'),
    (U&'251\2011555\20110100','phone'),
    (U&'251\2012555\20120100','character:U+2012 at position 4'),
    (U&'251\2212555\22120100','character:U+2212 at position 4'),
    (U&'251\fe63555\fe630100','character:U+FE63 at position 4'),
    (U&'251\ff0d555\ff0d0100','phone'),
    (U&'251\200d555\200d0100','character:U+200D at position 4'),
    (U&'2\200d5\200d1\200d5\200d5\200d5\200d0\200d1\200d0\200d0','character:U+200D at position 2'),
    (U&'251\2060555\20600100','character:U+2060 at position 4'),
    (U&'251\00ad555\00ad0100','character:U+00AD at position 4'),
    (U&'251\200c555\200c0100','character:U+200C at position 4'),
    (U&'251\180e555\180e0100','character:U+180E at position 4'),
    (U&'\ff12\ff15\ff11-\ff15\ff15\ff15-\ff10\ff11\ff10\ff10','phone'),
    (U&'\ff12\ff15\ff11\ff15\ff15\ff15\ff10\ff11\ff10\ff10','phone'),
    ('(251)555 0100','phone'),
    ('(251) 555 0100','phone'),
    ('(251)555-0100','phone'),
    ('251/555/0100','phone'),
    ('251_555_0100','phone'),
    ('251,555,0100','phone'),
    ('251 555 0100 ext 12','phone'),
    ('2 5 1 5 5 5 0 1 0 0','phone'),
    ('2-5-1-5-5-5-0-1-0-0','phone'),
    ('25 15 55 01 00','phone'),
    ('251 . . 555 . . 0100','phone'),
    ('251 ( 555 ) 0100','phone'),
    ('[251] 555-0100','local_phone'),
    ('251.555.0100','phone'),
    ('1-251-555-0100','phone'),
    ('+1 251 555 0100','phone'),
    ('+12515550100','phone'),
    ('Suite 555-0100','local_phone'),
    ('Rm 555-1234','local_phone'),
    ('bob@landlord.co','email'),
    ('bob @ landlord.co','email'),
    (U&'bob\00a0@\00a0landlord.co','email'),
    (U&'bob\ff20landlord.co','email'),
    ('bob@landlord','email'),
    ('www.bayside','url'),
    ('http://x','url'),
    (U&'251\2010555\20100100','phone'),
    (U&'251\2015555\20150100','character:U+2015 at position 4'),
    (U&'251\fe58555\fe580100','character:U+FE58 at position 4'),
    (U&'251\200e555\200f0100','character:U+200E at position 4'),
    (U&'251\2061555\20640100','character:U+2061 at position 4'),
    (U&'251\feff555\feff0100','character:U+FEFF at position 4'),
    (U&'cell 555\20110100','local_phone'),
    (U&'Suite 555\22120100','character:U+2212 at position 10'),
    (U&'bob\fe6blandlord.co','character:U+FE6B at position 4'),
    ('bob@landlord_co','email'),
    ('Call (251) 555 - 0100','phone'),
    (U&'251\2028555\20280100','character:U+2028 at position 4'),
    (U&'251\1680555\16800100','character:U+1680 at position 4'),
    (U&'cell 555 \200d - \200d 0100','character:U+200D at position 10'),
    (U&'251\221255\2212501\221200','character:U+2212 at position 4'),
    (U&'25\201015\201055\201001\201000','phone'),
    ('bob(at)landlord.co','email'),
    ('landlord.realty','url'),
    ('bayside.health','url'),
    (U&'251\fe0f555\fe0f0100','character:U+FE0F at position 4'),
    (U&'251\034f555\034f0100','character:U+034F at position 4'),
    (U&'251\180b555\180b0100','character:U+180B at position 4'),
    (U&'251\+0e0020555\+0e00200100','character:U+E0020 at position 4'),
    (U&'251\3164555\31640100','character:U+3164 at position 4'),
    (U&'251\2800555\28000100','character:U+2800 at position 4'),
    (U&'251\17b4555\17b40100','character:U+17B4 at position 4'),
    (U&'251\115f555\115f0100','character:U+115F at position 4'),
    (U&'251\00b7555\00b70100','character:U+00B7 at position 4'),
    (U&'251\2022555\20220100','phone'),
    (U&'251\2027555\20270100','character:U+2027 at position 4'),
    (U&'251\2043555\20430100','character:U+2043 at position 4'),
    ('251~555~0100','phone'),
    ('251|555|0100','phone'),
    ('251:555:0100','phone'),
    ('251*555*0100','phone'),
    ('251 x 555 x 0100','phone'),
    (U&'251\2044555\20440100','character:U+2044 at position 4'),
    (U&'251\ff0f555\ff0f0100','phone'),
    (U&'\0662\0665\0661-\0665\0665\0665-\0660\0661\0660\0660','character:U+0662 at position 1'),
    (U&'\06f2\06f5\06f1\06f5\06f5\06f5\06f0\06f1\06f0\06f0','character:U+06F2 at position 1'),
    (U&'\+01d7d0\+01d7d3\+01d7cf-555-0100','character:U+1D7D0 at position 1'),
    (U&'\2461\2464\2460-555-0100','character:U+2461 at position 1'),
    (U&'\00b2\2075\00b9-555-0100','character:U+00B2 at position 1'),
    (U&'\2082\2085\2081-555-0100','character:U+2082 at position 1'),
    (U&'\+01d7f8\+01d7fb\+01d7f7 555 0100','character:U+1D7F8 at position 1'),
    (U&'251 555 0100\0085','control_character'),
    (U&'251\0085555\00850100','control_character'),
    (U&'251\2028555\20290100','character:U+2028 at position 4'),
    (U&'251-555-0100\20e3','character:U+20E3 at position 13'),
    (U&'2\03035\03031-555-0100','character:U+0303 at position 2'),
    ('bob @landlord','email'),
    ('bob@ landlord','email'),
    ('bob [at] landlord [dot] com','url'),
    ('bob at landlord dot com','url'),
    (U&'bob@landlord\2024com','character:U+2024 at position 13'),
    (U&'bob@landlord\3002com','character:U+3002 at position 13'),
    (U&'bob@landlord\ff0ecom','email'),
    ('landlord.co','url'),
    ('bayside.co','url'),
    ('landlord.ai','url'),
    ('bayside.app','url'),
    ('landlord.properties','url'),
    ('WWW.LANDLORD.COM','url'),
    (U&'landlord\2024com','character:U+2024 at position 9'),
    ('landlord .com','url'),
    ('landlord. com','url'),
    ('(251) 555-0100','phone'),
    ('251.5550100','phone'),
    ('2515550100','phone'),
    (U&'+1 (251) 555\20110100','phone'),
    ('1 (251) 555 0100','phone'),
    ('251 555 0 1 0 0','phone'),
    ('2 51 55 50 10 0','phone'),
    ('251-55-50-100','phone'),
    ('2515-550-100','phone'),
    ('25155-50100','phone'),
    ('Ste 555-0100','local_phone'),
    ('Ste 555.0100','local_phone'),
    ('Ste #555-0100','local_phone'),
    (U&'\202e1144 edoc etaG','character:U+202E at position 1'),
    (U&'\202etegdub no thgit si tneilc :eton lanretnI','character:U+202E at position 1'),
    (U&'\202a251\202c555\202d0100','character:U+202A at position 1'),
    (U&'\2066251\2069 555 0100','character:U+2066 at position 1'),
    (U&'\061c251 555 0100','character:U+061C at position 1'),
    (U&'\2067x\2068y','character:U+2067 at position 1'),
    (U&'251\fe00555\fe0f0100','character:U+FE00 at position 4'),
    (U&'251\+0e00011555\e007f0100','character:U+E0001 at position 4'),
    (U&'\3164','character:U+3164 at position 1'),
    (U&'\115f\1160','character:U+115F at position 1'),
    (U&'251\17b5555\17b50100','character:U+17B5 at position 4'),
    (U&'251\180d555\180d0100','character:U+180D at position 4'),
    (U&'\06f2\06f5\06f1-555-0100','character:U+06F2 at position 1'),
    (U&'251 555 0100\200b','character:U+200B at position 13'),
    (U&'\00ad','character:U+00AD at position 1'),
    ('bob (at) landlord.co','email'),
    ('bob[at]landlord.health','email'),
    ('bob at landlord dot net','url'),
    ('landlord (dot) org','url'),
    ('landlord.law','url'),
    ('bayside.care','url'),
    ('bayside.clinic','url'),
    ('landlord.homes','url'),
    ('landlord.gov','url'),
    ('landlord.edu','url'),
    ('landlord.me','url'),
    (U&'251 \2022 555 \2022 0100','phone'),
    (U&'251\00d7555\00d70100','phone'),
    (U&'251\00b1555\00b10100','phone'),
    (U&'251\00b0555\00b00100','phone'),
    (U&'251\00a7555\00a70100','phone'),
    (U&'251\2019555\20190100','phone'),
    (U&'251\201c555\201d0100','phone'),
    (U&'251\2018555\20180100','phone'),
    ('251"555"0100','phone'),
    ('251''555''0100','phone'),
    (U&'Call 251 \00d7 555 \00d7 0100','phone'),
    ('Gate #4411','access_code'),
    ('Gate: 4411#','access_code'),
    ('Front gate 4411','access_code'),
    ('PIN 4411','access_code'),
    ('Code 4411 at front gate','access_code'),
    ('Combo 4411 (front gate)','access_code'),
    ('Keypad 1234','access_code'),
    ('Alarm: 90210','access_code'),
    ('Lock #007','access_code'),
    ('Passcode 8812','access_code'),
    ('Door 4411','access_code'),
    ('bit.ly/abc','url'),
    ('goo.gl/x','url'),
    ('tinyurl.com/abc','url'),
    ('ftp://files.example','url'),
    ('landlord[.]com','url'),
    ('landlord (.) com','url'),
    (U&'A\013fB','character:U+013F at position 2'),
    (U&'Wait\2026\00ad','character:U+00AD at position 6'),
    (U&'Hwy\0149 90','character:U+0149 at position 4'),
    (U&'Wait\2026\202e1144','character:U+202E at position 6'),
    (U&'Wait\2026\200b','character:U+200B at position 6'),
    ('Rates@market','email'),
    ('Bldg 200/300/1400','phone'),
    ('2021-2026 12 suites','phone'),
    ('Springhill Ave.Me','url'),
    (U&'Suite\00ad 210','character:U+00AD at position 6'),
    (U&'Bayside\2028Medical Plaza','character:U+2028 at position 8'),
    (repeat('A',121),'too_long'),
    (E'Available\nnow','control_character'),
    ('   ','empty')) x(value,rule) loop
    if ops.tour_client_text_violation(v_case.value) is distinct from v_case.rule then
      raise exception 'client value rule: % gave %, expected %', v_case.value, ops.tour_client_text_violation(v_case.value), v_case.rule;
    end if;
    if ops.tour_public_value_safe('parking',to_jsonb(v_case.value)) is distinct from false then raise exception 'value-safe admitted parking %', v_case.value; end if;
  end loop;
  if ops.tour_client_text_violation(null) is distinct from 'not_text' then raise exception 'client value rule admitted null'; end if;

  -- Size / asking economics: every part, text-stored numbers included.
  if ops.tour_public_value_safe('size','{"value":4200,"unit":"SF"}') is distinct from true
     or ops.tour_public_value_safe('size','{"value":"4,200","unit":"RSF","label":"4,200 RSF @ $28.50/SF"}') is distinct from true
     or ops.tour_public_value_safe('asking_economics','{"value":24,"currency":"USD","period":"NNN"}') is distinct from true
     or ops.tour_public_value_safe('asking_economics','{"min":24,"max":26.5,"currency":"USD"}') is distinct from true then
    raise exception 'value-safe refused an ordinary size/economics value';
  end if;
  for v_case in select * from (values
    ('{"min":10,"max":5}','metric_range'),
    ('{"value":4200,"unit":"SF","label":"owner cell 251-555-0100"}','label.phone'),
    ('{"value":"251 555 01 00","unit":"SF"}','value.phone'),
    ('{"min":"555-0100","max":5000}','min.local_phone'),
    ('{"value":24,"unit":"SF","period":"NNN gate code 4411"}','period.access_code'),
    ('{"value":24,"verifier":"x"}','metric_shape'),
    ('{"unit":"SF"}','metric_shape'),
    ('{"value":24,"unit":{"x":1}}','unit.not_text'),
    ('"4200 SF"','metric_shape')) x(value,rule) loop
    if ops.tour_client_value_violation('size',v_case.value::jsonb) is distinct from v_case.rule then
      raise exception 'size rule: % gave %, expected %', v_case.value, ops.tour_client_value_violation('size',v_case.value::jsonb), v_case.rule;
    end if;
    if ops.tour_public_value_safe('size',v_case.value::jsonb) is distinct from false then raise exception 'value-safe admitted size %', v_case.value; end if;
  end loop;
  if ops.tour_public_value_safe('size',jsonb_build_object('value',4200,'unit',repeat('U',121))) is distinct from false then
    raise exception 'value-safe admitted an over-long size unit';
  end if;

  -- Route acceptance refuses a stop label that could never be sealed.
  foreach v_needle in array array['Stop 9','ABCD','A-1',' A'] loop
    begin
      insert into ops.tour_property_membership(organization_tenant_id,tour_id,property_id,route_version,route_sequence,route_label,assertion_set_digest,selected_at)
      values('tour-client-share-proof','b2000000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001',9,1,v_needle,'sha256:'||repeat('d',64),now());
      raise exception 'route acceptance admitted stop label %', v_needle;
    exception when raise_exception then
      if sqlerrm<>'route stop label must be a 1-3 letter or digit client marker' then raise; end if;
    end;
  end loop;

  -- The entrance coordinate a client drives to is human-verified before seal.
  perform ops.append_tour_entrance_verification_receipt(jsonb_build_object(
    'organization_tenant_id','tour-client-share-proof','property_id','b1000000-0000-4000-8000-000000000001',
    'coordinate_candidate_id','be000000-0000-4000-8000-000000000001','verifier_actor_id',v_actor_id,
    'verified_at',to_char(now()-interval '1 day','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'evidence_reference','proof:native-navigation:client-share','native_navigation_proof',jsonb_build_object('status','passed'),
    'receipt_digest','sha256:'||repeat('4',64)));

  -- Seal refusal: each smuggled value, in place of the clean fact for the same
  -- allowed key, inside an otherwise complete client set. The refusal names
  -- the field and the rule, never the value.
  for v_internal in select * from (values
    ('ac100000-0000-4000-8000-000000000021'::uuid,'b9000000-0000-4000-8000-000000000021','parking','phone'),
    ('ac100000-0000-4000-8000-000000000022'::uuid,'b9000000-0000-4000-8000-000000000022','availability','email'),
    ('ac100000-0000-4000-8000-000000000023'::uuid,'b9000000-0000-4000-8000-000000000023','suite','access_code'),
    ('ac100000-0000-4000-8000-000000000024'::uuid,'b9000000-0000-4000-8000-000000000024','size','label.phone'),
    ('ac100000-0000-4000-8000-000000000025'::uuid,'b9000000-0000-4000-8000-000000000025','property_type','internal_note'),
    ('ac100000-0000-4000-8000-000000000026'::uuid,'b9000000-0000-4000-8000-000000000026','display.address','lockbox'),
    ('ac100000-0000-4000-8000-000000000027'::uuid,'b9000000-0000-4000-8000-000000000027','asking_economics','period.url'),
    ('ac100000-0000-4000-8000-000000000028'::uuid,'b9000000-0000-4000-8000-000000000028','size','value.phone')) x(projection_id,assertion_id,field_key,rule)
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
        v_actor_id,'sha256:'||repeat('6',64));
      raise exception 'seal admitted internal text inside allowed field %', v_internal.field_key;
    exception when raise_exception then
      if sqlerrm<>format('projection fact %s is not client-safe: %s', v_internal.field_key, v_internal.rule) then raise; end if;
    end;
  end loop;

  -- Seal refusal: a stop whose label is free text rather than a short marker.
  insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
  values('ac100000-0000-4000-8000-000000000031','tour-client-share-proof','b2000000-0000-4000-8000-000000000002',1,1,now(),true,'sha256:'||repeat('0',64),'draft');
  begin
    perform ops.seal_tour_public_projection('tour-client-share-proof','ac100000-0000-4000-8000-000000000031',
      ('['||v_all_client_facts||']')::jsonb,v_actor_id,'sha256:'||repeat('6',64));
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
        v_actor_id,'sha256:'||repeat('6',64));
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

  -- A clean seal of exactly the client fields -- ordinary text the old rule
  -- refused -- succeeds, is promoted for the map, and shares.
  insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
  values('ac100000-0000-4000-8000-000000000001','tour-client-share-proof','b2000000-0000-4000-8000-000000000001',
    (select coalesce(max(projection_version),0)+1 from ops.tour_public_projection where organization_tenant_id='tour-client-share-proof' and tour_id='b2000000-0000-4000-8000-000000000001'),
    1,now(),true,'sha256:'||repeat('0',64),'draft');
  v_digest:=ops.seal_tour_public_projection('tour-client-share-proof','ac100000-0000-4000-8000-000000000001',
    ('['||v_all_client_facts||']')::jsonb,v_actor_id,'sha256:'||repeat('6',64));
  if v_digest !~ '^sha256:[a-f0-9]{64}$' then raise exception 'client projection did not seal'; end if;
  perform ops.record_tour_map_promotion_receipt('tour-client-share-proof','ac100000-0000-4000-8000-000000000001',jsonb_build_object(
    'decision','approved','reviewed_at',to_char(now(),'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'decision_reason','All required map promotion checks passed.',
    'brief_version','tour-map-brief.v1','canonical_dataset_version','proof-v1','selected_prototype_id','carr-map-tour-v1',
    'component_registry_version','maplibre-6.1.0','route_version',1,
    'provider_rights_receipt_ids',jsonb_build_array('b5000000-0000-4000-8000-000000000001'),
    'mobile_test_evidence',jsonb_build_object('status','passed'),'native_navigation_test_evidence',jsonb_build_object('status','passed'),
    'offline_test_evidence',jsonb_build_object('status','passed'),'required_checks',jsonb_build_object(
      'canonical_address_and_coordinate_review',true,'claims_and_layers_have_source_as_of_rights_and_review_state',true,
      'deterministic_rebuild_from_canonical_record',true,'exact_native_navigation_handoff',true,
      'locked_appointments_dwell_and_buffers_preserved',true,'map_list_route_offline_order_parity',true,
      'no_unresolved_route_critical_unknown_or_conflict',true,'optional_context_layers_progressively_disclosed',true,
      'ordered_offline_itinerary_verified',true,'phone_and_ipad_interaction_test',true,
      'provider_terms_attribution_expiry_and_cost_gate_passed',true),'receipt_digest','sha256:'||repeat('9',64)),v_actor_id);
  v_share:=ops.issue_tour_share_grant('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','sha256:'||repeat('a',64),'["view_packet","view_map"]',now()+interval '1 day','sha256:'||repeat('b',64),v_actor_id);
  perform ops.exchange_tour_share_token('sha256:'||repeat('a',64),'sha256:'||repeat('e',64),now()+interval '1 day','sha256:'||repeat('f',64));
  v_packet:=ops.read_tour_share_packet('sha256:'||repeat('e',64));
  v_render:=ops.read_tour_packet_for_render('tour-client-share-proof','ac100000-0000-4000-8000-000000000001',v_actor_id);
  v_map:=ops.read_tour_share_map('sha256:'||repeat('e',64));
  if v_packet is null or jsonb_array_length(v_packet->'stops')<>1 then raise exception 'client packet did not read back one stop'; end if;
  if v_render is null or jsonb_array_length(v_render->'packet'->'properties')<>1 then raise exception 'render packet did not read back one property'; end if;
  if v_map is null or jsonb_array_length(v_map->'points')<>1 or v_map->'points'->0->>'route_label'<>'A' then raise exception 'client map did not read back one marked point: %', v_map; end if;

  -- Positive: every allowlisted field arrives, ordinary text intact.
  if v_packet->'stops'->0->>'name'<>'Westgate Pines Medical Center' or v_packet->'stops'->0->>'address'<>'6 Medical Park Dr. Mobile, AL 36608'
     or v_packet->'stops'->0->>'suite'<>'Suites 101-1050' or v_packet->'stops'->0->>'property_type'<>'Class A medical office'
     or v_packet->'stops'->0->'size'->>'label'<>'2 suites @ 1,200 SF' or v_packet->'stops'->0->'asking_economics'->>'period'<>'SF/yr NNN'
     or v_packet->'stops'->0->>'availability'<>'Available 03-15-2027' or v_packet->'stops'->0->>'parking'<>'Keypad entry lobby; 4/1,000 surface'
     or v_packet->'stops'->0->>'route_label'<>'A' or v_render->'packet'->'properties'->0->>'route_label'<>'A' then
    raise exception 'client packet dropped an allowlisted field: %', v_packet;
  end if;

  -- Negative: only allowlisted stop keys, and no internal material anywhere.
  for v_key in select jsonb_object_keys(v_packet->'stops'->0) union select jsonb_object_keys(v_render->'packet'->'properties'->0) loop
    if not (v_key = any(v_allowed_stop_keys)) then raise exception 'client stop carries non-allowlisted key %', v_key; end if;
  end loop;
  for v_key in select jsonb_object_keys(v_map->'points'->0) loop
    if not (v_key = any(array['property_ref','route_sequence','route_label','latitude','longitude'])) then raise exception 'map point carries key %', v_key; end if;
  end loop;
  v_text := v_packet::text || v_render::text || v_map::text;
  foreach v_needle in array v_forbidden loop
    if strpos(v_text, v_needle) > 0 then raise exception 'internal material reached a client read: %', v_needle; end if;
  end loop;
  if v_packet->'caveat' is distinct from 'null'::jsonb or v_render->'packet'->'caveat' is distinct from 'null'::jsonb then
    raise exception 'packet-level caveat must stay an explicit null';
  end if;
  if not ops.tour_public_projection_client_safe('tour-client-share-proof','ac100000-0000-4000-8000-000000000001') then
    raise exception 'clean projection judged unsafe';
  end if;

  -- Legacy parity. A projection sealed before 0591 may hold a now-internal
  -- fact, internal text inside an allowed field, or sit on a free-text stop
  -- label. Plant each in the shared projection with triggers suspended (as a
  -- pre-0591 seal was), and prove the list, the PDF render read AND the map
  -- all refuse it -- none trims it -- then undo it and prove all three return.
  for v_legacy in select * from (values
    ('internal access fact',
     $s$insert into ops.tour_public_projection_fact(organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001','b9000000-0000-4000-8000-000000000009',1,'access')$s$,
     $s$delete from ops.tour_public_projection_fact where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='access'$s$),
    ('internal caveat fact',
     $s$insert into ops.tour_public_projection_fact(organization_tenant_id,projection_id,property_id,field_assertion_id,route_version,display_field_key) values ('tour-client-share-proof','ac100000-0000-4000-8000-000000000001','b1000000-0000-4000-8000-000000000001','b9000000-0000-4000-8000-000000000010',1,'caveat')$s$,
     $s$delete from ops.tour_public_projection_fact where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='caveat'$s$),
    ('owner phone inside parking',
     $s$update ops.tour_public_projection_fact set field_assertion_id='b9000000-0000-4000-8000-000000000021' where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='parking'$s$,
     $s$update ops.tour_public_projection_fact set field_assertion_id='b9000000-0000-4000-8000-000000000008' where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='parking'$s$),
    ('phone stored as a size number',
     $s$update ops.tour_public_projection_fact set field_assertion_id='b9000000-0000-4000-8000-000000000028' where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='size'$s$,
     $s$update ops.tour_public_projection_fact set field_assertion_id='b9000000-0000-4000-8000-000000000005' where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='size'$s$),
    -- a clean value, but the fact claims a different field than its assertion
    ('fact whose field is not its assertion''s field',
     $s$update ops.tour_public_projection_fact set field_assertion_id='b9000000-0000-4000-8000-000000000004' where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='suite'$s$,
     $s$update ops.tour_public_projection_fact set field_assertion_id='b9000000-0000-4000-8000-000000000003' where projection_id='ac100000-0000-4000-8000-000000000001' and display_field_key='suite'$s$),
    ('free-text stop label',
     $s$update ops.tour_property_membership set route_label='Stop 1' where id='bd000000-0000-4000-8000-000000000001'$s$,
     $s$update ops.tour_property_membership set route_label='A' where id='bd000000-0000-4000-8000-000000000001'$s$)) x(name,plant,undo)
  loop
    set local session_replication_role = replica;
    execute v_legacy.plant;
    set local session_replication_role = origin;
    if ops.tour_public_projection_client_safe('tour-client-share-proof','ac100000-0000-4000-8000-000000000001') is distinct from false then
      raise exception 'legacy % judged client-safe', v_legacy.name;
    end if;
    if ops.read_tour_share_packet('sha256:'||repeat('e',64)) is not null then raise exception 'share list showed a legacy %', v_legacy.name; end if;
    if ops.read_tour_packet_for_render('tour-client-share-proof','ac100000-0000-4000-8000-000000000001',v_actor_id) is not null then
      raise exception 'PDF render read showed a legacy %', v_legacy.name;
    end if;
    if ops.read_tour_share_map('sha256:'||repeat('e',64)) is not null then raise exception 'map showed a legacy %', v_legacy.name; end if;
    set local session_replication_role = replica;
    execute v_legacy.undo;
    set local session_replication_role = origin;
    v_packet:=ops.read_tour_share_packet('sha256:'||repeat('e',64));
    v_render:=ops.read_tour_packet_for_render('tour-client-share-proof','ac100000-0000-4000-8000-000000000001',v_actor_id);
    v_map:=ops.read_tour_share_map('sha256:'||repeat('e',64));
    if v_packet is null or v_render is null or v_map is null
       or jsonb_array_length(v_packet->'stops')<>1 or jsonb_array_length(v_render->'packet'->'properties')<>1 or jsonb_array_length(v_map->'points')<>1 then
      raise exception 'clean projection did not come back on every surface after undoing legacy %', v_legacy.name;
    end if;
  end loop;
end $client_allowlist$;

-- The code point sweep: every BMP code point (surrogates excluded) and a
-- sample of the astral planes (every 251st, plus the tags, the variation
-- selector supplement, the mathematical digits, the digit-comma forms, the
-- outlined digits and an emoji), each judged as 'a' || chr(cp) || 'a'. The
-- allowed set and every refusal reason must equal the JavaScript rule's, which
-- test/tour-client-share-allowlist.test.mjs computes and pins here:
--   68106 code points, 403 allowed in 21 ranges:
--   20-3f,41-7e,a0,a7,b0-b1,c0-f6,f8-13e,141-148,14a-17f,2000-200a,2010-2011,2013-2014,2018-2019,201c-201d,2022,2026,202f,205f,3000,ff01-ff1f,ff21-ff5e
do $client_text_sweep$
declare v_allow text; v_full text;
begin
  with cps as (
    select cp from generate_series(1, 65535) cp where cp not between 55296 and 57343
    union select generate_series(65536, 1114111, 251)
    union select generate_series(917504, 917631)
    union select generate_series(917760, 917999)
    union select generate_series(120782, 120831)
    union select generate_series(127232, 127244)
    union select generate_series(118000, 118009)
    union select 127973),
  judged as (select cp, ops.tour_client_text_violation('a' || chr(cp) || 'a') v from cps),
  islands as (select cp, cp - row_number() over (order by cp) grp from judged where v is null),
  ranges as (select min(cp) lo, max(cp) hi from islands group by grp)
  select (select string_agg(case when lo = hi then to_hex(lo) else to_hex(lo) || '-' || to_hex(hi) end, ',' order by lo) from ranges),
         (select string_agg(to_hex(cp) || '=' || coalesce(v, ''), ',' order by cp) from judged)
    into v_allow, v_full;
  if v_allow is distinct from '20-3f,41-7e,a0,a7,b0-b1,c0-f6,f8-13e,141-148,14a-17f,2000-200a,2010-2011,2013-2014,2018-2019,201c-201d,2022,2026,202f,205f,3000,ff01-ff1f,ff21-ff5e' then
    raise exception 'code point sweep: the database allows %', v_allow;
  end if;
  if md5(v_full) <> 'b570facfd96219f00028e196d7acfd8b' then
    raise exception 'code point sweep: a refusal reason differs from the JavaScript rule';
  end if;
end $client_text_sweep$;

rollback;

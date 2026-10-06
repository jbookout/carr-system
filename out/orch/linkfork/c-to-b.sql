CREATE SCHEMA mb;
CREATE TABLE mb.entity(id uuid PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('assignment','attachment','building','campaign','capture','client','commit','deal','decision','decision_event','defect','deployment','doctrine_document','doctrine_section','engagement','event','f01_corporate_artifact','f01_document','format','incident','job_receipt','lead','loop','next_action','party','pillar','platform','property_negotiation','record_flag','record_source','relationship','repo','rule','run','siep_package','vendor','work_request')), UNIQUE(id,kind));
CREATE TABLE mb.d_assignment(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'assignment' CHECK(kind='assignment'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_attachment(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'attachment' CHECK(kind='attachment'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_building(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'building' CHECK(kind='building'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_campaign(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'campaign' CHECK(kind='campaign'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_capture(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'capture' CHECK(kind='capture'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_client(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'client' CHECK(kind='client'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_commit(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'commit' CHECK(kind='commit'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_deal(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'deal' CHECK(kind='deal'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_decision(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'decision' CHECK(kind='decision'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_decision_event(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'decision_event' CHECK(kind='decision_event'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_defect(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'defect' CHECK(kind='defect'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_deployment(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'deployment' CHECK(kind='deployment'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_doctrine_document(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'doctrine_document' CHECK(kind='doctrine_document'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_doctrine_section(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'doctrine_section' CHECK(kind='doctrine_section'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_engagement(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'engagement' CHECK(kind='engagement'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_event(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'event' CHECK(kind='event'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_f01_corporate_artifact(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'f01_corporate_artifact' CHECK(kind='f01_corporate_artifact'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_f01_document(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'f01_document' CHECK(kind='f01_document'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_format(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'format' CHECK(kind='format'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_incident(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'incident' CHECK(kind='incident'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_job_receipt(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'job_receipt' CHECK(kind='job_receipt'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_lead(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'lead' CHECK(kind='lead'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_loop(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'loop' CHECK(kind='loop'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_next_action(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'next_action' CHECK(kind='next_action'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_party(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'party' CHECK(kind='party'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_pillar(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'pillar' CHECK(kind='pillar'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_platform(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'platform' CHECK(kind='platform'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_property_negotiation(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'property_negotiation' CHECK(kind='property_negotiation'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_record_flag(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'record_flag' CHECK(kind='record_flag'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_record_source(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'record_source' CHECK(kind='record_source'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_relationship(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'relationship' CHECK(kind='relationship'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_repo(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'repo' CHECK(kind='repo'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_rule(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'rule' CHECK(kind='rule'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_run(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'run' CHECK(kind='run'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_siep_package(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'siep_package' CHECK(kind='siep_package'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_vendor(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'vendor' CHECK(kind='vendor'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.d_work_request(id uuid PRIMARY KEY,payload text NOT NULL,kind text NOT NULL DEFAULT 'work_request' CHECK(kind='work_request'),FOREIGN KEY(id,kind) REFERENCES mb.entity(id,kind));
CREATE TABLE mb.edge(id uuid PRIMARY KEY,family text NOT NULL,src_kind text NOT NULL,src_id uuid NOT NULL,dst_kind text NOT NULL,dst_id uuid NOT NULL,relation text NOT NULL,CHECK((family='doctrine_link' AND src_kind='doctrine_section' AND dst_kind IN ('doctrine_document','doctrine_section','party','deal','decision','rule','loop','capture') AND relation IN ('citation','related','example','source')) OR (family='incident_link' AND src_kind='incident' AND dst_kind IN ('run','deployment','work_request','defect','decision') AND relation IN ('reference')) OR (family='siep_evidence_link' AND src_kind='siep_package' AND dst_kind IN ('job_receipt','decision_event') AND relation IN ('source','tests','migration','deploy','readback','live_readback','rollback','independent_review','joe_approval','joe_go_no_go','zero_unresolved_findings','zero_blockers','two_clean_audit_cycles','material_fix')) OR (family='f01_derivative_link' AND src_kind='f01_corporate_artifact' AND dst_kind IN ('party','lead','deal','rule','doctrine_section','loop','decision') AND relation IN ('reference')) OR (family='j102_document_link' AND src_kind='f01_document' AND dst_kind IN ('relationship','engagement','assignment','property_negotiation','deal') AND relation IN ('reference')) OR (family='j102_artifact_link' AND src_kind='f01_corporate_artifact' AND dst_kind IN ('relationship','engagement','assignment','property_negotiation','deal') AND relation IN ('reference')) OR (family='event' AND src_kind='event' AND dst_kind IN ('party','lead','deal','rule','doctrine_section','loop','decision') AND relation IN ('reference')) OR (family='record_source' AND src_kind='record_source' AND dst_kind IN ('party','lead','deal','rule','doctrine_section','loop','decision','client','building') AND relation IN ('reference')) OR (family='next_action' AND src_kind='next_action' AND dst_kind IN ('deal','client','lead','vendor') AND relation IN ('reference')) OR (family='record_flag' AND src_kind='record_flag' AND dst_kind IN ('lead','client','vendor','party','deal','campaign','platform','pillar','format','repo','commit') AND relation IN ('reference')) OR (family='attachment' AND src_kind='attachment' AND dst_kind IN ('party','lead','deal','rule','doctrine_section','loop','decision') AND relation IN ('reference'))),FOREIGN KEY(src_id,src_kind) REFERENCES mb.entity(id,kind),FOREIGN KEY(dst_id,dst_kind) REFERENCES mb.entity(id,kind),UNIQUE(family,src_kind,src_id,dst_kind,dst_id,relation));
CREATE INDEX ON mb.edge(src_kind,src_id);
CREATE INDEX ON mb.edge(dst_kind,dst_id);
CREATE VIEW mb.relationships AS SELECT * FROM mb.edge;
CREATE UNIQUE INDEX ON mb.edge(src_id,dst_kind,dst_id) WHERE family='siep_evidence_link';
CREATE UNIQUE INDEX ON mb.edge(dst_kind,dst_id) WHERE family='f01_derivative_link';
CREATE SCHEMA mb_cdc;
CREATE TABLE mb_cdc.changes(seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,table_name text NOT NULL,operation text NOT NULL,row_data jsonb NOT NULL,old_id uuid);
CREATE FUNCTION mb_cdc.capture() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO mb_cdc.changes(table_name,operation,row_data,old_id)
 VALUES (TG_TABLE_NAME,TG_OP,CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END,
         CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.id END);
 RETURN NULL;
END $$;
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_assignment FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_attachment FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_building FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_campaign FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_capture FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_client FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_commit FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_deal FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_decision FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_decision_event FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_defect FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_deployment FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_doctrine_document FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_doctrine_section FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_engagement FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_event FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_f01_corporate_artifact FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_f01_document FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_format FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_incident FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_job_receipt FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_lead FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_loop FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_next_action FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_party FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_pillar FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_platform FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_property_negotiation FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_record_flag FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_record_source FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_relationship FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_repo FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_rule FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_run FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_siep_package FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_vendor FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_work_request FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_doctrine_link FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_incident_link FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_siep_evidence_link FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_f01_derivative_link FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_j102_document_link FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_j102_artifact_link FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_event FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_record_source FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_next_action FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_record_flag FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE TRIGGER mb_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_attachment FOR EACH ROW EXECUTE FUNCTION mb_cdc.capture();
CREATE FUNCTION mb_cdc.replay() RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE r record; applied bigint := 0;
BEGIN
 LOCK TABLE c.d_assignment,c.d_attachment,c.d_building,c.d_campaign,c.d_capture,c.d_client,c.d_commit,c.d_deal,c.d_decision,c.d_decision_event,c.d_defect,c.d_deployment,c.d_doctrine_document,c.d_doctrine_section,c.d_engagement,c.d_event,c.d_f01_corporate_artifact,c.d_f01_document,c.d_format,c.d_incident,c.d_job_receipt,c.d_lead,c.d_loop,c.d_next_action,c.d_party,c.d_pillar,c.d_platform,c.d_property_negotiation,c.d_record_flag,c.d_record_source,c.d_relationship,c.d_repo,c.d_rule,c.d_run,c.d_siep_package,c.d_vendor,c.d_work_request,c.l_doctrine_link,c.l_incident_link,c.l_siep_evidence_link,c.l_f01_derivative_link,c.l_j102_document_link,c.l_j102_artifact_link,c.l_event,c.l_record_source,c.l_next_action,c.l_record_flag,c.l_attachment IN SHARE MODE;
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_doctrine_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_doctrine_link' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_incident_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_incident_link' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_siep_evidence_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_siep_evidence_link' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_f01_derivative_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_f01_derivative_link' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_j102_document_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_j102_document_link' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_j102_artifact_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_j102_artifact_link' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_event' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_record_source' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_record_source' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_next_action' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_next_action' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_record_flag' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_record_flag' AND old_id IS NOT NULL);
DELETE FROM mb.edge WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_attachment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_attachment' AND old_id IS NOT NULL);
DELETE FROM mb.d_assignment WHERE id IN (SELECT id FROM mb.d_assignment WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_assignment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_assignment' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_assignment source WHERE source.id=mb.d_assignment.id));
DELETE FROM mb.entity WHERE kind='assignment' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_assignment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_assignment' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_assignment source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_attachment WHERE id IN (SELECT id FROM mb.d_attachment WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_attachment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_attachment' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_attachment source WHERE source.id=mb.d_attachment.id));
DELETE FROM mb.entity WHERE kind='attachment' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_attachment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_attachment' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_attachment source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_building WHERE id IN (SELECT id FROM mb.d_building WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_building' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_building' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_building source WHERE source.id=mb.d_building.id));
DELETE FROM mb.entity WHERE kind='building' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_building' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_building' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_building source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_campaign WHERE id IN (SELECT id FROM mb.d_campaign WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_campaign' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_campaign' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_campaign source WHERE source.id=mb.d_campaign.id));
DELETE FROM mb.entity WHERE kind='campaign' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_campaign' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_campaign' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_campaign source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_capture WHERE id IN (SELECT id FROM mb.d_capture WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_capture' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_capture' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_capture source WHERE source.id=mb.d_capture.id));
DELETE FROM mb.entity WHERE kind='capture' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_capture' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_capture' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_capture source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_client WHERE id IN (SELECT id FROM mb.d_client WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_client' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_client' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_client source WHERE source.id=mb.d_client.id));
DELETE FROM mb.entity WHERE kind='client' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_client' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_client' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_client source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_commit WHERE id IN (SELECT id FROM mb.d_commit WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_commit' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_commit' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_commit source WHERE source.id=mb.d_commit.id));
DELETE FROM mb.entity WHERE kind='commit' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_commit' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_commit' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_commit source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_deal WHERE id IN (SELECT id FROM mb.d_deal WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_deal' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_deal' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_deal source WHERE source.id=mb.d_deal.id));
DELETE FROM mb.entity WHERE kind='deal' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_deal' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_deal' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_deal source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_decision WHERE id IN (SELECT id FROM mb.d_decision WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_decision' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_decision' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_decision source WHERE source.id=mb.d_decision.id));
DELETE FROM mb.entity WHERE kind='decision' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_decision' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_decision' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_decision source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_decision_event WHERE id IN (SELECT id FROM mb.d_decision_event WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_decision_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_decision_event' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_decision_event source WHERE source.id=mb.d_decision_event.id));
DELETE FROM mb.entity WHERE kind='decision_event' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_decision_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_decision_event' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_decision_event source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_defect WHERE id IN (SELECT id FROM mb.d_defect WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_defect' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_defect' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_defect source WHERE source.id=mb.d_defect.id));
DELETE FROM mb.entity WHERE kind='defect' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_defect' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_defect' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_defect source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_deployment WHERE id IN (SELECT id FROM mb.d_deployment WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_deployment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_deployment' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_deployment source WHERE source.id=mb.d_deployment.id));
DELETE FROM mb.entity WHERE kind='deployment' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_deployment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_deployment' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_deployment source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_doctrine_document WHERE id IN (SELECT id FROM mb.d_doctrine_document WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_doctrine_document' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_doctrine_document' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_doctrine_document source WHERE source.id=mb.d_doctrine_document.id));
DELETE FROM mb.entity WHERE kind='doctrine_document' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_doctrine_document' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_doctrine_document' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_doctrine_document source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_doctrine_section WHERE id IN (SELECT id FROM mb.d_doctrine_section WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_doctrine_section' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_doctrine_section' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_doctrine_section source WHERE source.id=mb.d_doctrine_section.id));
DELETE FROM mb.entity WHERE kind='doctrine_section' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_doctrine_section' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_doctrine_section' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_doctrine_section source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_engagement WHERE id IN (SELECT id FROM mb.d_engagement WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_engagement' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_engagement' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_engagement source WHERE source.id=mb.d_engagement.id));
DELETE FROM mb.entity WHERE kind='engagement' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_engagement' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_engagement' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_engagement source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_event WHERE id IN (SELECT id FROM mb.d_event WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_event' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_event source WHERE source.id=mb.d_event.id));
DELETE FROM mb.entity WHERE kind='event' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_event' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_event source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_f01_corporate_artifact WHERE id IN (SELECT id FROM mb.d_f01_corporate_artifact WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_f01_corporate_artifact' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_f01_corporate_artifact' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_f01_corporate_artifact source WHERE source.id=mb.d_f01_corporate_artifact.id));
DELETE FROM mb.entity WHERE kind='f01_corporate_artifact' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_f01_corporate_artifact' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_f01_corporate_artifact' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_f01_corporate_artifact source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_f01_document WHERE id IN (SELECT id FROM mb.d_f01_document WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_f01_document' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_f01_document' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_f01_document source WHERE source.id=mb.d_f01_document.id));
DELETE FROM mb.entity WHERE kind='f01_document' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_f01_document' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_f01_document' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_f01_document source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_format WHERE id IN (SELECT id FROM mb.d_format WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_format' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_format' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_format source WHERE source.id=mb.d_format.id));
DELETE FROM mb.entity WHERE kind='format' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_format' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_format' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_format source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_incident WHERE id IN (SELECT id FROM mb.d_incident WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_incident' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_incident' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_incident source WHERE source.id=mb.d_incident.id));
DELETE FROM mb.entity WHERE kind='incident' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_incident' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_incident' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_incident source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_job_receipt WHERE id IN (SELECT id FROM mb.d_job_receipt WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_job_receipt' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_job_receipt' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_job_receipt source WHERE source.id=mb.d_job_receipt.id));
DELETE FROM mb.entity WHERE kind='job_receipt' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_job_receipt' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_job_receipt' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_job_receipt source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_lead WHERE id IN (SELECT id FROM mb.d_lead WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_lead' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_lead' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_lead source WHERE source.id=mb.d_lead.id));
DELETE FROM mb.entity WHERE kind='lead' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_lead' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_lead' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_lead source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_loop WHERE id IN (SELECT id FROM mb.d_loop WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_loop' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_loop' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_loop source WHERE source.id=mb.d_loop.id));
DELETE FROM mb.entity WHERE kind='loop' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_loop' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_loop' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_loop source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_next_action WHERE id IN (SELECT id FROM mb.d_next_action WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_next_action' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_next_action' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_next_action source WHERE source.id=mb.d_next_action.id));
DELETE FROM mb.entity WHERE kind='next_action' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_next_action' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_next_action' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_next_action source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_party WHERE id IN (SELECT id FROM mb.d_party WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_party' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_party' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_party source WHERE source.id=mb.d_party.id));
DELETE FROM mb.entity WHERE kind='party' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_party' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_party' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_party source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_pillar WHERE id IN (SELECT id FROM mb.d_pillar WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_pillar' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_pillar' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_pillar source WHERE source.id=mb.d_pillar.id));
DELETE FROM mb.entity WHERE kind='pillar' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_pillar' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_pillar' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_pillar source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_platform WHERE id IN (SELECT id FROM mb.d_platform WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_platform' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_platform' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_platform source WHERE source.id=mb.d_platform.id));
DELETE FROM mb.entity WHERE kind='platform' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_platform' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_platform' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_platform source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_property_negotiation WHERE id IN (SELECT id FROM mb.d_property_negotiation WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_property_negotiation' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_property_negotiation' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_property_negotiation source WHERE source.id=mb.d_property_negotiation.id));
DELETE FROM mb.entity WHERE kind='property_negotiation' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_property_negotiation' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_property_negotiation' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_property_negotiation source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_record_flag WHERE id IN (SELECT id FROM mb.d_record_flag WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_record_flag' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_record_flag' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_record_flag source WHERE source.id=mb.d_record_flag.id));
DELETE FROM mb.entity WHERE kind='record_flag' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_record_flag' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_record_flag' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_record_flag source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_record_source WHERE id IN (SELECT id FROM mb.d_record_source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_record_source' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_record_source' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_record_source source WHERE source.id=mb.d_record_source.id));
DELETE FROM mb.entity WHERE kind='record_source' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_record_source' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_record_source' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_record_source source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_relationship WHERE id IN (SELECT id FROM mb.d_relationship WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_relationship' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_relationship' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_relationship source WHERE source.id=mb.d_relationship.id));
DELETE FROM mb.entity WHERE kind='relationship' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_relationship' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_relationship' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_relationship source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_repo WHERE id IN (SELECT id FROM mb.d_repo WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_repo' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_repo' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_repo source WHERE source.id=mb.d_repo.id));
DELETE FROM mb.entity WHERE kind='repo' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_repo' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_repo' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_repo source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_rule WHERE id IN (SELECT id FROM mb.d_rule WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_rule' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_rule' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_rule source WHERE source.id=mb.d_rule.id));
DELETE FROM mb.entity WHERE kind='rule' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_rule' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_rule' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_rule source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_run WHERE id IN (SELECT id FROM mb.d_run WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_run' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_run' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_run source WHERE source.id=mb.d_run.id));
DELETE FROM mb.entity WHERE kind='run' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_run' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_run' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_run source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_siep_package WHERE id IN (SELECT id FROM mb.d_siep_package WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_siep_package' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_siep_package' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_siep_package source WHERE source.id=mb.d_siep_package.id));
DELETE FROM mb.entity WHERE kind='siep_package' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_siep_package' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_siep_package' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_siep_package source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_vendor WHERE id IN (SELECT id FROM mb.d_vendor WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_vendor' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_vendor' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_vendor source WHERE source.id=mb.d_vendor.id));
DELETE FROM mb.entity WHERE kind='vendor' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_vendor' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_vendor' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_vendor source WHERE source.id=mb.entity.id);
DELETE FROM mb.d_work_request WHERE id IN (SELECT id FROM mb.d_work_request WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_work_request' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_work_request' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_work_request source WHERE source.id=mb.d_work_request.id));
DELETE FROM mb.entity WHERE kind='work_request' AND id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_work_request' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_work_request' AND old_id IS NOT NULL) AND NOT EXISTS (SELECT 1 FROM c.d_work_request source WHERE source.id=mb.entity.id);
FOR r IN SELECT 'd_assignment'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_assignment source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_assignment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_assignment' AND old_id IS NOT NULL) UNION ALL SELECT 'd_attachment'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_attachment source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_attachment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_attachment' AND old_id IS NOT NULL) UNION ALL SELECT 'd_building'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_building source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_building' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_building' AND old_id IS NOT NULL) UNION ALL SELECT 'd_campaign'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_campaign source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_campaign' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_campaign' AND old_id IS NOT NULL) UNION ALL SELECT 'd_capture'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_capture source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_capture' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_capture' AND old_id IS NOT NULL) UNION ALL SELECT 'd_client'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_client source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_client' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_client' AND old_id IS NOT NULL) UNION ALL SELECT 'd_commit'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_commit source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_commit' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_commit' AND old_id IS NOT NULL) UNION ALL SELECT 'd_deal'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_deal source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_deal' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_deal' AND old_id IS NOT NULL) UNION ALL SELECT 'd_decision'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_decision source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_decision' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_decision' AND old_id IS NOT NULL) UNION ALL SELECT 'd_decision_event'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_decision_event source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_decision_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_decision_event' AND old_id IS NOT NULL) UNION ALL SELECT 'd_defect'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_defect source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_defect' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_defect' AND old_id IS NOT NULL) UNION ALL SELECT 'd_deployment'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_deployment source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_deployment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_deployment' AND old_id IS NOT NULL) UNION ALL SELECT 'd_doctrine_document'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_doctrine_document source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_doctrine_document' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_doctrine_document' AND old_id IS NOT NULL) UNION ALL SELECT 'd_doctrine_section'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_doctrine_section source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_doctrine_section' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_doctrine_section' AND old_id IS NOT NULL) UNION ALL SELECT 'd_engagement'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_engagement source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_engagement' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_engagement' AND old_id IS NOT NULL) UNION ALL SELECT 'd_event'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_event source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_event' AND old_id IS NOT NULL) UNION ALL SELECT 'd_f01_corporate_artifact'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_f01_corporate_artifact source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_f01_corporate_artifact' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_f01_corporate_artifact' AND old_id IS NOT NULL) UNION ALL SELECT 'd_f01_document'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_f01_document source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_f01_document' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_f01_document' AND old_id IS NOT NULL) UNION ALL SELECT 'd_format'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_format source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_format' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_format' AND old_id IS NOT NULL) UNION ALL SELECT 'd_incident'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_incident source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_incident' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_incident' AND old_id IS NOT NULL) UNION ALL SELECT 'd_job_receipt'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_job_receipt source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_job_receipt' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_job_receipt' AND old_id IS NOT NULL) UNION ALL SELECT 'd_lead'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_lead source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_lead' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_lead' AND old_id IS NOT NULL) UNION ALL SELECT 'd_loop'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_loop source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_loop' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_loop' AND old_id IS NOT NULL) UNION ALL SELECT 'd_next_action'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_next_action source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_next_action' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_next_action' AND old_id IS NOT NULL) UNION ALL SELECT 'd_party'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_party source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_party' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_party' AND old_id IS NOT NULL) UNION ALL SELECT 'd_pillar'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_pillar source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_pillar' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_pillar' AND old_id IS NOT NULL) UNION ALL SELECT 'd_platform'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_platform source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_platform' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_platform' AND old_id IS NOT NULL) UNION ALL SELECT 'd_property_negotiation'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_property_negotiation source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_property_negotiation' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_property_negotiation' AND old_id IS NOT NULL) UNION ALL SELECT 'd_record_flag'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_record_flag source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_record_flag' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_record_flag' AND old_id IS NOT NULL) UNION ALL SELECT 'd_record_source'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_record_source source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_record_source' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_record_source' AND old_id IS NOT NULL) UNION ALL SELECT 'd_relationship'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_relationship source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_relationship' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_relationship' AND old_id IS NOT NULL) UNION ALL SELECT 'd_repo'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_repo source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_repo' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_repo' AND old_id IS NOT NULL) UNION ALL SELECT 'd_rule'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_rule source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_rule' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_rule' AND old_id IS NOT NULL) UNION ALL SELECT 'd_run'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_run source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_run' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_run' AND old_id IS NOT NULL) UNION ALL SELECT 'd_siep_package'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_siep_package source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_siep_package' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_siep_package' AND old_id IS NOT NULL) UNION ALL SELECT 'd_vendor'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_vendor source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_vendor' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_vendor' AND old_id IS NOT NULL) UNION ALL SELECT 'd_work_request'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,0 stage FROM c.d_work_request source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='d_work_request' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='d_work_request' AND old_id IS NOT NULL) UNION ALL SELECT 'l_doctrine_link'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_doctrine_link source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_doctrine_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_doctrine_link' AND old_id IS NOT NULL) UNION ALL SELECT 'l_incident_link'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_incident_link source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_incident_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_incident_link' AND old_id IS NOT NULL) UNION ALL SELECT 'l_siep_evidence_link'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_siep_evidence_link source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_siep_evidence_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_siep_evidence_link' AND old_id IS NOT NULL) UNION ALL SELECT 'l_f01_derivative_link'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_f01_derivative_link source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_f01_derivative_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_f01_derivative_link' AND old_id IS NOT NULL) UNION ALL SELECT 'l_j102_document_link'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_j102_document_link source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_j102_document_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_j102_document_link' AND old_id IS NOT NULL) UNION ALL SELECT 'l_j102_artifact_link'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_j102_artifact_link source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_j102_artifact_link' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_j102_artifact_link' AND old_id IS NOT NULL) UNION ALL SELECT 'l_event'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_event source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_event' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_event' AND old_id IS NOT NULL) UNION ALL SELECT 'l_record_source'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_record_source source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_record_source' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_record_source' AND old_id IS NOT NULL) UNION ALL SELECT 'l_next_action'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_next_action source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_next_action' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_next_action' AND old_id IS NOT NULL) UNION ALL SELECT 'l_record_flag'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_record_flag source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_record_flag' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_record_flag' AND old_id IS NOT NULL) UNION ALL SELECT 'l_attachment'::text table_name,'INSERT'::text operation,to_jsonb(source) row_data,NULL::uuid old_id,1 stage FROM c.l_attachment source WHERE id IN (SELECT (row_data->>'id')::uuid FROM mb_cdc.changes WHERE table_name='l_attachment' UNION SELECT old_id FROM mb_cdc.changes WHERE table_name='l_attachment' AND old_id IS NOT NULL) ORDER BY stage LOOP
 CASE r.table_name WHEN 'd_assignment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_assignment WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'assignment') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_assignment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_attachment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_attachment WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'attachment') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_attachment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_building' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_building WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'building') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_building(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_campaign' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_campaign WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'campaign') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_campaign(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_capture' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_capture WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'capture') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_capture(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_client' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_client WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'client') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_client(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_commit' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_commit WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'commit') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_commit(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deal' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_deal WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'deal') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_deal(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_decision WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'decision') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_decision(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision_event' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_decision_event WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'decision_event') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_decision_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_defect' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_defect WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'defect') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_defect(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deployment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_deployment WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'deployment') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_deployment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_document' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_doctrine_document WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'doctrine_document') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_doctrine_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_section' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_doctrine_section WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'doctrine_section') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_doctrine_section(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_engagement' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_engagement WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'engagement') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_engagement(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_event' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_event WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'event') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_corporate_artifact' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_f01_corporate_artifact WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'f01_corporate_artifact') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_f01_corporate_artifact(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_document' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_f01_document WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'f01_document') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_f01_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_format' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_format WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'format') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_format(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_incident' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_incident WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'incident') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_incident(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_job_receipt' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_job_receipt WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'job_receipt') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_job_receipt(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_lead' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_lead WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'lead') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_lead(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_loop' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_loop WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'loop') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_loop(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_next_action' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_next_action WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'next_action') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_next_action(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_party' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_party WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'party') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_party(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_pillar' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_pillar WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'pillar') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_pillar(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_platform' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_platform WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'platform') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_platform(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_property_negotiation' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_property_negotiation WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'property_negotiation') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_property_negotiation(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_flag' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_record_flag WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'record_flag') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_record_flag(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_source' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_record_source WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'record_source') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_record_source(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_relationship' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_relationship WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'relationship') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_relationship(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_repo' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_repo WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'repo') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_repo(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_rule' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_rule WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'rule') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_rule(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_run' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_run WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'run') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_run(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_siep_package' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_siep_package WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'siep_package') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_siep_package(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_vendor' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_vendor WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'vendor') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_vendor(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_work_request' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_work_request WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'work_request') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_work_request(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'l_doctrine_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'doctrine_link','doctrine_section',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_incident_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'incident_link','incident',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_siep_evidence_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'siep_evidence_link','siep_package',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_f01_derivative_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'f01_derivative_link','f01_corporate_artifact',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_document_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'j102_document_link','f01_document',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_artifact_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'j102_artifact_link','f01_corporate_artifact',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_event' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'event','event',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_source' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'record_source','record_source',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_next_action' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'next_action','next_action',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_flag' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'record_flag','record_flag',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_attachment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'attachment','attachment',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; ELSE RAISE EXCEPTION 'unknown CDC table'; END CASE;
 END LOOP;
 SELECT count(*) INTO applied FROM mb_cdc.changes;
 DELETE FROM mb_cdc.changes;
 RETURN applied;
END $$;
BEGIN ISOLATION LEVEL REPEATABLE READ;
INSERT INTO mb.entity(id,kind) SELECT id,'assignment' FROM c.d_assignment UNION ALL SELECT id,'attachment' FROM c.d_attachment UNION ALL SELECT id,'building' FROM c.d_building UNION ALL SELECT id,'campaign' FROM c.d_campaign UNION ALL SELECT id,'capture' FROM c.d_capture UNION ALL SELECT id,'client' FROM c.d_client UNION ALL SELECT id,'commit' FROM c.d_commit UNION ALL SELECT id,'deal' FROM c.d_deal UNION ALL SELECT id,'decision' FROM c.d_decision UNION ALL SELECT id,'decision_event' FROM c.d_decision_event UNION ALL SELECT id,'defect' FROM c.d_defect UNION ALL SELECT id,'deployment' FROM c.d_deployment UNION ALL SELECT id,'doctrine_document' FROM c.d_doctrine_document UNION ALL SELECT id,'doctrine_section' FROM c.d_doctrine_section UNION ALL SELECT id,'engagement' FROM c.d_engagement UNION ALL SELECT id,'event' FROM c.d_event UNION ALL SELECT id,'f01_corporate_artifact' FROM c.d_f01_corporate_artifact UNION ALL SELECT id,'f01_document' FROM c.d_f01_document UNION ALL SELECT id,'format' FROM c.d_format UNION ALL SELECT id,'incident' FROM c.d_incident UNION ALL SELECT id,'job_receipt' FROM c.d_job_receipt UNION ALL SELECT id,'lead' FROM c.d_lead UNION ALL SELECT id,'loop' FROM c.d_loop UNION ALL SELECT id,'next_action' FROM c.d_next_action UNION ALL SELECT id,'party' FROM c.d_party UNION ALL SELECT id,'pillar' FROM c.d_pillar UNION ALL SELECT id,'platform' FROM c.d_platform UNION ALL SELECT id,'property_negotiation' FROM c.d_property_negotiation UNION ALL SELECT id,'record_flag' FROM c.d_record_flag UNION ALL SELECT id,'record_source' FROM c.d_record_source UNION ALL SELECT id,'relationship' FROM c.d_relationship UNION ALL SELECT id,'repo' FROM c.d_repo UNION ALL SELECT id,'rule' FROM c.d_rule UNION ALL SELECT id,'run' FROM c.d_run UNION ALL SELECT id,'siep_package' FROM c.d_siep_package UNION ALL SELECT id,'vendor' FROM c.d_vendor UNION ALL SELECT id,'work_request' FROM c.d_work_request;
INSERT INTO mb.d_assignment(id,payload) SELECT id,payload FROM c.d_assignment;
INSERT INTO mb.d_attachment(id,payload) SELECT id,payload FROM c.d_attachment;
INSERT INTO mb.d_building(id,payload) SELECT id,payload FROM c.d_building;
INSERT INTO mb.d_campaign(id,payload) SELECT id,payload FROM c.d_campaign;
INSERT INTO mb.d_capture(id,payload) SELECT id,payload FROM c.d_capture;
INSERT INTO mb.d_client(id,payload) SELECT id,payload FROM c.d_client;
INSERT INTO mb.d_commit(id,payload) SELECT id,payload FROM c.d_commit;
INSERT INTO mb.d_deal(id,payload) SELECT id,payload FROM c.d_deal;
INSERT INTO mb.d_decision(id,payload) SELECT id,payload FROM c.d_decision;
INSERT INTO mb.d_decision_event(id,payload) SELECT id,payload FROM c.d_decision_event;
INSERT INTO mb.d_defect(id,payload) SELECT id,payload FROM c.d_defect;
INSERT INTO mb.d_deployment(id,payload) SELECT id,payload FROM c.d_deployment;
INSERT INTO mb.d_doctrine_document(id,payload) SELECT id,payload FROM c.d_doctrine_document;
INSERT INTO mb.d_doctrine_section(id,payload) SELECT id,payload FROM c.d_doctrine_section;
INSERT INTO mb.d_engagement(id,payload) SELECT id,payload FROM c.d_engagement;
INSERT INTO mb.d_event(id,payload) SELECT id,payload FROM c.d_event;
INSERT INTO mb.d_f01_corporate_artifact(id,payload) SELECT id,payload FROM c.d_f01_corporate_artifact;
INSERT INTO mb.d_f01_document(id,payload) SELECT id,payload FROM c.d_f01_document;
INSERT INTO mb.d_format(id,payload) SELECT id,payload FROM c.d_format;
INSERT INTO mb.d_incident(id,payload) SELECT id,payload FROM c.d_incident;
INSERT INTO mb.d_job_receipt(id,payload) SELECT id,payload FROM c.d_job_receipt;
INSERT INTO mb.d_lead(id,payload) SELECT id,payload FROM c.d_lead;
INSERT INTO mb.d_loop(id,payload) SELECT id,payload FROM c.d_loop;
INSERT INTO mb.d_next_action(id,payload) SELECT id,payload FROM c.d_next_action;
INSERT INTO mb.d_party(id,payload) SELECT id,payload FROM c.d_party;
INSERT INTO mb.d_pillar(id,payload) SELECT id,payload FROM c.d_pillar;
INSERT INTO mb.d_platform(id,payload) SELECT id,payload FROM c.d_platform;
INSERT INTO mb.d_property_negotiation(id,payload) SELECT id,payload FROM c.d_property_negotiation;
INSERT INTO mb.d_record_flag(id,payload) SELECT id,payload FROM c.d_record_flag;
INSERT INTO mb.d_record_source(id,payload) SELECT id,payload FROM c.d_record_source;
INSERT INTO mb.d_relationship(id,payload) SELECT id,payload FROM c.d_relationship;
INSERT INTO mb.d_repo(id,payload) SELECT id,payload FROM c.d_repo;
INSERT INTO mb.d_rule(id,payload) SELECT id,payload FROM c.d_rule;
INSERT INTO mb.d_run(id,payload) SELECT id,payload FROM c.d_run;
INSERT INTO mb.d_siep_package(id,payload) SELECT id,payload FROM c.d_siep_package;
INSERT INTO mb.d_vendor(id,payload) SELECT id,payload FROM c.d_vendor;
INSERT INTO mb.d_work_request(id,payload) SELECT id,payload FROM c.d_work_request;
INSERT INTO mb.edge SELECT * FROM c.relationships;
COMMIT;
BEGIN;
SET LOCAL lock_timeout='1s';
DO $$
DECLARE acquired boolean := false;
BEGIN
 FOR attempt IN 1..1000 LOOP
  BEGIN
   LOCK TABLE c.d_assignment,c.d_attachment,c.d_building,c.d_campaign,c.d_capture,c.d_client,c.d_commit,c.d_deal,c.d_decision,c.d_decision_event,c.d_defect,c.d_deployment,c.d_doctrine_document,c.d_doctrine_section,c.d_engagement,c.d_event,c.d_f01_corporate_artifact,c.d_f01_document,c.d_format,c.d_incident,c.d_job_receipt,c.d_lead,c.d_loop,c.d_next_action,c.d_party,c.d_pillar,c.d_platform,c.d_property_negotiation,c.d_record_flag,c.d_record_source,c.d_relationship,c.d_repo,c.d_rule,c.d_run,c.d_siep_package,c.d_vendor,c.d_work_request,c.l_doctrine_link,c.l_incident_link,c.l_siep_evidence_link,c.l_f01_derivative_link,c.l_j102_document_link,c.l_j102_artifact_link,c.l_event,c.l_record_source,c.l_next_action,c.l_record_flag,c.l_attachment IN SHARE MODE NOWAIT;
   acquired := true;
   EXIT;
  EXCEPTION WHEN lock_not_available THEN
   PERFORM pg_sleep(0.005);
  END;
 END LOOP;
 IF NOT acquired THEN RAISE EXCEPTION 'cutover lock budget exhausted'; END IF;
END $$;
SELECT mb_cdc.replay();
CREATE OR REPLACE FUNCTION mb_cdc.capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
 SELECT TG_TABLE_NAME table_name,TG_OP operation,
  CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END row_data,
  CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.id END old_id INTO r;
 CASE r.table_name WHEN 'd_assignment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_assignment WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'assignment') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_assignment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_attachment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_attachment WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'attachment') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_attachment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_building' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_building WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'building') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_building(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_campaign' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_campaign WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'campaign') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_campaign(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_capture' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_capture WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'capture') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_capture(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_client' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_client WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'client') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_client(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_commit' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_commit WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'commit') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_commit(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deal' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_deal WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'deal') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_deal(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_decision WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'decision') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_decision(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision_event' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_decision_event WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'decision_event') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_decision_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_defect' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_defect WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'defect') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_defect(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deployment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_deployment WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'deployment') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_deployment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_document' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_doctrine_document WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'doctrine_document') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_doctrine_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_section' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_doctrine_section WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'doctrine_section') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_doctrine_section(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_engagement' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_engagement WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'engagement') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_engagement(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_event' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_event WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'event') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_corporate_artifact' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_f01_corporate_artifact WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'f01_corporate_artifact') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_f01_corporate_artifact(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_document' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_f01_document WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'f01_document') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_f01_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_format' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_format WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'format') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_format(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_incident' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_incident WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'incident') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_incident(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_job_receipt' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_job_receipt WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'job_receipt') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_job_receipt(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_lead' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_lead WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'lead') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_lead(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_loop' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_loop WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'loop') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_loop(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_next_action' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_next_action WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'next_action') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_next_action(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_party' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_party WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'party') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_party(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_pillar' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_pillar WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'pillar') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_pillar(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_platform' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_platform WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'platform') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_platform(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_property_negotiation' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_property_negotiation WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'property_negotiation') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_property_negotiation(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_flag' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_record_flag WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'record_flag') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_record_flag(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_source' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_record_source WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'record_source') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_record_source(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_relationship' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_relationship WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'relationship') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_relationship(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_repo' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_repo WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'repo') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_repo(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_rule' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_rule WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'rule') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_rule(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_run' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_run WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'run') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_run(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_siep_package' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_siep_package WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'siep_package') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_siep_package(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_vendor' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_vendor WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'vendor') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_vendor(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_work_request' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.d_work_request WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
  DELETE FROM mb.entity WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.entity(id,kind) VALUES ((r.row_data->>'id')::uuid,'work_request') ON CONFLICT DO NOTHING;
  INSERT INTO mb.d_work_request(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'l_doctrine_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'doctrine_link','doctrine_section',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_incident_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'incident_link','incident',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_siep_evidence_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'siep_evidence_link','siep_package',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_f01_derivative_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'f01_derivative_link','f01_corporate_artifact',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_document_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'j102_document_link','f01_document',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_artifact_link' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'j102_artifact_link','f01_corporate_artifact',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_event' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'event','event',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_source' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'record_source','record_source',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_next_action' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'next_action','next_action',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_flag' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'record_flag','record_flag',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; WHEN 'l_attachment' THEN
 IF r.operation='DELETE' OR r.old_id IS DISTINCT FROM (r.row_data->>'id')::uuid AND r.old_id IS NOT NULL THEN
  DELETE FROM mb.edge WHERE id=coalesce(r.old_id,(r.row_data->>'id')::uuid);
 END IF;
 IF r.operation<>'DELETE' THEN
  INSERT INTO mb.edge(id,family,src_kind,src_id,dst_kind,dst_id,relation) VALUES ((r.row_data->>'id')::uuid,'attachment','attachment',(r.row_data->>'src_id')::uuid,r.row_data->>'dst_kind',(r.row_data->>'dst_id')::uuid,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET family=EXCLUDED.family,src_kind=EXCLUDED.src_kind,src_id=EXCLUDED.src_id,dst_kind=EXCLUDED.dst_kind,dst_id=EXCLUDED.dst_id,relation=EXCLUDED.relation;
 END IF; ELSE RAISE EXCEPTION 'unknown CDC table'; END CASE;
 RETURN NULL;
END $$;
CREATE OR REPLACE VIEW mb_cdc.current_relationships AS SELECT * FROM mb.relationships;
COMMIT;

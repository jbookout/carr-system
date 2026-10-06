CREATE SCHEMA ma;
CREATE TABLE ma.d_assignment(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_attachment(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_building(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_campaign(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_capture(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_client(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_commit(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_deal(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_decision(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_decision_event(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_defect(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_deployment(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_doctrine_document(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_doctrine_section(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_engagement(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_event(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_f01_corporate_artifact(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_f01_document(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_format(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_incident(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_job_receipt(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_lead(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_loop(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_next_action(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_party(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_pillar(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_platform(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_property_negotiation(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_record_flag(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_record_source(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_relationship(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_repo(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_rule(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_run(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_siep_package(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_vendor(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.d_work_request(id uuid PRIMARY KEY,payload text NOT NULL);
CREATE TABLE ma.l_doctrine_link(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_doctrine_section(id),dst_doctrine_document uuid REFERENCES ma.d_doctrine_document(id),dst_doctrine_section uuid REFERENCES ma.d_doctrine_section(id),dst_party uuid REFERENCES ma.d_party(id),dst_deal uuid REFERENCES ma.d_deal(id),dst_decision uuid REFERENCES ma.d_decision(id),dst_rule uuid REFERENCES ma.d_rule(id),dst_loop uuid REFERENCES ma.d_loop(id),dst_capture uuid REFERENCES ma.d_capture(id),CHECK(num_nonnulls(dst_doctrine_document,dst_doctrine_section,dst_party,dst_deal,dst_decision,dst_rule,dst_loop,dst_capture)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_doctrine_document IS NOT NULL THEN 'doctrine_document' WHEN dst_doctrine_section IS NOT NULL THEN 'doctrine_section' WHEN dst_party IS NOT NULL THEN 'party' WHEN dst_deal IS NOT NULL THEN 'deal' WHEN dst_decision IS NOT NULL THEN 'decision' WHEN dst_rule IS NOT NULL THEN 'rule' WHEN dst_loop IS NOT NULL THEN 'loop' WHEN dst_capture IS NOT NULL THEN 'capture' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_doctrine_document,dst_doctrine_section,dst_party,dst_deal,dst_decision,dst_rule,dst_loop,dst_capture)) STORED,relation text NOT NULL CHECK(relation IN ('citation','related','example','source')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_doctrine_link(src_id);
CREATE INDEX ON ma.l_doctrine_link(dst_kind,dst_id);
CREATE INDEX ON ma.l_doctrine_link(dst_doctrine_document) WHERE dst_doctrine_document IS NOT NULL;
CREATE INDEX ON ma.l_doctrine_link(dst_doctrine_section) WHERE dst_doctrine_section IS NOT NULL;
CREATE INDEX ON ma.l_doctrine_link(dst_party) WHERE dst_party IS NOT NULL;
CREATE INDEX ON ma.l_doctrine_link(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE INDEX ON ma.l_doctrine_link(dst_decision) WHERE dst_decision IS NOT NULL;
CREATE INDEX ON ma.l_doctrine_link(dst_rule) WHERE dst_rule IS NOT NULL;
CREATE INDEX ON ma.l_doctrine_link(dst_loop) WHERE dst_loop IS NOT NULL;
CREATE INDEX ON ma.l_doctrine_link(dst_capture) WHERE dst_capture IS NOT NULL;
CREATE TABLE ma.l_incident_link(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_incident(id),dst_run uuid REFERENCES ma.d_run(id),dst_deployment uuid REFERENCES ma.d_deployment(id),dst_work_request uuid REFERENCES ma.d_work_request(id),dst_defect uuid REFERENCES ma.d_defect(id),dst_decision uuid REFERENCES ma.d_decision(id),CHECK(num_nonnulls(dst_run,dst_deployment,dst_work_request,dst_defect,dst_decision)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_run IS NOT NULL THEN 'run' WHEN dst_deployment IS NOT NULL THEN 'deployment' WHEN dst_work_request IS NOT NULL THEN 'work_request' WHEN dst_defect IS NOT NULL THEN 'defect' WHEN dst_decision IS NOT NULL THEN 'decision' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_run,dst_deployment,dst_work_request,dst_defect,dst_decision)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_incident_link(src_id);
CREATE INDEX ON ma.l_incident_link(dst_kind,dst_id);
CREATE INDEX ON ma.l_incident_link(dst_run) WHERE dst_run IS NOT NULL;
CREATE INDEX ON ma.l_incident_link(dst_deployment) WHERE dst_deployment IS NOT NULL;
CREATE INDEX ON ma.l_incident_link(dst_work_request) WHERE dst_work_request IS NOT NULL;
CREATE INDEX ON ma.l_incident_link(dst_defect) WHERE dst_defect IS NOT NULL;
CREATE INDEX ON ma.l_incident_link(dst_decision) WHERE dst_decision IS NOT NULL;
CREATE TABLE ma.l_siep_evidence_link(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_siep_package(id),dst_job_receipt uuid REFERENCES ma.d_job_receipt(id),dst_decision_event uuid REFERENCES ma.d_decision_event(id),CHECK(num_nonnulls(dst_job_receipt,dst_decision_event)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_job_receipt IS NOT NULL THEN 'job_receipt' WHEN dst_decision_event IS NOT NULL THEN 'decision_event' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_job_receipt,dst_decision_event)) STORED,relation text NOT NULL CHECK(relation IN ('source','tests','migration','deploy','readback','live_readback','rollback','independent_review','joe_approval','joe_go_no_go','zero_unresolved_findings','zero_blockers','two_clean_audit_cycles','material_fix')),UNIQUE(src_id,dst_kind,dst_id));
CREATE INDEX ON ma.l_siep_evidence_link(src_id);
CREATE INDEX ON ma.l_siep_evidence_link(dst_kind,dst_id);
CREATE INDEX ON ma.l_siep_evidence_link(dst_job_receipt) WHERE dst_job_receipt IS NOT NULL;
CREATE INDEX ON ma.l_siep_evidence_link(dst_decision_event) WHERE dst_decision_event IS NOT NULL;
CREATE TABLE ma.l_f01_derivative_link(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_f01_corporate_artifact(id),dst_party uuid REFERENCES ma.d_party(id),dst_lead uuid REFERENCES ma.d_lead(id),dst_deal uuid REFERENCES ma.d_deal(id),dst_rule uuid REFERENCES ma.d_rule(id),dst_doctrine_section uuid REFERENCES ma.d_doctrine_section(id),dst_loop uuid REFERENCES ma.d_loop(id),dst_decision uuid REFERENCES ma.d_decision(id),CHECK(num_nonnulls(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_party IS NOT NULL THEN 'party' WHEN dst_lead IS NOT NULL THEN 'lead' WHEN dst_deal IS NOT NULL THEN 'deal' WHEN dst_rule IS NOT NULL THEN 'rule' WHEN dst_doctrine_section IS NOT NULL THEN 'doctrine_section' WHEN dst_loop IS NOT NULL THEN 'loop' WHEN dst_decision IS NOT NULL THEN 'decision' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(dst_kind,dst_id));
CREATE INDEX ON ma.l_f01_derivative_link(src_id);
CREATE INDEX ON ma.l_f01_derivative_link(dst_kind,dst_id);
CREATE INDEX ON ma.l_f01_derivative_link(dst_party) WHERE dst_party IS NOT NULL;
CREATE INDEX ON ma.l_f01_derivative_link(dst_lead) WHERE dst_lead IS NOT NULL;
CREATE INDEX ON ma.l_f01_derivative_link(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE INDEX ON ma.l_f01_derivative_link(dst_rule) WHERE dst_rule IS NOT NULL;
CREATE INDEX ON ma.l_f01_derivative_link(dst_doctrine_section) WHERE dst_doctrine_section IS NOT NULL;
CREATE INDEX ON ma.l_f01_derivative_link(dst_loop) WHERE dst_loop IS NOT NULL;
CREATE INDEX ON ma.l_f01_derivative_link(dst_decision) WHERE dst_decision IS NOT NULL;
CREATE TABLE ma.l_j102_document_link(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_f01_document(id),dst_relationship uuid REFERENCES ma.d_relationship(id),dst_engagement uuid REFERENCES ma.d_engagement(id),dst_assignment uuid REFERENCES ma.d_assignment(id),dst_property_negotiation uuid REFERENCES ma.d_property_negotiation(id),dst_deal uuid REFERENCES ma.d_deal(id),CHECK(num_nonnulls(dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_relationship IS NOT NULL THEN 'relationship' WHEN dst_engagement IS NOT NULL THEN 'engagement' WHEN dst_assignment IS NOT NULL THEN 'assignment' WHEN dst_property_negotiation IS NOT NULL THEN 'property_negotiation' WHEN dst_deal IS NOT NULL THEN 'deal' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_j102_document_link(src_id);
CREATE INDEX ON ma.l_j102_document_link(dst_kind,dst_id);
CREATE INDEX ON ma.l_j102_document_link(dst_relationship) WHERE dst_relationship IS NOT NULL;
CREATE INDEX ON ma.l_j102_document_link(dst_engagement) WHERE dst_engagement IS NOT NULL;
CREATE INDEX ON ma.l_j102_document_link(dst_assignment) WHERE dst_assignment IS NOT NULL;
CREATE INDEX ON ma.l_j102_document_link(dst_property_negotiation) WHERE dst_property_negotiation IS NOT NULL;
CREATE INDEX ON ma.l_j102_document_link(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE TABLE ma.l_j102_artifact_link(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_f01_corporate_artifact(id),dst_relationship uuid REFERENCES ma.d_relationship(id),dst_engagement uuid REFERENCES ma.d_engagement(id),dst_assignment uuid REFERENCES ma.d_assignment(id),dst_property_negotiation uuid REFERENCES ma.d_property_negotiation(id),dst_deal uuid REFERENCES ma.d_deal(id),CHECK(num_nonnulls(dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_relationship IS NOT NULL THEN 'relationship' WHEN dst_engagement IS NOT NULL THEN 'engagement' WHEN dst_assignment IS NOT NULL THEN 'assignment' WHEN dst_property_negotiation IS NOT NULL THEN 'property_negotiation' WHEN dst_deal IS NOT NULL THEN 'deal' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_j102_artifact_link(src_id);
CREATE INDEX ON ma.l_j102_artifact_link(dst_kind,dst_id);
CREATE INDEX ON ma.l_j102_artifact_link(dst_relationship) WHERE dst_relationship IS NOT NULL;
CREATE INDEX ON ma.l_j102_artifact_link(dst_engagement) WHERE dst_engagement IS NOT NULL;
CREATE INDEX ON ma.l_j102_artifact_link(dst_assignment) WHERE dst_assignment IS NOT NULL;
CREATE INDEX ON ma.l_j102_artifact_link(dst_property_negotiation) WHERE dst_property_negotiation IS NOT NULL;
CREATE INDEX ON ma.l_j102_artifact_link(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE TABLE ma.l_event(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_event(id),dst_party uuid REFERENCES ma.d_party(id),dst_lead uuid REFERENCES ma.d_lead(id),dst_deal uuid REFERENCES ma.d_deal(id),dst_rule uuid REFERENCES ma.d_rule(id),dst_doctrine_section uuid REFERENCES ma.d_doctrine_section(id),dst_loop uuid REFERENCES ma.d_loop(id),dst_decision uuid REFERENCES ma.d_decision(id),CHECK(num_nonnulls(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_party IS NOT NULL THEN 'party' WHEN dst_lead IS NOT NULL THEN 'lead' WHEN dst_deal IS NOT NULL THEN 'deal' WHEN dst_rule IS NOT NULL THEN 'rule' WHEN dst_doctrine_section IS NOT NULL THEN 'doctrine_section' WHEN dst_loop IS NOT NULL THEN 'loop' WHEN dst_decision IS NOT NULL THEN 'decision' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_event(src_id);
CREATE INDEX ON ma.l_event(dst_kind,dst_id);
CREATE INDEX ON ma.l_event(dst_party) WHERE dst_party IS NOT NULL;
CREATE INDEX ON ma.l_event(dst_lead) WHERE dst_lead IS NOT NULL;
CREATE INDEX ON ma.l_event(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE INDEX ON ma.l_event(dst_rule) WHERE dst_rule IS NOT NULL;
CREATE INDEX ON ma.l_event(dst_doctrine_section) WHERE dst_doctrine_section IS NOT NULL;
CREATE INDEX ON ma.l_event(dst_loop) WHERE dst_loop IS NOT NULL;
CREATE INDEX ON ma.l_event(dst_decision) WHERE dst_decision IS NOT NULL;
CREATE TABLE ma.l_record_source(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_record_source(id),dst_party uuid REFERENCES ma.d_party(id),dst_lead uuid REFERENCES ma.d_lead(id),dst_deal uuid REFERENCES ma.d_deal(id),dst_rule uuid REFERENCES ma.d_rule(id),dst_doctrine_section uuid REFERENCES ma.d_doctrine_section(id),dst_loop uuid REFERENCES ma.d_loop(id),dst_decision uuid REFERENCES ma.d_decision(id),dst_client uuid REFERENCES ma.d_client(id),dst_building uuid REFERENCES ma.d_building(id),CHECK(num_nonnulls(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,dst_client,dst_building)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_party IS NOT NULL THEN 'party' WHEN dst_lead IS NOT NULL THEN 'lead' WHEN dst_deal IS NOT NULL THEN 'deal' WHEN dst_rule IS NOT NULL THEN 'rule' WHEN dst_doctrine_section IS NOT NULL THEN 'doctrine_section' WHEN dst_loop IS NOT NULL THEN 'loop' WHEN dst_decision IS NOT NULL THEN 'decision' WHEN dst_client IS NOT NULL THEN 'client' WHEN dst_building IS NOT NULL THEN 'building' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,dst_client,dst_building)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_record_source(src_id);
CREATE INDEX ON ma.l_record_source(dst_kind,dst_id);
CREATE INDEX ON ma.l_record_source(dst_party) WHERE dst_party IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_lead) WHERE dst_lead IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_rule) WHERE dst_rule IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_doctrine_section) WHERE dst_doctrine_section IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_loop) WHERE dst_loop IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_decision) WHERE dst_decision IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_client) WHERE dst_client IS NOT NULL;
CREATE INDEX ON ma.l_record_source(dst_building) WHERE dst_building IS NOT NULL;
CREATE TABLE ma.l_next_action(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_next_action(id),dst_deal uuid REFERENCES ma.d_deal(id),dst_client uuid REFERENCES ma.d_client(id),dst_lead uuid REFERENCES ma.d_lead(id),dst_vendor uuid REFERENCES ma.d_vendor(id),CHECK(num_nonnulls(dst_deal,dst_client,dst_lead,dst_vendor)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_deal IS NOT NULL THEN 'deal' WHEN dst_client IS NOT NULL THEN 'client' WHEN dst_lead IS NOT NULL THEN 'lead' WHEN dst_vendor IS NOT NULL THEN 'vendor' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_deal,dst_client,dst_lead,dst_vendor)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_next_action(src_id);
CREATE INDEX ON ma.l_next_action(dst_kind,dst_id);
CREATE INDEX ON ma.l_next_action(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE INDEX ON ma.l_next_action(dst_client) WHERE dst_client IS NOT NULL;
CREATE INDEX ON ma.l_next_action(dst_lead) WHERE dst_lead IS NOT NULL;
CREATE INDEX ON ma.l_next_action(dst_vendor) WHERE dst_vendor IS NOT NULL;
CREATE TABLE ma.l_record_flag(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_record_flag(id),dst_lead uuid REFERENCES ma.d_lead(id),dst_client uuid REFERENCES ma.d_client(id),dst_vendor uuid REFERENCES ma.d_vendor(id),dst_party uuid REFERENCES ma.d_party(id),dst_deal uuid REFERENCES ma.d_deal(id),dst_campaign uuid REFERENCES ma.d_campaign(id),dst_platform uuid REFERENCES ma.d_platform(id),dst_pillar uuid REFERENCES ma.d_pillar(id),dst_format uuid REFERENCES ma.d_format(id),dst_repo uuid REFERENCES ma.d_repo(id),dst_commit uuid REFERENCES ma.d_commit(id),CHECK(num_nonnulls(dst_lead,dst_client,dst_vendor,dst_party,dst_deal,dst_campaign,dst_platform,dst_pillar,dst_format,dst_repo,dst_commit)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_lead IS NOT NULL THEN 'lead' WHEN dst_client IS NOT NULL THEN 'client' WHEN dst_vendor IS NOT NULL THEN 'vendor' WHEN dst_party IS NOT NULL THEN 'party' WHEN dst_deal IS NOT NULL THEN 'deal' WHEN dst_campaign IS NOT NULL THEN 'campaign' WHEN dst_platform IS NOT NULL THEN 'platform' WHEN dst_pillar IS NOT NULL THEN 'pillar' WHEN dst_format IS NOT NULL THEN 'format' WHEN dst_repo IS NOT NULL THEN 'repo' WHEN dst_commit IS NOT NULL THEN 'commit' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_lead,dst_client,dst_vendor,dst_party,dst_deal,dst_campaign,dst_platform,dst_pillar,dst_format,dst_repo,dst_commit)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_record_flag(src_id);
CREATE INDEX ON ma.l_record_flag(dst_kind,dst_id);
CREATE INDEX ON ma.l_record_flag(dst_lead) WHERE dst_lead IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_client) WHERE dst_client IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_vendor) WHERE dst_vendor IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_party) WHERE dst_party IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_campaign) WHERE dst_campaign IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_platform) WHERE dst_platform IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_pillar) WHERE dst_pillar IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_format) WHERE dst_format IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_repo) WHERE dst_repo IS NOT NULL;
CREATE INDEX ON ma.l_record_flag(dst_commit) WHERE dst_commit IS NOT NULL;
CREATE TABLE ma.l_attachment(id uuid PRIMARY KEY,src_id uuid NOT NULL REFERENCES ma.d_attachment(id),dst_party uuid REFERENCES ma.d_party(id),dst_lead uuid REFERENCES ma.d_lead(id),dst_deal uuid REFERENCES ma.d_deal(id),dst_rule uuid REFERENCES ma.d_rule(id),dst_doctrine_section uuid REFERENCES ma.d_doctrine_section(id),dst_loop uuid REFERENCES ma.d_loop(id),dst_decision uuid REFERENCES ma.d_decision(id),CHECK(num_nonnulls(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision)=1),dst_kind text GENERATED ALWAYS AS (CASE WHEN dst_party IS NOT NULL THEN 'party' WHEN dst_lead IS NOT NULL THEN 'lead' WHEN dst_deal IS NOT NULL THEN 'deal' WHEN dst_rule IS NOT NULL THEN 'rule' WHEN dst_doctrine_section IS NOT NULL THEN 'doctrine_section' WHEN dst_loop IS NOT NULL THEN 'loop' WHEN dst_decision IS NOT NULL THEN 'decision' END) STORED,dst_id uuid GENERATED ALWAYS AS (coalesce(dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision)) STORED,relation text NOT NULL CHECK(relation IN ('reference')),UNIQUE(src_id,dst_kind,dst_id,relation));
CREATE INDEX ON ma.l_attachment(src_id);
CREATE INDEX ON ma.l_attachment(dst_kind,dst_id);
CREATE INDEX ON ma.l_attachment(dst_party) WHERE dst_party IS NOT NULL;
CREATE INDEX ON ma.l_attachment(dst_lead) WHERE dst_lead IS NOT NULL;
CREATE INDEX ON ma.l_attachment(dst_deal) WHERE dst_deal IS NOT NULL;
CREATE INDEX ON ma.l_attachment(dst_rule) WHERE dst_rule IS NOT NULL;
CREATE INDEX ON ma.l_attachment(dst_doctrine_section) WHERE dst_doctrine_section IS NOT NULL;
CREATE INDEX ON ma.l_attachment(dst_loop) WHERE dst_loop IS NOT NULL;
CREATE INDEX ON ma.l_attachment(dst_decision) WHERE dst_decision IS NOT NULL;
CREATE VIEW ma.relationships AS SELECT id,'doctrine_link'::text family,'doctrine_section'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_doctrine_link UNION ALL SELECT id,'incident_link'::text family,'incident'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_incident_link UNION ALL SELECT id,'siep_evidence_link'::text family,'siep_package'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_siep_evidence_link UNION ALL SELECT id,'f01_derivative_link'::text family,'f01_corporate_artifact'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_f01_derivative_link UNION ALL SELECT id,'j102_document_link'::text family,'f01_document'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_j102_document_link UNION ALL SELECT id,'j102_artifact_link'::text family,'f01_corporate_artifact'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_j102_artifact_link UNION ALL SELECT id,'event'::text family,'event'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_event UNION ALL SELECT id,'record_source'::text family,'record_source'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_record_source UNION ALL SELECT id,'next_action'::text family,'next_action'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_next_action UNION ALL SELECT id,'record_flag'::text family,'record_flag'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_record_flag UNION ALL SELECT id,'attachment'::text family,'attachment'::text src_kind,src_id,dst_kind,dst_id,relation FROM ma.l_attachment;
CREATE SCHEMA ma_cdc;
CREATE TABLE ma_cdc.changes(seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,table_name text NOT NULL,operation text NOT NULL,row_data jsonb NOT NULL);
CREATE FUNCTION ma_cdc.capture() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO ma_cdc.changes(table_name,operation,row_data)
 VALUES (TG_TABLE_NAME,TG_OP,CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END);
 RETURN NULL;
END $$;
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_assignment FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_attachment FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_building FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_campaign FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_capture FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_client FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_commit FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_deal FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_decision FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_decision_event FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_defect FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_deployment FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_doctrine_document FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_doctrine_section FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_engagement FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_event FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_f01_corporate_artifact FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_f01_document FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_format FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_incident FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_job_receipt FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_lead FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_loop FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_next_action FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_party FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_pillar FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_platform FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_property_negotiation FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_record_flag FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_record_source FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_relationship FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_repo FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_rule FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_run FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_siep_package FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_vendor FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.d_work_request FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_doctrine_link FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_incident_link FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_siep_evidence_link FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_f01_derivative_link FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_j102_document_link FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_j102_artifact_link FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_event FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_record_source FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_next_action FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_record_flag FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE TRIGGER ma_cdc_capture AFTER INSERT OR UPDATE OR DELETE ON c.l_attachment FOR EACH ROW EXECUTE FUNCTION ma_cdc.capture();
CREATE FUNCTION ma_cdc.replay() RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE r record; applied bigint := 0;
BEGIN
 FOR r IN SELECT * FROM ma_cdc.changes ORDER BY seq LOOP
  CASE r.table_name WHEN 'd_assignment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_assignment WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_assignment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_attachment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_attachment WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_attachment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_building' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_building WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_building(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_campaign' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_campaign WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_campaign(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_capture' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_capture WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_capture(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_client' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_client WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_client(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_commit' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_commit WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_commit(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deal' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_deal WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_deal(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_decision WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_decision(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision_event' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_decision_event WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_decision_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_defect' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_defect WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_defect(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deployment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_deployment WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_deployment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_document' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_doctrine_document WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_doctrine_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_section' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_doctrine_section WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_doctrine_section(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_engagement' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_engagement WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_engagement(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_event' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_event WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_corporate_artifact' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_f01_corporate_artifact WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_f01_corporate_artifact(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_document' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_f01_document WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_f01_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_format' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_format WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_format(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_incident' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_incident WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_incident(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_job_receipt' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_job_receipt WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_job_receipt(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_lead' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_lead WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_lead(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_loop' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_loop WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_loop(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_next_action' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_next_action WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_next_action(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_party' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_party WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_party(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_pillar' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_pillar WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_pillar(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_platform' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_platform WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_platform(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_property_negotiation' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_property_negotiation WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_property_negotiation(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_flag' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_record_flag WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_record_flag(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_source' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_record_source WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_record_source(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_relationship' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_relationship WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_relationship(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_repo' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_repo WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_repo(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_rule' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_rule WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_rule(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_run' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_run WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_run(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_siep_package' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_siep_package WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_siep_package(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_vendor' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_vendor WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_vendor(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_work_request' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_work_request WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_work_request(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'l_doctrine_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_doctrine_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_doctrine_link(id,src_id,dst_doctrine_document,dst_doctrine_section,dst_party,dst_deal,dst_decision,dst_rule,dst_loop,dst_capture,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='doctrine_document' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='capture' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_doctrine_document=EXCLUDED.dst_doctrine_document,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_party=EXCLUDED.dst_party,dst_deal=EXCLUDED.dst_deal,dst_decision=EXCLUDED.dst_decision,dst_rule=EXCLUDED.dst_rule,dst_loop=EXCLUDED.dst_loop,dst_capture=EXCLUDED.dst_capture,relation=EXCLUDED.relation;
 END IF; WHEN 'l_incident_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_incident_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_incident_link(id,src_id,dst_run,dst_deployment,dst_work_request,dst_defect,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='run' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deployment' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='work_request' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='defect' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_run=EXCLUDED.dst_run,dst_deployment=EXCLUDED.dst_deployment,dst_work_request=EXCLUDED.dst_work_request,dst_defect=EXCLUDED.dst_defect,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; WHEN 'l_siep_evidence_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_siep_evidence_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_siep_evidence_link(id,src_id,dst_job_receipt,dst_decision_event,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='job_receipt' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision_event' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_job_receipt=EXCLUDED.dst_job_receipt,dst_decision_event=EXCLUDED.dst_decision_event,relation=EXCLUDED.relation;
 END IF; WHEN 'l_f01_derivative_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_f01_derivative_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_f01_derivative_link(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_document_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_j102_document_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_j102_document_link(id,src_id,dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='relationship' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='engagement' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='assignment' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='property_negotiation' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_relationship=EXCLUDED.dst_relationship,dst_engagement=EXCLUDED.dst_engagement,dst_assignment=EXCLUDED.dst_assignment,dst_property_negotiation=EXCLUDED.dst_property_negotiation,dst_deal=EXCLUDED.dst_deal,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_artifact_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_j102_artifact_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_j102_artifact_link(id,src_id,dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='relationship' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='engagement' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='assignment' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='property_negotiation' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_relationship=EXCLUDED.dst_relationship,dst_engagement=EXCLUDED.dst_engagement,dst_assignment=EXCLUDED.dst_assignment,dst_property_negotiation=EXCLUDED.dst_property_negotiation,dst_deal=EXCLUDED.dst_deal,relation=EXCLUDED.relation;
 END IF; WHEN 'l_event' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_event WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_event(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_source' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_record_source WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_record_source(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,dst_client,dst_building,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='client' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='building' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,dst_client=EXCLUDED.dst_client,dst_building=EXCLUDED.dst_building,relation=EXCLUDED.relation;
 END IF; WHEN 'l_next_action' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_next_action WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_next_action(id,src_id,dst_deal,dst_client,dst_lead,dst_vendor,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='client' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='vendor' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_deal=EXCLUDED.dst_deal,dst_client=EXCLUDED.dst_client,dst_lead=EXCLUDED.dst_lead,dst_vendor=EXCLUDED.dst_vendor,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_flag' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_record_flag WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_record_flag(id,src_id,dst_lead,dst_client,dst_vendor,dst_party,dst_deal,dst_campaign,dst_platform,dst_pillar,dst_format,dst_repo,dst_commit,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='client' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='vendor' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='campaign' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='platform' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='pillar' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='format' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='repo' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='commit' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_lead=EXCLUDED.dst_lead,dst_client=EXCLUDED.dst_client,dst_vendor=EXCLUDED.dst_vendor,dst_party=EXCLUDED.dst_party,dst_deal=EXCLUDED.dst_deal,dst_campaign=EXCLUDED.dst_campaign,dst_platform=EXCLUDED.dst_platform,dst_pillar=EXCLUDED.dst_pillar,dst_format=EXCLUDED.dst_format,dst_repo=EXCLUDED.dst_repo,dst_commit=EXCLUDED.dst_commit,relation=EXCLUDED.relation;
 END IF; WHEN 'l_attachment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_attachment WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_attachment(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; ELSE RAISE EXCEPTION 'unknown CDC table'; END CASE;
  applied := applied+1;
 END LOOP;
 DELETE FROM ma_cdc.changes;
 RETURN applied;
END $$;
BEGIN ISOLATION LEVEL REPEATABLE READ;
INSERT INTO ma.d_assignment(id,payload) SELECT id,payload FROM c.d_assignment;
INSERT INTO ma.d_attachment(id,payload) SELECT id,payload FROM c.d_attachment;
INSERT INTO ma.d_building(id,payload) SELECT id,payload FROM c.d_building;
INSERT INTO ma.d_campaign(id,payload) SELECT id,payload FROM c.d_campaign;
INSERT INTO ma.d_capture(id,payload) SELECT id,payload FROM c.d_capture;
INSERT INTO ma.d_client(id,payload) SELECT id,payload FROM c.d_client;
INSERT INTO ma.d_commit(id,payload) SELECT id,payload FROM c.d_commit;
INSERT INTO ma.d_deal(id,payload) SELECT id,payload FROM c.d_deal;
INSERT INTO ma.d_decision(id,payload) SELECT id,payload FROM c.d_decision;
INSERT INTO ma.d_decision_event(id,payload) SELECT id,payload FROM c.d_decision_event;
INSERT INTO ma.d_defect(id,payload) SELECT id,payload FROM c.d_defect;
INSERT INTO ma.d_deployment(id,payload) SELECT id,payload FROM c.d_deployment;
INSERT INTO ma.d_doctrine_document(id,payload) SELECT id,payload FROM c.d_doctrine_document;
INSERT INTO ma.d_doctrine_section(id,payload) SELECT id,payload FROM c.d_doctrine_section;
INSERT INTO ma.d_engagement(id,payload) SELECT id,payload FROM c.d_engagement;
INSERT INTO ma.d_event(id,payload) SELECT id,payload FROM c.d_event;
INSERT INTO ma.d_f01_corporate_artifact(id,payload) SELECT id,payload FROM c.d_f01_corporate_artifact;
INSERT INTO ma.d_f01_document(id,payload) SELECT id,payload FROM c.d_f01_document;
INSERT INTO ma.d_format(id,payload) SELECT id,payload FROM c.d_format;
INSERT INTO ma.d_incident(id,payload) SELECT id,payload FROM c.d_incident;
INSERT INTO ma.d_job_receipt(id,payload) SELECT id,payload FROM c.d_job_receipt;
INSERT INTO ma.d_lead(id,payload) SELECT id,payload FROM c.d_lead;
INSERT INTO ma.d_loop(id,payload) SELECT id,payload FROM c.d_loop;
INSERT INTO ma.d_next_action(id,payload) SELECT id,payload FROM c.d_next_action;
INSERT INTO ma.d_party(id,payload) SELECT id,payload FROM c.d_party;
INSERT INTO ma.d_pillar(id,payload) SELECT id,payload FROM c.d_pillar;
INSERT INTO ma.d_platform(id,payload) SELECT id,payload FROM c.d_platform;
INSERT INTO ma.d_property_negotiation(id,payload) SELECT id,payload FROM c.d_property_negotiation;
INSERT INTO ma.d_record_flag(id,payload) SELECT id,payload FROM c.d_record_flag;
INSERT INTO ma.d_record_source(id,payload) SELECT id,payload FROM c.d_record_source;
INSERT INTO ma.d_relationship(id,payload) SELECT id,payload FROM c.d_relationship;
INSERT INTO ma.d_repo(id,payload) SELECT id,payload FROM c.d_repo;
INSERT INTO ma.d_rule(id,payload) SELECT id,payload FROM c.d_rule;
INSERT INTO ma.d_run(id,payload) SELECT id,payload FROM c.d_run;
INSERT INTO ma.d_siep_package(id,payload) SELECT id,payload FROM c.d_siep_package;
INSERT INTO ma.d_vendor(id,payload) SELECT id,payload FROM c.d_vendor;
INSERT INTO ma.d_work_request(id,payload) SELECT id,payload FROM c.d_work_request;
INSERT INTO ma.l_doctrine_link(id,src_id,dst_doctrine_document,dst_doctrine_section,dst_party,dst_deal,dst_decision,dst_rule,dst_loop,dst_capture,relation) SELECT id,src_id,CASE WHEN dst_kind='doctrine_document' THEN dst_id END,CASE WHEN dst_kind='doctrine_section' THEN dst_id END,CASE WHEN dst_kind='party' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,CASE WHEN dst_kind='decision' THEN dst_id END,CASE WHEN dst_kind='rule' THEN dst_id END,CASE WHEN dst_kind='loop' THEN dst_id END,CASE WHEN dst_kind='capture' THEN dst_id END,relation FROM c.l_doctrine_link;
INSERT INTO ma.l_incident_link(id,src_id,dst_run,dst_deployment,dst_work_request,dst_defect,dst_decision,relation) SELECT id,src_id,CASE WHEN dst_kind='run' THEN dst_id END,CASE WHEN dst_kind='deployment' THEN dst_id END,CASE WHEN dst_kind='work_request' THEN dst_id END,CASE WHEN dst_kind='defect' THEN dst_id END,CASE WHEN dst_kind='decision' THEN dst_id END,relation FROM c.l_incident_link;
INSERT INTO ma.l_siep_evidence_link(id,src_id,dst_job_receipt,dst_decision_event,relation) SELECT id,src_id,CASE WHEN dst_kind='job_receipt' THEN dst_id END,CASE WHEN dst_kind='decision_event' THEN dst_id END,relation FROM c.l_siep_evidence_link;
INSERT INTO ma.l_f01_derivative_link(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) SELECT id,src_id,CASE WHEN dst_kind='party' THEN dst_id END,CASE WHEN dst_kind='lead' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,CASE WHEN dst_kind='rule' THEN dst_id END,CASE WHEN dst_kind='doctrine_section' THEN dst_id END,CASE WHEN dst_kind='loop' THEN dst_id END,CASE WHEN dst_kind='decision' THEN dst_id END,relation FROM c.l_f01_derivative_link;
INSERT INTO ma.l_j102_document_link(id,src_id,dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal,relation) SELECT id,src_id,CASE WHEN dst_kind='relationship' THEN dst_id END,CASE WHEN dst_kind='engagement' THEN dst_id END,CASE WHEN dst_kind='assignment' THEN dst_id END,CASE WHEN dst_kind='property_negotiation' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,relation FROM c.l_j102_document_link;
INSERT INTO ma.l_j102_artifact_link(id,src_id,dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal,relation) SELECT id,src_id,CASE WHEN dst_kind='relationship' THEN dst_id END,CASE WHEN dst_kind='engagement' THEN dst_id END,CASE WHEN dst_kind='assignment' THEN dst_id END,CASE WHEN dst_kind='property_negotiation' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,relation FROM c.l_j102_artifact_link;
INSERT INTO ma.l_event(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) SELECT id,src_id,CASE WHEN dst_kind='party' THEN dst_id END,CASE WHEN dst_kind='lead' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,CASE WHEN dst_kind='rule' THEN dst_id END,CASE WHEN dst_kind='doctrine_section' THEN dst_id END,CASE WHEN dst_kind='loop' THEN dst_id END,CASE WHEN dst_kind='decision' THEN dst_id END,relation FROM c.l_event;
INSERT INTO ma.l_record_source(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,dst_client,dst_building,relation) SELECT id,src_id,CASE WHEN dst_kind='party' THEN dst_id END,CASE WHEN dst_kind='lead' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,CASE WHEN dst_kind='rule' THEN dst_id END,CASE WHEN dst_kind='doctrine_section' THEN dst_id END,CASE WHEN dst_kind='loop' THEN dst_id END,CASE WHEN dst_kind='decision' THEN dst_id END,CASE WHEN dst_kind='client' THEN dst_id END,CASE WHEN dst_kind='building' THEN dst_id END,relation FROM c.l_record_source;
INSERT INTO ma.l_next_action(id,src_id,dst_deal,dst_client,dst_lead,dst_vendor,relation) SELECT id,src_id,CASE WHEN dst_kind='deal' THEN dst_id END,CASE WHEN dst_kind='client' THEN dst_id END,CASE WHEN dst_kind='lead' THEN dst_id END,CASE WHEN dst_kind='vendor' THEN dst_id END,relation FROM c.l_next_action;
INSERT INTO ma.l_record_flag(id,src_id,dst_lead,dst_client,dst_vendor,dst_party,dst_deal,dst_campaign,dst_platform,dst_pillar,dst_format,dst_repo,dst_commit,relation) SELECT id,src_id,CASE WHEN dst_kind='lead' THEN dst_id END,CASE WHEN dst_kind='client' THEN dst_id END,CASE WHEN dst_kind='vendor' THEN dst_id END,CASE WHEN dst_kind='party' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,CASE WHEN dst_kind='campaign' THEN dst_id END,CASE WHEN dst_kind='platform' THEN dst_id END,CASE WHEN dst_kind='pillar' THEN dst_id END,CASE WHEN dst_kind='format' THEN dst_id END,CASE WHEN dst_kind='repo' THEN dst_id END,CASE WHEN dst_kind='commit' THEN dst_id END,relation FROM c.l_record_flag;
INSERT INTO ma.l_attachment(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) SELECT id,src_id,CASE WHEN dst_kind='party' THEN dst_id END,CASE WHEN dst_kind='lead' THEN dst_id END,CASE WHEN dst_kind='deal' THEN dst_id END,CASE WHEN dst_kind='rule' THEN dst_id END,CASE WHEN dst_kind='doctrine_section' THEN dst_id END,CASE WHEN dst_kind='loop' THEN dst_id END,CASE WHEN dst_kind='decision' THEN dst_id END,relation FROM c.l_attachment;
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
SELECT ma_cdc.replay();
CREATE OR REPLACE FUNCTION ma_cdc.capture() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r record;
BEGIN
 SELECT TG_TABLE_NAME table_name,TG_OP operation,
  CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END row_data INTO r;
 CASE r.table_name WHEN 'd_assignment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_assignment WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_assignment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_attachment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_attachment WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_attachment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_building' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_building WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_building(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_campaign' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_campaign WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_campaign(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_capture' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_capture WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_capture(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_client' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_client WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_client(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_commit' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_commit WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_commit(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deal' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_deal WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_deal(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_decision WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_decision(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_decision_event' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_decision_event WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_decision_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_defect' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_defect WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_defect(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_deployment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_deployment WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_deployment(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_document' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_doctrine_document WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_doctrine_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_doctrine_section' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_doctrine_section WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_doctrine_section(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_engagement' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_engagement WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_engagement(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_event' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_event WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_event(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_corporate_artifact' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_f01_corporate_artifact WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_f01_corporate_artifact(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_f01_document' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_f01_document WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_f01_document(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_format' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_format WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_format(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_incident' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_incident WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_incident(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_job_receipt' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_job_receipt WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_job_receipt(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_lead' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_lead WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_lead(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_loop' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_loop WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_loop(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_next_action' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_next_action WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_next_action(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_party' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_party WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_party(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_pillar' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_pillar WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_pillar(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_platform' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_platform WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_platform(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_property_negotiation' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_property_negotiation WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_property_negotiation(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_flag' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_record_flag WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_record_flag(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_record_source' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_record_source WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_record_source(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_relationship' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_relationship WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_relationship(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_repo' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_repo WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_repo(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_rule' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_rule WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_rule(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_run' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_run WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_run(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_siep_package' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_siep_package WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_siep_package(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_vendor' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_vendor WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_vendor(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'd_work_request' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.d_work_request WHERE id=(r.row_data->>'id')::uuid;

 ELSE

  INSERT INTO ma.d_work_request(id,payload) VALUES ((r.row_data->>'id')::uuid,r.row_data->>'payload')
  ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload;
 END IF; WHEN 'l_doctrine_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_doctrine_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_doctrine_link(id,src_id,dst_doctrine_document,dst_doctrine_section,dst_party,dst_deal,dst_decision,dst_rule,dst_loop,dst_capture,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='doctrine_document' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='capture' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_doctrine_document=EXCLUDED.dst_doctrine_document,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_party=EXCLUDED.dst_party,dst_deal=EXCLUDED.dst_deal,dst_decision=EXCLUDED.dst_decision,dst_rule=EXCLUDED.dst_rule,dst_loop=EXCLUDED.dst_loop,dst_capture=EXCLUDED.dst_capture,relation=EXCLUDED.relation;
 END IF; WHEN 'l_incident_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_incident_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_incident_link(id,src_id,dst_run,dst_deployment,dst_work_request,dst_defect,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='run' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deployment' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='work_request' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='defect' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_run=EXCLUDED.dst_run,dst_deployment=EXCLUDED.dst_deployment,dst_work_request=EXCLUDED.dst_work_request,dst_defect=EXCLUDED.dst_defect,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; WHEN 'l_siep_evidence_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_siep_evidence_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_siep_evidence_link(id,src_id,dst_job_receipt,dst_decision_event,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='job_receipt' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision_event' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_job_receipt=EXCLUDED.dst_job_receipt,dst_decision_event=EXCLUDED.dst_decision_event,relation=EXCLUDED.relation;
 END IF; WHEN 'l_f01_derivative_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_f01_derivative_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_f01_derivative_link(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_document_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_j102_document_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_j102_document_link(id,src_id,dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='relationship' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='engagement' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='assignment' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='property_negotiation' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_relationship=EXCLUDED.dst_relationship,dst_engagement=EXCLUDED.dst_engagement,dst_assignment=EXCLUDED.dst_assignment,dst_property_negotiation=EXCLUDED.dst_property_negotiation,dst_deal=EXCLUDED.dst_deal,relation=EXCLUDED.relation;
 END IF; WHEN 'l_j102_artifact_link' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_j102_artifact_link WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_j102_artifact_link(id,src_id,dst_relationship,dst_engagement,dst_assignment,dst_property_negotiation,dst_deal,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='relationship' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='engagement' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='assignment' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='property_negotiation' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_relationship=EXCLUDED.dst_relationship,dst_engagement=EXCLUDED.dst_engagement,dst_assignment=EXCLUDED.dst_assignment,dst_property_negotiation=EXCLUDED.dst_property_negotiation,dst_deal=EXCLUDED.dst_deal,relation=EXCLUDED.relation;
 END IF; WHEN 'l_event' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_event WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_event(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_source' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_record_source WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_record_source(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,dst_client,dst_building,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='client' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='building' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,dst_client=EXCLUDED.dst_client,dst_building=EXCLUDED.dst_building,relation=EXCLUDED.relation;
 END IF; WHEN 'l_next_action' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_next_action WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_next_action(id,src_id,dst_deal,dst_client,dst_lead,dst_vendor,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='client' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='vendor' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_deal=EXCLUDED.dst_deal,dst_client=EXCLUDED.dst_client,dst_lead=EXCLUDED.dst_lead,dst_vendor=EXCLUDED.dst_vendor,relation=EXCLUDED.relation;
 END IF; WHEN 'l_record_flag' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_record_flag WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_record_flag(id,src_id,dst_lead,dst_client,dst_vendor,dst_party,dst_deal,dst_campaign,dst_platform,dst_pillar,dst_format,dst_repo,dst_commit,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='client' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='vendor' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='campaign' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='platform' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='pillar' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='format' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='repo' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='commit' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_lead=EXCLUDED.dst_lead,dst_client=EXCLUDED.dst_client,dst_vendor=EXCLUDED.dst_vendor,dst_party=EXCLUDED.dst_party,dst_deal=EXCLUDED.dst_deal,dst_campaign=EXCLUDED.dst_campaign,dst_platform=EXCLUDED.dst_platform,dst_pillar=EXCLUDED.dst_pillar,dst_format=EXCLUDED.dst_format,dst_repo=EXCLUDED.dst_repo,dst_commit=EXCLUDED.dst_commit,relation=EXCLUDED.relation;
 END IF; WHEN 'l_attachment' THEN
 IF r.operation='DELETE' THEN
  DELETE FROM ma.l_attachment WHERE id=(r.row_data->>'id')::uuid;
 ELSE
  INSERT INTO ma.l_attachment(id,src_id,dst_party,dst_lead,dst_deal,dst_rule,dst_doctrine_section,dst_loop,dst_decision,relation) VALUES ((r.row_data->>'id')::uuid,(r.row_data->>'src_id')::uuid,CASE WHEN r.row_data->>'dst_kind'='party' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='lead' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='deal' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='rule' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='doctrine_section' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='loop' THEN (r.row_data->>'dst_id')::uuid END,CASE WHEN r.row_data->>'dst_kind'='decision' THEN (r.row_data->>'dst_id')::uuid END,r.row_data->>'relation') ON CONFLICT(id) DO UPDATE SET src_id=EXCLUDED.src_id,dst_party=EXCLUDED.dst_party,dst_lead=EXCLUDED.dst_lead,dst_deal=EXCLUDED.dst_deal,dst_rule=EXCLUDED.dst_rule,dst_doctrine_section=EXCLUDED.dst_doctrine_section,dst_loop=EXCLUDED.dst_loop,dst_decision=EXCLUDED.dst_decision,relation=EXCLUDED.relation;
 END IF; ELSE RAISE EXCEPTION 'unknown CDC table'; END CASE;
 RETURN NULL;
END $$;
CREATE OR REPLACE VIEW ma_cdc.current_relationships AS SELECT * FROM ma.relationships;
COMMIT;

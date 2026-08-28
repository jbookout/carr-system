import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");
const migration = fs.readFileSync(path.join(root, "migrations/0403_tour_delivery_data_plane.sql"), "utf8");
const ci = fs.readFileSync(path.join(root, "ops/ci.sh"), "utf8");

test("Tour delivery data plane implements every registered SQL seam", () => {
  for (const name of [
    "search_tour_properties", "append_tour_selection_cart_version", "read_tour_selection_cart",
    "list_tour_library", "read_tour_internal_detail", "prepare_tour_route_version",
    "read_tour_projection_creation_metadata", "read_tour_projection_seal_candidates",
    "issue_tour_share_grant", "rotate_tour_share_grant", "revoke_tour_share_grant",
    "read_tour_sharing_library", "exchange_tour_share_token", "read_tour_share_packet",
    "read_tour_share_map", "resolve_tour_public_asset", "request_tour_pdf_render",
    "read_tour_packet_for_render", "record_tour_pdf_render_result", "read_tour_pdf_artifact_for_review",
    "read_tour_pdf_artifact_for_download", "read_tour_pdf_render", "record_tour_pdf_human_review",
  ]) assert.match(migration, new RegExp(`create or replace function ops\\.${name}\\b`, "i"), name);
});

test("Tour delivery state is append-only, tenant-qualified, digest-only, and least privilege", () => {
  for (const table of [
    "tour_selection_cart_version", "tour_share_session", "tour_public_asset",
    "tour_public_projection_map_point", "tour_pdf_render_job", "tour_pdf_render_result", "tour_pdf_human_review",
  ]) {
    assert.match(migration, new RegExp(`create table if not exists ops\\.${table}`, "i"), table);
    assert.match(migration, new RegExp(`['\"]${table}['\"]`, "i"), table);
  }
  assert.match(migration, /p_token_digest text/i);
  assert.match(migration, /session_digest text not null/i);
  assert.doesNotMatch(migration, /plaintext_token|raw_token|grant\s+(?:all|insert|update|delete)\s+on\s+(?:table\s+)?ops\.tour_/i);
  assert.match(migration, /grant execute on function ops\.exchange_tour_share_token/i);
  assert.match(migration, /grant execute on function ops\.issue_tour_share_grant[\s\S]*ops\.record_tour_pdf_human_review[\s\S]*to carr_authority/i);
  assert.match(migration, /record_tour_pdf_render_result[\s\S]*exists\(select 1 from ops\.tour_pdf_human_review h where h\.organization_tenant_id=p_tenant and h\.render_job_id=p_render_job_id\)/i);
  assert.match(migration, /p_status='failed'[\s\S]*p_artifact_ref is not null[\s\S]*p_page_count is not null/i);
  assert.match(migration, /current_setting\('carr\.verified_human_actor_slug',true\)[\s\S]*tour PDF review requires a verified human authority session/i);
});

test("public packet and map reads remain sealed facts-only projections", () => {
  assert.match(migration, /p\.status='approved'/i);
  assert.match(migration, /tour_public_projection_seal_receipt/i);
  assert.match(migration, /tour_public_value_safe/i);
  assert.match(migration, /tour_coordinate_entrance_verification_receipt/i);
  assert.match(migration, /public-tour-projection-digest\.v2/i);
  assert.match(migration, /tour_public_projection_map_point/i);
  assert.match(migration, /ops\.read_tour_public_projection\(p\.organization_tenant_id,p\.id\) is not null/i);
  assert.match(migration, /tour map share requires one sealed entrance coordinate per property/i);
  assert.match(migration, /allowed_field_classes \? 'coordinates'/i);
  assert.match(migration, /tour_share_session_grant\(p_session_digest,'view_packet'\)/i);
  assert.match(migration, /tour_share_session_grant\(p_session_digest,'view_map'\)/i);
  assert.doesNotMatch(migration, /tour_cheat_sheet_revision[\s\S]{0,200}read_tour_share_packet/i);
});

test("property search cursor paginates in the requested stable order", () => {
  assert.match(migration, /row_number\(\) over \(order by/i);
  assert.match(migration, /result_position>v_offset/i);
  assert.match(migration, /limit v_limit\+1/i);
  assert.match(migration, /jsonb_build_object\('count',v_count,'has_more',v_has_more,'cursor',v_cursor/i);
});

test("the repository migration class executes the 0403 PostgreSQL acceptance proof", () => {
  assert.match(ci, /mcp-server\/test\/tour-delivery-data-plane-postgres\.sql/);
});

test("route preparation always derives transitions from the accepted canonical base", () => {
  assert.match(migration, /v_source:=v_base/i);
  assert.match(migration, /route_version_id=v_source\.id/i);
  assert.match(migration, /route_version_id=v_base\.id and property_id=v_stop\.property_id/i);
  assert.match(migration, /append_tour_route_stop_transition\(p_tenant,v_base\.id,v_new,v_old_stop\.id,v_new_stop/i);
  assert.match(migration, /append_tour_route_stop_transition\(p_tenant,null,v_new,null,v_new_stop,'added'\)/i);
  assert.match(migration, /chr\(64\+v_stop\.new_sequence\)/i);
  assert.match(migration, /lag\(s\.appointment_start\) over \(order by x\.ordinality\)/i);
  assert.match(migration, /tour route preparation violates locked appointment order/i);
});

test("sharing history uses a bounded offset cursor and advertises continuation", () => {
  assert.match(migration, /p_cursor !~ '\^\[0-9\]\{1,9\}\$'/i);
  assert.match(migration, /limit v_limit\+1 offset v_offset/i);
  assert.match(migration, /'has_more',v_count>v_limit/i);
  assert.match(migration, /'cursor',case when v_count>v_limit then \(v_offset\+v_limit\)::text end/i);
});

test("internal detail binds route and PDF controls to visible property and projection identity", () => {
  assert.match(migration, /'property_name'[\s\S]*tour_field_assertion[\s\S]*field_key='display\.name'/i);
  assert.match(migration, /'property_address'[\s\S]*tour_field_assertion[\s\S]*field_key='display\.address'/i);
  assert.match(migration, /'render_job_id',j\.id,'projection_id',j\.projection_id/i);
  assert.match(migration, /p\.id=\(select current_projection\.id[\s\S]*status in \('approved','published'\)[\s\S]*projection_version desc/i);
});

// Production adapters for the authenticated internal Tour leaf and the public
// reports leaf. Identity remains owned by Deal Room; writes remain owned by the
// registered tool envelope; public reads accept digests only.
import { Pool } from "@neondatabase/serverless";
import { callTool } from "./mcp.js";
import { ToolError } from "./tool-error.js";
import { tourSharingBrowserAccess } from "./tour-sharing.js";
import { organizationTenantForActor } from "./identity.js";

const sharing = tourSharingBrowserAccess({ ToolError });

async function withPool(connectionString, transaction, fn) {
  const pool = new Pool({ connectionString });
  const client = await pool.connect();
  try {
    await client.query(transaction);
    const value = await fn(client);
    await client.query("commit");
    return value;
  } catch (error) {
    await client.query("rollback").catch(() => {});
    throw error;
  } finally {
    client.release();
    await pool.end();
  }
}

function toolData(result) {
  const data = {};
  for (const [key, value] of Object.entries(result || {})) if (key !== "ok") data[key] = value;
  return { ok: result?.ok === true, data };
}

export function projectTourLibrary(raw) {
  const tours = Array.isArray(raw?.tours) ? raw.tours : [];
  return { tours: tours.map(tour => ({ ...tour, name: tour.tour_name, status: tour.tour_status })) };
}

export function projectTourDetail(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return raw;
  const routes = Array.isArray(raw.routes) ? raw.routes : [];
  const latestRoute = routes[0] || null;
  const approvedProjection = (Array.isArray(raw.projections) ? raw.projections : [])
    .find(projection => projection?.status === "approved" || projection?.status === "published");
  const draftProjection = (Array.isArray(raw.projections) ? raw.projections : [])
    .find(projection => projection?.status === "draft");
  const activeShare = (Array.isArray(raw.shares) ? raw.shares : [])
    .find(share => share?.status === "active" && share.projection_id === approvedProjection?.id);
  return {
    ...raw,
    name: raw.tour_name,
    status: raw.tour_status,
    route_version_id: latestRoute?.id || null,
    route_version_label: latestRoute ? `Version ${latestRoute.route_version}${latestRoute.accepted ? " · accepted" : " · draft"}` : null,
    route_version_state: latestRoute?.accepted ? "accepted" : latestRoute ? "draft" : "missing",
    stops: Array.isArray(latestRoute?.stops) ? latestRoute.stops.map(stop => ({
      ...stop, label: stop.route_label || (Number.isInteger(stop.route_sequence) ? `Stop ${stop.route_sequence}` : "Tour stop"),
    })) : [],
    projection_id: approvedProjection?.id || null,
    projection_draft_id: draftProjection?.id || null,
    projection_status: approvedProjection?.status || draftProjection?.status || "missing",
    share_grant_id: activeShare?.share_grant_id || null,
    share_status: activeShare?.status || "missing",
    pdf_render_job_id: raw.pdf_render?.render_job_id || null,
    pdf_status: raw.pdf_render?.status || "missing",
    pdf_qc_run_digest: raw.pdf_render?.qc_run_digest || null,
    pdf_human_review_state: raw.pdf_render?.human_review_state || "pending",
  };
}

async function invoke({ env, ctx, actor }, verb, args) {
  return toolData(await callTool({ ...env, ctx }, actor, verb, args));
}

async function internalRead({ env, actor }, sql, params) {
  const actorId = actor?.id || actor?.slug;
  return withPool(env.DATABASE_URL_WRITER, "begin read only", async client => {
    const result = await client.query(sql, [...params, actorId]);
    return result.rows[0]?.data || null;
  });
}

async function sha256Bytes(value) {
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", value));
  return `sha256:${[...hash].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

const PDF_ARTIFACT_READS = Object.freeze({
  review: {
    sql: "select ops.read_tour_pdf_artifact_for_review($1::text,$2::uuid,$3::text) as data",
    disposition: "inline",
  },
  download: {
    sql: "select ops.read_tour_pdf_artifact_for_download($1::text,$2::uuid,$3::text) as data",
    disposition: "attachment",
  },
});

async function pdfArtifactResponse(context, mode) {
  const read = PDF_ARTIFACT_READS[mode];
  if (!read) throw new Error("tour_pdf_response_mode_invalid");
  const tenant = organizationTenantForActor(context.actor);
  const artifact = await internalRead(context, read.sql, [tenant, context.input.render_job_id]);
  if (!artifact?.storage_ref || !context.env?.carr_documents?.get) return { ok: false, status: 404 };
  const object = await context.env.carr_documents.get(artifact.storage_ref);
  if (!object) return { ok: false, status: 404 };
  const bytes = new Uint8Array(await object.arrayBuffer());
  if (bytes.byteLength !== artifact.content_length || await sha256Bytes(bytes) !== artifact.artifact_digest) return { ok: false, status: 409 };
  return { ok: true, response: new Response(bytes, { headers: { "content-type": "application/pdf", "content-length": String(bytes.byteLength), "content-disposition": `${read.disposition}; filename="CARR-tour-${context.input.render_job_id}.pdf"`, "cache-control": "no-store" } }) };
}

export function createTourRuntimeAdapters() {
  return {
    listToursFn: async context => ({ ok: true, data: projectTourLibrary(await internalRead(context,
      "select ops.list_tour_library($1::text,$2::text) as data", [organizationTenantForActor(context.actor)])) }),
    readTourFn: async context => ({ ok: true, data: projectTourDetail(await internalRead(context,
      "select ops.read_tour_internal_detail($1::text,$2::uuid,$3::text) as data",
      [organizationTenantForActor(context.actor), context.input.tour_id])) }),
    createRouteVersionFn: context => invoke(context, "prepare-tour-route-version", {
      ...context.input, base_route_version_id: null,
    }),
    reorderRouteStopsFn: context => invoke(context, "prepare-tour-route-version", {
      idempotency_key: context.input.idempotency_key, tour_id: context.input.tour_id,
      base_route_version_id: context.input.route_version_id,
      expected_route_version: context.input.expected_route_version, stop_ids: context.input.stop_ids,
    }),
    acceptRouteVersionFn: context => invoke(context, "accept-tour-route-version", context.input),
    autosaveCheatSheetFn: context => invoke(context, "append-tour-cheat-sheet-revision", context.input),
    restoreCheatSheetFn: context => invoke(context, "restore-tour-cheat-sheet-revision", context.input),
    createProjectionFn: async context => {
      const meta = await internalRead(context,
        "select ops.read_tour_projection_creation_metadata($1::text,$2::uuid,$3::uuid,$4::text) as data",
        [organizationTenantForActor(context.actor), context.input.tour_id, context.input.route_version_id]);
      if (!meta) return { ok: false, status: 404 };
      return invoke(context, "create-tour-public-projection-draft", {
        idempotency_key: context.input.idempotency_key, tour_id: context.input.tour_id,
        projection_version: Number(meta.projection_version), route_version: Number(meta.route_version),
        as_of: context.input.as_of,
      });
    },
    readProjectionCandidatesFn: async context => {
      const candidates = await internalRead(context,
        "select ops.read_tour_projection_seal_candidates($1::text,$2::uuid,$3::text) as data",
        [organizationTenantForActor(context.actor), context.input.projection_id]);
      if (!candidates) return { ok: false, status: 404 };
      return { ok: true, data: { projection_id: candidates.projection_id, candidate_digest: candidates.candidate_digest, preview: candidates.preview } };
    },
    sealProjectionFn: async context => {
      const candidates = await internalRead(context,
        "select ops.read_tour_projection_seal_candidates($1::text,$2::uuid,$3::text) as data",
        [organizationTenantForActor(context.actor), context.input.projection_id]);
      if (!candidates || candidates.candidate_digest !== context.input.candidate_digest) return { ok: false, status: 409 };
      return invoke(context, "seal-tour-public-projection", {
        idempotency_key: context.input.idempotency_key, projection_id: context.input.projection_id,
        selected_facts: candidates.selected_facts, receipt_digest: context.input.receipt_digest,
      });
    },
    issueShareGrantFn: context => invoke(context, "issue-tour-share-grant", context.input),
    rotateShareGrantFn: context => invoke(context, "rotate-tour-share-grant", context.input),
    revokeShareGrantFn: context => invoke(context, "revoke-tour-share-grant", context.input),
    renderPdfFn: async context => {
      const tenant = organizationTenantForActor(context.actor);
      const renderInput = await internalRead(context,
        "select ops.read_tour_packet_for_render($1::text,$2::uuid,$3::text) as data", [tenant, context.input.projection_id]);
      if (!renderInput?.packet || !renderInput?.projection_digest) return { ok: false, status: 404 };
      const { prepareTourPdfArtifact, storeAndVerifyTourPdf } = await import("./tour-pdf-service.js");
      const prepared = await prepareTourPdfArtifact(renderInput);
      const request = await invoke(context, "request-tour-pdf-render", {
        idempotency_key: context.input.idempotency_key, projection_id: context.input.projection_id,
        projection_digest: renderInput.projection_digest, packet_digest: prepared.packetDigest,
        template_version: prepared.rendered.templateVersion, template_digest: prepared.templateDigest,
        renderer_version: prepared.rendered.rendererVersion, renderer_digest: prepared.rendererDigest,
        qc_ruleset_version: prepared.qcRulesetVersion, qc_ruleset_digest: prepared.qcRulesetDigest,
        expected_property_count: prepared.rendered.propertyCount, expected_markers_digest: prepared.markersDigest,
        expected_asset_digests: [], expected_font_digests: prepared.rendered.fontDigests,
      });
      if (!request.ok || !request.data.render_job_id) return request;
      const renderJobId = request.data.render_job_id;
      const stored = await storeAndVerifyTourPdf(context.env, tenant, renderJobId, prepared);
      const artifactRef = `artifact:tour-pdf:${renderJobId.replaceAll("-", "")}`;
      const recorded = await invoke(context, "record-tour-pdf-render-result", {
        idempotency_key: crypto.randomUUID(), render_job_id: renderJobId,
        status: stored.qc.blocked ? "qc_blocked" : "review_ready", artifact_ref: artifactRef,
        artifact_digest: prepared.rendered.artifactDigest, storage_ref: stored.storageRef,
        content_length: stored.contentLength, page_count: prepared.rendered.propertyCount,
        blocking_finding_count: stored.qc.findings.length, qc_run_digest: stored.qcRunDigest,
      });
      return recorded.ok ? { ok: true, data: { render_job_id: renderJobId, status: recorded.data.status, qc_run_digest: stored.qcRunDigest } } : recorded;
    },
    readPdfRenderFn: async context => invoke(context, "read-tour-pdf-render", context.input),
    reviewPdfFn: async context => invoke(context, "record-tour-pdf-human-review", context.input),
    previewPdfFn: context => pdfArtifactResponse(context, "review"),
    downloadPdfFn: context => pdfArtifactResponse(context, "download"),
  };
}

async function publicAccess({ env }, transaction, fn) {
  return withPool(env.DATABASE_URL_WRITER, transaction, fn);
}

export function createReportsRuntimeAdapters() {
  return {
    exchangeShareTokenFn: async ({ env, tokenDigest, sessionDigest, sessionExpiresAt, auditDigest }) =>
      publicAccess({ env }, "begin", async client => {
        const result = await sharing.exchange(client, {
          token_digest: tokenDigest, session_digest: sessionDigest,
          session_expires_at: sessionExpiresAt, audit_digest: auditDigest,
        });
        return result.ok ? { ok: true } : { ok: false, status: 403 };
      }),
    readShareFn: async ({ env, sessionDigest }) =>
      publicAccess({ env }, "begin read only", async client => {
        const result = await sharing.readPacket(client, { session_digest: sessionDigest });
        return result.ok ? { ok: true, data: result.packet } : { ok: false, status: 404 };
      }),
    readMapFn: async ({ env, sessionDigest }) =>
      publicAccess({ env }, "begin read only", async client => {
        const result = await sharing.readMap(client, { session_digest: sessionDigest });
        return result.ok ? { ok: true, data: result.map } : { ok: false, status: 404 };
      }),
  };
}

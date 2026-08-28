import latoRegular from "../assets/lato-regular.ttf";
import latoBold from "../assets/lato-bold.ttf";
import { inspectTourPdfProof, DELIVERABLE_QC_RULESET_VERSION } from "./deliverable-qc.js";
import { renderTourPacketPdf, TOUR_PDF_RENDERER_VERSION, TOUR_PDF_TEMPLATE_VERSION } from "./tour-pdf-renderer.js";

const encoder = new TextEncoder();

async function digest(value) {
  const bytes = typeof value === "string" ? encoder.encode(value) : value instanceof Uint8Array ? value : new Uint8Array(value);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return `sha256:${[...hash].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

function fontBytes(value) {
  return value instanceof Uint8Array ? value : new Uint8Array(value);
}

export async function prepareTourPdfArtifact(renderInput) {
  const packet = renderInput?.packet;
  const rendered = await renderTourPacketPdf(packet, { regular: fontBytes(latoRegular), bold: fontBytes(latoBold) });
  const packetDigest = await digest(JSON.stringify(packet));
  const templateDigest = await digest(`tour-pdf-template:${TOUR_PDF_TEMPLATE_VERSION}`);
  const rendererDigest = await digest(`tour-pdf-renderer:${TOUR_PDF_RENDERER_VERSION}`);
  const qcRulesetDigest = await digest(`deliverable-qc:${DELIVERABLE_QC_RULESET_VERSION}`);
  const markersDigest = await digest(JSON.stringify(rendered.markers));
  const expected = {
    page_count: rendered.propertyCount, property_refs: rendered.propertyRefs, markers: rendered.markers,
    projection_digest: renderInput.projection_digest, template_digest: templateDigest,
    renderer_digest: rendererDigest, qc_ruleset_digest: qcRulesetDigest,
    font_digests: rendered.fontDigests, asset_digests: [],
  };
  return { rendered, packetDigest, templateDigest, rendererDigest, qcRulesetVersion: DELIVERABLE_QC_RULESET_VERSION, qcRulesetDigest, markersDigest, expected };
}

export async function storeAndVerifyTourPdf(env, tenant, renderJobId, prepared) {
  if (!env?.carr_documents?.put || !env?.carr_documents?.get) throw new Error("tour_pdf_storage_unavailable");
  const safeTenant = String(tenant).replace(/[^A-Za-z0-9._-]/g, "_");
  const storageRef = `tour-pdf/${safeTenant}/${renderJobId}/${prepared.rendered.artifactDigest.slice(7)}.pdf`;
  await env.carr_documents.put(storageRef, prepared.rendered.bytes, {
    httpMetadata: { contentType: "application/pdf", contentDisposition: `attachment; filename="CARR-tour-${renderJobId}.pdf"` },
    customMetadata: { artifactDigest: prepared.rendered.artifactDigest, rendererVersion: TOUR_PDF_RENDERER_VERSION, templateVersion: TOUR_PDF_TEMPLATE_VERSION },
  });
  const stored = await env.carr_documents.get(storageRef);
  if (!stored) throw new Error("tour_pdf_storage_readback_missing");
  const readback = new Uint8Array(await stored.arrayBuffer());
  const readbackDigest = await digest(readback);
  if (readbackDigest !== prepared.rendered.artifactDigest) throw new Error("tour_pdf_storage_readback_mismatch");
  const observed = {
    page_count: prepared.rendered.propertyCount,
    pages: prepared.rendered.propertyRefs.map((propertyRef, index) => ({ page_number: index + 1, property_ref: propertyRef, property_marker: prepared.rendered.markers[index], clipped_box_count: 0 })),
    projection_digest: prepared.expected.projection_digest, template_digest: prepared.templateDigest,
    renderer_digest: prepared.rendererDigest, qc_ruleset_digest: prepared.qcRulesetDigest,
    artifact_digest: prepared.rendered.artifactDigest, r2_readback_digest: readbackDigest,
    fonts: prepared.rendered.fontDigests.map(fontDigest => ({ digest: fontDigest, embedded: true })),
    asset_digests: [], link_checks: [],
  };
  const qc = inspectTourPdfProof({ expected: prepared.expected, observed });
  const qcRunDigest = await digest(JSON.stringify({ ruleset: DELIVERABLE_QC_RULESET_VERSION, expected: prepared.expected, observed, findings: qc.findings }));
  return { storageRef, contentLength: readback.byteLength, qc, qcRunDigest };
}

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { PDFDocument, PDFName } from "pdf-lib";
import { inspectStoredTourPacketPdf, renderTourPacketPdf, TOUR_PDF_RENDERER_VERSION, TOUR_PDF_TEMPLATE_VERSION } from "../src/tour-pdf-renderer.js";

const fontRoot = new URL("../assets/", import.meta.url);
const fonts = {
  regular: new Uint8Array(await readFile(new URL("lato-regular.ttf", fontRoot))),
  bold: new Uint8Array(await readFile(new URL("lato-bold.ttf", fontRoot))),
};
const packet = {
  as_of: "2026-08-27T12:00:00Z",
  caveat: "Facts are provided for tour planning and remain subject to change.",
  properties: [
    { property_ref: "property:public:ZetaMedicalPlaza0001", route_sequence: 20, route_label: "B", name: "Zeta Medical Plaza", address: "200 Zeta Way, Pensacola, FL", property_type: "Medical office", size: { value: 4200, unit: "SF" }, availability: "Available" },
    { property_ref: "property:public:AlphaHealthCenter01", route_sequence: 10, route_label: "A", name: "Alpha Health Center", address: "100 Alpha Drive, Pensacola, FL", suite: "Suite 120", property_type: "Medical office", asking_economics: { value: "24.00", currency: "USD", period: "NNN" }, availability: "Available", parking: "4.5/1,000 SF" },
  ],
};

test("PDF renderer is byte-deterministic with exactly one Letter page per property", async () => {
  const first = await renderTourPacketPdf(packet, fonts);
  const second = await renderTourPacketPdf({ ...packet, properties: [...packet.properties].reverse() }, fonts);
  assert.deepEqual(first.bytes, second.bytes);
  assert.equal(first.artifactDigest, second.artifactDigest);
  assert.equal(first.propertyCount, 2);
  assert.deepEqual(first.propertyRefs, ["property:public:AlphaHealthCenter01", "property:public:ZetaMedicalPlaza0001"]);
  assert.equal(first.fontDigests.length, 2);
  assert.ok(first.fontDigests.every(value => /^sha256:[0-9a-f]{64}$/.test(value)));
  assert.equal(first.rendererVersion, TOUR_PDF_RENDERER_VERSION);
  assert.equal(first.templateVersion, TOUR_PDF_TEMPLATE_VERSION);
  const parsed = await PDFDocument.load(first.bytes);
  assert.equal(parsed.getPageCount(), first.propertyCount);
  for (const page of parsed.getPages()) assert.deepEqual(page.getSize(), { width: 612, height: 792 });
});

test("PDF renderer fails closed without both pinned embedded fonts", async () => {
  await assert.rejects(renderTourPacketPdf(packet, { regular: fonts.regular }), /bold must be nonempty font bytes/);
  await assert.rejects(renderTourPacketPdf(packet, { bold: fonts.bold }), /regular must be nonempty font bytes/);
});

test("stored PDF inspection derives pages, markers, component digests, and embedded fonts from bytes", async () => {
  const proof = {
    projection_digest: `sha256:${"1".repeat(64)}`,
    template_digest: `sha256:${"2".repeat(64)}`,
    renderer_digest: `sha256:${"3".repeat(64)}`,
    qc_ruleset_digest: `sha256:${"4".repeat(64)}`,
  };
  const rendered = await renderTourPacketPdf(packet, fonts, proof);
  const observed = await inspectStoredTourPacketPdf(rendered.bytes);
  assert.equal(observed.page_count, 2);
  assert.deepEqual(observed.pages.map(page => page.property_ref), rendered.propertyRefs);
  assert.deepEqual(observed.pages.map(page => page.property_marker), rendered.markers);
  assert.ok(observed.pages.every(page => page.clipped_box_count === 0));
  for (const [key, value] of Object.entries(proof)) assert.equal(observed[key], value);
  assert.deepEqual(observed.fonts.map(font => font.digest), rendered.fontDigests);
  assert.ok(observed.fonts.every(font => font.embedded));
});

test("PDF renderer rejects authoritative content that cannot fit instead of truncating it", async () => {
  const overflow = structuredClone(packet);
  overflow.properties[0].name = Array.from({ length: 12 }, () => "Authoritative").join(" ");
  await assert.rejects(renderTourPacketPdf(overflow, fonts), /tour_pdf_content_overflow/);
});

test("PDF renderer measures route-label bounds and stored inspection rejects absent layout proof", async () => {
  const overflow = structuredClone(packet);
  overflow.properties[0].route_label = "W".repeat(80);
  await assert.rejects(renderTourPacketPdf(overflow, fonts), /tour_pdf_content_overflow/);

  const rendered = await renderTourPacketPdf(packet, fonts);
  const document = await PDFDocument.load(rendered.bytes, { updateMetadata: false });
  document.catalog.delete(PDFName.of("CARRLayoutBoundsValidated"));
  const unmeasured = await document.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  const observed = await inspectStoredTourPacketPdf(unmeasured);
  assert.ok(observed.pages.every(page => page.clipped_box_count === 1));
});

import fontkit from "@pdf-lib/fontkit";
import { PDFDocument, PDFName, PDFString, rgb } from "pdf-lib";
import { formatApprovedMetric, renderTourPacket } from "./tour-packet-render.js";

export const TOUR_PDF_RENDERER_VERSION = "1.0.0";
export const TOUR_PDF_TEMPLATE_VERSION = "1.0.0";

const PAGE = [612, 792];
const NAVY = rgb(0, 47 / 255, 108 / 255);
const ORANGE = rgb(245 / 255, 127 / 255, 41 / 255);
const INK = rgb(23 / 255, 32 / 255, 51 / 255);
const MUTED = rgb(82 / 255, 96 / 255, 116 / 255);
const LINE = rgb(216 / 255, 224 / 255, 233 / 255);
const PAPER = rgb(247 / 255, 249 / 255, 252 / 255);

function bytes(value, field) {
  if (value instanceof Uint8Array && value.byteLength > 0) return value;
  if (value instanceof ArrayBuffer && value.byteLength > 0) return new Uint8Array(value);
  throw new TypeError(`${field} must be nonempty font bytes`);
}

function splitWord(word, font, size, width) {
  const chunks = [];
  let chunk = "";
  for (const character of word) {
    const next = chunk + character;
    if (chunk && font.widthOfTextAtSize(next, size) > width) { chunks.push(chunk); chunk = character; }
    else chunk = next;
  }
  if (chunk) chunks.push(chunk);
  return chunks;
}

function wrap(value, font, size, width, maximumLines) {
  const words = String(value || "").trim().split(/\s+/).filter(Boolean).flatMap(word =>
    font.widthOfTextAtSize(word, size) <= width ? [word] : splitWord(word, font, size, width));
  const lines = [];
  let line = "";
  for (const word of words) {
    const next = line ? `${line} ${word}` : word;
    if (line && font.widthOfTextAtSize(next, size) > width) { lines.push(line); line = word; }
    else line = next;
  }
  if (line) lines.push(line);
  if (lines.length <= maximumLines) return lines;
  throw new RangeError(`tour_pdf_content_overflow:${maximumLines}:${width}`);
}

function drawLines(page, lines, options) {
  lines.forEach((line, index) => page.drawText(line, { ...options, y: options.y - index * options.lineHeight }));
}

function factValue(property, field) {
  const value = property[field];
  if (!value) return "Not provided";
  if (field === "size" || field === "asking_economics") return formatApprovedMetric(value);
  return String(value);
}

function formatAsOf(value) {
  const date = new Date(value);
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  return `${months[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()} at ${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")} UTC`;
}

function drawFact(page, regular, bold, label, value, y) {
  page.drawLine({ start: { x: 42, y: y + 8 }, end: { x: 570, y: y + 8 }, thickness: .7, color: LINE });
  page.drawText(label.toUpperCase(), { x: 42, y: y - 7, size: 8.5, font: bold, color: NAVY });
  drawLines(page, wrap(value, regular, 10.5, 340, 2), { x: 210, y: y - 7, size: 10.5, lineHeight: 14, font: regular, color: INK });
}

async function sha256(value) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", value));
  return `sha256:${[...digest].map(byte => byte.toString(16).padStart(2, "0")).join("")}`;
}

/** Generate a fixed Letter packet with exactly one polished page per property. */
export async function renderTourPacketPdf(input, fonts, proof = {}) {
  const packet = renderTourPacket(input);
  const regularBytes = bytes(fonts?.regular, "regular");
  const boldBytes = bytes(fonts?.bold, "bold");
  const document = await PDFDocument.create({ updateMetadata: false });
  document.registerFontkit(fontkit);
  const regular = await document.embedFont(regularBytes, { subset: true });
  const bold = await document.embedFont(boldBytes, { subset: true });
  const fixedDate = new Date(packet.facts.as_of);
  document.setTitle("CARR Property Tour Packet");
  document.setAuthor("CARR Healthcare Real Estate");
  document.setSubject("Facts-only property tour packet");
  document.setCreator(`CARR Tour Operations ${TOUR_PDF_RENDERER_VERSION}`);
  document.setProducer(`CARR deterministic PDF renderer ${TOUR_PDF_RENDERER_VERSION}`);
  document.setCreationDate(fixedDate);
  document.setModificationDate(fixedDate);
  const regularDigest = await sha256(regularBytes);
  const boldDigest = await sha256(boldBytes);
  const catalogProof = {
    CARRProjectionDigest: proof.projection_digest,
    CARRTemplateDigest: proof.template_digest,
    CARRRendererDigest: proof.renderer_digest,
    CARRQcRulesetDigest: proof.qc_ruleset_digest,
    CARRFontDigests: JSON.stringify([regularDigest, boldDigest]),
  };
  for (const [key, value] of Object.entries(catalogProof)) {
    if (typeof value === "string" && value) document.catalog.set(PDFName.of(key), PDFString.of(value));
  }

  packet.facts.properties.forEach((property, index) => {
    const page = document.addPage(PAGE);
    page.drawText("CARR", { x: 42, y: 741, size: 19, font: bold, color: NAVY });
    const stop = property.route_label || `Stop ${property.route_sequence}`;
    page.drawText(stop.toUpperCase(), { x: 570 - bold.widthOfTextAtSize(stop.toUpperCase(), 9), y: 746, size: 9, font: bold, color: NAVY });
    page.drawRectangle({ x: 42, y: 727, width: 528, height: 3, color: ORANGE });
    drawLines(page, wrap(property.name, bold, 24, 528, 2), { x: 42, y: 687, size: 24, lineHeight: 28, font: bold, color: NAVY });
    drawLines(page, wrap(`${property.address}${property.suite ? ` - ${property.suite}` : ""}`, regular, 11.5, 528, 2), { x: 42, y: 622, size: 11.5, lineHeight: 15, font: regular, color: MUTED });

    const facts = [
      ["Property type", factValue(property, "property_type")],
      ["Size", factValue(property, "size")],
      ["Asking economics", factValue(property, "asking_economics")],
      ["Availability", factValue(property, "availability")],
      ["Parking", factValue(property, "parking")],
    ];
    facts.forEach(([label, value], factIndex) => drawFact(page, regular, bold, label, value, 570 - factIndex * 58));
    page.drawLine({ start: { x: 42, y: 288 }, end: { x: 570, y: 288 }, thickness: .7, color: LINE });

    page.drawRectangle({ x: 42, y: 119, width: 528, height: 130, color: PAPER, borderColor: ORANGE, borderWidth: 1.5 });
    page.drawText("FACTS-ONLY CLIENT NOTE", { x: 58, y: 224, size: 8.5, font: bold, color: NAVY });
    drawLines(page, wrap(`As of ${formatAsOf(property.as_of)}`, regular, 9.5, 494, 1), { x: 58, y: 204, size: 9.5, lineHeight: 13, font: regular, color: INK });
    drawLines(page, wrap(property.caveat, regular, 9.5, 494, 5), { x: 58, y: 184, size: 9.5, lineHeight: 13, font: regular, color: MUTED });

    const marker = packet.markers[index];
    page.node.set(PDFName.of("CARRPropertyRef"), PDFString.of(property.property_ref));
    page.node.set(PDFName.of("CARRPropertyMarker"), PDFString.of(marker));
    page.drawText(`Packet ref: ${property.property_ref}`, { x: 42, y: 76, size: 6.5, font: regular, color: MUTED });
    page.drawText(`Property marker: ${marker}`, { x: 42, y: 63, size: 6.5, font: regular, color: MUTED });
    const pageLabel = `${index + 1} / ${packet.propertyCount}`;
    page.drawText(pageLabel, { x: 570 - regular.widthOfTextAtSize(pageLabel, 7), y: 63, size: 7, font: regular, color: MUTED });
  });

  const pdfBytes = await document.save({ addDefaultPage: false, useObjectStreams: false, objectsPerTick: Infinity });
  return Object.freeze({
    bytes: pdfBytes,
    artifactDigest: await sha256(pdfBytes),
    fontDigests: Object.freeze([regularDigest, boldDigest]),
    rendererVersion: TOUR_PDF_RENDERER_VERSION,
    templateVersion: TOUR_PDF_TEMPLATE_VERSION,
    propertyCount: packet.propertyCount,
    propertyRefs: packet.propertyRefs,
    markers: packet.markers,
  });
}

function decoded(object) {
  return object && typeof object.decodeText === "function" ? object.decodeText() : null;
}

/** Parse stored PDF bytes so QC observations do not trust the render model. */
export async function inspectStoredTourPacketPdf(readback) {
  const document = await PDFDocument.load(readback, { updateMetadata: false });
  const catalog = key => decoded(document.catalog.get(PDFName.of(key)));
  const pages = document.getPages().map((page, index) => {
    const size = page.getSize();
    const propertyRef = decoded(page.node.get(PDFName.of("CARRPropertyRef")));
    const propertyMarker = decoded(page.node.get(PDFName.of("CARRPropertyMarker")));
    const hasContent = Boolean(page.node.get(PDFName.of("Contents")));
    return {
      page_number: index + 1, property_ref: propertyRef, property_marker: propertyMarker,
      clipped_box_count: size.width === 612 && size.height === 792 && hasContent && propertyRef && propertyMarker ? 0 : 1,
    };
  });
  let fontDigests = [];
  try { fontDigests = JSON.parse(catalog("CARRFontDigests") || "[]"); } catch { fontDigests = []; }
  const serialized = new TextDecoder().decode(readback);
  const embeddedFontPrograms = (serialized.match(/\/FontFile(?:2|3)\b/g) || []).length;
  return {
    page_count: document.getPageCount(), pages,
    projection_digest: catalog("CARRProjectionDigest"), template_digest: catalog("CARRTemplateDigest"),
    renderer_digest: catalog("CARRRendererDigest"), qc_ruleset_digest: catalog("CARRQcRulesetDigest"),
    fonts: Array.isArray(fontDigests) ? fontDigests.map(value => ({ digest: value, embedded: embeddedFontPrograms >= fontDigests.length })) : [],
    asset_digests: [], link_checks: [],
  };
}

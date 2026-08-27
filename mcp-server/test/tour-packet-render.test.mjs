import assert from "node:assert/strict";
import test from "node:test";
import { formatApprovedMetric, renderTourPacket, TourPacketRenderError } from "../src/tour-packet-render.js";

const refs = {
  alpha: "property:public:AlphaHealthCenter01",
  zeta: "property:public:ZetaMedicalPlaza0001",
};
const packet = Object.freeze({
  as_of: "2026-08-27T12:00:00Z",
  caveat: "Facts are provided for tour planning and remain subject to change.",
  properties: [
    { property_ref: refs.zeta, route_sequence: 20, route_label: "Stop 2", name: "Zeta Medical Plaza", address: "200 Zeta Way, Pensacola, FL", property_type: "Medical office", size: { value: 4200, unit: "SF" }, availability: "Available" },
    { property_ref: refs.alpha, route_sequence: 10, route_label: "Stop 1", name: "Alpha Health Center", address: "100 Alpha Drive, Pensacola, FL", suite: "Suite 120", property_type: "Medical office", asking_economics: { value: "24.00", currency: "USD", period: "NNN" }, availability: "Available", parking: "4.5/1,000 SF" },
  ],
});

test("Tour packet rendering is deterministic in immutable route order with public identity parity", () => {
  const first = renderTourPacket(packet);
  const second = renderTourPacket({ ...packet, properties: [...packet.properties].reverse() });
  assert.equal(first.html, second.html);
  assert.equal(first.propertyCount, 2);
  assert.deepEqual(first.propertyRefs, [refs.alpha, refs.zeta]);
  assert.equal((first.html.match(/data-deliverable-page="property"/g) || []).length, 2);
  assert.equal((first.html.match(/data-property-ref=/g) || []).length, 2);
  assert.ok(first.html.indexOf("Alpha Health Center") < first.html.indexOf("Zeta Medical Plaza"));
  assert.match(first.html, /data-route-sequence="10"/);
  assert.match(first.html, /data-template-version="1\.0\.0"/);
  assert.match(first.html, /#002F6C/);
  assert.match(first.html, /#F57F29/);
  assert.equal((first.html.match(/<main\b/g) || []).length, 1);
  assert.match(first.html, /break-after: page/);
  assert.match(first.html, /page-break-after: always/);
  assert.match(first.html, /height: 9\.9in/);
  assert.doesNotMatch(first.html, /<section[^>]*(?:overview|cover)/i);
});

test("Tour packet preserves allowlisted structured metrics with deterministic formatting", () => {
  const result = renderTourPacket(packet);
  assert.deepEqual(result.facts.properties[0].asking_economics, { value: "24.00", currency: "USD", period: "NNN" });
  assert.deepEqual(result.facts.properties[1].size, { value: 4200, unit: "SF" });
  assert.equal(formatApprovedMetric({ min: 20, max: 25, currency: "USD", unit: "SF", period: "NNN", label: "Rate" }), "Rate: USD 20–25 SF / NNN");
  assert.match(result.html, /USD 24\.00 \/ NNN/);
  assert.match(result.html, /4200 SF/);
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], size: { value: 4200, provider: "private" } }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_forbidden_field");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], size: { value: { nested: "no" } } }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_text");
});

test("Tour packet refuses unsafe facts, duplicate public identity/route order, and overflow", () => {
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], provider: "Private vendor" }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_forbidden_field");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], availability: "Call agent@example.test" }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_forbidden_contact");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], name: "A".repeat(361) }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_overflow");
  assert.throws(() => renderTourPacket({ ...packet, as_of: "2026-02-30T12:00:00Z" }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_as_of");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], name: "Unsafe\u0000name" }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_text");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], size: { label: "Size" } }] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_invalid_metric");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], route_sequence: 10 }, packet.properties[1]] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_duplicate_route_sequence");
  assert.throws(() => renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], property_ref: refs.alpha }, packet.properties[1]] }), error => error instanceof TourPacketRenderError && error.code === "tour_packet_duplicate_property_ref");
});

test("HTML escaping preserves facts as text and cannot become markup", () => {
  const result = renderTourPacket({ ...packet, properties: [{ ...packet.properties[0], name: "Clinic <North> & East" }] });
  assert.match(result.html, /Clinic &lt;North&gt; &amp; East/);
  assert.doesNotMatch(result.html, /<North>/);
});

# Tour Operations presentation research

Date: 2026-08-27

Status: current web and Reddit review complete. Direct X/Grok evidence was not accessible in the available research environment, so no X claims are included.

## Product decision

Tour Operations uses one ordered facts projection to drive two paired deliverables:

1. A print-stable PDF with exactly one property per Letter page and no cover, agenda, overview, or broker-conclusion page.
2. A responsive confidential share page with the same immutable stop order, a list-first presentation, an optional secondary map, property-bound comments, and explicit Interested / Discuss / Remove reactions.

The PDF and web surface must show the same approved facts. Internal contacts, source evidence, rights metadata, provider payloads, access notes, broker analysis, and canonical identifiers never enter the client projection.

## Design system

- Use CARR navy `#002F6C` for structure and CARR orange `#F57F29` only for focus, calls to action, and small accents.
- Preserve generous white space, a fixed typographic hierarchy, and a consistent fact grid across every property page.
- Keep route order visible as `Stop 1`, `Stop 2`, and so on across PDF, web, reactions, and notes.
- Prefer one reviewed property image when a rights-cleared immutable asset is available. The layout must remain polished and complete without an image.
- Express reaction state with words and control state, never color alone.
- Keep the web list usable without a map. The map is orientation support, not the sole navigation or information surface.

## Print and PDF rules

- Author directly for US Letter with a fixed safe live area; do not depend on viewer auto-fit or shrink-to-page behavior.
- Pin template, renderer, font, asset, projection, and QC-rule digests for every render request.
- Require one machine-readable public property reference and one unique route marker per page.
- Block delivery on page-count mismatch, page-order drift, duplicate/missing markers, clipped boxes, missing font embedding, asset mismatch, unsafe or failed links, digest mismatch, or R2 readback mismatch.
- Treat a clean automated QC run only as `review_required`. A separate human receipt may accept or reject the artifact; neither action is publication authority.

## Responsive and interaction rules

- Use semantic headings and ordered lists, visible keyboard focus, labelled form controls, text status feedback, and single-column reflow under zoom or narrow widths.
- Require an explicit user action before exchanging a fragment bearer for a host-only session cookie. Scanner GETs remain inert.
- Persist the latest reaction and confirm saves in text. Bind every note and reaction to an opaque public property reference.
- Keep PDF download session-scoped and verify the downloaded artifact bytes against the immutable SHA-256 digest before delivery.

## Evidence

Primary guidance:

- Adobe, print-ready PDF production: https://helpx.adobe.com/indesign/desktop/print/print-production-and-file-creation/produce-print-ready-pdf-files.html
- Adobe, print scaling and page sizing: https://helpx.adobe.com/in/acrobat/kb/scale-or-resize-printed-pages.html
- W3C, WCAG 2.2: https://www.w3.org/TR/WCAG22/
- W3C, reflow guidance for non-web documents and software: https://www.w3.org/TR/2023/DNOTE-wcag2ict-20230815/
- W3C, accessible form notifications: https://www.w3.org/WAI/tutorials/forms/notifications/
- Adobe, accessible PDF authoring: https://helpx.adobe.com/ie/indesign/using/creating-accessible-pdfs.html
- Apple, Maps human-interface guidance: https://developer.apple.com/design/human-interface-guidelines/maps

Anecdotal market texture, not standards or authoritative evidence:

- Reddit discussion favoring a richer interactive property page alongside static materials: https://www.reddit.com/r/RealEstatePhotography/comments/1bghe1t
- Reddit discussion favoring print collateral paired with an online brochure/link: https://www.reddit.com/r/RealEstatePhotography/comments/kt5nsi

The Reddit signal supports the paired print-plus-responsive approach, but it does not override the CARR facts-only, one-property-per-page, rights, confidentiality, accessibility, or human-publication requirements.

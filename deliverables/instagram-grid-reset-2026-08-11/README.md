# CARR Instagram grid reset — August 11, 2026

Nine review-ready Instagram concepts built to break the repeated navy-card grid. The deliverable intentionally uses **one** dark all-type cover (`09`) and eight distinct cover systems.

## What is included

- `index.html` — interactive review board. Open normally for the 3×3 grid. Add `?card=1` through `?card=9` to render a single 1080×1350 cover.
- `captions.md` — Instagram captions, alt text, job tags, factual source note, and footage requirements.
- `rendered/` — generated PNGs after running the commands below.

## Visual rotation

| Posts | Cover system |
| --- | --- |
| 01 | Cream editorial lease annotation |
| 02 | Split-document collage |
| 03 | Orange timing dial |
| 04 | Data visualization |
| 05 | Myth / reality split |
| 06 | Local-market route map |
| 07 | Field Reel cover |
| 08 | Stepped decision timeline |
| 09 | The one intentional CARR navy statement |

## Honest production status

Posts 07 and any future property-led posts need real, cleared local footage. No local photography was fabricated, and no generic doctor stock was used. The supplied Reel cover is a finished thumbnail treatment, not a substitute for the footage.

## Render

```bash
cd /Users/booko/carr-system/deliverables/instagram-grid-reset-2026-08-11
mkdir -p rendered
node render.mjs
```

The renderer creates `card-01.png` through `card-09.png`, plus `grid-preview.png`.

## Review gate

This is a review-draft package only. It does not schedule or publish anything. Before scheduling, lint each final caption, use actual footage for the Reel, vision-check all exports, and obtain Joe’s approval of the full batch.

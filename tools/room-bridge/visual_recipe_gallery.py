"""Render a self-contained, CARR-owned visual recipe comparison gallery."""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import design_kernel
import visual_recipe_library as recipes


ROOT = Path(__file__).resolve().parents[2]


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _sample(recipe_id: str) -> str:
    common = '<p class="sample-title">Baldwin County urgent-care search</p><p class="sample-meta">Synthetic comparison content · 3 submarkets · observed 09:20 CT</p>'
    bodies = {
        "recipe:operational-command-center": '<div class="sample-queue"><b>Needs attention</b><span class="state">! 2 evidence checks due</span><span>15 active candidates</span><span>Next: compare Eastern Shore</span></div>',
        "recipe:spatial-market-map": '<div class="sample-map"><i>A</i><i>B</i><i>C</i><b>10-minute field</b></div><p class="sample-note">A · Eastern Shore · source vintage shown</p>',
        "recipe:evidence-timeline": '<ol class="sample-timeline"><li><b>09:20</b> demographic source refreshed</li><li><b>09:05</b> route check captured</li><li><b>Yesterday</b> client criteria confirmed</li></ol>',
        "recipe:executive-briefing": '<div class="sample-answer"><b>Decision:</b> inspect Eastern Shore first.<span>Why: access, demand signal, and client criteria align.</span></div><div class="sample-matrix"><span>Access</span><b>strong</b><span>Evidence</span><b>current</b></div>',
        "recipe:client-presentation": '<div class="sample-story"><b>Where the search stands</b><span>Three areas fit the stated patient-access goal.</span><span>Next meeting: compare the two leading options.</span></div>',
        "recipe:dense-analytical-workstation": '<div class="sample-table"><b>Area</b><b>Access</b><b>Freshness</b><span>Eastern Shore</span><span>8.4</span><span>current</span><span>Foley</span><span>7.9</span><span>review</span><span>Daphne</span><span>7.5</span><span>unknown</span></div>',
        "recipe:ambient-workspace": '<div class="sample-ambient"><b>Welcome back</b><span>Two changes since your last review.</span><span class="state">○ calm, current orientation</span></div>',
        "recipe:mobile-field-mode": '<div class="sample-mobile"><b>At the property</b><span>Eastern Shore · 10:18 CT</span><button type="button">Capture observation</button></div>',
        "recipe:print-export-artifact": '<div class="sample-print"><b>SEARCH BRIEF · 24 AUG 2026</b><span>Purpose: compare submarkets</span><span>Sources and as-of times follow</span></div>',
        "recipe:comparison-review": '<div class="sample-compare"><span>Option A</span><span>Option B</span><b>same source content</b><b>same source content</b></div>',
    }
    return common + bodies[recipe_id]


def render(library: dict, kernel: dict) -> str:
    value = recipes.validate_library(library, kernel)
    library_digest = recipes.digest(value)
    cards = []
    for index, recipe in enumerate(value["recipes"], 1):
        jobs = " · ".join(recipe["jobs"])
        cards.append(f'''<article class="recipe-card recipe-{esc(recipe["recipe_id"].split(":", 1)[1])}" data-recipe-id="{esc(recipe["recipe_id"])}">
  <header><span class="recipe-number">{index:02d}</span><div><h2>{esc(recipe["label"])}</h2><p>{esc(recipe["fit_statement"])}</p></div></header>
  <div class="preview" aria-label="Synthetic visual direction preview">{_sample(recipe["recipe_id"])}</div>
  <dl><div><dt>Best for</dt><dd>{esc(jobs)}</dd></div><div><dt>Density / motion</dt><dd>{esc(recipe["information_density"])} / {esc(recipe["motion_posture"].replace("_", " "))}</dd></div><div><dt>Avoid</dt><dd>{esc("; ".join(recipe["exclusions"]))}</dd></div></dl>
  <button class="choose" type="button" aria-pressed="false" data-recipe="{esc(recipe["recipe_id"])}">Preview selection</button>
</article>''')
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CARR Visual Recipe Gallery</title>
<style>
:root{{--primitive-brand-navy:#002F6C;--primitive-brand-navy-deep:#00224D;--primitive-brand-orange:#F57F29;--paper:#F4EDDF;--card:#FFFAF0;--field:#F7F0E3;--line:#DDD1BC;--ink:#172235;--muted:#5F6B7B;--quiet:#87909D;--risk:#B63A2F;--risk-bg:#F9E7E2;--warn:#9A6516;--warn-bg:#F9EFD9;--good:#2D7136;--blue-bg:#E7EEF8;--shadow:0 16px 48px rgba(0,31,73,.16);--touch-min:44px;--surface-canvas:var(--paper);--surface-raised:var(--card);--surface-field:var(--field);--content-primary:var(--ink);--content-secondary:var(--muted);--border-subtle:var(--line);--action-primary:var(--primitive-brand-navy);--action-focus:var(--primitive-brand-orange);--status-risk:var(--risk);--status-risk-surface:var(--risk-bg);--status-warning:var(--warn);--status-warning-surface:var(--warn-bg);--status-good:var(--good);--status-information-surface:var(--blue-bg);--component-card-background:var(--surface-raised);--component-card-border:var(--border-subtle);--component-card-shadow:var(--shadow);--component-control-min-size:var(--touch-min);--component-control-focus-outline:3px solid var(--action-focus);--component-control-focus-offset:3px;font:15px system-ui,-apple-system,sans-serif}}
:root[data-theme="dark"]{{--paper:#0D192B;--card:#14243B;--field:#182B46;--line:#2A3D58;--ink:#EDF3FB;--muted:#B0BFD2;--quiet:#7D8DA4;--risk:#EF8A7C;--risk-bg:#3B211F;--warn:#E2BD6F;--warn-bg:#342C18;--good:#83CB8D;--blue-bg:#1B3151;--shadow:0 18px 55px rgba(0,34,77,.4)}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--surface-canvas);color:var(--content-primary);line-height:1.42}}button{{font:inherit;color:inherit}}button:focus-visible{{outline:var(--component-control-focus-outline);outline-offset:var(--component-control-focus-offset)}}main{{width:min(1500px,100%);padding:clamp(12px,3vw,36px);margin:auto;overflow-wrap:anywhere}}.masthead{{display:grid;grid-template-columns:1fr auto;gap:18px;align-items:start;border-bottom:1px solid var(--border-subtle);padding-bottom:18px}}h1{{margin:0;font-size:clamp(1.65rem,3.5vw,2.8rem);letter-spacing:-.04em}}h2{{margin:0;font-size:1.1rem}}p{{margin:.35rem 0;color:var(--content-secondary)}}.masthead p{{max-width:76ch}}.theme{{min-height:var(--component-control-min-size);padding:0 14px;border:1px solid var(--component-card-border);border-radius:999px;background:var(--component-card-background)}}.notice{{display:flex;gap:10px;align-items:flex-start;margin:18px 0;padding:12px 14px;border-left:4px solid var(--action-primary);background:var(--status-information-surface)}}.notice b{{white-space:nowrap}}.state{{font-weight:700;color:var(--status-good)}}.gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:16px;align-items:stretch}}.recipe-card{{display:flex;flex-direction:column;min-width:0;border:1px solid var(--component-card-border);border-radius:var(--radius,14px);padding:14px;background:var(--component-card-background);box-shadow:var(--component-card-shadow)}}.recipe-card>header{{display:flex;gap:10px;align-items:flex-start;min-height:72px}}.recipe-number{{font-weight:800;color:var(--action-primary);font-variant-numeric:tabular-nums;white-space:nowrap}}.preview{{flex:1;min-height:210px;margin:10px 0;padding:12px;border:1px solid var(--border-subtle);border-radius:10px;background:var(--surface-field);display:flex;flex-direction:column;gap:9px;overflow:hidden}}.sample-title{{margin:0;color:var(--content-primary);font-size:.95rem;font-weight:800}}.sample-meta{{margin:0;font-size:.72rem}}.sample-queue,.sample-answer,.sample-story,.sample-ambient,.sample-mobile,.sample-print{{display:grid;gap:8px;padding:12px;border:1px solid var(--border-subtle);background:var(--surface-raised)}}.sample-queue{{border-left:5px solid var(--status-warning)}}.sample-map{{position:relative;display:grid;place-items:center;min-height:112px;border:1px solid var(--border-subtle);background:var(--surface-raised)}}.sample-map:before,.sample-map:after{{content:"";position:absolute;border:1px solid var(--border-subtle);transform:rotate(35deg);width:90%;height:1px}}.sample-map:after{{transform:rotate(-22deg)}}.sample-map i{{position:relative;z-index:1;display:grid;place-items:center;width:30px;height:30px;border-radius:999px;background:var(--action-primary);color:var(--surface-raised);font-style:normal;font-weight:800}}.sample-map i:nth-child(1){{transform:translate(-55px,22px)}}.sample-map i:nth-child(2){{transform:translate(8px,-28px)}}.sample-map i:nth-child(3){{transform:translate(55px,10px)}}.sample-map b{{position:absolute;bottom:10px;color:var(--content-secondary);font-size:.72rem}}.sample-note{{font-size:.8rem}}.sample-timeline{{display:grid;gap:14px;margin:0;padding:0 0 0 20px;border-left:3px solid var(--action-primary)}}.sample-timeline li{{padding-left:4px}}.sample-matrix,.sample-table,.sample-compare{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--border-subtle);border:1px solid var(--border-subtle)}}.sample-matrix>* ,.sample-compare>*{{padding:8px;background:var(--surface-raised)}}.sample-table{{grid-template-columns:1.5fr 1fr 1fr}}.sample-table>*{{padding:6px;background:var(--surface-raised);font-size:.78rem}}.sample-story{{margin:auto 12px;padding:18px;line-height:1.7;border-bottom:5px solid var(--action-primary)}}.sample-ambient{{margin:auto 8px;border-radius:18px;box-shadow:var(--shadow);padding:20px}}.sample-mobile{{margin:auto;max-width:220px;border-radius:20px;padding:18px;box-shadow:var(--shadow)}}.sample-mobile button{{min-height:var(--component-control-min-size);border:0;background:var(--action-primary);color:var(--surface-raised);border-radius:8px;font-weight:700}}.sample-print{{margin:12px 0;border:2px solid var(--content-primary);font-family:ui-monospace,monospace}}.sample-compare{{grid-template-columns:1fr 1fr}}dl{{display:grid;gap:7px;margin:0 0 12px;font-size:.79rem}}dl div{{display:grid;grid-template-columns:110px 1fr;gap:8px}}dt{{font-weight:800;color:var(--content-secondary)}}dd{{margin:0}}.choose{{min-height:var(--component-control-min-size);padding:0 12px;border:1px solid var(--action-primary);border-radius:8px;background:transparent;color:var(--action-primary);font-weight:800}}.choose[aria-pressed="true"]{{background:var(--action-primary);color:var(--surface-raised)}}.receipt{{position:sticky;bottom:10px;margin-top:16px;padding:12px 14px;border:1px solid var(--component-card-border);border-radius:10px;background:var(--component-card-background);box-shadow:var(--component-card-shadow)}}.receipt p{{margin:0}}.provenance{{margin-top:18px;color:var(--content-secondary);font-size:.75rem}}@media(max-width:620px){{.masthead{{grid-template-columns:1fr}}.theme{{width:max-content}}.gallery{{grid-template-columns:1fr}}.recipe-card>header{{min-height:0}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important;scroll-behavior:auto!important}}}}
</style></head><body><main data-library-id="{esc(value["library_id"])}" data-library-digest="{esc(library_digest)}">
<header class="masthead"><div><h1>Visual Recipe Gallery</h1><p>Ten CARR-owned presentation archetypes, shown with the same synthetic search content so Joe and Dell can compare the visual register—not a different underlying story.</p></div><button class="theme" type="button" aria-pressed="false">Toggle dark mode</button></header>
<aside class="notice" data-non-color-status><b class="state">Human choice required</b><span>Choose 2–4 candidate recipes for a future surface, inspect them, then record the selected recipe and rationale. A recipe cannot change authority, truth, accessibility, tokens, or promotion status.</span></aside>
<section class="gallery" aria-label="CARR visual recipe comparisons">{"".join(cards)}</section>
<section class="receipt" aria-live="polite"><p id="selection">No recipe preview selected. These controls only make the comparison visible; a production surface records the human selection with an exact recipe digest.</p></section>
<footer class="provenance">Library {esc(value["version"])} · {esc(library_digest)} · CARR-owned general visual patterns · synthetic comparison content only.</footer>
</main><script>
const root=document.documentElement, selection=document.querySelector('#selection');
document.querySelector('.theme').addEventListener('click',event=>{{const dark=root.dataset.theme!=='dark';root.dataset.theme=dark?'dark':'light';event.currentTarget.setAttribute('aria-pressed',String(dark));event.currentTarget.textContent=dark?'Use light mode':'Toggle dark mode';}});
document.querySelectorAll('.choose').forEach(button=>button.addEventListener('click',()=>{{document.querySelectorAll('.choose').forEach(item=>item.setAttribute('aria-pressed','false'));button.setAttribute('aria-pressed','true');selection.textContent=`Preview selected: ${{button.dataset.recipe.replace('recipe:','').replaceAll('-',' ')}}. Human confirmation and rationale are still required; no automatic winner was chosen.`;}}));
</script></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the self-contained CARR visual recipe gallery")
    parser.add_argument("--library", type=Path, default=ROOT / "design" / "carr-visual-recipe-library.v1.json")
    parser.add_argument("--kernel", type=Path, default=ROOT / "design" / "carr-design-kernel.v1.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(render(recipes.load(args.library), recipes.load(args.kernel)), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Render the study with local CSS and native disclosure controls."""
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "study-draft.md"
OUT = HERE / "study.html"
TOKEN = re.compile(r"(`[^`]+`|\*\*.+?\*\*|\[[^\]]+\]\(https?://[^)]+\))")


def inline(text):
    parts = TOKEN.split(text)
    rendered = []
    for part in parts:
        if part.startswith("`") and part.endswith("`"):
            rendered.append("<code>" + escape(part[1:-1]) + "</code>")
        elif part.startswith("**") and part.endswith("**"):
            rendered.append("<strong>" + inline(part[2:-2]) + "</strong>")
        elif part.startswith("[") and "](" in part:
            label, url = part[1:-1].split("](", 1)
            rendered.append('<a href="' + escape(url, quote=True) + '">' + escape(label) + "</a>")
        else:
            rendered.append(escape(part))
    return "".join(rendered)


def block(text):
    if text.startswith("|"):
        rows = [line.strip().strip("|").split("|") for line in text.splitlines()]
        headings = "".join("<th scope=\"col\">" + inline(item.strip()) + "</th>" for item in rows[0])
        body = "".join("<tr>" + "".join("<td>" + inline(item.strip()) + "</td>" for item in row) + "</tr>"
                       for row in rows[2:])
        return '<div class="table-wrap" tabindex="0"><table><thead><tr>' + headings + \
            "</tr></thead><tbody>" + body + "</tbody></table></div>"
    return "<p>" + inline(text.replace("\n", " ")) + "</p>"


def blocks(text):
    return [part.strip() for part in text.strip().split("\n\n") if part.strip()]


def disclosure(label, parts, opened=False):
    return ('<details' + (' open' if opened else '') + '><summary>' + escape(label) +
            '</summary><div class="details-body">' + ''.join(block(part) for part in parts) + '</div></details>')


chunks = re.split(r"\n## ", SOURCE.read_text())
intro = chunks[0].split("\n\n", 1)[1]
intro_blocks = blocks(intro)
sections = [(chunk.split("\n", 1)[0], chunk.split("\n", 1)[1]) for chunk in chunks[1:]]
labels = ["Metric definitions", "Delivery paths", "Rule noise & gold audit", "Methods & sources",
          "Two selector designs", "Evidence & limitations"]
ids = ["metrics", "paths", "rules", "methods", "designs", "evidence"]
nav = ''.join('<a href="#' + rid + '">' + escape(label) + '</a>' for rid, label in zip(ids, labels))
main = '<section class="finding"><h2>The paths accumulate candidates without checking the full condition</h2>' + \
    ''.join(block(part) for part in intro_blocks[1:]) + '</section>'
method_labels = ["Structured tool, verb, path, and action predicates", "Rule-specific negative triggers",
                 "Per-rule thresholds", "Calibration and rare rules", "A small local text classifier",
                 "Retrieve, then rerank; embeddings", "Deduplicate verified context",
                 "One-line pointers and full-text availability", "Model admission and call limits"]
for number, ((title, text), rid) in enumerate(zip(sections, ids), 1):
    parts = blocks(text)
    content = ''
    if rid == "paths":
        content = ''.join(block(part) for part in parts[:2])
        content += '<aside class="callout"><b>The current Jev path is advisory.</b> Its high-score suggestions do not authorize rule delivery. The operative code returns an empty authoritative selection.</aside>'
        content += disclosure("Implementation details for every delivery path", parts[2:])
    elif rid == "methods":
        content = '<p>Start with local action predicates. Test a small local classifier when prompt intent needs broader coverage. Fit features and thresholds on TRAIN only, and retain independently labelled real turns.</p>'
        content += ''.join(disclosure(label, [part]) for label, part in zip(method_labels, parts))
    elif rid == "designs":
        content = block(parts[0]) + block(parts[1])
        content += '<div class="status"><span class="status-dot"></span>First predicate candidate rejected. Final calibrated counterfactual:82.3% precision and 82.4% availability; see final.html.</div>'
        content += disclosure("Shadow comparison contract and independent health labels", parts[2:])
    elif rid == "evidence":
        content = block(parts[0]) + block(parts[1])
        content += disclosure("Source revisions, reconstruction, and RED/GREEN checks", parts[2:])
    else:
        content = ''.join(block(part) for part in parts)
    main += '<section class="report-section" id="' + rid + '"><div class="section-kicker">' + str(number).zfill(2) + \
        '</div><h2>' + escape(title) + '</h2>' + content + '</section>'

source_urls = [
    ("Anthropic hook guide", "https://code.claude.com/docs/en/hooks-guide"),
    ("Scikit-learn decision thresholds", "https://scikit-learn.org/stable/modules/classification_threshold.html"),
    ("Scikit-learn probability calibration", "https://scikit-learn.org/stable/modules/calibration.html"),
    ("Official text classification tutorial, pinned 1.4.2", "https://raw.githubusercontent.com/scikit-learn/scikit-learn/1.4.2/doc/tutorial/text_analytics/working_with_text_data.rst"),
    ("SentenceTransformers retrieve and rerank", "https://www.sbert.net/examples/sentence_transformer/applications/retrieve_rerank/README.html"),
    ("SentenceTransformers cross-encoder applications", "https://www.sbert.net/examples/cross_encoder/applications/README.html"),
]
source_links = ''.join('<li><a href="' + escape(url, quote=True) + '">' + escape(label) + '</a></li>'
                       for label, url in source_urls)
css = r"""
:root{--navy:#0b2037;--navy2:#15334f;--orange:#ec6c2a;--ink:#e6edf6;--muted:#adc0d5;--line:#2d4761;--paper:#07111e;--white:#0d2033;--amber:#142c42}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:#ff9b57;text-underline-offset:3px;overflow-wrap:anywhere}a:hover{color:#ffc093}a:focus-visible,summary:focus-visible,.table-wrap:focus-visible{outline:3px solid var(--orange);outline-offset:4px}.topbar{background:var(--navy);color:#c3d0df;font-size:12px;letter-spacing:.13em;text-transform:uppercase;padding:18px max(24px,calc((100vw - 1140px)/2));border-bottom:1px solid #33506b}.topbar b{color:white;font-size:16px;margin-right:14px;letter-spacing:.09em}.hero{background:var(--navy);color:white;padding:46px max(24px,calc((100vw - 1140px)/2)) 44px;border-bottom:5px solid var(--orange)}.eyebrow{color:#ffac7e;font-size:12px;text-transform:uppercase;letter-spacing:.13em;font-weight:750}.hero h1{font-size:clamp(30px,4vw,46px);line-height:1.15;letter-spacing:-.025em;font-weight:750;max-width:900px;margin:15px 0 24px}.hero .lead{max-width:940px;color:#dde7f2;font-size:18px;line-height:1.7;margin:0}.hero code{background:#28425c;color:white}.container{max-width:1188px;margin:0 auto;padding:30px 24px 54px}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin-bottom:28px}.stat{background:var(--white);border:1px solid var(--line);border-top:3px solid var(--navy);border-radius:5px;padding:20px;box-shadow:0 3px 9px #142f4810}.stat.audit{border-top-color:var(--orange)}.stat-label{font-size:12px;font-weight:750;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}.stat-value{font-size:38px;line-height:1.2;font-weight:780;letter-spacing:-.035em;color:var(--ink);margin:8px 0}.stat-foot{font-size:12px;color:var(--muted);line-height:1.55}.nav{display:flex;gap:8px;flex-wrap:wrap;padding:0 0 26px}.nav a{padding:7px 12px;font-size:12px;text-decoration:none;border:1px solid #cdd8e3;border-radius:4px;background:var(--white);font-weight:650;color:var(--ink)}.nav a:hover{border-color:var(--orange);background:var(--amber)}.finding,.report-section,.source-footer{background:var(--white);border:1px solid var(--line);border-radius:6px;padding:32px 36px;margin-bottom:23px;box-shadow:0 2px 8px #15334f06}.finding{border-left:5px solid var(--orange)}h2{font-size:24px;line-height:1.3;letter-spacing:-.018em;margin:0 0 22px;color:var(--ink)}p{margin:0 0 18px;max-width:1020px}p:last-child{margin-bottom:0}strong{font-weight:750}code{font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#17334d;padding:2px 5px;border-radius:3px;overflow-wrap:anywhere}p code{word-break:break-word}.section-kicker{font-size:11px;letter-spacing:.08em;font-weight:800;color:var(--orange);margin-bottom:8px}.table-wrap{overflow-x:auto;margin:23px 0 24px;border:1px solid var(--line);border-radius:4px}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.55}th{text-align:left;background:var(--navy);color:#fff;padding:13px 15px;font-weight:650;vertical-align:bottom}td{padding:14px 15px;border-bottom:1px solid var(--line);vertical-align:top}tbody tr:nth-child(even){background:#10283e}tbody tr:last-child td{border-bottom:0}td:first-child{font-weight:650;color:var(--ink);min-width:165px}td:nth-child(2),td:nth-child(3),td:nth-child(4){font-variant-numeric:tabular-nums}.callout{background:var(--amber);border-left:3px solid var(--orange);padding:16px 20px;font-size:14px;margin:22px 0}.callout b{color:var(--ink)}details{border:1px solid var(--line);border-radius:4px;margin:12px 0;background:#fff}summary{cursor:pointer;color:var(--ink);font-weight:700;font-size:14px;padding:15px 18px;line-height:1.5;background:#112a40}summary::marker{color:var(--orange)}details[open] summary{border-bottom:1px solid var(--line)}.details-body{padding:21px 23px;font-size:14px;line-height:1.8}.details-body p{max-width:980px}.status{display:flex;align-items:center;gap:10px;background:var(--amber);padding:14px 18px;border:1px solid var(--line);border-radius:4px;font-size:13px;font-weight:650;color:#ffba8c;margin:22px 0}.status-dot{width:9px;height:9px;background:var(--orange);border-radius:50%;flex:none}.source-footer h2{font-size:20px;margin-bottom:10px}.source-footer p,.source-footer li{font-size:13px;line-height:1.8}.source-footer ul{columns:2;column-gap:35px;padding-left:20px;margin:16px 0 23px}.source-footer li{break-inside:avoid;margin-bottom:6px}.receipt{border-top:1px solid var(--line);padding-top:18px;color:var(--muted);font-size:12px;overflow-wrap:anywhere}.receipt a{font-weight:650}.footer{color:var(--muted);font-size:12px;text-align:center;margin:28px 0 0}
@media(max-width:850px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.finding,.report-section,.source-footer{padding:25px}.source-footer ul{columns:1}table{min-width:720px}}
@media(max-width:480px){.container{padding:23px 14px}.hero,.topbar{padding-left:20px;padding-right:20px}.hero{padding-top:34px}.hero .lead{font-size:16px}.stats{gap:10px}.stat{padding:16px 13px}.stat-value{font-size:32px}.stat-label{font-size:10px}.stat-foot{font-size:11px}.finding,.report-section,.source-footer{padding:22px 18px}.nav{gap:6px}.nav a{padding:6px 9px}h2{font-size:21px}.details-body{padding:18px 16px}.status{align-items:flex-start}}
@media print{body{background:var(--white);font-size:11px}.topbar,.hero{background:var(--white);color:var(--ink);padding:12px 0}.hero .lead{color:var(--ink);font-size:13px}.hero h1{font-size:27px}.container{padding:15px 0;max-width:none}.nav{display:none}.stats{gap:8px}.stat{padding:10px;box-shadow:none}.stat-value{font-size:24px}.finding,.report-section,.source-footer{box-shadow:none;padding:18px 0;border:0;border-bottom:1px solid var(--line);border-radius:0}.table-wrap{overflow:visible}table{min-width:0;font-size:10px}th{background:#e5ebf1;color:var(--ink)}td,th{padding:7px 8px}tr{break-inside:avoid}details>summary{background:var(--white)}details>.details-body{display:block!important;padding:14px}details{break-inside:avoid}a{color:var(--ink)}.source-footer ul{columns:1}.footer{font-size:10px}}
"""
html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>Rule delivery precision study | CARR</title><style>' + css + '</style></head><body>'
html += '<div class="topbar"><b>CARR</b> Engineering / Rule delivery / 05 October 2026</div><header class="hero"><div class="eyebrow">Precision study · Baseline, diagnosis, and selection designs</div><h1>Why three of four delivered rules do not apply</h1><p class="lead">' + inline(intro_blocks[0]) + '</p></header>'
html += '<main class="container"><div class="stats" aria-label="Original baseline and separate proposed audit correction">'
for label, value, foot, kind in [
    ("Original JIT precision", "27.1%", "71 applicable / 262 newly delivered<br>Frozen 72-turn test split", ""),
    ("By-turn availability", "81.3%", "583 available / 717 applicable<br>Assumes full text stays in context", ""),
    ("If gold corrections accepted", "31.3%", "82 applicable / 262 newly delivered<br>11 proposed labels; original stays frozen", "audit"),
    ("Full boot plus rule index", "34,845", "Estimated startup tokens<br>71 full statements + 199-rule index", ""),
]:
    html += '<div class="stat ' + kind + '"><div class="stat-label">' + label + '</div><div class="stat-value">' + value + '</div><div class="stat-foot">' + foot + '</div></div>'
html += '</div><nav class="nav" aria-label="Report sections">' + nav + '</nav>' + main
html += '<aside class="source-footer"><h2>Primary sources read in full</h2><p>Full-read status applies to each substantive page body. It does not extend to linked pages. Partial references are explicitly excluded in the evidence section above.</p><ul>' + source_links + '</ul><div class="receipt">Supporting artifacts: <a href="source-map.json">all-rule metrics and source hashes</a> · <a href="gold-audit.json">pair-level proposed gold corrections</a> · <a href="shadow-selftest-evidence.json">RED/GREEN checks</a> · <a href="study-draft.md">complete study text</a><br>Source revision <code>b4dc32fde6c07be4b19497ba0a14e1fcf64c7237</code>. No candidate TEST scoring by this study agent. No model-selector calls. Shadow comparison is off by default.</div></aside><p class="footer">Completed study. The final report contains the frozen winner and held-out before/after results.</p></main></body></html>'
OUT.write_text(html, encoding="utf-8")


class Check(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.external_assets, self.data = [], [], []
        self.tables = self.disclosures = self.scripts = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "a": self.links.append(attributes.get("href"))
        if tag in {"script", "link", "img", "iframe"}:
            self.external_assets.append((tag, attributes))
        self.tables += tag == "table"
        self.disclosures += tag == "details"
        self.scripts += tag == "script"

    def handle_data(self, value):
        self.data.append(value)


check = Check()
check.feed(html)
assert check.tables == 3 and check.disclosures == 12
assert not check.external_assets and check.scripts == 0
assert set(url for _, url in source_urls).issubset(check.links)
assert all('#' + rid in check.links for rid in ids)
assert all(marker in html for marker in ["71 TP and 191 FP", "82/262 = 31.298%", "594/728 = 81.593%", "140 TP/336 FP = 29.412%", "1,353/1,690 = 80.059%", "completed TRAIN calibration", "no longer exists on disk"])
plain = "".join(check.data)
for paragraph in intro_blocks + [part for _, text in sections for part in blocks(text) if not part.startswith('|')]:
    text = re.sub(r"\[([^]]+)\]\(https?://[^)]+\)", r"\1", paragraph).replace('`','').replace('**','')
    assert text in plain, "A substantive paragraph was omitted"
print(f"Rendered {OUT.name}: {len(html.encode('utf-8')):,} bytes, {check.tables} tables, {check.disclosures} disclosures, all substantive paragraphs and six primary citations retained.")

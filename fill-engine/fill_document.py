#!/usr/bin/env python3
"""
fill_document.py — the generalized fill engine (ORDER 13(b), wave2-design 2c).

The vault's `DNA/Deal Management/fill-engine/fill_deal_template.py` filled ONE
shape: an xlsx by {cell: value} plus a LibreOffice recalc. This generalizes it
to the two shapes the document factory needs — docx slot filling and xlsx cell
filling — with a PDF render for either, and it never modifies a template in
place: every fill copies first.

  fill(template_path, out_path, edits)        -> out_path      (docx or xlsx)
  to_pdf(src_path, out_pdf_path)              -> out_pdf_path

`edits` is a list of {"where": <address>, "text": <string>}:

  docx   para:<i>                 the i-th body paragraph
         table:<t>:<r>:<c>        table t, row r, cell c
  xlsx   cell:<Sheet>!<A1>        a named sheet
         cell:<A1>                the active sheet

WHY TEXT-ONLY EDITS: branding is everything this engine must not break. A CARR
template carries its letterhead in header1.xml, its palette in theme1.xml and
its table styling in the table properties — none of which is touched here. The
engine rewrites the TEXT of an addressed run and deletes its siblings, so the
filled cell keeps the template's own font, size, weight and colour. Everything
not named in `edits` is byte-identical to the template, which is the property
ORDER 13 asks for and the reason no template is ever edited in place.

LibreOffice is required for the PDF render and for the xlsx recalc (the same
dependency the vault engine already had). It is NOT bundled: `soffice_path()`
looks in the three places it lives on a Mac and raises a plain-language error
naming the install command if it finds none.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

SOFFICE_CANDIDATES = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/local/bin/soffice",
    "/usr/bin/soffice",
]


class FillError(Exception):
    pass


def soffice_path() -> str:
    env = os.environ.get("CARR_SOFFICE")
    if env and os.path.exists(env):
        return env
    for p in SOFFICE_CANDIDATES:
        if os.path.exists(p):
            return p
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    raise FillError(
        "LibreOffice not found. The fill engine needs it for the PDF render and "
        "the xlsx recalc. Install it with:  brew install --cask libreoffice  "
        "(or set CARR_SOFFICE to the soffice binary)."
    )


# ---------------------------------------------------------------- addressing

_PARA = re.compile(r"^para:(\d+)$")
_CELL = re.compile(r"^table:(\d+):(\d+):(\d+)$")
_XL = re.compile(r"^cell:(?:(.+)!)?([A-Z]{1,3}\d{1,7})$")


def parse_address(where: str):
    m = _PARA.match(where)
    if m:
        return ("para", int(m.group(1)))
    m = _CELL.match(where)
    if m:
        return ("table", int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _XL.match(where)
    if m:
        return ("cell", m.group(1), m.group(2))
    raise FillError(f"unrecognised slot address {where!r} "
                    "(expected para:N, table:T:R:C, or cell:[Sheet!]A1)")


# ---------------------------------------------------------------- docx fill

def _unwrap_content_controls(el) -> None:
    """Replace every Word content control (w:sdt) in `el` with its own content.

    CARR's letterhead templates are built on content controls bound to a custom
    XML part: the lease LOI has one for the letter Date and two more for Broker
    Email and Broker Phone. python-docx does NOT see runs inside a control, so
    a naive fill leaves them standing: the first C-112 draft rendered with the
    template's stale 'October 27, 2023' and with 'first.last@carr.us' plus a
    live 'Click or tap here to enter text' placeholder sitting beside the value
    that was supposed to replace them. Found in the rendered PDF, not in the
    XML — which is the fill-engine workflow's own rule about reading the
    rendered document rather than a text extraction, earning its keep.

    Unwrapping is the correct semantic: a filled document is a document, not a
    form. The control's runs are kept exactly as they are, so the character
    formatting the control carried survives.
    """
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    while True:
        sdts = el.findall(f".//{W}sdt")
        if not sdts:
            return
        # deepest last: process one at a time and re-scan, so nesting is safe
        sdt = sdts[-1]
        parent = sdt.getparent()
        content = sdt.find(f"{W}sdtContent")
        idx = list(parent).index(sdt)
        if content is not None:
            for child in list(content):
                parent.insert(idx, child)
                idx += 1
        parent.remove(sdt)


def _set_paragraph_text(para, text: str) -> None:
    """Replace a paragraph's text, keeping the FIRST run's formatting.

    Word stores a paragraph as a sequence of runs; a template's placeholder is
    often split across several of them by spell-check or tracked-change history.
    Writing into run 0 and dropping the rest is the only way to guarantee the
    result reads as one clean value in the template's own typeface. A paragraph
    with no runs (an empty line) gets one created, inheriting the paragraph
    style rather than the document default.
    """
    _unwrap_content_controls(para._p)
    runs = para.runs
    if not runs:
        para.add_run(text)
        return
    runs[0].text = text
    for extra in runs[1:]:
        extra._element.getparent().remove(extra._element)


def _set_cell_text(cell, text: str) -> None:
    """Replace a table cell's text across the whole cell, first paragraph wins.

    Multi-line template cells (the LOI's Broker Commission cell is three
    sentences over one paragraph) collapse to the single filled value; extra
    paragraphs are removed so a stale second line cannot survive a fill.
    """
    _unwrap_content_controls(cell._tc)
    paras = cell.paragraphs
    _set_paragraph_text(paras[0], text)
    for extra in paras[1:]:
        extra._element.getparent().remove(extra._element)


def fill_docx(template: str, out: str, edits: list[dict]) -> str:
    import docx  # imported lazily so an xlsx-only run needs no python-docx

    if os.path.abspath(template) == os.path.abspath(out):
        raise FillError("refusing to fill a template in place; give a distinct out path")
    d = docx.Document(template)
    for e in edits:
        addr = parse_address(e["where"])
        text = "" if e.get("text") is None else str(e["text"])
        if addr[0] == "para":
            i = addr[1]
            if i >= len(d.paragraphs):
                raise FillError(f"{e['where']}: document has {len(d.paragraphs)} paragraphs")
            _set_paragraph_text(d.paragraphs[i], text)
        elif addr[0] == "table":
            _, t, r, c = addr
            if t >= len(d.tables):
                raise FillError(f"{e['where']}: document has {len(d.tables)} tables")
            tb = d.tables[t]
            if r >= len(tb.rows) or c >= len(tb.columns):
                raise FillError(f"{e['where']}: table {t} is {len(tb.rows)}x{len(tb.columns)}")
            _set_cell_text(tb.rows[r].cells[c], text)
        else:
            raise FillError(f"{e['where']}: cell addresses belong to xlsx templates")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    d.save(out)
    return out


# ---------------------------------------------------------------- xlsx fill

def fill_xlsx(template: str, out: str, edits: list[dict], recalc: bool = True) -> str:
    import openpyxl

    if os.path.abspath(template) == os.path.abspath(out):
        raise FillError("refusing to fill a template in place; give a distinct out path")
    wb = openpyxl.load_workbook(template)
    for e in edits:
        addr = parse_address(e["where"])
        if addr[0] != "cell":
            raise FillError(f"{e['where']}: paragraph/table addresses belong to docx templates")
        _, sheet, ref = addr
        ws = wb[sheet] if sheet else wb.active
        ws[ref] = e.get("text")
    wb.calculation.fullCalcOnLoad = True
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if not recalc:
        wb.save(out)
        return out
    # LibreOffice recalc, same trick the vault engine used: save, round-trip
    # through headless Calc so every formula holds a computed value the moment
    # the client opens it, then move the result into place.
    indir = tempfile.mkdtemp(prefix="carrfill_in_")
    outdir = tempfile.mkdtemp(prefix="carrfill_out_")
    try:
        src = os.path.join(indir, "book.xlsx")
        wb.save(src)
        subprocess.run(
            [soffice_path(), "--headless", "--calc", "--convert-to",
             "xlsx:Calc MS Excel 2007 XML", "--outdir", outdir, src],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "HOME": tempfile.gettempdir()})
        produced = os.path.join(outdir, "book.xlsx")
        if not os.path.exists(produced):
            raise FillError("LibreOffice produced no recalculated workbook")
        shutil.move(produced, out)
    finally:
        shutil.rmtree(indir, ignore_errors=True)
        shutil.rmtree(outdir, ignore_errors=True)
    return out


def fill(template: str, out: str, edits: list[dict]) -> str:
    ext = os.path.splitext(template)[1].lower()
    if ext == ".docx":
        return fill_docx(template, out, edits)
    if ext in (".xlsx", ".xlsm"):
        return fill_xlsx(template, out, edits)
    raise FillError(f"no filler for {ext} templates")


# ---------------------------------------------------------------- pdf render

def to_pdf(src: str, out_pdf: str) -> str:
    """Headless LibreOffice render. The client-facing artifact is ALWAYS the PDF
    (tool-contracts §3: clients get PDFs, never working files) so this is not an
    optional step — a document with no PDF is not finished."""
    outdir = tempfile.mkdtemp(prefix="carrpdf_")
    try:
        subprocess.run(
            [soffice_path(), "--headless", "--convert-to", "pdf", "--outdir", outdir, src],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "HOME": tempfile.gettempdir()})
        produced = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
        if not os.path.exists(produced):
            raise FillError(f"LibreOffice produced no PDF for {src}")
        os.makedirs(os.path.dirname(os.path.abspath(out_pdf)), exist_ok=True)
        shutil.move(produced, out_pdf)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
    return out_pdf


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 4:
        print("usage: fill_document.py <template> <out> <edits.json> [--pdf <out.pdf>]")
        raise SystemExit(2)
    fill(sys.argv[1], sys.argv[2], json.load(open(sys.argv[3])))
    print("wrote", sys.argv[2])
    if "--pdf" in sys.argv:
        p = to_pdf(sys.argv[2], sys.argv[sys.argv.index("--pdf") + 1])
        print("wrote", p)

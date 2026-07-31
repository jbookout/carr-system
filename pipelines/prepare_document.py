#!/usr/bin/env python3
"""
prepare_document.py — the LOCAL half of the document factory (ORDER 13).

THE SPLIT, and why it is not a shortcut. `prepare-document` is an MCP verb in a
Cloudflare Worker: no filesystem, no LibreOffice, no OneDrive. So the verb owns
everything the RECORD layer can answer — resolve the deal, apply the reviewed
field map, decide every slot's value or mark it owed, create the `document` row
under the envelope with its event — and returns a fill PLAN. This script owns
everything only a machine standing in Joe's file system can do: copy the
template, write the slots, render the PDF, run the leak guard and the writing
lint, and file the result. Neither half re-derives the other's work; the plan is
the whole contract between them.

  # 1. get the plan (deployed Worker, or local-verb.mjs against a branch)
  # 2. produce the files:
  ./run.sh ... | .venv/bin/python pipelines/prepare_document.py plan.json
       [--route onedrive|staging] [--finish]

  --route staging   (default) writes into out/documents/ only
  --route onedrive  files working + PDF into the deal's OneDrive folder, unless
                    the writing lint returns a HARD finding — a HARD finding
                    blocks the OneDrive copy by design and says so
  --finish          with DATABASE_URL set, uploads the working file and the PDF
                    to the R2 archive under the self-enforced quota, writes their
                    attachment rows, and patches the document row (lint/leak
                    results, attachment pointers)
  --r2-dry-run      exercise the quota gate and report, upload nothing, write
                    nothing

WHICH FILE IS THE SENDABLE ONE (Joe's doctrine, 2026-07-31, superseding the old
blanket "clients get PDFs, never working files"): a LETTER goes to the listing
agent as the working .docx, because the counterparty edits and revises it and
that editing IS the negotiation; the PDF is the record and preview copy. A
SPREADSHEET goes out as the PDF so the formulas stay ours, and the working
workbook never leaves. Naming a sendable format is not permission to send:
Joe sends, and no verb in this system can.

Every outbound .docx is SCRUBBED before it routes — comments, tracked changes,
comment authors, custom properties, and the authorship metadata inherited from
CARR's corporate template (the C-112 draft carried a named individual's Windows
template path until this ran). Branding is untouched; see fill_document.py.

THE QUOTA (ORDER 20, Joe's requirement 2026-07-31) is a HARD cap, not an alert.
It lives in `system_config` under `r2.quota_gb` and DEFAULTS TO 8 GB when that
row is absent, which is under Cloudflare's 10 GB free tier on purpose. An upload
that would cross it is REFUSED: nothing uploads, nothing is deleted to make
room, the OneDrive copies stand, and the archive copy is recorded as owed on the
document row with the reason. See lib/r2_archive.py.

NOTHING IS EVER SENT. The output is a DRAFT for Joe to read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "fill-engine"))
sys.path.insert(0, os.path.join(REPO, "lib"))
from fill_document import fill, to_pdf, FillError, colored_runs, scrub_docx  # noqa: E402
import r2_archive as r2  # noqa: E402

VAULT = os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
ONEDRIVE_DEALS = os.environ.get(
    "CARR_ONEDRIVE_DEALS",
    "/Users/booko/Library/CloudStorage/OneDrive-CARR,Inc/Joe's Folder/Deals/Active Deals")
STAGING = os.path.join(REPO, "out", "documents")

# The leak guard's named checks. Each is a fact about CARR's own material that
# must never reach a client-facing file, and each fires on a signature rather
# than on a judgment call.
LEAK_PATTERNS = [
    (r"205-?643-?6555", "the placeholder phone number (standing data rule: never stored, never shipped)"),
    (r"INTERNAL[\s_-]?ONLY", "an internal-only marker"),
    (r"CommissionComparison|BaseRentCalculator", "the commission calculator, which never reaches a client"),
    (r"sf_commission_placeholder|sf_close_date_placeholder", "a Salesforce placeholder column name"),
]


def doc_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for r in t.rows:
                parts.extend(c.text for c in r.cells)
        return "\n".join(x for x in parts if x and x.strip())
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        parts = []
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and c.value.strip():
                        parts.append(c.value)
        return "\n".join(parts)
    return ""


def leak_guard(text: str, listing_side_names: list[str]) -> list[str]:
    findings = []
    for pat, why in LEAK_PATTERNS:
        if re.search(pat, text, re.I):
            findings.append(f"{why} (pattern {pat})")
    for name in listing_side_names:
        if name and re.search(re.escape(name), text, re.I):
            findings.append(f"a listing-side party name appears in a client-facing file: {name}")
    return findings


OWED_MARKER = re.compile(r"^\s*\[OWED:")


def color_check(path: str) -> list[dict]:
    """The no-coloured-text finish check (Joe's convention, 2026-07-31).

    Blue or red in a CARR template means "this gets replaced". A finished letter
    is all black. So after the fill, anything still coloured that is NOT one of
    our own red OWED markers is a finding: either a slot no field map covers, or
    template text that nobody has patched. It rides alongside the leak guard and
    the writing lint because it is the same class of check, a fact about the
    produced file rather than a judgment about it.

    It REPORTS rather than blocks, and that is deliberate. Several coloured runs
    are `carry` slots holding CARR's own standing language that the map declines
    to write (the base-year line, the commission language), so blocking would
    stop every LOI this factory will ever produce. The lint HARD gate is the one
    that blocks; this one tells Joe where to look.
    """
    if os.path.splitext(path)[1].lower() != ".docx":
        return []
    findings = []
    for r in colored_runs(path):
        if OWED_MARKER.match(r["text"]):
            continue                       # our own marker, red on purpose
        # SEEN IN THE C-112 RENDER, not reasoned about: the coloured text left
        # standing by `carry` slots is not merely coloured, it is UNFINISHED.
        # The Broker Commission cell reads "4% of the total base rent for the 10
        # year term | $10.00 per RSF", HVAC reads "Owner to pay for and install
        # | ensure". A pipe is the template author's way of writing "pick one",
        # and a document going to the listing agent with both alternatives still
        # in it is a worse failure than a colour. Flagged separately so it reads
        # as what it is.
        r = dict(r, unresolved_option=" | " in r["text"] or r["text"].strip().endswith("|"))
        findings.append(r)
    return findings


def run_lint(text: str, label: str) -> dict:
    """tools/writing-lint.py as the gate it already is. Exit 1 = HARD finding."""
    tmp = os.path.join(STAGING, f".lint-{label}.txt")
    os.makedirs(STAGING, exist_ok=True)
    with open(tmp, "w") as fh:
        fh.write(text)
    p = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "writing-lint.py"), tmp, "--surface", "proposal"],
        capture_output=True, text=True)
    os.remove(tmp)
    return {"hard": p.returncode != 0, "exit": p.returncode,
            "report": (p.stdout or "") + (p.stderr or "")}


def sha256_of(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def find_deal_folder(plan: dict) -> str | None:
    """Match Joe's EXISTING per-deal folder rather than minting a new one.

    His convention is one folder per deal under Active Deals, named the way he
    thinks of the deal, which is not always the client party name (C-112's folder
    is 'Gulf Coast Pelvic Health'; the party row says 'Gulf Coast Pelvic Floor').
    Matching by shared words beats matching by string equality, and a miss
    returns None so the caller reports it instead of creating a second folder
    beside the real one.
    """
    if not os.path.isdir(ONEDRIVE_DEALS):
        return None
    wanted = {plan["deal"].get("name"), plan["deal"].get("client_name"),
              plan["deal"].get("org_name")}
    tokens = set()
    for w in wanted:
        if w:
            tokens |= {t.lower() for t in re.findall(r"[A-Za-z]+", w) if len(t) > 2}
    best, best_score = None, 0
    for entry in os.listdir(ONEDRIVE_DEALS):
        full = os.path.join(ONEDRIVE_DEALS, entry)
        if not os.path.isdir(full) or entry.startswith("_") or entry.startswith("."):
            continue
        et = {t.lower() for t in re.findall(r"[A-Za-z]+", entry) if len(t) > 2}
        score = len(tokens & et)
        if score > best_score:
            best, best_score = full, score
    return best if best_score >= 2 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="the prepare-document plan JSON ('-' for stdin)")
    ap.add_argument("--route", choices=["staging", "onedrive"], default="staging")
    ap.add_argument("--finish", action="store_true",
                    help="archive to R2 + write attachment rows + patch the document row "
                         "(needs DATABASE_URL)")
    ap.add_argument("--r2-bucket", default="carr-documents")
    ap.add_argument("--r2-dry-run", action="store_true",
                    help="run the quota gate and report, upload nothing, write no rows")
    a = ap.parse_args()

    plan = json.load(sys.stdin if a.plan == "-" else open(a.plan))
    tmpl_rel = plan["template"]["source_path"]
    tmpl = os.path.join(VAULT, tmpl_rel)
    if not os.path.exists(tmpl):
        print(f"STOP: template not found at {tmpl}", file=sys.stderr)
        return 2
    ext = os.path.splitext(tmpl)[1].lower()
    os.makedirs(STAGING, exist_ok=True)
    working = os.path.join(STAGING, plan["basename"] + ext)
    pdf = os.path.join(STAGING, plan["basename"] + ".pdf")

    # ---- fill. The template is opened read-only and copied; nothing writes to it.
    before_sha, _ = sha256_of(tmpl)
    # The `owed` flag rides through to the engine on purpose: it is what decides
    # the colour Joe reads the draft by (black = real, red = still needs him).
    edits = [{"where": e["where"], "text": e["text"], "owed": bool(e.get("owed"))}
             for e in plan["edits"]]
    try:
        fill(tmpl, working, edits)
        to_pdf(working, pdf)
    except FillError as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 2
    after_sha, _ = sha256_of(tmpl)
    if before_sha != after_sha:
        print("STOP: the TEMPLATE changed on disk during the fill. That must never happen.", file=sys.stderr)
        return 2

    # ---- the sendable class, and the scrub that follows from it.
    # Joe's doctrine (2026-07-31), which supersedes the old blanket "clients get
    # PDFs, never working files": a LETTER goes to the listing agent as a .docx
    # so they can edit and revise it, and that editing is the negotiation. A
    # SPREADSHEET goes as a PDF exactly so the formulas stay ours. So the
    # outbound artifact is per template kind, not one rule for both, and the
    # scrub applies to whichever one is actually going to leave.
    sendable_role = "working" if ext == ".docx" else "pdf"
    scrub = None
    if sendable_role == "working" and ext == ".docx":
        scrub = scrub_docx(working)
        to_pdf(working, pdf)          # re-render so the record copy matches the scrubbed file

    text = doc_text(working)
    leaks = leak_guard(text, plan.get("listing_side_names", []))
    lint = run_lint(text, plan["basename"][:40])
    color_findings = color_check(working)

    blocked = bool(leaks) or lint["hard"]
    routed_to, route_note = STAGING, "staging only (--route staging)"
    if a.route == "onedrive":
        if blocked:
            route_note = ("BLOCKED from OneDrive: "
                          + ("leak guard findings; " if leaks else "")
                          + ("writing-lint HARD findings" if lint["hard"] else "")
                          + ". Staging copy only, per the ORDER 13 gate.")
        else:
            folder = find_deal_folder(plan)
            if not folder:
                route_note = ("no matching deal folder under Active Deals; staged instead of "
                              "creating a second folder beside the real one")
            else:
                for src in (working, pdf):
                    shutil.copy2(src, os.path.join(folder, os.path.basename(src)))
                routed_to, route_note = folder, "filed to the deal's existing OneDrive folder"

    out = {
        "document_id": plan["document_id"],
        "template": plan["template"]["slug"],
        "template_version": plan["template"]["template_version"],
        "template_unchanged": before_sha == after_sha,
        "working": working, "pdf": pdf,
        "working_sha256": sha256_of(working)[0], "working_bytes": sha256_of(working)[1],
        "pdf_sha256": sha256_of(pdf)[0], "pdf_bytes": sha256_of(pdf)[1],
        "slots_filled": len([e for e in plan["edits"] if not e.get("owed")]),
        "slots_owed": len(plan["owed"]),
        "slots_carried": len(plan["carried"]),
        "leak_findings": leaks,
        "lint_hard": lint["hard"], "lint_report": lint["report"],
        "sendable": {
            "role": sendable_role,
            "file": working if sendable_role == "working" else pdf,
            "not_sendable": pdf if sendable_role == "working" else working,
            "rule": ("Letters go to the listing agent as the WORKING .docx so they can edit and "
                     "revise it; the PDF is the record and preview copy."
                     if sendable_role == "working" else
                     "Spreadsheets go out as the PDF so the formulas stay ours; the working "
                     "workbook never leaves."),
            "human_gate": "Sendable names the format, not permission. Joe sends; no verb can."},
        "scrub": scrub,
        "color_findings": color_findings,
        "color_note": ("Joe's convention: blue or red marks text that gets replaced, and a letter "
                       "that goes out is all black. Filled values are forced black and OWED markers "
                       "are forced red. Anything else still coloured is listed above: it is either a "
                       "slot no map covers or template text nobody patched. Reported, not blocking "
                       "— several are 'carry' slots holding CARR's standing language on purpose."
                       + (" THIS FILE IS THE SENDABLE ONE, so every finding above is a colour the "
                          "listing agent would see. Read them before handing it over."
                          if sendable_role == "working" and color_findings else "")),
        "routed_to": routed_to, "route_note": route_note,
        "status": "draft",
        "human_gate": "DRAFT for Joe to review. Nothing was sent; no verb in this system can send.",
    }

    if a.finish:
        out["finish"] = finish_records(plan, out, a.r2_bucket, a.r2_dry_run)
    print(json.dumps(out, indent=2))
    return 0


def finish_records(plan: dict, out: dict, bucket: str, dry_run: bool = False) -> dict:
    """Upload the archive copies, write the attachment rows, patch the document.

    Pipeline-writes-direct, the ORDER 17 precedent: this is file-side bookkeeping
    that no verb can perform, because the bytes only exist on this Mac. The
    document row itself was created by the verb under the full envelope.

    ORDER 20 closed the half ORDER 13 had to leave owed. `attachment.r2_key` is
    `not null unique`, so the upload is what makes the row insertable: the object
    goes up FIRST, under the quota guard, and only then does a row claim it. If
    the quota refuses, no row is written and the owed note says so in Joe's
    words rather than in an error code.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"skipped": "DATABASE_URL not set"}
    import psycopg
    res = {"r2_bucket": bucket, "attachments": []}
    with psycopg.connect(url) as cn, cn.cursor() as cur:
        cur.execute("select id from actor where slug='joe'")
        actor = cur.fetchone()[0]

        cap, provenance = r2.quota_bytes(cn)
        led = r2.load_ledger(bucket)
        res["reconcile"] = r2.reconcile(led, cn, bucket)
        res["quota"] = {"cap_bytes": cap, "provenance": provenance}

        files = [("working", out["working"], out["working_sha256"], out["working_bytes"]),
                 ("pdf", out["pdf"], out["pdf_sha256"], out["pdf_bytes"])]
        att_ids, refusal = {}, None
        for role, path, sha, size in files:
            key = r2.object_key(plan["deal"].get("client_ref"), sha, path)
            try:
                up = r2.upload(path, key, sha, size, cap, provenance, led, bucket, dry_run)
            except r2.QuotaExceeded as q:
                refusal = q
                res["quota_refusal"] = q.message
                res["quota_detail"] = q.detail
                break
            res["attachments"].append({"role": role, **up})
            if dry_run:
                continue
            # One object, one attachment row. A row that already claims this key
            # is reused rather than duplicated, which is what makes the rerun a
            # no-op instead of a unique-violation.
            cur.execute("select id from attachment where r2_key=%s", (key,))
            row = cur.fetchone()
            if row:
                att_ids[role] = row[0]
                continue
            cur.execute(
                """insert into attachment (subject_type, subject_id, r2_key, filename, mime,
                     sha256, bytes, created_by)
                   values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                ("deal", plan["deal"]["id"], key, os.path.basename(path),
                 r2.mime_for(path), sha, size, actor))
            att_ids[role] = cur.fetchone()[0]

        if refusal is not None:
            note = (f"working: {out['working']} (sha256 {out['working_sha256'][:12]}) · "
                    f"pdf: {out['pdf']} (sha256 {out['pdf_sha256'][:12]}) · "
                    f"OWED: R2 archive copy refused by the self-enforced quota "
                    f"({refusal.detail['used_bytes']} of {refusal.detail['quota_bytes']} bytes used; "
                    f"this upload would have been over by {refusal.detail['over_by_bytes']}). "
                    f"The OneDrive copies stand; only the archive copy is missing.")
            cause = "files produced locally; R2 archive copy OWED (quota refusal)"
        else:
            note = (f"working: {out['working']} (sha256 {out['working_sha256'][:12]}) · "
                    f"pdf: {out['pdf']} (sha256 {out['pdf_sha256'][:12]}) · "
                    f"archived to R2 bucket {bucket}")
            cause = "files produced locally and archived to R2"

        if not dry_run:
            cur.execute(
                """update document set lint_passed=%s, leak_check_passed=%s, note=%s,
                     working_attachment=coalesce(%s, working_attachment),
                     pdf_attachment=coalesce(%s, pdf_attachment)
                   where id=%s""",
                (not out["lint_hard"], not out["leak_findings"], note,
                 att_ids.get("working"), att_ids.get("pdf"), plan["document_id"]))
            cur.execute(
                """insert into event (occurred_at, actor_id, verb, subject_type, subject_id, field,
                     new_value, cause, agent_rationale)
                   values (now(),%s,'prepare-document','deal',%s,'document.files',%s::jsonb,'automation_job',%s)""",
                (actor, plan["deal"]["id"],
                 json.dumps({"document_id": plan["document_id"],
                             "working_sha256": out["working_sha256"],
                             "pdf_sha256": out["pdf_sha256"],
                             "r2_keys": [a["key"] for a in res["attachments"]]}),
                 cause))
            cn.commit()
            res["document_patched"] = True
        res["attachments_owed"] = refusal is not None
        res["usage"] = r2.usage_summary(led, cap, provenance)
    return res


if __name__ == "__main__":
    raise SystemExit(main())

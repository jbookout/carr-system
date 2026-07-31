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
  --finish          with DATABASE_URL set, writes the attachment rows and patches
                    the document row (lint/leak results, attachment pointers)

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
from fill_document import fill, to_pdf, FillError  # noqa: E402

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
                    help="write attachment rows + patch the document row (needs DATABASE_URL)")
    ap.add_argument("--r2-bucket", default="carr-documents")
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
    edits = [{"where": e["where"], "text": e["text"]} for e in plan["edits"]]
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

    text = doc_text(working)
    leaks = leak_guard(text, plan.get("listing_side_names", []))
    lint = run_lint(text, plan["basename"][:40])

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
        "routed_to": routed_to, "route_note": route_note,
        "status": "draft",
        "human_gate": "DRAFT for Joe to review. Nothing was sent; no verb in this system can send.",
    }

    if a.finish:
        out["finish"] = finish_records(plan, out, a.r2_bucket)
    print(json.dumps(out, indent=2))
    return 0


def finish_records(plan: dict, out: dict, bucket: str) -> dict:
    """Write the attachment rows and patch the document row.

    Pipeline-writes-direct, the ORDER 17 precedent: this is file-side bookkeeping
    that no verb can perform, because the bytes only exist on this Mac. The
    document row itself was created by the verb under the full envelope.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"skipped": "DATABASE_URL not set"}
    import psycopg
    res = {"r2_bucket": bucket, "attachments": []}
    r2_ok = shutil.which("wrangler") is not None
    res["r2_uploaded"] = False
    res["r2_note"] = ("R2 upload not attempted by this run: uploading is a production write and "
                      "creating the bucket is a human tap. See the report.")
    with psycopg.connect(url) as cn, cn.cursor() as cur:
        cur.execute("select id from actor where slug='joe'")
        actor = cur.fetchone()[0]
        # attachment.r2_key is NOT NULL and UNIQUE. Until the bucket exists there
        # is no honest key to write, so the attachment rows are OWED rather than
        # faked with a local path pretending to be object storage.
        if not res["r2_uploaded"]:
            note = (f"working: {out['working']} (sha256 {out['working_sha256'][:12]}) · "
                    f"pdf: {out['pdf']} (sha256 {out['pdf_sha256'][:12]}) · "
                    f"OWED: R2 copy + attachment rows (no bucket yet; attachment.r2_key is NOT NULL)")
            cur.execute("update document set lint_passed=%s, leak_check_passed=%s, note=%s where id=%s",
                        (not out["lint_hard"], not out["leak_findings"], note, plan["document_id"]))
            cur.execute(
                """insert into event (occurred_at, actor_id, verb, subject_type, subject_id, field,
                     new_value, cause, agent_rationale)
                   values (now(),%s,'prepare-document','deal',%s,'document.files',%s::jsonb,'automation_job',%s)""",
                (actor, plan["deal"]["id"],
                 json.dumps({"document_id": plan["document_id"],
                             "working_sha256": out["working_sha256"],
                             "pdf_sha256": out["pdf_sha256"]}),
                 "files produced locally; R2 copy and attachment rows owed"))
            cn.commit()
            res["document_patched"] = True
            res["attachments_owed"] = True
    return res


if __name__ == "__main__":
    raise SystemExit(main())

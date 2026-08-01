"""The five Wave 1 export targets (specs: record-layer/exporter-specs-2026-07-30.md).

xlsx targets use the TEMPLATE approach: the live workbook is opened as the
template, its static sheets (Config, Legend, Dashboard formulas, Intake Log)
are preserved untouched, and only the data sheet rows are rewritten. This is
what makes Dashboard formulas keep self-deriving and legacy consumers keep
parsing. At freeze, a frozen template copy replaces the live file as the
template source (exporters/templates/) so live-file drift can't leak in.

Graph nodes are NOT a separate exporter: run.sh graph already derives them
from these files, so the nightly order is: five exporters, then graph.
"""

from pathlib import Path

import openpyxl

from .common import VAULT

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _template(rel):
    """Frozen template if present (post-freeze), else the live file (pre-cutover rehearsal)."""
    frozen = TEMPLATE_DIR / Path(rel).name
    return frozen if frozen.exists() else VAULT / rel


def _rewrite_sheet(wb, sheet_name, header, rows):
    ws = wb[sheet_name]
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)
    live_header = [c.value for c in ws[1]][: len(header)]
    if [h for h in live_header if h] != [h for h in header if h]:
        raise ValueError(f"{sheet_name} header changed: {live_header} != {header}")
    for r in rows:
        ws.append(list(r))
    return ws


# ---------------- lead-registry.xlsx ----------------

REGISTRY_REL = "DNA/Leads/lead-registry.xlsx"
REGISTRY_COLS = ["Lead ID", "Date In", "Owner", "Stage", "Segment", "Contact Name", "Practice",
                 "Specialty", "City/Market", "County", "Email", "Phone", "Source Type",
                 "Source Detail (V-ID / event / referrer)", "Report-Back Due", "Drip Campaign",
                 "Drip Added", "Next Action", "Next Action Date", "Last Touch", "SF Deal",
                 "Detail File", "Notes", "Est-Lease-Event", "Event-Source", "Event-Confidence"]


def build_registry(tmp_path, cur):
    cur.execute('select * from v_export_leads order by "Lead ID"')
    cols = [d[0] for d in cur.description]
    data = cur.fetchall()
    rows = [[r[cols.index(c)] for c in REGISTRY_COLS] for r in data]  # _suppressed carried in DB, not a sheet column
    wb = openpyxl.load_workbook(_template(REGISTRY_REL))
    _rewrite_sheet(wb, "Registry", REGISTRY_COLS, rows)
    wb.save(tmp_path)
    return len(rows), rows


# ---------------- client-roster.xlsx ----------------

ROSTER_REL = "DNA/Clients/client-roster.xlsx"
ROSTER_COLS = ["Client ID", "Name", "Practice / Entity", "Owner", "Status", "Specialty / Type",
               "Market / Location", "Deal Type", "Referral Source", "Contact", "Phone", "Email",
               "Possible Duplicate Of", "Detail File", "Notes"]


def build_roster(tmp_path, cur):
    cur.execute('select * from v_export_clients order by "Client ID"')
    cols = [d[0] for d in cur.description]
    rows = [[r[cols.index(c)] for c in ROSTER_COLS] for r in cur.fetchall()]
    wb = openpyxl.load_workbook(_template(ROSTER_REL))
    _rewrite_sheet(wb, "Clients", ROSTER_COLS, rows)
    wb.save(tmp_path)
    return len(rows), rows


# ---------------- vendors.xlsx ----------------

VENDORS_REL = "DNA/Network/vendors.xlsx"
VENDORS_COLS = ["ID", "Name", "Company", "Category", "Vertical", "Title", "Owner", "Stage",
                "Last Touch", "Next Step", "Referral-active?", "Territory", "State", "Offers",
                "Seeking", "Links", "Rivalry Group", "Originated / Referred", "Phone", "Email",
                "Notes", "Enrich?"]


def build_vendors(tmp_path, cur):
    cur.execute('select * from v_export_vendors order by "ID"')
    cols = [d[0] for d in cur.description]
    data = cur.fetchall()
    in_market = [[r[cols.index(c)] for c in VENDORS_COLS]
                 for r in data if not r[cols.index("_out_of_market")]]
    wb = openpyxl.load_workbook(_template(VENDORS_REL))
    _rewrite_sheet(wb, "Vendors", VENDORS_COLS, in_market)
    # Out of Market sheet keeps its explanatory first row; data rows follow it.
    # v1: out-of-market rows stay wherever the sheet held them at freeze; the
    # flag routes NEW moves. Revisit at freeze with real data.
    wb.save(tmp_path)
    return len(in_market), in_market


# ---------------- lead-router-2026-07-13.xlsx (target #8, Wave 3) ----------------
#
# The router regenerates from prospect_pool so every remaining reader keeps
# working — Dell's side included, and Dell has no DB path at all (ORDER 28's
# central finding). It rides the same A8 gate as the other seven.
#
# DEATH SENTENCE, recorded here and in the amendment-5 shim registry: this target
# retires at the Wave 4 repoint, once the board view is CONFIRMED the only reader
# — confirmed, not assumed. Until then it regenerates nightly.
#
# FIDELITY: the sheet's 17 columns split in two. Nine are DB-owned and come from
# the record. The other eight (Owns?, SUNBIZ entities, Lic Yrs, Licensed, Age
# Band, # at Address, Typical Term (est), License) pass back out of source_row
# verbatim with their native types intact — the same rule build_deals applies to
# the legacy deal fields. Row ORDER is the source file's, restored from
# source_seq: jsonb carries no order, and a reshuffled sheet is a diff nobody
# can read.
#
# EVERY POOL ROW EXPORTS, whatever its status. A suppressed_dup is still a row and
# a promoted row is still part of the market map. Filtering here would quietly
# shrink the file Dell reads, which is never-pre-qualify failing at the far end.

ROUTER_REL = "DNA/Leads/lead-router-2026-07-13.xlsx"
ROUTER_SHEET = "Lead Router"
ROUTER_COLS = ["SEGMENT", "THE PLAY", "Owns?", "SUNBIZ entities", "Name", "Profession",
               "Lic Yrs", "Licensed", "Age Band", "# at Address", "Practice Address",
               "City", "County", "Typical Term (est)", "Email", "Phone", "License"]
ROUTER_DB_OWNED = {
    "SEGMENT": "SEGMENT", "THE PLAY": "THE PLAY", "Name": "Name", "Profession": "Profession",
    "Practice Address": "Practice Address", "City": "City", "County": "County",
    "Email": "Email", "Phone": "Phone",
}


def build_router(tmp_path, cur):
    cur.execute("select * from v_export_pool order by source_seq")
    cols = [d[0] for d in cur.description]
    rows = []
    for r in cur.fetchall():
        rec = dict(zip(cols, r))
        legacy = rec.get("source_row") or {}
        rows.append([rec[ROUTER_DB_OWNED[c]] if c in ROUTER_DB_OWNED else legacy.get(c)
                     for c in ROUTER_COLS])
    wb = openpyxl.load_workbook(_template(ROUTER_REL))
    _rewrite_sheet(wb, ROUTER_SHEET, ROUTER_COLS, rows)
    wb.save(tmp_path)
    return len(rows), rows


# ---------------- panhandle-team-deals.json ----------------

DEALS_REL = "DNA/Deal Management/panhandle-team-deals.json"


def build_deals(tmp_path, cur):
    import json
    from datetime import datetime, timezone
    cur.execute("select * from v_export_deals order by name")
    cols = [d[0] for d in cur.description]
    deals = []
    for r in cur.fetchall():
        row = dict(zip(cols, r))
        legacy = row.get("source_row") or {}
        # FIDELITY RULE (reconciliation round 2): legacy passthrough wins for
        # vocabulary-rich fields (txn, carr) the DB normalizes internally;
        # DB values win for fields it actively owns (name, phase, owner, seg)
        # — those now render display-faithfully (phase label, initcap owner).
        legacy.update({
            "name": row["name"], "phase": row["phase"],
            "owner": row["owner"] or legacy.get("owner"),
            "txn": legacy["txn"] if "txn" in legacy else row["deal_type"],
            "seg": legacy["seg"] if "seg" in legacy else row["segment"],
            "carr": legacy.get("carr") if legacy.get("carr") is not None
                    else row["PLACEHOLDER_sf_commission_never_sum"],
        })
        deals.append(legacy)
    doc = {
        "source": "GENERATED from the CARR record layer — do not hand-edit; regenerated nightly",
        "captured": datetime.now(timezone.utc).isoformat(),
        "count": len(deals),
        "placeholders": "Salesforce Total Commission and Close Date are placeholders: never sum, never forecast, never rank by them.",
        "notes": "OPEN-pipeline view. This file was never the full deal record and still is not (see memory: Outlook deal folders are the real record).",
        "deals": deals,
        "schema": "see v_export_deals + record-layer/exporter-specs-2026-07-30.md",
    }
    tmp_path.write_text(json.dumps(doc, indent=2, default=str))
    return len(deals), deals


# ---------------- clients-active.md ----------------

ACTIVE_REL = "DNA/Clients/clients-active.md"
ACTIVE_COLS = ["Owner", "Name", "C-ID", "Status", "Deal Type", "Specialty", "Location",
               "Last Touch", "Next Step", "Detail"]


FROZEN_ACTIVE = Path(__file__).resolve().parent.parent / "frozen-sources" / "2026-07-30" / "clients-active.md"


def _lifted_header():
    """The file's own prose, carried verbatim from the frozen copy.

    The identity of this file -- what it is, what it replaced, where narrative
    lives -- was written by Joe and is not the exporter's to paraphrase. Only two
    things are dropped: the hand-maintained 'Last updated' / 'Last synced' stamps,
    which would be stale the moment they were copied into a nightly-regenerated
    file (no-fabrication applies to metadata too).
    """
    keep = []
    for line in FROZEN_ACTIVE.read_text().splitlines():
        if line.startswith("## "):
            break
        if line.startswith("# ") or line.startswith("Last updated:") or line.startswith("Last synced:"):
            continue
        keep.append(line)
    while keep and not keep[-1].strip():
        keep.pop()
    return keep


def _md_cell(v):
    """Render one value as a markdown table cell.

    A pipe inside a value silently splits the row into an extra column and every
    cell after it shifts left -- C-131's location ("Marietta | Smyrna") did exactly
    that, putting a city where the Next Step belonged. Newlines end the row
    outright. Both are escaped rather than stripped: the data stays verbatim, the
    table stays parseable.
    """
    if v is None:
        return ""
    return str(v).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def build_clients_active(tmp_path, cur):
    cur.execute('select * from v_export_clients_active order by "Owner", "Name"')
    cols = [d[0] for d in cur.description]
    rows = [[r[cols.index(c)] for c in ACTIVE_COLS] for r in cur.fetchall()]
    lines = [
        "# Clients — Shared Active Index (both partners, one book)",
        "",
        "> **GENERATED from the CARR record layer — do not hand-edit; regenerated nightly.**",
        "> Membership is DERIVED (an open deal, or a status flagged as pipeline-active), not",
        "> stored. Records change via the MCP verbs (log-activity, update-deal,",
        "> set-next-action...); this file is a rendered view. Where the prose below predates",
        "> the record layer and says to update rows in place, the MCP verbs are how you do it now.",
        "",
        *_lifted_header(),
        "",
        "## Active pipeline",
        "",
        "| " + " | ".join(ACTIVE_COLS) + " |",
        "|" + "---|" * len(ACTIVE_COLS),
    ]
    for r in rows:
        lines.append("| " + " | ".join(_md_cell(v) for v in r) + " |")
    from datetime import datetime, timezone
    lines += ["", f"*Exported: {datetime.now(timezone.utc).isoformat()}*", ""]
    tmp_path.write_text("\n".join(lines))
    return len(rows), rows


# ---------------- compiled rules (the taught-rules loop) ----------------

RULES_SHARED_REL = "DNA/compiled-rules-shared.md"
RULES_JOE_REL = "00_Context/compiled-rules-joe.md"
RULES_INTRO_REL = "DNA/Network/introduction-rules.md"

# ORDER 37. introduction-rules.md is DOMAIN-scoped: vendor↔vendor politics, read
# by engine.md before it proposes an intro and before any joint guest list goes
# out. Those rules live in the SAME store as every other taught rule, and they
# are shared (personal_to null — Joe and Dell both teach them), so without a
# filter every one of them would ALSO print in compiled-rules-shared.md and the
# always-read core would silently gain nineteen vendor-politics rules nobody
# asked it to carry. The shared and personal renders therefore EXCLUDE this
# scope and the intro render INCLUDES only it: one store, three audiences, no
# rule printed in two files.
INTRO_SCOPE = "intro_politics"


def _is_intro(row):
    return (row.get("scope") or {}).get("kind") == INTRO_SCOPE


def _rules_header(scope_line):
    return [
        "> **GENERATED from the CARR record layer's rule store — do not hand-edit.**",
        "> These rules BIND like the rules in `00_Context/ai-operating-notes.md`.",
        f"> {scope_line}",
        ">",
        "> **To add a rule, do not edit this file.** Capture it with the `teach` verb",
        "> (the human's verbatim words as `human_quote`), get the human's yes via",
        "> `activate-rule`, then refresh with `run.sh export --only compiled-rules`.",
        "> Only ACTIVE rules appear here — a proposed rule binds nobody by design.",
        "",
    ]


def _build_rules(tmp_path, cur, personal_slug, title, scope_line):
    """One row per active rule. personal_slug None = the shared file."""
    if personal_slug is None:
        cur.execute("select * from v_compiled_rules where personal_to is null")
    else:
        cur.execute("select * from v_compiled_rules where personal_to = %s", (personal_slug,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    rows = [r for r in rows if not _is_intro(r)]        # ORDER 37, see INTRO_SCOPE
    lines = [f"# {title}", ""] + _rules_header(scope_line)
    if not rows:
        lines += ["*No active rules yet. The first one lands here the moment it is taught "
                  "and activated.*", ""]
    for r in rows:
        when = r["activated_at"].date().isoformat() if r["activated_at"] else "date unrecorded"
        line = f"- **{r['statement']}** — taught by {r['taught_by']}, {when}"
        if r["human_quote"]:
            line += f' ("{r["human_quote"]}")'
        if r["enforcement"] and r["enforcement"] != "prose":
            line += f"  `[{r['enforcement']}]`"
        lines.append(line)
    from datetime import datetime, timezone
    lines += ["", f"*Exported: {datetime.now(timezone.utc).isoformat()} · "
                  f"{len(rows)} active rule(s)*", ""]
    tmp_path.write_text("\n".join(lines))
    # canonical rows for the checksum: the rule content, not the timestamp line
    return len(rows), [[r["statement"], r["human_quote"], r["taught_by"],
                        r["personal_to"], r["enforcement"]] for r in rows]


def build_rules_shared(tmp_path, cur):
    return _build_rules(
        tmp_path, cur, None, "Compiled rules — SHARED (both partners)",
        "SHARED SCOPE: these apply to Joe's brain and Dell's brain alike.")


def build_rules_joe(tmp_path, cur):
    return _build_rules(
        tmp_path, cur, "joe", "Compiled rules — Joe (personal)",
        "PERSONAL SCOPE: these apply to Joe's brain only. Dell's equivalent file "
        "lives on his side and is generated the same way.")


# ---------------- the loop accumulators (one-writer Phase A, ORDER 31) ----------------

LOOP_TARGETS = {
    "open-loops.md": "00_Context/open-loops.md",
    "open-loops-backlog.md": "00_Context/open-loops-backlog.md",
    "action-required.md": "DNA/Team/action-required.md",
    "team-loops.md": "DNA/Team/team-loops.md",
}

# NO GENERATED BANNER IS INJECTED INTO THESE FOUR, and that is deliberate.
# Every other generated file opens with one. These four are read by the heartbeat,
# the Monday brief and Dell's sessions as a first act, and each OPENS with a
# doctrine paragraph that IS the rule those readers obey — open-loops.md's marker
# convention, action-required.md's escalation clause. A banner above that prose
# changes the first thing every reader sees and breaks the round-trip diff the
# order's done-test turns on. The do-not-hand-edit warning belongs IN the stored
# prose, added once by a human at the live flip, into the block the human owns.
# Until that flip these renders are staging-only, so nothing sits unlabelled in
# the vault.


def _loop_cell(v):
    """One cell, verbatim.

    NOT _md_cell. That escapes pipes and folds newlines to <br>, which is right
    for a value arriving from a spreadsheet and wrong here: these values came OUT
    of markdown tables carrying their own escapes (team-loops T36 quotes an email
    subject containing an escaped pipe) and one cell legitimately spans two lines
    (T54). Re-escaping would double the backslashes and folding would destroy the
    line break. Both are content changes, on the one surface whose entire test is
    that nothing changed.
    """
    return "" if v is None else str(v)


def build_loop_file(rel_path):
    """One builder per file; the render walks the stored blocks in order."""

    def build(tmp_path, cur):
        cur.execute(
            "select seq, block_key, prose_md, header_cols, col_order "
            "from loop_block where rel_path = %s order by seq", (rel_path,))
        blocks = cur.fetchall()
        if not blocks:
            raise ValueError(f"no loop_block rows for {rel_path} — the importer has not run")

        lines, canonical = [], []
        for seq, block_key, prose_md, header_cols, col_order in blocks:
            # A prose-only block is emitted even when EMPTY: the last block of
            # open-loops-backlog.md is exactly that, and it is what carries the
            # file's trailing newline. Dropping it as falsy cost a byte and the
            # round-trip diff caught it.
            if prose_md or block_key is None:
                lines.append(prose_md)
            if block_key is None:
                continue
            if header_cols:
                lines.append("| " + " | ".join(header_cols) + " |")
                lines.append("|" + "---|" * len(header_cols))
            cur.execute(
                "select row_col_order, number, owner, title, body, since_text, "
                "       unblocks, source_note, closed_text, outcome, "
                "       marker_literal, extra_cells "
                "  from v_export_loops "
                " where rel_path = %s and block_key = %s and loop_id is not null "
                " order by render_seq", (rel_path, block_key))
            for r in cur.fetchall():
                (row_order, number, owner, title, body, since_text, unblocks,
                 source_note, closed_text, outcome, marker_literal, extra) = r
                vals = {"number": number, "owner": owner, "title": title, "body": body,
                        "since_text": since_text, "unblocks": unblocks,
                        "source_note": source_note, "closed_text": closed_text,
                        "outcome": outcome}
                # The marker literal was split off the item's own text at import;
                # it goes back onto the same cell with the same single space.
                if marker_literal:
                    text_field = "body" if body is not None else "title"
                    vals[text_field] = marker_literal + " " + _loop_cell(vals[text_field])
                order = row_order or col_order
                cells = []
                for name in order:
                    if name.startswith("extra:"):
                        cells.append(_loop_cell((extra or {}).get(name.split(":", 1)[1])))
                    else:
                        cells.append(_loop_cell(vals.get(name)))
                lines.append("| " + " | ".join(cells) + " |")
                canonical.append([number, list(order), cells])

        tmp_path.write_text("\n".join(lines))
        return len(canonical), canonical

    return build


# ---------------- the dossiers (one-writer Phase B, ORDER 36) ----------------

DOSSIER_DIR = "DNA/Clients/prospects"

# The 20 hand-maintained dossiers, enumerated by the RECORD LAYER, not by a
# directory listing: they are exactly the clients carrying `notes_path`
# (`select count(*) from client where notes_path is not null` = 20, verified in
# production 2026-08-01). Migration 0028 normalises those paths and asserts the
# count of 20 inside the migration, so this list and the database cannot drift
# apart silently — each builder re-checks its own rel_path against
# v_export_dossier_subject and refuses to write if it is missing.
#
# Four dossier-SHAPED files in the same folder are deliberately NOT here because
# no record points at them: Ahlborn-FamilyDental-Loxley.md (C-153),
# Finelli-JosephFinelli.md (C-154), AltaPointe-enterprise.md, VictusDental-Le.md
# (a fifth, Beasley-intake.md, is an intake per DNA/Clients/INDEX.md, not a
# dossier). They stay hand-maintained; parked for Fable, see the ORDER 36 log.
DOSSIER_FILES = [
    "AmericanFamilyCare.md",
    "AnointedOT-Sears.md",
    "BayAreaOralSurgery.md",
    "Beasley.md",
    "BlakesEnrickment-Heard.md",
    "CosmeticDermatology.md",
    "DeepWaters-Pappas.md",
    "FirstCallDPC-Petersen.md",
    "GulfCoastPelvicFloor.md",
    "HealthcareForKids.md",
    "Hughes-DentalStartup-SRB.md",
    "LifeDentalGroup.md",
    "Lindsey-LighthouseDental.md",
    "OceanWounds-Lerner.md",
    "PCB-Jeremiah-relocation.md",
    "PremierHealthWellness-RandallMacDonnell.md",
    "Renalus.md",
    "SerenityCardiology-Brown.md",
    "Tyrer-DentalStartup-Moultrie.md",
    "Weiler-Rejuvime.md",
]

# UNLIKE the four loop renders, a dossier DOES carry the generated banner. The
# loop files open with doctrine prose their readers obey, so a banner above it
# changes the first thing every reader sees; a dossier opens with a subject
# heading and its whole point after Phase B is that nobody edits it again
# ("Nobody opens a dossier to edit it again, from any surface, including
# phones" — two-writer-endgame R1). The banner is the instruction that makes
# that true.
DOSSIER_BANNER = [
    "> **GENERATED from the CARR record layer — do not hand-edit.**",
    "> The header below is record fields; the analysis below it is `kind=analysis`",
    "> rows on this subject's timeline. Newest analysis prints in full, earlier",
    "> analysis collapses to title + date + author (the full text is on the record).",
    ">",
    "> **To add analysis, do not edit this file.** Log it against this subject with",
    "> the analysis verb, then refresh with `run.sh export --only dossier`.",
    "",
]

# The header fields, in the order the hand-maintained dossiers printed them.
# Only fields that EXIST on the record are listed. The hand files also carried
# Situation / Key angle / Outreach notes / Next step / Requirements snapshot,
# which have no columns on `client`; those arrive as analysis rows at import
# rather than being invented as schema. See the ORDER 36 log's byte-identity note.
DOSSIER_HEADER_FIELDS = [
    ("Client ID", "client_ref"),
    ("Status", "client_status"),
    ("Owner", "owner_label"),
    ("Specialty / Practice type", "specialty_type_label"),
    ("Vertical", "vertical"),
    ("Subtype", "subtype"),
    ("Client type", "client_type"),
    ("Deal type", "deal_type_label"),
    ("Contact", "contact_label"),
    ("Representation (ETL)", "etl_status"),
    ("Lead source", "acquisition_source"),
    ("Lead source detail", "acquisition_detail"),
    ("Last touch", "last_touch"),
]


def _dossier_stamp(occurred_at, author):
    """Date + author for one analysis row. Never fabricated.

    A row whose author could not be established at import is stored against the
    importing actor with source='import'; the render says so rather than
    printing a name the file never claimed.
    """
    when = occurred_at.date().isoformat() if occurred_at else "date unrecorded"
    return f"{when} · {author or 'author unrecorded'}"


def build_dossier(rel_path):
    """One builder per dossier file: record header, then the analysis stream."""

    def build(tmp_path, cur):
        cur.execute("select * from v_export_dossier_subject where rel_path = %s", (rel_path,))
        cols = [d[0] for d in cur.description]
        subj = cur.fetchall()
        if len(subj) != 1:
            raise ValueError(
                f"{rel_path}: v_export_dossier_subject returned {len(subj)} rows, want 1 — "
                "the client's notes_path moved or 0028 has not been applied")
        s = dict(zip(cols, subj[0]))

        cur.execute(
            "select recency_rank, occurred_at, title, body, owed, author, source "
            "  from v_export_dossier_analysis where rel_path = %s order by recency_rank",
            (rel_path,))
        notes = cur.fetchall()

        lines = [f"# {s['subject_name']}", ""] + DOSSIER_BANNER
        for label, key in DOSSIER_HEADER_FIELDS:
            v = s.get(key)
            if v not in (None, ""):
                lines.append(f"- **{label}:** {v}")
        lines.append("")

        if not notes:
            # Not an error. A dossier whose analysis has not been imported yet
            # renders its header and says so, rather than looking complete.
            lines += ["*No analysis rows on this subject yet — the import for this "
                      "file has not run. Nothing is missing from the record; nothing "
                      "has been written to it either.*", ""]
        else:
            _rank, occurred_at, title, body, owed, author, _source = notes[0]
            lines += ["## Analysis — current", "",
                      f"### {title}", f"*{_dossier_stamp(occurred_at, author)}*", ""]
            if body:
                lines += [body, ""]
            if owed:
                lines += [f"**Owed:** {owed}", ""]
            if len(notes) > 1:
                lines += ["## Earlier analysis", "",
                          "*Collapsed to title, date and author. The full text lives on the "
                          "record — ask for the subject's timeline.*", ""]
                for _r, occ, ttl, _b, _o, auth, _s in notes[1:]:
                    lines.append(f"- **{ttl}** — {_dossier_stamp(occ, auth)}")
                lines.append("")

        from datetime import datetime, timezone
        lines += [f"*Exported: {datetime.now(timezone.utc).isoformat()} · "
                  f"{len(notes)} analysis row(s)*", ""]
        tmp_path.write_text("\n".join(lines))

        # Canonical rows for the export checksum: the CONTENT, never the
        # timestamp line (which would make every run look like a change).
        canonical = [[str(s.get(k)) for _l, k in DOSSIER_HEADER_FIELDS]] + [
            [r[0], str(r[1]), r[2], r[3], r[4], r[5]] for r in notes]
        return len(notes), canonical

    return build


TARGETS = {
    "lead-registry.xlsx": (REGISTRY_REL, build_registry),
    "client-roster.xlsx": (ROSTER_REL, build_roster),
    "vendors.xlsx": (VENDORS_REL, build_vendors),
    "panhandle-team-deals.json": (DEALS_REL, build_deals),
    "clients-active.md": (ACTIVE_REL, build_clients_active),
    "compiled-rules-shared": (RULES_SHARED_REL, build_rules_shared),
    "compiled-rules-joe": (RULES_JOE_REL, build_rules_joe),
    # #8 (Wave 3, ORDER 25d). Carries a death sentence — see build_router.
    "lead-router-2026-07-13.xlsx": (ROUTER_REL, build_router),
    # #9-#12 (one-writer Phase A, ORDER 31d). `--only loop` refreshes all four,
    # the same prefix-match convenience `--only compiled-rules` relies on.
    **{f"loop-{name}": (rel, build_loop_file(rel)) for name, rel in LOOP_TARGETS.items()},
    # #13-#32 (one-writer Phase B, ORDER 36). `--only dossier` refreshes all 20;
    # `--only dossier-Renalus.md` refreshes exactly one, which is what the
    # file-by-file migration gate in step 8 calls per file.
    **{f"dossier-{name}": (f"{DOSSIER_DIR}/{name}", build_dossier(f"{DOSSIER_DIR}/{name}"))
       for name in DOSSIER_FILES},
}

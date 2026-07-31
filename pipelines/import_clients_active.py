"""Import the curated content of clients-active.md into the record layer.

Why this exists: clients-active.md was never an import source, so the Next Step
texts Joe maintained by hand -- the operational content of the file -- existed in
no table. Regenerating the file from the DB would have silently blanked that
column. This lifts those texts into real records (next_action) and the Last Touch
dates into real activity stamps, so the derived file can carry them honestly.

The file is a SOURCE here, never a membership list: which clients are "active" is
derived (open deal OR client_status.is_active_pipeline), per amendment 0. Nothing
in this importer creates, merges, or restates a client.

Idempotent via record_source (source_system 'clients-active', key = C-ID): a
re-run after a partial failure skips what already landed.

Usage: CARR_IMPORT_DB_URL=... .venv/bin/python -m pipelines.import_clients_active
       [--source PATH] [--dry-run]
"""

import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO / "frozen-sources" / "2026-07-30" / "clients-active.md"
SOURCE_SYSTEM = "clients-active"

# The Owner cell is prose, not a key ("Dell (Joe searching)"). Map on the leading
# name and never guess past it: an unmapped owner is reported, not defaulted.
OWNER_PATTERNS = ((r"^joe\b", "joe"), (r"^dell\b", "dell"))

# The file's own way of writing "nothing here". Dell's Salesforce backfill rows
# carry these in Next Step. Turning them into next_action rows would invent 35
# open balls that nobody owes -- and they would surface in v_today_triage as
# undated, permanently-due items. An empty marker is data, not a next step.
NULL_MARKERS = {"", "—", "-", "–", "--", "n/a", "na", "tbd", "none", "(none)"}


def is_null_marker(cell):
    return (cell or "").strip().lower() in NULL_MARKERS


def parse_owner(cell):
    s = (cell or "").strip().lower()
    for pat, slug in OWNER_PATTERNS:
        if re.match(pat, s):
            return slug
    return None


def parse_date(cell):
    """Only real dates. '—', '2026-07 (lead delivered)', 'On first contact' are prose."""
    s = (cell or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3]), 12, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_table(text):
    """Pull the pipe tables out of the markdown. Returns list of dicts keyed by header.

    The file holds MORE THAN ONE table (Joe's curated index, then Dell's Salesforce
    backfill), each with its own header row. Treating the second header as a data
    row produced two junk records whose C-ID was the literal string 'C-ID' -- so a
    repeated header line is recognised and skipped, not imported.
    """
    rows, header = [], None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):        # the |---|---| separator
            continue
        if header is None:
            header = cells
            continue
        if cells[:len(header)] == header:            # a repeated header, not data
            continue
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells)))
    return header, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    url = os.environ.get("CARR_IMPORT_DB_URL")
    if not url:
        raise SystemExit("CARR_IMPORT_DB_URL not set")

    header, rows = parse_table(Path(a.source).read_text())
    report = {"next_actions": [], "activities": [], "no_cid": [], "no_match": [],
              "no_owner": [], "status_mismatch": [], "skipped_done": [], "no_next_step": [],
              "dup_in_file": []}

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("select slug, id from actor")
        actors = dict(cur.fetchall())
        cur.execute("""select c.roster_ref, c.id, cs.label
                         from client c join client_status cs on cs.slug = c.status
                        where c.roster_ref is not null""")
        clients = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        cur.execute("select external_key from record_source where source_system=%s",
                    (SOURCE_SYSTEM,))
        already = {r[0] for r in cur.fetchall()}
        seen = set()          # C-IDs handled in THIS run; `already` grows with them

        for r in rows:
            cid = (r.get("C-ID") or "").strip()
            name = (r.get("Name") or "").strip()
            if not cid:
                report["no_cid"].append(name or "(unnamed row)")
                continue
            if cid not in clients:
                report["no_match"].append(f"{cid} ({name})")
                continue
            if cid in seen:
                # C-127 appears in Joe's table AND again in Dell's backfill.
                # Without this the activity stamp would be written twice.
                report["dup_in_file"].append(f"{cid} ({name})")
                continue
            if cid in already:
                report["skipped_done"].append(cid)
                continue
            seen.add(cid)

            client_id, db_status = clients[cid]
            file_status = (r.get("Status") or "").strip()
            if file_status and file_status.lower() != (db_status or "").lower():
                report["status_mismatch"].append((cid, name, db_status, file_status))

            owner_slug = parse_owner(r.get("Owner"))
            if owner_slug is None:
                report["no_owner"].append(f"{cid} ({name}): {r.get('Owner')!r}")
                continue
            owner_id = actors[owner_slug]
            sys_id = actors["system"]

            step = (r.get("Next Step") or "").strip()
            if not is_null_marker(step):
                if not a.dry_run:
                    cur.execute("""
                        insert into next_action
                            (subject_type, subject_id, owner_id, description, status,
                             created_by, updated_by)
                        values ('client', %s, %s, %s, 'open', %s, %s)
                        on conflict (subject_type, subject_id, owner_id)
                            where status = 'open' do nothing
                    """, (client_id, owner_id, step, sys_id, sys_id))
                report["next_actions"].append((cid, owner_slug, step[:70]))
            else:
                report["no_next_step"].append(cid)

            touched = parse_date(r.get("Last Touch"))
            if touched:
                if not a.dry_run:
                    cur.execute("""
                        insert into activity
                            (occurred_at, actor_id, kind, summary, client_id, source, updated_by)
                        values (%s, %s, 'note', %s, %s, 'import', %s)
                    """, (touched, owner_id,
                          f"Last touch carried from clients-active.md at freeze ({cid})",
                          client_id, sys_id))
                report["activities"].append((cid, touched.date().isoformat()))

            if not a.dry_run:
                cur.execute("""
                    insert into record_source (entity_type, entity_id, source_system, external_key)
                    values ('client', %s, %s, %s)
                    on conflict (source_system, external_key) do nothing
                """, (client_id, SOURCE_SYSTEM, cid))

        if a.dry_run:
            conn.rollback()
        else:
            conn.commit()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"# clients-active.md import — {stamp}"
             + ("  (DRY RUN — nothing written)" if a.dry_run else ""), "",
             f"Source: `{a.source}`", f"Table rows parsed: {len(rows)}", ""]
    lines += ["## Written", f"- next_action (client-scoped, open): {len(report['next_actions'])}"]
    for cid, own, step in report["next_actions"]:
        lines.append(f"    - {cid} [{own}] {step}")
    lines += [f"- activity stamps from Last Touch: {len(report['activities'])}", ""]
    lines += ["## Not written (reported, never guessed)"]
    for key, title in (("no_cid", "rows with no C-ID"), ("no_match", "C-ID with no client"),
                       ("no_owner", "unmapped Owner"),
                       ("no_next_step", "Next Step empty or a null marker ('—')"),
                       ("dup_in_file", "C-ID listed twice in the file (second ignored)"),
                       ("skipped_done", "already imported (idempotent skip)")):
        v = report[key]
        lines.append(f"- {title}: {len(v)}" + (f" — {', '.join(map(str, v))}" if v else ""))
    lines += ["", "## Status mismatches — JOE'S REVIEW (nothing overwritten)"]
    if report["status_mismatch"]:
        for cid, name, dbs, fs in report["status_mismatch"]:
            lines.append(f"- {cid} {name}: roster/DB '{dbs}' vs curated file '{fs}'")
    else:
        lines.append("- none")
    out = REPO / "out" / f"clients-active-import-{stamp}.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"report -> {out}")
    print(f"  next_actions {len(report['next_actions'])} · activities {len(report['activities'])}"
          f" · no-C-ID {len(report['no_cid'])} · no-match {len(report['no_match'])}"
          f" · no-owner {len(report['no_owner'])} · status-mismatch {len(report['status_mismatch'])}")


if __name__ == "__main__":
    main()

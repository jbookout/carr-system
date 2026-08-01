"""record_sources.py — one read path for the derived-surface consumers (ORDER 29a).

WHY THIS EXISTS
  The Deal Room and the Obsidian graph were built when the generated files WERE
  the record: they opened vendors.xlsx / lead-registry.xlsx / client-roster.xlsx /
  panhandle-team-deals.json and derived their surfaces from whatever those files
  happened to hold. Since the record layer went live those files are exports —
  the database is the record and the file is a rendering of it. A local consumer
  that reads the rendering is one export failure away from deriving a board from
  yesterday's truth without noticing.

  ORDER 28's inventory found the same thing the other way round: Cowork sessions
  and Dell's side have no database path at all, so the files are permanent
  infrastructure, not scar tissue. Nothing is retired here. The files keep being
  generated exactly as before; only LOCAL CODE gains a second way to read them.

WHAT IT GUARANTEES
  Byte-identical derived output in either mode. The record path queries the same
  views the exporters query (`v_export_vendors`, `v_export_leads`,
  `v_export_clients`, `v_export_deals`), projects the same columns in the same
  order, applies the same filters, and reproduces the two shape changes a
  round-trip through the file makes:
    1. xlsx has no date type — openpyxl hands back `datetime` at midnight where
       the view hands back `date`. Records mode promotes date -> datetime so a
       consumer sees the shape it has always seen.
    2. JSON has no date or Decimal type — the deals exporter serialises with
       `default=str`. Records mode round-trips the deal list through json for
       the same reason.
  Proven, not asserted: `tools/parity-records.py` runs every consumer in both
  modes on the same day's data and diffs the derived output.

MODE SELECTION (per consumer, highest precedence first)
    --files / --records on the command line
    CARR_SOURCE_MODE=files|records in the environment
    the consumer's own default
  Records mode falls back to files, loudly on stderr, when there is no exporter
  credential or psycopg is not importable (a plain `python3`, Dell's runtime, a
  machine with no db.env). A credential that is present but fails is NOT
  swallowed: that is a real outage and it raises.

The column lists come from `exporters/targets.py` by import, never by copy, so
they cannot drift from what the exporters actually write.
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MODE_FILES = "files"
MODE_RECORDS = "records"

VENDORS_REL = "DNA/Network/vendors.xlsx"
LEADS_REL = "DNA/Leads/lead-registry.xlsx"
CLIENTS_REL = "DNA/Clients/client-roster.xlsx"
DEALS_REL = "DNA/Deal Management/panhandle-team-deals.json"


# ---------------- mode selection ----------------

def resolve_mode(argv, default=MODE_RECORDS):
    """(mode, argv-without-the-mode-flags). Pass sys.argv[1:]; positional args survive."""
    mode = os.environ.get("CARR_SOURCE_MODE") or default
    rest = []
    for a in argv:
        if a == "--files":
            mode = MODE_FILES
        elif a == "--records":
            mode = MODE_RECORDS
        else:
            rest.append(a)
    if mode not in (MODE_FILES, MODE_RECORDS):
        sys.exit(f"unknown source mode {mode!r} (files|records)")
    return mode, rest


def _exporter_url():
    """Same lookup exporters/common.py does. Returns None when unconfigured."""
    url = os.environ.get("CARR_DB_EXPORTER_URL")
    if url:
        return url
    env = Path.home() / ".config/carr/db.env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("CARR_DB_EXPORTER_URL="):
                return line.split("=", 1)[1].strip() or None
    return None


def _records_available():
    """(bool, why-not). Missing credential or missing driver is a fallback, not a failure."""
    if not _exporter_url():
        return False, "no CARR_DB_EXPORTER_URL (see ~/.config/carr/db.env)"
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False, "psycopg not importable by this interpreter (use .venv/bin/python)"
    return True, ""


def effective_mode(mode, label="source"):
    """Resolve records -> files when the record path is unreachable. Says so on stderr."""
    if mode != MODE_RECORDS:
        return MODE_FILES
    ok, why = _records_available()
    if ok:
        return MODE_RECORDS
    print(f"[{label}] records mode unavailable ({why}) — falling back to the generated files",
          file=sys.stderr)
    return MODE_FILES


def source_note(mode):
    return "records (v_export_* views)" if mode == MODE_RECORDS else "generated files"


# ---------------- shared shape rules ----------------

def _as_file_shape(v):
    """A date read back out of an xlsx is a datetime at midnight. Match it."""
    if type(v) is date:
        return datetime(v.year, v.month, v.day)
    return v


def _connect():
    sys.path.insert(0, str(REPO))
    from exporters.common import connect
    return connect()


def _view_rows(query, colspec, drop_when=None):
    """Rows of `query` projected onto `colspec`, in view order, file-shaped."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(query)
        cols = [d[0] for d in cur.description]
        data = cur.fetchall()
    out = []
    for r in data:
        if drop_when is not None and r[cols.index(drop_when)]:
            continue
        out.append({c: _as_file_shape(r[cols.index(c)]) for c in colspec})
    return out


def _sheet_rows(path, sheet):
    """The historical read: one dict per non-empty data row, keyed by header."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        wb.close()
        return []
    ws = wb[sheet]
    it = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h else "" for h in next(it, [])]
    out = []
    for r in it:
        d = {hdr[i]: (r[i] if i < len(r) else None) for i in range(len(hdr))}
        if any(v not in (None, "") for v in d.values()):
            out.append(d)
    wb.close()
    return out


def _targets():
    sys.path.insert(0, str(REPO))
    from exporters import targets
    return targets


# ---------------- the four sources ----------------

def load_vendors(root, mode):
    if mode == MODE_RECORDS:
        t = _targets()
        # `_out_of_market` rows never reach the Vendors sheet, so they must not
        # reach a records-mode consumer either.
        return _view_rows('select * from v_export_vendors order by "ID"',
                          t.VENDORS_COLS, drop_when="_out_of_market")
    return _sheet_rows(os.path.join(root, VENDORS_REL), "Vendors")


def load_leads(root, mode):
    if mode == MODE_RECORDS:
        t = _targets()
        return _view_rows('select * from v_export_leads order by "Lead ID"', t.REGISTRY_COLS)
    return _sheet_rows(os.path.join(root, LEADS_REL), "Registry")


def load_clients(root, mode):
    if mode == MODE_RECORDS:
        t = _targets()
        return _view_rows('select * from v_export_clients order by "Client ID"', t.ROSTER_COLS)
    return _sheet_rows(os.path.join(root, CLIENTS_REL), "Clients")


def load_deals_doc(root, mode):
    """The whole deal document: {'deals': [...], 'captured': ...} plus the file's prose.

    The merge below mirrors `exporters/targets.py:build_deals` — the fidelity rule
    (legacy passthrough wins for txn/seg/carr, the DB wins for name/phase/owner)
    is the exporter's, not this module's. targets.py is the authority; parity is
    what proves this copy still agrees with it.
    """
    if mode != MODE_RECORDS:
        path = os.path.join(root, DEALS_REL)
        if not os.path.exists(path):
            return {"deals": [], "captured": ""}
        with open(path) as fh:
            return json.load(fh)

    with _connect() as conn, conn.cursor() as cur:
        cur.execute("select * from v_export_deals order by name")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    deals = []
    for r in rows:
        row = dict(zip(cols, r))
        legacy = row.get("source_row") or {}
        legacy.update({
            "name": row["name"], "phase": row["phase"],
            "owner": row["owner"] or legacy.get("owner"),
            "txn": legacy["txn"] if "txn" in legacy else row["deal_type"],
            "seg": legacy["seg"] if "seg" in legacy else row["segment"],
            "carr": legacy.get("carr") if legacy.get("carr") is not None
                    else row["PLACEHOLDER_sf_commission_never_sum"],
        })
        deals.append(legacy)
    # The file goes through json.dumps(default=str); a records-mode consumer must
    # see the same scalars (no date, no Decimal) or its output differs on type alone.
    deals = json.loads(json.dumps(deals, default=str))
    from datetime import timezone
    return {
        "source": "READ LIVE from the CARR record layer (v_export_deals)",
        "captured": datetime.now(timezone.utc).isoformat(),
        "count": len(deals),
        "deals": deals,
    }

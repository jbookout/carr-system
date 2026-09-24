#!/usr/bin/env python3
"""restore-watermark.py — the EXACT half of a restore rehearsal (V5-F08 item 4).

bin/restore-rehearse.sh has always compared the restored database against LIVE
production and gated on ">= 90% of production's rows". That is the right
truncation alarm, and it is approximate by construction: production keeps
moving after the dump is taken. V5-F08 asks for more — "restore from an
independently controlled copy succeeds with exact watermark/hash" — and the
only thing a restore can be compared EXACTLY against is the artifact itself.

  count        the decrypted pg_dump plaintext on stdin -> per table, the rows
               the artifact CARRIES and a content digest over them (sha256 of
               the table's COPY text lines, sorted bytewise), read from its own
               COPY blocks. Prints JSON. The plaintext is streamed, never
               written anywhere.
  restored     the same watermark read from the RESTORED database: every
               public/ops base table (extension-owned tables excepted), COPY'd
               out in pg_dump's own text form and column list, through a
               read-only session. The DSN comes from the environment variable
               --dsn-env names, never from an argument.
  compare      artifact vs restored watermark. Exit 0 only on exact equality —
               rows AND content digest, table for table, none missing either side.
  digest       the sha256 of a stored file, as "sha256:<hex>".
  fetch-copy   the independently held copy AND the digest its producer
               recorded, both read from the store, never from an operator: the
               nightly workflow run's provider-authenticated "Backup artifact"
               Check (bin/backup-workflow-status.py writes it), cross-checked
               against the artifact API, then the artifact's own bytes. Writes
               copy.json and the extracted .sql.age into --out-dir.
  receipt      the typed restore-exercise-receipt.v1 that
               mcp-server/src/recovery-matrix.v5.js evaluateRestoreExercise
               reads, built from fetch-copy's copy.json.
  verify-receipt
               re-reads the Check and the artifact named in a receipt from the
               provider and exits 1 unless the recorded digest, the store's
               digest and the production instant still say what the receipt
               says; on a match it prints the receipt with the copy block AS
               RE-READ, stamped with the verify binding the evaluator requires
               (lib/recovery_evidence.py). A receipt is never trusted because
               it exists.
  outbound-census
               every device row of the RESTORED ops.notification_delivery as
               an outbound item, plus the provider readbacks from --readbacks,
               as the bound request evaluateOutboundQueueRelease reads. The
               item list comes from the database, never from a caller.

Nothing here writes to a database or reads a credential. fetch-copy and
verify-receipt call the GitHub API through the logged-in `gh`.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from lib.recovery_evidence import bind, canonical_json  # noqa: E402

COPY_RE = re.compile(
    rb'^COPY ((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*))\.((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*))'
    rb'( \((?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)(?:, (?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*))*\))? FROM stdin;$'
)
TERMINATOR = b"\\."
RECEIPT_KIND = "restore-exercise-receipt.v1"
TARGET_KINDS = ("disposable_branch", "disposable_local_cluster", "staging")
COPY_KEYS = ("copy_id", "custody_domain", "primary_domain", "produced_at", "producer_id",
             "recorded_artifact_digest", "recorded_digest_source", "store_readback_digest")
DUMP_MEMBER_RE = re.compile(r"^carr-[0-9]{8}\.sql\.age$")

# The facts about the nightly workflow copy that are true by construction of
# .github/workflows/backup-nightly.yml, not reported by anyone at run time.
CUSTODY_DOMAIN = "github-actions-artifacts"
PRIMARY_DOMAIN = "neon-primary"
PRODUCER_ID = "backup-nightly-workflow"
DIGEST_SOURCE_KIND = "github_actions_backup_check"

# pg_dump's own session settings for COPY text (pg_dump.c setup_connection), so
# the restored side renders every value exactly as the artifact carries it.
# TimeZone is pinned to UTC: the nightly dump runs on a UTC runner against a
# UTC server, so timestamptz values in the artifact carry +00.
SESSION_SETTINGS = (
    ("DateStyle", "ISO"), ("IntervalStyle", "postgres"), ("extra_float_digits", "3"),
    ("TimeZone", "UTC"), ("client_encoding", "UTF8"), ("standard_conforming_strings", "on"),
    ("default_transaction_read_only", "on"), ("search_path", "pg_catalog"),
)
TABLES_SQL = """
select n.nspname, c.relname
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname in ('public', 'ops') and c.relkind = 'r'
   and not exists (select 1 from pg_depend d
                    where d.classid = 'pg_class'::regclass and d.objid = c.oid and d.deptype = 'e')
 order by 1, 2
"""


def _ident(token: bytes) -> str:
    text = token.decode("utf-8")
    if text.startswith('"'):
        return text[1:-1].replace('""', '"')
    return text


def _as_bytes(raw: bytes | str) -> bytes:
    return raw if isinstance(raw, bytes) else raw.encode("utf-8")


class _Table:
    """One table's rows, hashed order-free: sha256 over the sorted row lines."""

    def __init__(self) -> None:
        self.lines: list[bytes] = []

    def add(self, line: bytes) -> None:
        self.lines.append(line)

    def entry(self) -> dict[str, Any]:
        h = hashlib.sha256()
        for line in sorted(self.lines):
            h.update(line)
            h.update(b"\n")
        return {"rows": len(self.lines), "content_digest": "sha256:" + h.hexdigest()}


def watermark_from_dump(lines: Iterable[bytes | str]) -> dict[str, dict[str, Any]]:
    """Per schema.table: rows, content digest and the COPY column list, from a plain pg_dump stream.

    A COPY block is the header line, one line per row, then a line that is
    exactly backslash-dot. pg_dump escapes embedded newlines and backslashes
    inside COPY text, so a data row can never be that terminator. A stream that
    ends inside a block is a truncated artifact and raises rather than guessing.
    """
    out: dict[str, dict[str, Any]] = {}
    table: str | None = None
    columns = ""
    current = _Table()
    for raw in lines:
        line = _as_bytes(raw).rstrip(b"\n")
        if table is None:
            m = COPY_RE.match(line)
            if m:
                table = f"{_ident(m.group(1))}.{_ident(m.group(2))}"
                if table in out:
                    raise ValueError(f"table {table} has two COPY blocks")
                columns = (m.group(3) or b"").decode("utf-8").strip()
                current = _Table()
            continue
        if line == TERMINATOR:
            out[table] = {**current.entry(), "copy_columns": columns}
            table = None
        else:
            current.add(line)
    if table is not None:
        raise ValueError(f"stream ended inside the COPY block for {table}: truncated artifact")
    if not out:
        raise ValueError("no COPY blocks found: not a plain-format data dump")
    return out


def count_copy_rows(lines: Iterable[bytes | str]) -> dict[str, int]:
    """Row counts only (the older, weaker view; kept for callers that want just counts)."""
    return {t: e["rows"] for t, e in watermark_from_dump(lines).items()}


def strip_columns(watermark: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {t: {"rows": e["rows"], "content_digest": e["content_digest"]} for t, e in watermark.items()}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def restored_watermark(dsn: str, artifact: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The restored database's watermark, read-only, in the artifact's own COPY form."""
    import psycopg  # the repo venv's driver; imported here so the pure subcommands need none

    out: dict[str, dict[str, Any]] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for name, value in SESSION_SETTINGS:
                cur.execute("select set_config(%s, %s, false)", (name, value))
            cur.execute(TABLES_SQL)
            tables = [(schema, rel) for schema, rel in cur.fetchall()]
            for schema, rel in tables:
                key = f"{schema}.{rel}"
                columns = str(artifact.get(key, {}).get("copy_columns", ""))
                # The artifact's column list, as pg_dump wrote it (generated columns
                # excluded, attnum order); validated by COPY_RE when it was read.
                statement = " ".join(p for p in ("COPY", f"{_quote(schema)}.{_quote(rel)}", columns, "TO STDOUT") if p)
                table = _Table()
                pending = b""
                with cur.copy(statement) as copy:
                    for chunk in copy:
                        pending += bytes(chunk)
                        *complete, pending = pending.split(b"\n")
                        for line in complete:
                            table.add(line)
                if pending:
                    raise ValueError(f"COPY output for {key} did not end with a newline")
                out[key] = table.entry()
    return out


def compare(artifact: dict[str, dict[str, Any]], restored: dict[str, dict[str, Any]]) -> list[dict]:
    tables = sorted(set(artifact) | set(restored))
    diffs = []
    for t in tables:
        a, r = artifact.get(t), restored.get(t)
        a_rows, r_rows = (a or {}).get("rows"), (r or {}).get("rows")
        a_dig, r_dig = (a or {}).get("content_digest"), (r or {}).get("content_digest")
        if a_rows != r_rows or a_dig != r_dig:
            diffs.append({"table": t, "artifact_rows": a_rows, "restored_rows": r_rows,
                          "content_differs": a_dig != r_dig})
    return diffs


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ── the independently held copy, read back from the store ────────────────────

def _status_module():
    spec = importlib.util.spec_from_file_location("backup_workflow_status", REPO / "ops" / "backup-workflow-status.py")
    if spec is None or spec.loader is None:
        raise ImportError("cannot load ops/backup-workflow-status.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before it runs: its dataclasses resolve annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BACKUP_RUN_EVENTS = ("schedule", "workflow_dispatch")
BACKUP_RUN_BRANCH = "main"


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_bound_to_run(bws, repository: str, identity, run: dict[str, Any], item: dict[str, Any]) -> bool:
    """Is this Actions-app Check, whose external_id already equals the run identity, THIS run's?

    A Check a workflow creates with GITHUB_TOKEN does not land in its own run's
    check suite: read live on 2026-09-24, 0 of the last 5 nightly runs had it
    there; each sat in another github-actions suite on the same head. So the
    run is bound three ways, all provider-assigned except the envelope: the
    suite GitHub placed the Check in is a github-actions suite for this head
    on main; the Check's provider-stamped start and completion fall inside the
    run's own window; and its external_id (checked by matching_checks) names
    this run id and attempt. Its own run's suite is accepted as well.
    RESIDUAL (documented, not closed): a different workflow with checks:write
    running on the same main commit inside the same window could write an
    identical envelope; admitting a workflow to main is the control there.
    """
    check_suite = item.get("check_suite")
    suite_id = check_suite.get("id") if isinstance(check_suite, dict) else None
    if suite_id is None:
        return False
    if suite_id != run.get("check_suite_id"):
        suite = bws.api(f"/repos/{repository}/check-suites/{int(suite_id)}")
        if not isinstance(suite, dict):
            return False
        raw_app = suite.get("app")
        app: dict[str, Any] = raw_app if isinstance(raw_app, dict) else {}
        if (app.get("id") != bws.ACTIONS_APP_ID or app.get("slug") != bws.ACTIONS_APP_SLUG
                or suite.get("head_branch") != BACKUP_RUN_BRANCH
                or str(suite.get("head_sha", "")).lower() != identity.head_sha):
            return False
    started, completed = _instant(item.get("started_at")), _instant(item.get("completed_at"))
    run_start, run_end = _instant(run.get("run_started_at")), _instant(run.get("updated_at"))
    if started is None or completed is None or run_start is None or run_end is None:
        return False
    return run_start <= started <= completed <= run_end


def _authentic_backup_check(bws, identity, run: dict[str, Any], check_run_id: int | None = None) -> dict[str, Any]:
    """The run's ONE "Backup artifact" Check, authenticated by what the provider assigned.

    The name and the external_id envelope select; they do not authenticate on
    their own (both are free text its creator writes). The Actions app stamp,
    the suite GitHub placed the Check in and the provider's own timestamps do;
    see _check_bound_to_run. Zero or several candidates fail closed.
    """
    repository = identity.repository
    stamped = [
        item for item in bws.matching_checks(identity)
        if isinstance(item.get("app"), dict)
        and item["app"].get("id") == bws.ACTIONS_APP_ID and item["app"].get("slug") == bws.ACTIONS_APP_SLUG
        and (check_run_id is None or item.get("id") == check_run_id)
    ]
    candidates = [item for item in stamped if _check_bound_to_run(bws, repository, identity, run, item)]
    if len(candidates) != 1:
        raise ValueError(
            f"expected one provider-authenticated Backup artifact Check bound to run {identity.run_id}, "
            f"found {len(candidates)} (of {len(stamped)} Actions-app Checks carrying its envelope); refusing")
    item = candidates[0]
    if item.get("status") != "completed" or item.get("conclusion") != "success":
        raise ValueError("the Backup artifact Check did not complete successfully")
    return item


def _backup_run(bws, repository: str, run_id: int):
    """A nightly backup run whose copy may be trusted: the workflow's own, on main, started by the schedule or by hand."""
    run = bws.api(f"/repos/{repository}/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise ValueError("workflow run API returned no object")
    if run.get("path") != bws.BACKUP_WORKFLOW_PATH:
        raise ValueError(f"run {run_id} is not a run of {bws.BACKUP_WORKFLOW_PATH}")
    if run.get("conclusion") != "success":
        raise ValueError(f"run {run_id} did not conclude success")
    # G1: a run of the backup workflow file from any other branch or trigger
    # (a pull request, a push to a feature branch) runs code nobody reviewed
    # onto main, so its artifact is not the producer's copy.
    if run.get("head_branch") != BACKUP_RUN_BRANCH:
        raise ValueError(f"run {run_id} ran on {run.get('head_branch')!r}, not {BACKUP_RUN_BRANCH}")
    if run.get("event") not in BACKUP_RUN_EVENTS:
        raise ValueError(f"run {run_id} was triggered by {run.get('event')!r}, not {' or '.join(BACKUP_RUN_EVENTS)}")
    head_sha = str(run.get("head_sha", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError(f"run {run_id} has no commit sha")
    # head_branch is a name; the commit must actually be in main's history.
    cmp = bws.api(f"/repos/{repository}/compare/{head_sha}...{BACKUP_RUN_BRANCH}")
    if not isinstance(cmp, dict) or cmp.get("status") not in ("ahead", "identical") or cmp.get("behind_by") != 0:
        raise ValueError(f"run {run_id}'s commit is not an ancestor of {BACKUP_RUN_BRANCH}")
    identity = bws.Identity(repository, int(run["id"]), int(run["run_attempt"]), head_sha)
    return run, identity


def read_copy_record(repository: str, run_id: int, check_run_id: int | None = None) -> dict[str, Any]:
    """The copy's facts, every one read from the provider: the Check summary and the artifact API."""
    bws = _status_module()
    run, identity = _backup_run(bws, repository, run_id)
    check = _authentic_backup_check(bws, identity, run, check_run_id)
    summary = bws.summary_of(check)
    artifacts = bws.run_artifacts(identity)
    if not bws.summary_matches_artifact(identity, summary, artifacts):
        raise ValueError("the Backup artifact Check summary does not match the artifact the store holds")
    assert isinstance(summary, dict)
    stored = next(a for a in artifacts if a.get("id") == summary["artifact_id"])
    return {
        "copy_id": str(summary["artifact_name"]),
        "custody_domain": CUSTODY_DOMAIN,
        "primary_domain": PRIMARY_DOMAIN,
        "producer_id": PRODUCER_ID,
        "produced_at": str(summary["artifact_created_at"]),
        "recorded_artifact_digest": str(summary["artifact_digest"]),
        "recorded_digest_source": {"kind": DIGEST_SOURCE_KIND, "check_run_id": int(check["id"]),
                                   "workflow_run_id": identity.run_id},
        "store_readback_digest": str(stored["digest"]),
        "_artifact_id": int(summary["artifact_id"]),
    }


def fetch_copy(repository: str, run_id: int, out_dir: Path) -> tuple[Path, Path]:
    """Download the stored artifact, extract its one dump, write copy.json. Returns (zip, dump)."""
    record = read_copy_record(repository, run_id)
    artifact_id = record.pop("_artifact_id")
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "artifact.zip"
    with archive.open("wb") as fh:
        got = subprocess.run(["gh", "api", f"/repos/{repository}/actions/artifacts/{artifact_id}/zip"],
                             stdout=fh, stderr=subprocess.PIPE, timeout=600, check=False)
    if got.returncode:
        raise ValueError(f"artifact download failed: {got.stderr.decode(errors='replace').strip()[-200:]}")
    with zipfile.ZipFile(archive) as zf:
        members = zf.namelist()
        if len(members) != 1 or not DUMP_MEMBER_RE.fullmatch(members[0]):
            raise ValueError(f"artifact must hold exactly one carr-YYYYMMDD.sql.age, found {members}")
        dump = out_dir / members[0]
        dump.write_bytes(zf.read(members[0]))
    (out_dir / "copy.json").write_text(json.dumps(record, sort_keys=True))
    return archive, dump


def verify_receipt(receipt: dict[str, Any], repository: str,
                   now: datetime | None = None) -> tuple[list[str], dict[str, Any] | None]:
    """Re-derive the receipt's copy block from the provider (review G4).

    Returns (differences, bound receipt). The bound receipt carries the copy
    block AS RE-READ, not as the input said it, stamped with the verify
    binding the evaluator requires; it is None whenever anything differs. A
    receipt that already carries a binding is refused: re-verify the unbound
    receipt restore-rehearse wrote, never re-stamp a stamped one.
    """
    if "verification" in receipt:
        return (["receipt already carries a verification block"], None)
    copy = receipt.get("copy", {})
    source = copy.get("recorded_digest_source", {})
    if source.get("kind") != DIGEST_SOURCE_KIND:
        return ([f"recorded digest source is {source.get('kind')!r}, not the producer's Check"], None)
    fresh = read_copy_record(repository, int(source["workflow_run_id"]), int(source["check_run_id"]))
    fresh.pop("_artifact_id")
    differs = [k for k in COPY_KEYS if fresh.get(k) != copy.get(k)]
    if differs:
        return (differs, None)
    return ([], bind({**receipt, "copy": {k: fresh[k] for k in COPY_KEYS}}, "restore_exercise", now))


# ── the outbound census, read from the RESTORED database (review G2) ─────────

OUTBOUND_CENSUS_SOURCE = "ops.notification_delivery:device"
OUTBOUND_SQL = """
select id::text, notification_id::text, channel, state, attempted_at
  from ops.notification_delivery
 where channel = 'device'
 order by id
"""
READBACK_KEYS = ("idempotency_key", "item_id", "read_at", "readback")


def _utc_micros(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def outbound_item(row_id: str, notification_id: str, channel: str, state: str, attempted_at: datetime) -> dict[str, Any]:
    """One device delivery row as an outbound item (mapping decided with Jev, 0.65).

    in_app rows are rendered in the record layer and have no provider effect,
    so only device rows are outbound. A pending row may have been handed to the
    push provider before the outage, so its outcome is unknown and it carries
    its attempt instant into the settle window; delivered, suppressed and
    failed rows are final. The envelope key is the delivery's natural key
    (unique (notification_id, channel)), digested canonically.
    """
    envelope = "sha256:" + hashlib.sha256(canonical_json({"channel": channel, "notification_id": notification_id}).encode()).hexdigest()
    return {"item_id": row_id, "envelope_digest": envelope,
            "state": "outcome_unknown" if state == "pending" else "settled",
            "last_attempt_at": _utc_micros(attempted_at)}


def census_digest(items: list[dict[str, Any]]) -> str:
    """Byte-for-byte recovery-matrix.v5.js v5OutboundCensusDigest."""
    keyed = [{k: i[k] for k in ("envelope_digest", "item_id", "last_attempt_at", "state")} for i in items]
    keyed.sort(key=lambda i: i["item_id"])
    return "sha256:" + hashlib.sha256(canonical_json(keyed).encode("utf-8")).hexdigest()


def restored_outbound_items(dsn: str) -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("set default_transaction_read_only = on")
            cur.execute(OUTBOUND_SQL)
            return [outbound_item(*row) for row in cur.fetchall()]


def outbound_census(items: list[dict[str, Any]], readbacks: list[dict[str, Any]], restore_id: str,
                    now: datetime | None = None) -> dict[str, Any]:
    """The evaluator's request: every restored device row, the readbacks as given, the census, bound."""
    if not isinstance(readbacks, list):
        raise ValueError("readbacks must be a JSON array")
    for i, rb in enumerate(readbacks):
        if not isinstance(rb, dict) or sorted(rb) != sorted(READBACK_KEYS):
            raise ValueError(f"readbacks[{i}] must hold exactly {', '.join(READBACK_KEYS)}")
    request = {
        "restore_id": restore_id,
        "census": {"source": OUTBOUND_CENSUS_SOURCE, "digest": census_digest(items), "item_count": len(items)},
        "items": items,
        "readbacks": readbacks,
    }
    return bind(request, "outbound_census", now)


def build_receipt(*, copy: dict, target_kind: str, oracle_id: str, observed_digest: str,
                  artifact: dict[str, dict[str, Any]], restored: dict[str, dict[str, Any]],
                  started_at: str, finished_at: str) -> dict:
    missing = [k for k in COPY_KEYS if k not in copy]
    extra = [k for k in copy if k not in COPY_KEYS]
    if missing or extra:
        raise ValueError(f"copy record must hold exactly {', '.join(COPY_KEYS)} (missing {missing}, unknown {extra})")
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"target kind must be one of {TARGET_KINDS}; a production restore is never receipted here")
    return {
        "receipt_kind": RECEIPT_KIND,
        "target_kind": target_kind,
        "copy": {k: copy[k] for k in COPY_KEYS},
        "oracle_id": oracle_id,
        "observed_artifact_digest": observed_digest,
        "artifact_watermark": strip_columns(artifact),
        "restored_watermark": strip_columns(restored),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("count")
    rs = sub.add_parser("restored")
    rs.add_argument("--artifact", required=True)
    rs.add_argument("--dsn-env", default="RESTORE_DSN")
    c = sub.add_parser("compare")
    c.add_argument("--artifact", required=True)
    c.add_argument("--restored", required=True)
    d = sub.add_parser("digest")
    d.add_argument("path")
    f = sub.add_parser("fetch-copy")
    f.add_argument("--repository", required=True)
    f.add_argument("--run-id", required=True, type=int)
    f.add_argument("--out-dir", required=True)
    r = sub.add_parser("receipt")
    r.add_argument("--copy-record", required=True)
    r.add_argument("--target-kind", required=True)
    r.add_argument("--oracle-id", required=True)
    r.add_argument("--observed-digest", required=True)
    r.add_argument("--artifact", required=True)
    r.add_argument("--restored", required=True)
    r.add_argument("--started-at", required=True)
    r.add_argument("--finished-at", required=True)
    v = sub.add_parser("verify-receipt")
    v.add_argument("--repository", required=True)
    v.add_argument("receipt")
    o = sub.add_parser("outbound-census")
    o.add_argument("--restore-id", required=True)
    o.add_argument("--readbacks", required=True, help="JSON array of provider readbacks, each with its own read_at")
    o.add_argument("--dsn-env", default="RESTORE_DSN")
    a = p.parse_args(argv)
    try:
        if a.cmd == "count":
            print(json.dumps(watermark_from_dump(sys.stdin.buffer), sort_keys=True))
            return 0
        if a.cmd == "digest":
            print(file_digest(Path(a.path)))
            return 0
        if a.cmd == "restored":
            dsn = os.environ.get(a.dsn_env, "")
            if not dsn:
                raise ValueError(f"${a.dsn_env} is empty; the restored database DSN is read from the environment only")
            print(json.dumps(restored_watermark(dsn, _load_json(a.artifact)), sort_keys=True))
            return 0
        if a.cmd == "fetch-copy":
            archive, dump = fetch_copy(a.repository, a.run_id, Path(a.out_dir))
            print(json.dumps({"archive": str(archive), "dump": str(dump)}))
            return 0
        if a.cmd == "verify-receipt":
            differs, bound = verify_receipt(_load_json(a.receipt), a.repository)
            if differs:
                print(json.dumps({"receipt_copy_matches_provider": False, "differs": differs}), file=sys.stderr)
                return 1
            print(json.dumps(bound, sort_keys=True))
            return 0
        if a.cmd == "outbound-census":
            dsn = os.environ.get(a.dsn_env, "")
            if not dsn:
                raise ValueError(f"${a.dsn_env} is empty; the restored database DSN is read from the environment only")
            request = outbound_census(restored_outbound_items(dsn), _load_json(a.readbacks), a.restore_id)
            print(json.dumps(request, sort_keys=True))
            return 0
        artifact = _load_json(a.artifact)
        restored = _load_json(a.restored)
        if a.cmd == "compare":
            diffs = compare(artifact, restored)
            print(f"WATERMARK tables={len(artifact)} mismatches={len(diffs)}")
            for d_ in diffs[:40]:
                print(f"  MISMATCH {d_['table']}: artifact={d_['artifact_rows']} restored={d_['restored_rows']}"
                      f" content_differs={str(d_['content_differs']).lower()}")
            return 0 if not diffs else 1
        receipt = build_receipt(copy=_load_json(a.copy_record), target_kind=a.target_kind,
                                oracle_id=a.oracle_id, observed_digest=a.observed_digest,
                                artifact=artifact, restored=restored,
                                started_at=a.started_at, finished_at=a.finished_at)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (ValueError, OSError, json.JSONDecodeError, KeyError, zipfile.BadZipFile) as exc:
        print(f"restore-watermark: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # a status-module StatusError or a driver error: still a contract failure
        print(f"restore-watermark: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

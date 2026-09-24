#!/usr/bin/env python3
"""pitr-restore-proof.py — PROVE how recent a point production can be restored
to (V5-F08, the record-layer RPO cell). Run through bin/pitr-restore-proof.sh.

WHY A PROOF AND NOT A READ. The database provider's API reports the project's
history retention, but no field anywhere names a "latest restorable point", and
"point-in-time restore is enabled" is a setting, not a measurement. So this
measures it, with a positive probe AND a negative control:

  1. write a POSITIVE probe row (ops.pitr_probe via ops.write_pitr_probe: the
     nonce and the write instant are made by the server, migration 0597);
  2. wait until the DATABASE clock is >= its write instant + 61 s, and take
     T = that clock floored to whole seconds (the provider's point and the
     probe's write instant share one clock, so the machine's clock is never
     consulted);
  3. wait until the database clock is >= T + 6 s and write a NEGATIVE probe;
  4. pass the neon-disposable-branch metering admission, record the branch name
     to a local state file, then ask the provider API for a branch whose
     parent_id is the production branch and parent_timestamp is T, expiring in
     one hour;
  5. read the branch back: parent_id, parent_timestamp == T exactly, and a
     resolved parent_lsn;
  6. through a READ-ONLY session on the branch: the positive probe must be
     present with the same id, nonce and write instant, the negative probe must
     be ABSENT, and the core tables must hold rows;
  7. delete the branch by id and confirm the provider answers 404 for it.

`verify` then RECOMPUTES what can still be recomputed rather than trusting the
file `prove` wrote: it re-reads both probe rows from production (read-only),
re-reads the default branch id and the history retention from the provider,
and re-confirms the branch is gone. Its output is the evidence block
mcp-server/bin/recovery-matrix-evaluate.mjs `rpo -` judges at the real current
time.

WHAT IS WRITTEN. Production business data is never written. The only writes
are the two probe rows, through the one function migration 0597 made for them,
on the owner connection; ops.pitr_probe is append-only. Every other production
session is read-only (default_transaction_read_only=on).

NO SECRET LEAVES THIS PROCESS. The provider key is read from the environment
(NEON_API_KEY, loaded by the wrapper) and sent only as an Authorization header.
Connection strings come from the provider API into memory and go straight to
the driver: never printed, never on an argument list, never in a file.

--stand-in-parent rehearses the whole flow when production does not yet have
migration 0597: it first creates a disposable branch of production, applies
0597 to IT, and runs the proof with that branch as the parent. Its evidence is
written to a separate file and fails the evaluator by construction
(branch_parent_not_production) — it proves the mechanism, never the RPO.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
API = "https://console.neon.tech/api/v2"
PROJECT_ID = "steep-field-48688294"
DATABASE = "neondb"
OWNER_ROLE = "neondb_owner"
BRANCH_PREFIX = "pitr-proof-"
STALE_AFTER = timedelta(hours=1)
LIFETIME_MINUTES = 60
PROBE_MARGIN_SECONDS = 60          # V5_PITR_PROBE_MARGIN_SECONDS, plus one second of slack below
NEGATIVE_MARGIN_SECONDS = 5        # V5_PITR_NEGATIVE_MARGIN_SECONDS, plus one second of slack below
CORE_TABLES = ("public.party", "ops.run")
OUT = REPO / "out" / "pitr-restore-proof.json"
STAND_IN_OUT = REPO / "out" / "pitr-restore-proof.stand-in.json"
STATE = REPO / "out" / "pitr-proof-branches.json"
MIGRATION = REPO / "migrations" / "0597_pitr_probe.sql"
READ_ONLY = "-c default_transaction_read_only=on"
US_FORMAT = """to_char(written_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')"""


class ProofError(RuntimeError):
    pass


def say(message: str) -> None:
    print(message, flush=True)


# ── the provider API (the key is a header, never an argument) ────────────────

def api(method: str, path: str, body: dict | None = None, query: dict | None = None) -> tuple[int, Any]:
    key = os.environ.get("NEON_API_KEY", "")
    if not key:
        raise ProofError("NEON_API_KEY is not loaded; run through bin/pitr-restore-proof.sh")
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "carr-pitr-restore-proof"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        return exc.code, payload


def api_ok(method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
    status, payload = api(method, path, body, query)
    if status >= 300:
        raise ProofError(f"provider {method} {path} answered {status}: {str(payload.get('message', ''))[:200]}")
    return payload


def branches() -> list[dict]:
    return list(api_ok("GET", f"/projects/{PROJECT_ID}/branches").get("branches", []))


def default_branch_id() -> str:
    found = [b["id"] for b in branches() if b.get("default")]
    if len(found) != 1:
        raise ProofError(f"expected one default branch, found {len(found)}")
    return str(found[0])


def retention() -> tuple[int, str]:
    project = api_ok("GET", f"/projects/{PROJECT_ID}").get("project", {})
    value = project.get("history_retention_seconds")
    if not isinstance(value, int):
        raise ProofError("the provider returned no history_retention_seconds")
    return value, iso_now()


def connection_uri(branch_id: str) -> str:
    payload = api_ok("GET", f"/projects/{PROJECT_ID}/connection_uri",
                     query={"branch_id": branch_id, "database_name": DATABASE, "role_name": OWNER_ROLE})
    uri = payload.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ProofError(f"no connection URI for branch {branch_id}")
    return uri


def host_of(uri: str) -> str:
    return urllib.parse.urlsplit(uri).hostname or ""


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── branch lifecycle: state file, sweep, delete-and-confirm ──────────────────

def load_state() -> list[dict]:
    try:
        return list(json.loads(STATE.read_text()))
    except (OSError, json.JSONDecodeError):
        return []


def save_state(rows: list[dict]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(rows, indent=2, sort_keys=True))


def delete_and_confirm(branch_id: str, never: set[str]) -> bool:
    """Delete one branch by id and require the provider to answer 404 for it afterwards."""
    if branch_id in never:
        raise ProofError(f"refusing to delete protected branch {branch_id}")
    status, _ = api("DELETE", f"/projects/{PROJECT_ID}/branches/{branch_id}")
    if status >= 300 and status != 404:
        return False
    for _ in range(30):
        status, _ = api("GET", f"/projects/{PROJECT_ID}/branches/{branch_id}")
        if status == 404:
            save_state([r for r in load_state() if r.get("id") != branch_id])
            return True
        time.sleep(2)
    return False


def sweep(never: set[str]) -> None:
    """Delete proof branches a crashed earlier run left: named in the state file, or prefixed and over an hour old."""
    now = datetime.now(timezone.utc)
    recorded = {r.get("id") for r in load_state() if r.get("id")}
    for b in branches():
        if b.get("default") or b.get("protected") or b["id"] in never:
            continue
        created = datetime.fromisoformat(str(b.get("created_at", "")).replace("Z", "+00:00"))
        stale = str(b.get("name", "")).startswith(BRANCH_PREFIX) and now - created > STALE_AFTER
        if b["id"] in recorded or stale:
            ok = delete_and_confirm(b["id"], never)
            say(f"  sweep: {'deleted' if ok else 'COULD NOT DELETE'} leftover proof branch {b['id']} ({b.get('name')})")
            if not ok:
                raise ProofError(f"leftover proof branch {b['id']} could not be deleted; delete it by hand")
    live = {b["id"] for b in branches()}
    save_state([r for r in load_state() if r.get("id") in live])


def admit_metered_branch() -> None:
    """The neon-disposable-branch metering admission, BEFORE any branch create below."""
    active = sum(1 for b in branches() if not b.get("default"))
    got = subprocess.run([sys.executable, str(REPO / "ops" / "platform-metering-gate.py"),
                          "--gate", "neon-disposable-branch", "--requested-lifetime-minutes", str(LIFETIME_MINUTES),
                          "--active-nondefault-branches", str(active), "--cleanup-registered"],
                         capture_output=True, text=True, check=False)
    if got.returncode:
        raise ProofError(f"neon-disposable-branch metering admission refused: {got.stderr.strip()[-200:]}")


def create_branch(name: str, parent_id: str, parent_timestamp: str | None, never: set[str], *,
                  expires: bool = True) -> dict:
    """Create one disposable branch through the API. Admission first; the name is recorded before the call.

    `expires=False` only for the stand-in parent: the provider refuses a child
    of a branch that carries an expiry, so that one branch relies on this
    process's teardown and, failing that, the next run's sweep (its name has
    the proof prefix and it is in the state file).
    """
    admit_metered_branch()
    save_state([*load_state(), {"name": name, "id": None, "requested_at": iso_now()}])
    spec: dict[str, Any] = {"parent_id": parent_id, "name": name}
    if expires:
        spec["expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=LIFETIME_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if parent_timestamp is not None:
        spec["parent_timestamp"] = parent_timestamp
    try:
        status, payload = api("POST", f"/projects/{PROJECT_ID}/branches",
                              {"branch": spec, "endpoints": [{"type": "read_write"}]})
    except Exception as exc:  # the call may have landed: find it by its unique name, never orphan it
        found = [b for b in branches() if b.get("name") == name]
        for b in found:
            ok = delete_and_confirm(b["id"], never)
            say(f"  create failed ambiguously; branch {b['id']} named {name}: {'deleted' if ok else 'COULD NOT DELETE'}")
        raise ProofError(f"branch create failed: {type(exc).__name__}") from exc
    if status >= 300:
        raise ProofError(f"branch create answered {status}: {str(payload.get('message', ''))[:200]}")
    branch = payload.get("branch", {})
    branch_id = str(branch.get("id", ""))
    if not branch_id or branch_id in never:
        raise ProofError("branch create returned no id, or a protected branch's id")
    save_state([*[r for r in load_state() if r.get("name") != name], {"name": name, "id": branch_id, "requested_at": iso_now()}])
    return branch


def read_back_ready(branch_id: str, attempts: int = 60) -> dict:
    """The branch as the provider reports it once created: it fills in the
    resolved parent point (parent_timestamp beside parent_lsn) only after the
    create operation finishes, so an immediate read shows neither."""
    back: dict = {}
    for _ in range(attempts):
        back = api_ok("GET", f"/projects/{PROJECT_ID}/branches/{branch_id}").get("branch", {})
        if back.get("current_state") == "ready" and back.get("parent_timestamp") and back.get("parent_lsn"):
            return back
        time.sleep(2)
    return back


# ── the database side (the driver takes the DSN; nothing prints it) ──────────

def connect(uri: str, *, read_only: bool, attempts: int = 40):
    import psycopg  # the repo venv's driver

    last: Exception | None = None
    for _ in range(attempts):
        try:
            return psycopg.connect(uri, autocommit=True, options=READ_ONLY if read_only else None, connect_timeout=15)
        except psycopg.OperationalError as exc:  # a fresh branch's compute takes a moment to start
            last = exc
            time.sleep(3)
    raise ProofError(f"could not connect: {type(last).__name__}")


def db_now(conn) -> float:
    return float(conn.execute("select extract(epoch from clock_timestamp())").fetchone()[0])


def wait_db_clock(conn, target_epoch: float) -> float:
    while True:
        now = db_now(conn)
        if now >= target_epoch:
            return now
        time.sleep(min(5.0, max(0.2, target_epoch - now)))


def write_probe(conn, role: str) -> dict:
    row = conn.execute(f"select id::text, nonce, {US_FORMAT} from ops.write_pitr_probe(%s)", (role,)).fetchone()
    return {"id": row[0], "nonce": row[1], "written_at": row[2]}


def read_probes(conn, ids: list[str]) -> dict[str, dict]:
    rows = conn.execute(f"select id::text, nonce, {US_FORMAT} from ops.pitr_probe where id = any(%s::uuid[])", (ids,)).fetchall()
    return {r[0]: {"id": r[0], "nonce": r[1], "written_at": r[2]} for r in rows}


def epoch_of(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def iso_seconds(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_exact(raw: str) -> str:
    """The provider's timestamp in UTC, losing nothing: a fractional second is kept, never floored away."""
    if not raw:
        raise ProofError("the provider returned no parent_timestamp for the branch")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if dt.microsecond else "%Y-%m-%dT%H:%M:%SZ")


# ── prove ─────────────────────────────────────────────────────────────────────

def prove(stand_in: bool) -> dict:
    production_id = default_branch_id()
    never = {production_id}
    say(f"  ok    production branch {production_id}")
    sweep(never)
    created: list[str] = []
    parent_id = production_id
    try:
        if stand_in:
            stand = create_branch(f"{BRANCH_PREFIX}parent-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}", production_id, None, never,
                                  expires=False)
            created.append(stand["id"])
            parent_id = stand["id"]
            with connect(connection_uri(parent_id), read_only=False) as conn:
                if host_of(connection_uri(parent_id)) == host_of(connection_uri(production_id)):
                    raise ProofError("stand-in parent resolves to production's host; refusing")
                with conn.transaction():
                    conn.execute(MIGRATION.read_text())
            say(f"  ok    stand-in parent {parent_id}: migration 0597 applied to the disposable branch only")
        parent_uri = connection_uri(parent_id)
        with connect(parent_uri, read_only=False) as writer:
            positive = write_probe(writer, "positive")
            say(f"  ok    positive probe {positive['id']} written {positive['written_at']}")
            reached = wait_db_clock(writer, epoch_of(positive["written_at"]) + PROBE_MARGIN_SECONDS + 1)
            t_epoch = math.floor(reached)
            t_iso = iso_seconds(t_epoch)
            wait_db_clock(writer, t_epoch + NEGATIVE_MARGIN_SECONDS + 1)
            negative = write_probe(writer, "negative")
            say(f"  ok    T = {t_iso}; negative probe {negative['id']} written {negative['written_at']}")
        name = f"{BRANCH_PREFIX}{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        branch = create_branch(name, parent_id, t_iso, never)
        branch_id = branch["id"]
        created.append(branch_id)
        back = read_back_ready(branch_id)
        say(f"  ok    branch {branch_id} read back: parent {back.get('parent_id')} at {back.get('parent_timestamp')}, lsn {back.get('parent_lsn')}")
        branch_uri = connection_uri(branch_id)
        if host_of(branch_uri) == host_of(parent_uri):
            raise ProofError("the proof branch resolves to its parent's host; refusing")
        with connect(branch_uri, read_only=True) as reader:
            seen = read_probes(reader, [positive["id"], negative["id"]])
            core = {t: int(reader.execute(f"select count(*) from {t}").fetchone()[0]) for t in CORE_TABLES}
        parent_ts = str(back.get("parent_timestamp", ""))
        observation = {
            "project_id": PROJECT_ID,
            "proof_parent_branch_id": parent_id,
            "branch_id": branch_id,
            "requested_parent_timestamp": t_iso,
            "branch_parent_id": str(back.get("parent_id", "")),
            "branch_parent_timestamp": utc_exact(parent_ts),
            "branch_parent_lsn": str(back.get("parent_lsn") or ""),
            "positive_probe": positive,
            "negative_probe": negative,
            "positive_on_branch": seen.get(positive["id"]),
            "negative_present_on_branch": negative["id"] in seen,
            "branch_core_table_rows": core,
        }
        say(f"  probe: positive on branch {observation['positive_on_branch'] == positive}; negative absent {not observation['negative_present_on_branch']}")
        if stand_in:
            # The stand-in parent is deleted below, so its probe rows are read back
            # now, read-only; production's are re-read by `verify` instead.
            with connect(parent_uri, read_only=True) as conn:
                rows = read_probes(conn, [positive["id"], negative["id"]])
            observation["stand_in_parent_readback"] = {
                "positive": rows.get(positive["id"]), "negative": rows.get(negative["id"]), "read_at": iso_now()}
        return observation
    finally:
        for branch_id in reversed(created):
            ok = delete_and_confirm(branch_id, never)
            say(f"  teardown: branch {branch_id} {'deleted (provider answers 404)' if ok else 'COULD NOT BE DELETED — it expires within the hour; delete it by hand'}")


# ── verify: recompute, then hand the evaluator the evidence block ─────────────

def verify(observation: dict, stand_in: bool) -> dict:
    production_id = default_branch_id()
    parent_id = observation["proof_parent_branch_id"]
    if not stand_in and parent_id != production_id:
        raise ProofError("the recorded proof parent is not the production branch")
    status, _ = api("GET", f"/projects/{PROJECT_ID}/branches/{observation['branch_id']}")
    ids = [observation["positive_probe"]["id"], observation["negative_probe"]["id"]]
    if stand_in:  # the stand-in parent is gone; its rows were read back before teardown
        readback = observation["stand_in_parent_readback"]
    else:
        with connect(connection_uri(production_id), read_only=True) as conn:
            rows = read_probes(conn, ids)
        readback = {"positive": rows.get(ids[0]), "negative": rows.get(ids[1]), "read_at": iso_now()}
    retention_seconds, retention_read_at = retention()
    return {
        "source": "pitr_branch_proof",
        "proof_target_kind": "disposable_branch",
        "project_id": observation["project_id"],
        "production_branch_id": production_id,
        "branch_id": observation["branch_id"],
        "requested_parent_timestamp": observation["requested_parent_timestamp"],
        "branch_parent_id": observation["branch_parent_id"],
        "branch_parent_timestamp": observation["branch_parent_timestamp"],
        "branch_parent_lsn": observation["branch_parent_lsn"],
        "positive_probe": observation["positive_probe"],
        "negative_probe": observation["negative_probe"],
        "positive_on_branch": observation["positive_on_branch"],
        "negative_present_on_branch": observation["negative_present_on_branch"],
        "branch_core_table_rows": observation["branch_core_table_rows"],
        "branch_deleted_confirmed": status == 404,
        "history_retention_seconds": retention_seconds,
        "retention_read_at": retention_read_at,
        "production_readback": readback,
    }


def _interrupt(signum, _frame) -> None:
    raise SystemExit(130)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=("prove", "verify"))
    p.add_argument("--stand-in-parent", action="store_true")
    a = p.parse_args(argv)
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)
    out = STAND_IN_OUT if a.stand_in_parent else OUT
    try:
        if a.mode == "prove":
            observation = prove(a.stand_in_parent)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(observation, indent=2, sort_keys=True))
            say(f"  observation: {out}")
            return 0
        print(json.dumps(verify(json.loads(out.read_text()), a.stand_in_parent), sort_keys=True))
        return 0
    except (ProofError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"PITR PROOF FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

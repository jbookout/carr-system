#!/usr/bin/env python3
"""
capture-bridge.py — CARR dictation rig, WO-4 capture-bridge rig-side client.

Spec: WO-4 capture bridge (Cloudflare Worker, built and already deployed by
a separate work order — see .claude/worktrees/wo4-capture-bridge/SUMMARY.md
for the wire contract this file was built against). This file is the LOCAL
client for three of its four surfaces: claim, status, session polling. It
does NOT build the distiller (/capture/candidates is the distiller's write
path, gated on a separate design decision) and it never touches vendor/quill,
~/.config/quill, launchd, or git.

Subcommands:
    claim  <session_dir>          — claim a session with the worker
    status <session_dir> <state> [detail]   — report a state transition
    poll   <session_dir>          — check whether a meeting_record landed
    check                         — print config/provisioning status

Config: ~/.config/carr-capture/config.json = {"base_url", "device_id",
"token"}. Missing config, or any of the three fields empty, is a CLEAN
NO-OP everywhere (log one line, exit 0) — Joe has not provisioned the
device token yet (see SUMMARY.md, "Human provisioning"), and nothing about
the capture pipeline may break in the meantime. The same rule applies to
every HTTP failure: network error, timeout, 4xx, 5xx. This tool sits beside
the actual recording pipeline, never inside its critical path — a down
worker, a bad token, or a stale session must never stop a meeting from
being recorded or transcribed.

Never logs the device token, the session token, or any Authorization header
value — only "present"/"missing" where that matters (the check subcommand).

Pure standard library, matching transcribe_session.py's convention: no
third-party imports, no venv needed.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# --- constants -------------------------------------------------------------

CONFIG_PATH = Path.home() / ".config" / "carr-capture" / "config.json"
LOG_PATH = Path.home() / "Library" / "Logs" / "capture-bridge.log"

HTTP_TIMEOUT_SECONDS = 10


class BridgeNetworkError(Exception):
    """Any transport-level failure: DNS, connection refused, timeout, ..."""


# --- logging -----------------------------------------------------------


def log_line(message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {message}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def iso_now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- config --------------------------------------------------------------


def load_config_raw() -> dict[str, Any] | None:
    """Whatever parses at CONFIG_PATH, or None. Never raises."""
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_config() -> dict[str, str] | None:
    """base_url/device_id/token, all non-empty, or None. None is the single
    signal every subcommand treats as "clean no-op" — this is the only
    place that decides whether the config is usable."""
    data = load_config_raw()
    if data is None:
        return None
    base_url = str(data.get("base_url") or "").strip()
    device_id = str(data.get("device_id") or "").strip()
    token = str(data.get("token") or "").strip()
    if not base_url or not device_id or not token:
        return None
    return {"base_url": base_url.rstrip("/"), "device_id": device_id, "token": token}


# --- json marker helpers ------------------------------------------------


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_json_atomic(path: Path, data: dict[str, Any], mode: int | None) -> bool:
    """Write to a temp file in the same directory, chmod if requested, then
    os.replace into place — the closest thing to an atomic write in plain
    Python, so a half-written marker is never mistaken for a finished one.
    Same discipline as consent-watch.sh's marker write."""
    tmp = path.parent / f"{path.name}.tmp.{os.getpid()}"
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log_line(f"ERROR could not write {path.name}: {exc}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


# --- http ------------------------------------------------------------------


def http_request(
    method: str,
    url: str,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None,
) -> tuple[int, dict[str, Any]]:
    """Returns (status_code, parsed_json_body). Raises BridgeNetworkError on
    any transport failure (DNS, connection refused, timeout) — an HTTP error
    status (4xx/5xx) is NOT raised, it's a normal return, since callers need
    the status code to distinguish e.g. 401 from 409 from 500."""
    all_headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        all_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeNetworkError(str(exc)) from exc

    parsed: dict[str, Any] = {}
    if raw:
        try:
            decoded = json.loads(raw.decode("utf-8"))
            if isinstance(decoded, dict):
                parsed = decoded
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {}
    return status, parsed


# --- claim -----------------------------------------------------------------


def claim_cmd(cfg: dict[str, str], session_dir: Path) -> int:
    name = session_dir.name
    capture_marker = session_dir / ".capture.json"
    ingested_marker = session_dir / "ingested.json"

    if capture_marker.exists():
        log_line(f"SKIP claim already-claimed session={name}")
        return 0
    if ingested_marker.exists():
        log_line(f"SKIP claim already-ingested session={name}")
        return 0

    announcement = read_json(session_dir / "announcement.json")
    if announcement is None:
        log_line(f"SKIP claim no-announcement session={name} (consent proof missing)")
        return 0
    announced_at = str(announcement.get("announcement_fired_at") or "").strip()
    if not announced_at:
        log_line(f"SKIP claim announcement.json missing announcement_fired_at session={name}")
        return 0

    meta = read_json(session_dir / "meta.json")
    if meta is None:
        log_line(f"SKIP claim no-meta session={name}")
        return 0
    started_at = str(meta.get("started") or "").strip()
    if not started_at:
        log_line(f"SKIP claim meta.json missing started session={name}")
        return 0

    body = {
        "nonce": str(uuid.uuid4()),
        "device_id": cfg["device_id"],
        "mode": "meeting",
        "started_at": started_at,
        "consent": {"announced_at": announced_at},
    }
    headers = {"Authorization": f"Bearer {cfg['token']}"}
    url = f"{cfg['base_url']}/capture/claim"

    try:
        status, resp = http_request("POST", url, body, headers)
    except BridgeNetworkError as exc:
        log_line(f"ERROR claim network failure session={name}: {exc}")
        return 0

    if status != 200:
        log_line(f"ERROR claim rejected session={name} status={status}")
        return 0

    session_token = str(resp.get("session_token") or "").strip()
    ttl_seconds = resp.get("ttl_seconds")
    if not session_token:
        log_line(f"ERROR claim response missing session_token session={name}")
        return 0

    now = datetime.now(timezone.utc)
    claimed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = ""
    try:
        expires_at = (now + timedelta(seconds=float(ttl_seconds))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError):
        log_line(f"WARN claim response has no usable ttl_seconds session={name}")

    marker = {
        "session_token": session_token,
        "claimed_at": claimed_at,
        "expires_at": expires_at,
    }
    if not write_json_atomic(capture_marker, marker, mode=0o600):
        log_line(f"ERROR claim: could not persist .capture.json session={name}")
        return 0

    log_line(f"CLAIMED session={name} ttl_seconds={ttl_seconds}")

    # Per spec: a successful claim immediately reports "recording". Routed
    # through status_cmd (re-reading the marker we just wrote) so there is
    # exactly one code path that posts a status update, ever.
    status_cmd(cfg, session_dir, "recording", None)
    return 0


# --- status ------------------------------------------------------------


def status_cmd(cfg: dict[str, str], session_dir: Path, state: str, detail: str | None) -> int:
    name = session_dir.name
    marker = read_json(session_dir / ".capture.json")
    if marker is None:
        log_line(f"NOOP status no-capture-marker session={name} state={state}")
        return 0
    session_token = str(marker.get("session_token") or "").strip()
    if not session_token:
        log_line(f"NOOP status capture-marker-missing-token session={name} state={state}")
        return 0

    body: dict[str, Any] = {
        "session_token": session_token,
        "state": state,
        "at": iso_now_utc(),
    }
    if detail:
        body["detail"] = detail
    url = f"{cfg['base_url']}/capture/status"

    try:
        status, _resp = http_request("POST", url, body, None)
    except BridgeNetworkError as exc:
        log_line(f"ERROR status network failure session={name} state={state}: {exc}")
        return 0

    if status == 200:
        log_line(f"STATUS posted session={name} state={state}")
    elif status == 409:
        log_line(
            f"STATUS rejected(409) session={name} state={state} "
            "(backward transition or terminal state already reached)"
        )
    else:
        log_line(f"ERROR status rejected session={name} state={state} status={status}")
    return 0


# --- poll ------------------------------------------------------------------


def poll_cmd(cfg: dict[str, str], session_dir: Path) -> int:
    name = session_dir.name
    capture_marker = session_dir / ".capture.json"
    marker = read_json(capture_marker)
    if marker is None:
        log_line(f"NOOP poll no-capture-marker session={name}")
        return 0
    session_token = str(marker.get("session_token") or "").strip()
    if not session_token:
        log_line(f"NOOP poll capture-marker-missing-token session={name}")
        return 0

    headers = {"Authorization": f"Bearer {session_token}"}
    url = f"{cfg['base_url']}/capture/session"

    try:
        status, resp = http_request("GET", url, None, headers)
    except BridgeNetworkError as exc:
        log_line(f"ERROR poll network failure session={name}: {exc}")
        return 0

    if status == 401:
        log_line(f"ERROR poll unauthorized(401) session={name} (session token invalid/expired?)")
        return 0
    if status != 200:
        log_line(f"ERROR poll failed session={name} status={status}")
        return 0

    meeting_record = resp.get("meeting_record")
    meeting_record_str = str(meeting_record).strip() if meeting_record is not None else ""
    state = resp.get("state")

    if not meeting_record_str:
        # HARD GUARD: this is the ordering purge-recordings.sh depends on.
        # Writing ingested.json here without a real meeting_record would
        # make client audio/transcripts eligible for deletion before the
        # distilled, attributed narrative that is supposed to replace them
        # actually exists. Write NOTHING in that case — ever.
        log_line(f"POLL session={name} state={state} meeting_record=null — no ingested.json written")
        return 0

    # candidates.confirmed may be a count or a list depending on the
    # response shape actually returned; only extract refs if it's a list.
    records: list[str] = []
    candidates = resp.get("candidates")
    if isinstance(candidates, dict):
        confirmed = candidates.get("confirmed")
        if isinstance(confirmed, list):
            for item in confirmed:
                if isinstance(item, dict):
                    ref = item.get("ref") or item.get("id")
                    if ref:
                        records.append(str(ref))
                elif isinstance(item, str) and item.strip():
                    records.append(item.strip())

    ingested = {
        "ingested_at": iso_now_utc(),
        "by": "capture-bridge",
        "meeting_record": meeting_record,
        "records": records,
    }
    if not write_json_atomic(session_dir / "ingested.json", ingested, mode=None):
        log_line(f"ERROR poll: could not write ingested.json session={name}")
        return 0

    try:
        capture_marker.unlink()
    except OSError as exc:
        log_line(
            f"WARN poll: ingested.json written but could not remove .capture.json "
            f"session={name}: {exc}"
        )
        return 0

    log_line(f"INGESTED session={name} meeting_record={meeting_record} records={len(records)}")
    return 0


# --- check -----------------------------------------------------------------


def check_cmd() -> int:
    exists = CONFIG_PATH.exists()
    print(f"config_path: {CONFIG_PATH}")
    print(f"config_file: {'exists' if exists else 'missing'}")

    raw = load_config_raw()
    if raw is None:
        print("base_url: missing")
        print("device_id: missing")
        print("token: missing")
        print("provisioned: no")
        return 0

    base_url = str(raw.get("base_url") or "").strip()
    device_id = str(raw.get("device_id") or "").strip()
    token = str(raw.get("token") or "").strip()
    print(f"base_url: {'set' if base_url else 'missing'}")
    print(f"device_id: {'set' if device_id else 'missing'}")
    print(f"token: {'present' if token else 'missing'}")
    print(f"provisioned: {'yes' if (base_url and device_id and token) else 'no'}")
    return 0


# --- main --------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write(
            "usage: capture-bridge.py <claim|status|poll|check> [args...]\n"
        )
        return 2

    cmd = argv[1]

    if cmd == "check":
        return check_cmd()

    if cmd == "claim":
        if len(argv) < 3:
            sys.stderr.write("usage: capture-bridge.py claim <session_dir>\n")
            return 2
        cfg = load_config()
        if cfg is None:
            log_line("NOOP claim: no usable config (missing base_url/device_id/token)")
            return 0
        return claim_cmd(cfg, Path(argv[2]).resolve())

    if cmd == "status":
        if len(argv) < 4:
            sys.stderr.write("usage: capture-bridge.py status <session_dir> <state> [detail]\n")
            return 2
        cfg = load_config()
        if cfg is None:
            log_line("NOOP status: no usable config (missing base_url/device_id/token)")
            return 0
        detail = argv[4] if len(argv) > 4 else None
        return status_cmd(cfg, Path(argv[2]).resolve(), argv[3], detail)

    if cmd == "poll":
        if len(argv) < 3:
            sys.stderr.write("usage: capture-bridge.py poll <session_dir>\n")
            return 2
        cfg = load_config()
        if cfg is None:
            log_line("NOOP poll: no usable config (missing base_url/device_id/token)")
            return 0
        return poll_cmd(cfg, Path(argv[2]).resolve())

    sys.stderr.write(f"usage: unknown subcommand {cmd!r}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

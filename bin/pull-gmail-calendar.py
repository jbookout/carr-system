#!/usr/bin/env python3
"""pull-gmail-calendar.py — ORDER 12 lane (c): the Google side of capture.

Two halves, deliberately at different maturities.

CALENDAR — LIVE. It rides the existing house pattern rather than inventing one:
`fetch-calendar.sh` already lands Joe's published Outlook feed at
DNA/Team/calendar-latest.ics (weekday task `calendar-fetch-daily`), and Dell's
Mac drops calendar-latest-dell.ics beside it at 7:55 via Drive sync. This script
reads whatever .ics files are already on disk and POSTs a normalized event per
meeting to the ingest socket. It NEVER fetches a feed itself — one fetcher, one
job, and a network failure stays in the fetcher's log where it already gets read.

GMAIL — BUILT TO THE AUTH BOUNDARY AND STOPS THERE, on purpose. The order is
explicit: the auth method is decided WITH Joe, with his credentials, in his
hands. `--gmail` therefore prints the decision he has to make and exits 78; it
holds no credential, tries no connection, and stores nothing. The CARR Outlook
mailbox is NOT this script's business and never will be — it stays on the
Copilot-drop path (settled).

Payloads are UNTRUSTED (addendum A12). A meeting title is somebody else's words;
it is data on its way to triage, never an instruction. This script does not
interpret content, it normalizes and forwards.

Idempotence is the whole design. external_id is
`<owner>:<UID>:<DTSTART>`, so re-running over the same window is free — the
socket's unique (source, external_id) collapses every repeat to duplicate:true.
A MOVED meeting changes its DTSTART and therefore lands as a new row, which is
correct: a reschedule is news. An edited title on an unmoved meeting does NOT
re-land; the socket updates nothing on conflict. That is a known, accepted edge
(the record layer learns the new title at the next human touch, and the first row
already carries the meeting).

  ./bin/pull-gmail-calendar.py                  calendar half, live POST
  ./bin/pull-gmail-calendar.py --dry-run        print what would be posted
  ./bin/pull-gmail-calendar.py --days-back 3 --days-ahead 21
  ./bin/pull-gmail-calendar.py --gmail          prints the auth decision, exits 78

Stdlib only, by choice: this runs from a scheduled session under whatever python3
is on PATH, and a dependency that has to be installed is a dependency that will
one day not be installed at 7am.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAULT = os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI",
)
TEAM = os.path.join(VAULT, "DNA", "Team")
LOG = os.path.join(REPO, "out", "capture-lanes.log")
ENVFILE = os.path.expanduser("~/.config/carr/ingest.env")
DEFAULT_URL = "https://api.practicecre.com/ingest"

# owner slug -> the file its feed lands in. Both are written by somebody else's
# job; this script is a reader of both.
FEEDS = {
    "joe": os.path.join(TEAM, "calendar-latest.ics"),
    "dell": os.path.join(TEAM, "calendar-latest-dell.ics"),
}


def say(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOG, "a") as fh:
        fh.write(f"{stamp}  calendar-pull  {msg}\n")


# ---------------------------------------------------------------- ics parsing

def unfold(text):
    """RFC 5545 line folding: a continuation line begins with space or tab."""
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def unescape(value):
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_events(text):
    """Every VEVENT as {prop: (value, {params})}. VTIMEZONE blocks are skipped —
    they also contain DTSTART and would otherwise pollute the event list."""
    events, cur, depth_tz = [], None, 0
    for line in unfold(text):
        if line == "BEGIN:VTIMEZONE":
            depth_tz += 1
            continue
        if line == "END:VTIMEZONE":
            depth_tz = max(0, depth_tz - 1)
            continue
        if depth_tz:
            continue
        if line == "BEGIN:VEVENT":
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        parts = head.split(";")
        name = parts[0].upper()
        params = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.upper()] = v
        cur[name] = (value, params)
    return events


# Offsets are resolved from the Windows TZID names Exchange publishes. The feed
# is a US-territory calendar; anything unrecognised is carried through as a naive
# local timestamp rather than guessed at, and flagged in the payload.
TZ_OFFSETS = {
    "CENTRAL STANDARD TIME": -5,      # CDT for the months this system runs in
    "EASTERN STANDARD TIME": -4,
    "MOUNTAIN STANDARD TIME": -6,
    "PACIFIC STANDARD TIME": -7,
    "GREENWICH STANDARD TIME": 0,
    "UTC": 0,
}


def parse_dt(value, params):
    """-> (datetime|date, tz_label, all_day). Returns None on anything unparseable."""
    tzid = params.get("TZID", "")
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        try:
            return dt.datetime.strptime(value, "%Y%m%d").date(), tzid or "DATE", True
        except ValueError:
            return None, tzid, True
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", value)
    if not m:
        return None, tzid, False
    try:
        naive = dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    except ValueError:
        return None, tzid, False
    if m.group(2) == "Z":
        return naive.replace(tzinfo=dt.timezone.utc), "UTC", False
    off = TZ_OFFSETS.get(tzid.upper())
    if off is None:
        return naive, tzid or "UNKNOWN", False          # naive, and the payload says so
    return naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=off))), tzid, False


def as_date(when):
    if isinstance(when, dt.datetime):
        return when.date()
    return when


def normalize(ev, owner, feed_path):
    uid = ev.get("UID", ("", {}))[0].strip()
    if not uid:
        return None
    start_raw, start_params = ev.get("DTSTART", (None, {}))
    if not start_raw:
        return None
    start, start_tz, all_day = parse_dt(start_raw, start_params)
    if start is None:
        return None
    end_raw, end_params = ev.get("DTEND", (None, {}))
    end, end_tz, _ = parse_dt(end_raw, end_params) if end_raw else (None, None, False)

    def iso(x):
        return x.isoformat() if x is not None else None

    return {
        "external_id": f"{owner}:{uid}:{start_raw}",
        "kind": "calendar_event",
        "owner": owner,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "trust": "untrusted_payload",
        "source_file": os.path.basename(feed_path),
        "event": {
            "uid": uid,
            "summary": unescape(ev.get("SUMMARY", ("", {}))[0]).strip(),
            "location": unescape(ev.get("LOCATION", ("", {}))[0]).strip(),
            "description": unescape(ev.get("DESCRIPTION", ("", {}))[0]).strip()[:4000],
            "organizer": ev.get("ORGANIZER", ("", {}))[0].strip(),
            "status": ev.get("STATUS", ("", {}))[0].strip(),
            "all_day": all_day,
            "starts_at": iso(start),
            "ends_at": iso(end),
            "start_tz": start_tz,
            "start_raw": start_raw,
            "recurrence_id": ev.get("RECURRENCE-ID", ("", {}))[0].strip() or None,
            "sequence": ev.get("SEQUENCE", ("", {}))[0].strip() or None,
        },
    }


# ---------------------------------------------------------------- the socket

def load_env():
    if not os.path.exists(ENVFILE):
        return {}
    out = {}
    with open(ENVFILE) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def post(url, token, payload, timeout=30):
    body = json.dumps(payload).encode()
    if len(body) > 1_000_000:                 # the socket rejects >1MiB outright
        return None, "payload_too_large_locally", False
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            # NOT cosmetic. Cloudflare's edge refuses urllib's default
            # "Python-urllib/3.x" agent with `error code: 1010` — a 403 the Worker
            # never sees, which reads exactly like an auth failure and is not one.
            # Measured 2026-07-31: same request, same token, UA absent -> 403 1010,
            # UA present -> 401 from our own handler. Any future sender into this
            # socket needs a named agent.
            "user-agent": "carr-capture/1 (+carr-system bin/pull-gmail-calendar.py)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode() or "{}")
            return resp.status, data, bool(data.get("duplicate"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200], False
    except Exception as e:                    # network, DNS, TLS, timeout
        return None, str(e)[:200], False


# ---------------------------------------------------------------- halves

def run_calendar(args):
    env = load_env()
    url = env.get("CARR_INGEST_URL", DEFAULT_URL)
    token = env.get("CARR_INGEST_TOKEN_CALENDAR", "")
    if not args.dry_run and not token:
        print(
            "calendar-pull: NOT CONFIGURED — CARR_INGEST_TOKEN_CALENDAR is not set in "
            f"{ENVFILE}.\ncalendar-pull: that token is Joe's to create; see "
            "DNA/Deal Management/record-layer/ingest-tokens-setup.md"
        )
        say("NOT CONFIGURED (no calendar token)")
        return 78

    today = dt.date.today()
    lo = today - dt.timedelta(days=args.days_back)
    hi = today + dt.timedelta(days=args.days_ahead)

    posted = dup = failed = skipped = 0
    seen = set()
    for owner, path in FEEDS.items():
        if not os.path.exists(path):
            print(f"calendar-pull: {owner}: no feed at {os.path.basename(path)} (skipped)")
            say(f"{owner}: feed absent {path}")
            continue
        age_h = (dt.datetime.now().timestamp() - os.path.getmtime(path)) / 3600.0
        with open(path, errors="replace") as fh:
            text = fh.read()
        events = parse_events(text)
        in_window = 0
        for ev in events:
            item = normalize(ev, owner, path)
            if item is None:
                skipped += 1
                continue
            starts = item["event"]["starts_at"]
            try:
                d = as_date(dt.datetime.fromisoformat(starts)) if "T" in (starts or "") \
                    else dt.date.fromisoformat(starts)
            except Exception:
                skipped += 1
                continue
            if not (lo <= d <= hi):
                continue
            if item["external_id"] in seen:      # the same meeting on both feeds
                continue
            seen.add(item["external_id"])
            in_window += 1
            if args.dry_run:
                print(json.dumps(item, indent=2)[:1200])
                continue
            code, resp, was_dup = post(url, token, item)
            if code and 200 <= code < 300:
                if was_dup:
                    dup += 1
                else:
                    posted += 1
            else:
                failed += 1
                say(f"FAIL {item['external_id'][:80]} -> {code} {resp}")
        print(
            f"calendar-pull: {owner}: {len(events)} events in feed, {in_window} in window "
            f"(feed age {age_h:.1f}h)"
        )
        say(f"{owner}: feed_events={len(events)} in_window={in_window} age_h={age_h:.1f}")

    print(
        f"calendar-pull: source=calendar window={lo}..{hi} posted={posted} "
        f"duplicate={dup} failed={failed} unparseable={skipped}"
    )
    say(f"summary posted={posted} duplicate={dup} failed={failed} unparseable={skipped}")
    return 1 if failed else 0


GMAIL_BOUNDARY = """\
calendar-pull: the Gmail half is NOT built past this point, by design (ORDER 12 lane c).

The remaining decision is Joe's and it needs his hands and his credentials:

  OPTION A — App password (simplest, ~3 minutes, no console)
    Requires 2-Step Verification on the Google account, then an app password from
    myaccount.google.com/apppasswords. The puller reads mail over IMAP with that
    password. One credential, no consent screen, no token refresh, revocable in one
    click. Downside: an app password is full mailbox access — read AND (over SMTP)
    send. That collides with the send-capability rule (secrets-inventory: SEND-class
    credentials live only behind the human-gated route), so it would have to be
    stored as a SEND-class secret even though this job only reads.

  OPTION B — Reuse the OAuth client already created for the connector
    Project carr-record-layer already holds the "CARR MCP Worker" OAuth client. Add
    the gmail.readonly scope and a desktop/loopback client, and the puller holds a
    refresh token scoped to READING mail only — it is structurally incapable of
    sending. Downside: Gmail scopes are RESTRICTED, so publishing that consent
    screen can pull the project into Google verification. Staying in Testing avoids
    that (refresh tokens expire every 7 days in Testing mode, which means a
    re-consent tap roughly weekly — the thing worth checking before choosing).

  Recommendation, stated plainly and not acted on: OPTION B if the weekly
  re-consent in Testing mode turns out not to apply (verify at build), OPTION A
  only with the SEND-class handling that an app password actually deserves.

Nothing was attempted, nothing was stored, no connection was made.
"""


def main():
    ap = argparse.ArgumentParser(description="ORDER 12 lane (c): calendar + Gmail capture")
    ap.add_argument("--gmail", action="store_true", help="print the auth decision and stop")
    ap.add_argument("--dry-run", action="store_true", help="print payloads, POST nothing")
    ap.add_argument("--days-back", type=int, default=1)
    ap.add_argument("--days-ahead", type=int, default=14)
    args = ap.parse_args()

    if args.gmail:
        print(GMAIL_BOUNDARY)
        say("gmail half invoked; stopped at the auth boundary as ordered")
        return 78
    return run_calendar(args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""graph-draft.py — create a draft in the REAL carr.us mailbox via Microsoft Graph.

WHY THIS REPLACES bin/outlook-draft.py. The AppleScript version works and is
useless: New Outlook exposes no mailbox to AppleScript at all — 0 of 157 folders
carry an account — so its drafts land in an "On My Computer" store with no
account behind them. They never sync, never reach the phone, and cannot be sent
from the carr.us identity. Joe found his Drafts folder empty, which is what
finally proved it. Graph writes to the actual mailbox.

WHY NOT THE BROWSER. Driving Outlook Web through Joe's logged-in Chrome would
also work and needs no approval, but he ruled it a LAST RESORT, not a peer
option: "I don't want you to write into my Chrome unless that's the only option."
This is the programmatic path he asked for instead.

THE ONE UNKNOWN, and running this settles it. Delegated Mail.ReadWrite is NOT on
Microsoft's admin-consent-required list, so whether Joe can consent by himself
depends on a single CARR tenant setting. Nobody has tested it. The first run of
this script IS the test: sign in and you either get an ordinary consent screen
(done, no IT involvement) or "Need admin approval" (then it is a CARR IT ask, and
now it is a fact rather than an assumption). An earlier session reported that ask
as a certainty without checking, which is the mistake this docstring exists to
stop repeating.

Tenant (discovered, not guessed): carr.us -> 934fc090-66d2-42ef-9550-40cd1b51cbb9
The device-authorization endpoint is present on it and answers.

THERE IS NO SEND PATH HERE. It creates drafts. Graph's sendMail is deliberately
not called and must not be added to this file — a human opens Drafts and presses
Send, which is the one human gate. If a send is ever wanted it belongs in its own
file with a hardcoded recipient allowlist, the way identity.js does it.

SETUP (once)
  1. portal.azure.com -> App registrations -> New registration
     - Supported account types: any Entra directory (multi-tenant is fine; the
       app does NOT have to live in CARR's tenant)
     - Authentication -> Add a platform -> Mobile and desktop -> tick
       "https://login.microsoftonline.com/common/oauth2/nativeclient"
     - Advanced settings -> Allow public client flows: YES  (device code needs it)
  2. API permissions -> Microsoft Graph -> Delegated -> Mail.ReadWrite
  3. echo "<the Application (client) ID>" > ~/.config/carr/graph-client-id
  4. bin/graph-draft.py --login       (sign in as joe.bookout@carr.us)

USAGE
  echo '{"to":"a@b.com","subject":"Hi","body":"line one\\nline two"}' \\
      | bin/graph-draft.py
  bin/graph-draft.py --to a@b.com --subject Hi --body 'text'
  bin/graph-draft.py --login    force a fresh sign-in
  bin/graph-draft.py --whoami   prove the token works, print the signed-in mailbox

Same JSON interface as bin/outlook-draft.py, so anything already calling that can
switch by changing the command and nothing else.
"""

import argparse
import html as html_mod
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TENANT = "934fc090-66d2-42ef-9550-40cd1b51cbb9"
AUTH = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "https://graph.microsoft.com/Mail.ReadWrite offline_access openid profile"

CFG = Path.home() / ".config" / "carr"
CLIENT_ID_FILE = CFG / "graph-client-id"
TOKEN_FILE = CFG / "graph-token.json"


def client_id():
    cid = (os.environ.get("CARR_GRAPH_CLIENT_ID") or "").strip()
    if not cid:
        try:
            cid = CLIENT_ID_FILE.read_text().strip()
        except FileNotFoundError:
            cid = ""
    if not cid:
        sys.exit(
            f"no client id. Register an app (see the setup notes at the top of "
            f"this file), then:\n  echo '<client-id>' > {CLIENT_ID_FILE}"
        )
    return cid


def post_form(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"error": f"http_{e.code}", "error_description": e.reason}


def save_tokens(tok):
    CFG.mkdir(parents=True, exist_ok=True)
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 120
    TOKEN_FILE.write_text(json.dumps(tok, indent=2))
    # Credential material. 0600 so it is not world-readable on a shared machine.
    TOKEN_FILE.chmod(0o600)
    return tok


def device_login():
    """Device-code flow. Prints a code for the human; never handles a password.

    The script never sees or asks for credentials — the human authenticates at
    Microsoft's own page and this only polls for the result.
    """
    cid = client_id()
    d = post_form(f"{AUTH}/devicecode", {"client_id": cid, "scope": SCOPES})
    if "user_code" not in d:
        sys.exit(f"device code refused: {d.get('error')} — "
                 f"{(d.get('error_description') or '')[:300]}")

    print("\n  Go to: " + d["verification_uri"])
    print("  Code:  " + d["user_code"])
    print("\n  Sign in as joe.bookout@carr.us. If you are told an administrator")
    print("  must approve this, that is the answer we were missing — stop and")
    print("  say so; it means the tenant blocks user consent.\n")

    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        t = post_form(f"{AUTH}/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": cid,
            "device_code": d["device_code"],
        })
        err = t.get("error")
        if not err:
            save_tokens(t)
            print("signed in; token cached at " + str(TOKEN_FILE))
            return t
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        sys.exit(f"sign-in failed: {err} — {(t.get('error_description') or '')[:300]}")
    sys.exit("device code expired before sign-in completed")


def access_token():
    try:
        tok = json.loads(TOKEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return device_login()["access_token"]

    if tok.get("expires_at", 0) > time.time():
        return tok["access_token"]

    if tok.get("refresh_token"):
        t = post_form(f"{AUTH}/token", {
            "grant_type": "refresh_token",
            "client_id": client_id(),
            "refresh_token": tok["refresh_token"],
            "scope": SCOPES,
        })
        if "access_token" in t:
            return save_tokens(t)["access_token"]
    return device_login()["access_token"]


def graph(method, path, payload=None):
    req = urllib.request.Request(
        GRAPH + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + access_token(),
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        sys.exit(f"Graph {method} {path} failed: HTTP {e.code} — {detail}")


def as_list(value):
    if value is None:
        return []
    parts = value.split(",") if isinstance(value, str) else list(value)
    return [p.strip() for p in parts if str(p).strip()]


def to_html(body):
    """Escape first, then break lines — a body containing < or & must survive."""
    return "<div>" + html_mod.escape(body or "").replace("\n", "<br>\n") + "</div>"


def recips(addrs):
    return [{"emailAddress": {"address": a}} for a in addrs]


def main():
    ap = argparse.ArgumentParser(description="Create a real mailbox draft. Never sends.")
    ap.add_argument("--login", action="store_true", help="force a fresh sign-in")
    ap.add_argument("--whoami", action="store_true", help="prove the token works")
    ap.add_argument("--to")
    ap.add_argument("--cc")
    ap.add_argument("--bcc")
    ap.add_argument("--subject")
    ap.add_argument("--body")
    ap.add_argument("--html")
    args = ap.parse_args()

    if args.login:
        device_login()
        return
    if args.whoami:
        me = graph("GET", "/me")
        print(f"signed in as {me.get('userPrincipalName')} ({me.get('displayName')})")
        return

    if args.to or args.subject or args.body or args.html:
        payload = {k: v for k, v in vars(args).items() if v is not None}
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            raise SystemExit("no input: pass flags or pipe JSON on stdin")
        payload = json.loads(raw)

    to = as_list(payload.get("to"))
    if not to:
        raise SystemExit("refusing to create a draft with no recipient")

    msg = {
        "subject": payload.get("subject") or "",
        "body": {"contentType": "HTML",
                 "content": payload.get("html") or to_html(payload.get("body", ""))},
        "toRecipients": recips(to),
    }
    if as_list(payload.get("cc")):
        msg["ccRecipients"] = recips(as_list(payload["cc"]))
    if as_list(payload.get("bcc")):
        msg["bccRecipients"] = recips(as_list(payload["bcc"]))

    created = graph("POST", "/me/messages", msg)
    print(created.get("id", "(created, no id returned)"))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""outlook-draft.py — put a DRAFT in Joe's local Outlook. Never sends.

WHY THIS EXISTS (Joe's ruling, 2026-08-03: "lets just do the local outlook
script"). The handover channel in ai-operating-notes.md:64 says drafts for Joe's
action are emailed to joe.bookout@carr.us. This replaces whatever transport sat
behind that with one we own outright.

WHY LOCAL OUTLOOK AND NOT AN API. carr.us is Microsoft 365 (SPF carries
spf.protection.outlook.com, with Barracuda inbound and Exclaimer signatures).
The two other candidates both lost on cost-of-access, not on capability:
  * Microsoft Graph (POST /me/messages) is the robust answer and works headless,
    but it needs an app registration in CARR's Azure tenant and Mail.ReadWrite
    usually requires ADMIN CONSENT — a CARR IT ask that may simply be refused.
  * Make.com still needs the same Microsoft authorization underneath, so it does
    not remove the hard part; it adds a third party holding mailbox credentials
    and a dependency Joe had already trimmed out of templates (team-loop T27).
Local Outlook needs no registration, no consent, no vendor and no money. Its one
real limit: it only runs while this Mac is awake. If drafts are ever needed while
the Mac is closed, THAT is the moment to go ask CARR IT about Graph — not before.

WHAT IT DELIBERATELY CANNOT DO. There is no send path here or in the AppleScript.
A draft lands in Drafts, addressed and formatted, and a human presses Send. That
keeps the one-human-gate rule intact and is why this lives on the Mac rather than
in the Worker, whose mcp.js states in capitals that no send capability exists or
will exist there.

USAGE
  echo '{"to":"a@b.com","subject":"Hi","body":"line one\\nline two"}' \\
      | bin/outlook-draft.py
  bin/outlook-draft.py --to a@b.com --subject Hi --body 'text'

  JSON keys: to, cc, bcc (string or list), subject, body (plain text),
             html (optional; when present it is used verbatim instead of body)

Prints the created message id. Exit 0 on success.
"""

import argparse
import html as html_mod
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "outlook-draft.applescript"


def as_list(value):
    """Accept 'a@b.com', 'a@b.com, c@d.com', or ['a@b.com', 'c@d.com']."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return [p.strip() for p in parts if str(p).strip()]


def to_html(body):
    """Plain text -> HTML. Escaped first, so a body containing < or & survives.

    Outlook types `content` as HTML, so handing it raw text collapses newlines
    and would silently mangle any angle bracket a draft happened to contain.
    """
    escaped = html_mod.escape(body or "")
    return "<div>" + escaped.replace("\n", "<br>\n") + "</div>"


def outlook_running():
    r = subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to (name of processes) contains "Microsoft Outlook"'],
        capture_output=True, text=True)
    return r.stdout.strip() == "true"


def create_draft(subject, body_html, to, cc, bcc):
    if not SCRIPT.exists():
        raise SystemExit(f"missing AppleScript: {SCRIPT}")
    # Every value goes through argv. Nothing is interpolated into the script, so
    # quotes/backslashes in a subject or body are inert data rather than syntax.
    args = ["osascript", str(SCRIPT), subject, body_html,
            ",".join(to), ",".join(cc), ",".join(bcc)]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"osascript failed: {r.stderr.strip()}")
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description="Create an Outlook draft. Never sends.")
    ap.add_argument("--to")
    ap.add_argument("--cc")
    ap.add_argument("--bcc")
    ap.add_argument("--subject")
    ap.add_argument("--body")
    ap.add_argument("--html")
    args = ap.parse_args()

    if args.to or args.subject or args.body or args.html:
        payload = {k: v for k, v in vars(args).items() if v is not None}
    else:
        raw = sys.stdin.read().strip()
        if not raw:
            raise SystemExit("no input: pass flags or pipe JSON on stdin")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SystemExit(f"stdin is not valid JSON: {e}")

    to = as_list(payload.get("to"))
    if not to:
        raise SystemExit("refusing to create a draft with no recipient")
    subject = payload.get("subject") or ""
    body_html = payload.get("html") or to_html(payload.get("body", ""))

    if not outlook_running():
        subprocess.run(["open", "-g", "-a", "Microsoft Outlook"], check=False)

    msg_id = create_draft(subject, body_html, to,
                          as_list(payload.get("cc")), as_list(payload.get("bcc")))
    print(msg_id)


if __name__ == "__main__":
    main()

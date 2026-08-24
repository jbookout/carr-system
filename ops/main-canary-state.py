#!/usr/bin/env python3
"""main-canary-state.py — is main red right now, and who says so?

LAYER 3 of the 2026-08-23 CI-failures council: merge freeze and attribution
while main is red. Both chairs asked for it and Codex attached the kill
criterion that shapes the whole file: "Kill any implementation under which a
skipped or neutral check accidentally satisfies branch protection." So this
file never reports a state it does not have. Not-knowing is a refusal, never a
pass.

BE HONEST ABOUT WHAT THE FREEZE ACTUALLY IS. Most of the blocking was already
there before this file existed, and pretending otherwise would misdescribe the
system: the required check builds refs/pull/N/merge, so a broken main is inside
every open PR's run and turns it red, and ruleset 20824501 will not let a red PR
merge. Main being red already stops merges. What was missing is everything
around that fact --

  1. THE PILOT. .github/workflows/automerge-pilot.yml is an unattended overnight
     merge lane. It would happily plan a merge against a broken main and find
     out 20 minutes later in its verify job. It now asks here first and declines
     in five seconds.
  2. THE NAME. Six sessions on 2026-08-22 saw an opaque red and each diagnosed
     it separately. ops/inherited-from-main.py tells a victim "not yours"; this
     tells anyone who asks WHOSE it is, which run found it, and on which commit.
  3. A LEVER. Joe can freeze merges by hand -- repository variable
     CARR_MAIN_FREEZE=on -- without editing a workflow or a ruleset.

There is deliberately NO force-green. A lever that can declare a broken main
healthy is a verdict weakener wearing a convenience hat, and the council
forbade weakening any verdict to improve the number.

WHERE THE STATE LIVES: nowhere. It is DERIVED, each time, from the canary's own
run history through the Actions API. That is the whole reason this design was
chosen over a repository variable: a variable has to be WRITTEN, the default
GITHUB_TOKEN cannot write repository variables (that needs a PAT), and a stored
flag can get stuck red after the fix lands and quietly freeze the repository
until someone remembers it exists. A derived state cannot get stuck -- the next
green canary run is the unfreeze, with nothing to remember and nothing to reset.

CANCELLED RUNS ARE NOT VERDICTS. The canary debounces by cancelling itself
during a merge burst, so most of its recent runs conclude "cancelled". Reading
one of those as a state is how a freeze would flap. Only success, failure and
timed_out count.

Exit 0 green (or reporting only) · 1 red or unknown under --require-green
  ops/main-canary-state.py                    # say what main's state is
  ops/main-canary-state.py --require-green    # refuse unless it is provably green
  ops/main-canary-state.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

WORKFLOW = "main-canary.yml"
# Cancelled and skipped are absent on purpose — see the header.
CONCLUSIVE = ("success", "failure", "timed_out")


def repo_slug() -> str | None:
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    p = subprocess.run(["git", "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    url = p.stdout.strip().removesuffix(".git")
    if url.startswith("git@") and ":" in url:
        return url.split(":", 1)[1]
    if "github.com/" in url:
        return url.split("github.com/", 1)[1]
    return None


def api(path: str) -> dict | None:
    """The Actions API, through gh when it is authenticated, else the token."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "carr-main-canary-state"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except (urllib.error.URLError, OSError, ValueError):
            pass  # fall through to gh
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def state() -> dict:
    """green | red | unknown, with the evidence that produced it."""
    if os.environ.get("CARR_MAIN_FREEZE", "").strip().lower() == "on":
        return {"state": "red", "source": "CARR_MAIN_FREEZE=on",
                "detail": "a human froze merges by hand; clear the repository "
                          "variable CARR_MAIN_FREEZE to unfreeze"}
    slug = repo_slug()
    if not slug:
        return {"state": "unknown", "source": "no repository",
                "detail": "could not work out which GitHub repository this is"}
    data = api(f"/repos/{slug}/actions/workflows/{WORKFLOW}/runs"
               f"?branch=main&per_page=20&event=push")
    if data is None:
        return {"state": "unknown", "source": "api",
                "detail": f"could not read {WORKFLOW} runs (no token, or the API refused)"}
    runs = data.get("workflow_runs") or []
    for run in runs:            # newest first, as the API returns them
        if run.get("conclusion") in CONCLUSIVE:
            green = run["conclusion"] == "success"
            return {"state": "green" if green else "red",
                    "source": f"{WORKFLOW} run {run.get('run_number')}",
                    "sha": (run.get("head_sha") or "")[:12],
                    "url": run.get("html_url", ""),
                    "detail": ("main was green when the canary last concluded"
                               if green else
                               f"the main canary FAILED on {(run.get('head_sha') or '')[:12]}")}
    if runs:
        return {"state": "unknown", "source": WORKFLOW,
                "detail": f"the last {len(runs)} canary runs were all cancelled or "
                          "still running — a merge burst is in flight, no verdict yet"}
    return {"state": "unknown", "source": WORKFLOW,
            "detail": f"{WORKFLOW} has never concluded a run on main"}


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--require-green", action="store_true",
                    help="exit nonzero unless main is PROVABLY green")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    s = state()
    if a.json:
        print(json.dumps(s, indent=2))
    else:
        print(f"main: {s['state'].upper()}  ({s['source']})")
        print(f"  {s['detail']}")
        if s.get("url"):
            print(f"  {s['url']}")

    if not a.require_green:
        return 0
    if s["state"] == "green":
        return 0

    print()
    if s["state"] == "red":
        print("MERGES ARE FROZEN — main is red, and this lane will not merge onto it.")
        print("  THE MOVE: fix main, in its own change. The canary going green is the")
        print("  unfreeze; there is nothing to reset and no flag to clear.")
        print("  Open PRs failing a check they did not touch are victims of this same")
        print("  break — ops/inherited-from-main.py names it for them.")
    else:
        # UNKNOWN REFUSES TOO, and that is the Codex kill criterion honoured: an
        # unattended overnight merge that cannot establish main is green must not
        # proceed on the strength of not having looked. Declining costs one night.
        print("MERGES ARE HELD — main's state could not be established, so this lane")
        print("  declines rather than merging on an unchecked base.")
        print("  THE MOVE: run the main canary on main (workflow_dispatch) and re-run")
        print("  this lane once it concludes.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # A crash is an unknown state, and unknown never satisfies --require-green.
        print(f"main-canary-state: unknown — {exc!r}")
        sys.exit(1)

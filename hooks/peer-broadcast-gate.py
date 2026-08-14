#!/usr/bin/env python3
"""peer-broadcast-gate.py — one question goes to one session, not to the room.

WHY (Joe, 2026-08-14): "why are all my sessions repeating there reports. this
shit is burning a lot of wasted tokens. fix it now"

WHAT HAPPENED. A session could not tell which peer owned five modified files, so
it asked nine of them. Seven replied, each with a multi-paragraph inventory of
its own session's work, because a session answering "are these yours" defends
itself by reciting everything it wrote. Every reply also landed in the asker's
context. Sixteen long messages.

The answer had been one command away the entire time. `git diff origin/main`
showed all five files byte-identical to the remote — there was nothing to own.
The "238 lines at risk" had been measured against a local HEAD five commits
behind. Nine sessions were interrupted to answer a question the repository had
already answered.

WHY A GATE AND NOT A RULE. The cost is externalised: the asker pays for one
message and every other session on the machine pays for reading and answering
it. A cost you do not feel is one you do not stop paying, which is what makes
this a bad fit for an advisory rule and a good fit for something that denies.

WHAT IT ALLOWS, deliberately. Replies are never blocked — answering a session
that wrote to you first is a conversation, not a broadcast, and a reply address
(uds:/tmp/cc-socks/...) is how those are addressed. Two named peers per window
are also allowed: asking the likely owner, being told no, and asking the next
best candidate is legitimate targeted work. The THIRD distinct named peer in the
same window is the fan-out this exists to stop.

It gates breadth, not content. It cannot tell whether a question was worth
asking; it can tell that the same session is working through a roster.
"""
import json
import os
import re
import sys
import time

STATE = os.path.expanduser("~/.cache/carr/peer-broadcast-gate.json")
WINDOW_SECONDS = 1800   # 30 minutes: long enough to span one investigation
MAX_NAMED_PEERS = 2     # the third distinct named peer is a broadcast

# A PEER IS ITS NAME. The ref is a disambiguator, not part of who it is.
#
# ListAgents prints `name [ref]`, and SendMessage REQUIRES that form whenever a
# bare name is ambiguous or unconfirmed: "'x' is not an agent in this
# conversation. Re-send with the ref to confirm you mean: x [d7e9b2]". This gate
# keyed on the raw string, so `x` and `x [d7e9b2]` counted as two sessions.
#
# That deadlocked on 2026-08-14. A caller who had already reached `x` was told
# `x [d7e9b2]` was "another one" — while the refusal listed `x` as already
# messaged in the same sentence — and neither spelling could be sent: the bare
# name was refused by the tool, the ref-qualified one by this gate. The single
# targeted message this gate exists to encourage was the one thing that could
# not be delivered.
#
# Anchored to the END and deliberately narrow: only a trailing bracketed token of
# ref-shaped characters is stripped, so a name that merely contains a bracket is
# untouched. Reply addresses return before this is ever consulted.
REF_SUFFIX = re.compile(r"\s*\[[0-9A-Za-z._:-]+\]\s*$")


def peer_identity(to: str) -> str:
    """The stable key for a recipient: its name, with any trailing [ref] removed.

    Falls back to the original string if stripping would leave nothing, so a
    recipient that is ONLY a bracketed token still counts as a peer rather than
    collapsing to empty and silently sharing one slot with every other such name.
    """
    return REF_SUFFIX.sub("", to).strip() or to


def load(now):
    try:
        with open(STATE) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    # Drop anything older than the window so a long session is not punished for
    # a burst it sent hours ago.
    #
    # Keys are normalised ON READ, not only on write, so state written before the
    # ref fix heals itself instead of keeping a session deadlocked until the
    # window expires. Where both spellings are present the NEWER timestamp wins,
    # which is what the live path would have recorded had it always normalised.
    fresh = {}
    for who, ts in data.items():
        if not isinstance(ts, (int, float)) or now - ts >= WINDOW_SECONDS:
            continue
        key = peer_identity(str(who))
        if ts > fresh.get(key, 0):
            fresh[key] = ts
    return fresh


def save(data):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, STATE)


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # never block on a malformed hook payload

    tool = payload.get("tool_name") or ""
    if not tool.endswith("SendMessage"):
        return 0

    to = str((payload.get("tool_input") or {}).get("to") or "").strip()
    if not to:
        return 0

    # A REPLY IS NOT A BROADCAST. Reply addresses are sockets; a named peer is
    # an outbound ask. Blocking replies would make the gate punish the sessions
    # behaving correctly, which is the opposite of the point.
    if to.startswith("uds:") or to.startswith("/"):
        return 0

    # Identity is the name; `x` and `x [d7e9b2]` are one peer. See REF_SUFFIX.
    who = peer_identity(to)

    now = time.time()
    seen = load(now)
    if who in seen:           # re-messaging the same peer is a thread, not fan-out
        seen[who] = now
        save(seen)
        return 0

    if len(seen) >= MAX_NAMED_PEERS:
        already = ", ".join(sorted(seen))
        print(
            "PEER BROADCAST GATE — refused.\n\n"
            f"You have already messaged {len(seen)} different sessions in the last "
            f"{WINDOW_SECONDS // 60} minutes ({already}), and this is another one. "
            "That is a roster, not a question.\n\n"
            "WHAT THIS COSTS, which you do not pay: every recipient reads it, composes "
            "a reply, and sends it back into your context — and a session asked 'is this "
            "yours' answers by reciting its entire session inventory. On 2026-08-14 nine "
            "asks produced seven multi-paragraph replies, all correct, all wasted.\n\n"
            "DO THIS INSTEAD:\n"
            "  1. ANSWER IT FROM THE REPO. Most peer questions are really questions about "
            "state. `git diff origin/main -- <paths>` settles ownership; `git log`, "
            "`git ls-tree` (which shows MODES, unlike diff) and the record verbs settle "
            "most of the rest. The nine-session broadcast above was answering a question "
            "one diff had already answered.\n"
            "  2. IF A PEER MUST BE ASKED, PICK ONE — the likely owner, from evidence: "
            "markers in the diff itself, the branch the content sits on, the lane the "
            "files belong to.\n"
            "  3. ONLY ASK WHAT A PEER ALONE KNOWS — what it intends, what it is mid-way "
            "through, whether it is about to write. Anything about the state of the tree "
            "is a query, not a conversation.\n\n"
            "Replies are never blocked, and neither is continuing a thread with a session "
            "you have already reached.",
            file=sys.stderr)
        return 2   # deny

    seen[who] = now
    save(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())

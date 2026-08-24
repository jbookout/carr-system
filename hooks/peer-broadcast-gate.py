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

OWN SUBAGENTS ARE NOT PEERS (2026-08-14, second false positive that day). An
orchestrating session spawned workers with the Agent tool and messaged them by
agentId (`aabf255ee92301b74`); the gate counted each worker as a "session",
filled the budget with them, and refused the session's own next worker. But the
externalised-cost rationale above is the entire justification for denying, and
it does not apply to a subagent: the reply lands in the asker's own context, so
the asker pays the full cost itself. That is orchestration, not interrogation.

The exemption is PROVEN, never pattern-matched. The hook payload carries this
session's transcript_path, and the transcript records every Agent-tool spawn as
a structural toolUseResult.agentId — so "is this recipient my own subagent" is
answerable from ground truth, the same attribution stance as
staging-attribution-gate.py. A recipient merely SHAPED like an agentId, or an
id that appears only as text inside a message, stays a peer: matching on shape
would let a session spend an unlimited budget on anything named like a hex
token, and #70 already showed what keying identity on spelling does. No
transcript, or an unreadable one, means no exemption — the gate keeps its
protection rather than guessing.

THE BUDGET IS PER-ASKER, AND WAS MACHINE-WIDE UNTIL 2026-08-23. The state lived
in ONE file — ~/.cache/carr/peer-broadcast-gate.json — and MAX_NAMED_PEERS was
counted across everything in it. Observed live from worktree session
magical-vaughan-5dc83f: that session had sent ZERO peer messages, and its FIRST
SendMessage was refused with "You have already messaged 2 different sessions in
the last 30 minutes (confident-dewdney-db5a60-b6, hopeful-colden-822136-71)".
Both were another session's messages. With ~34 concurrent sessions on this Mac,
two messages from any ONE of them exhausted the allowance for ALL of them — and
the refusal told each session it had done something it had not done.

That is the REF_SUFFIX deadlock arriving by a different road: the single
targeted message this gate exists to ENCOURAGE was the one thing that could not
be sent. Nothing in the rationale ever supported it. The externalised cost is
per-ASKER — one session working through a roster — so a per-session budget
preserves the protection exactly and gives up nothing: the third distinct peer
of any one session is still refused, at the same count, in the same window.

State is now keyed by the payload's own session_id, one file per session under
~/.cache/carr/peer-broadcast-gate/. A payload carrying no session id shares a
single "unknown" bucket rather than being handed a fresh budget — an
unidentifiable caller is CHARGED, never waved through, the same stance the
subagent exemption takes toward an unproven recipient. Abandoned session files
are swept (see sweep) so the cache does not accumulate one per session forever.
"""
import json
import os
import re
import sys
import time

STATE_HOME = os.path.expanduser("~/.cache/carr/peer-broadcast-gate")
# The machine-wide file this gate used until 2026-08-23. Nothing writes it now;
# sweep() removes it on the same terms as any other stale budget.
LEGACY_STATE = os.path.expanduser("~/.cache/carr/peer-broadcast-gate.json")
WINDOW_SECONDS = 1800   # 30 minutes: long enough to span one investigation
MAX_NAMED_PEERS = 2     # the third distinct named peer is a broadcast
STALE_SECONDS = WINDOW_SECONDS * 2   # a budget nobody has touched holds nothing live

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


def own_subagent(who: str, transcript_path: str) -> bool:
    """True iff THIS session's transcript records spawning agent `who`.

    The Agent tool's spawn lands in the transcript as a toolUseResult carrying
    agentId (verified against a live transcript, 2026-08-14, session db8cb4af).
    Only that structural record counts. The line pre-filter is plain substring —
    an agentId quoted inside a message string is escaped in the JSONL and never
    parses out as toolUseResult.agentId, so text mentions cannot mint an
    exemption. Any failure to read or parse means False: an unproven subagent
    is charged as a peer, never waved through.
    """
    if not transcript_path:
        return False
    try:
        with open(transcript_path, errors="ignore") as fh:
            for line in fh:
                if "agentId" not in line or who not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                result = rec.get("toolUseResult")
                if isinstance(result, dict) and result.get("agentId") == who:
                    return True
    except OSError:
        return False
    return False


def session_key(payload) -> str:
    """The filename this session's budget lives under, from its own session id.

    Sanitised down to alphanumerics, dash and underscore. A session id is
    machine-generated, but it becomes a PATH here, and nothing that becomes a
    path is trusted to stay inside its directory — `../../x` collapses to `x`.

    An absent or empty id falls back to ONE shared "unknown" bucket, which is
    deliberately the conservative end of the trade: unidentifiable callers share
    a budget instead of each being handed a fresh one. Keying on something a
    caller could vary at will would turn the fix into a way to spend an
    unlimited budget, which is the same mistake shape-matching an agentId would
    have been.
    """
    raw = str(payload.get("session_id") or payload.get("sessionId") or "")
    return "".join(c for c in raw if c.isalnum() or c in "-_") or "unknown"


def state_path(key: str) -> str:
    return os.path.join(STATE_HOME, f"{key}.json")


def sweep(now, keep=None):
    """Delete budgets nothing has touched for STALE_SECONDS.

    Sessions end without telling anyone, and one file per session would
    otherwise sit in the cache forever. mtime is the honest clock: save()
    rewrites the file on every recorded message, so every entry in a file is
    older than that file's mtime — past STALE_SECONDS it cannot hold a single
    live one, and deleting it does exactly what load() already does entry by
    entry. A file with a FRESH mtime is never touched, so this can never become
    a way to clear a running session's budget.

    NEVER raises. Housekeeping that can break the gate is worse than litter.
    Losing a race with a concurrent writer is harmless both ways: a deleted file
    is rebuilt from scratch by the next save, and a file created after the
    listing is simply swept next time.
    """
    try:
        names = os.listdir(STATE_HOME)
    except OSError:
        names = []
    for name in [*names, LEGACY_STATE]:
        if name == keep:
            continue
        path = name if os.path.isabs(name) else os.path.join(STATE_HOME, name)
        try:
            if now - os.path.getmtime(path) >= STALE_SECONDS:
                os.remove(path)
        except OSError:
            continue


def load(path, now):
    try:
        with open(path) as fh:
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


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


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

    # THE BUDGET IS THIS SESSION'S, not the machine's. One shared file meant two
    # messages from any session on this Mac exhausted every other session's
    # allowance — including sessions that had sent nothing at all. See the
    # module docstring.
    state = state_path(session_key(payload))

    now = time.time()
    sweep(now, keep=os.path.basename(state))
    seen = load(state, now)
    if who in seen:           # re-messaging the same peer is a thread, not fan-out
        seen[who] = now
        save(state, seen)
        return 0

    # MESSAGING YOUR OWN SUBAGENT IS ORCHESTRATION, NOT BROADCAST — the asker
    # pays the whole reply cost itself, so there is nobody this gate would be
    # protecting. Exempt entirely: neither refused nor charged to the budget.
    # Proven against the transcript's spawn records, never inferred from shape.
    transcript = str(payload.get("transcript_path")
                     or payload.get("transcriptPath") or "")
    if own_subagent(who, transcript):
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
    save(state, seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""peer-broadcast-gate-selftest.py — prove the gate blocks fan-out and nothing else.

The failure it guards is asymmetric: blocking a legitimate reply is worse than
letting one extra ask through, because a session that cannot answer a peer is
broken in a way that is hard to diagnose from the other end. So the allow cases
carry as much weight here as the deny case.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "peer-broadcast-gate.py")
WINDOW = 1800

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def run(to, state_dir, tool="SendMessage", transcript=None,
        transcript_key="transcript_path", session=None):
    """Invoke the gate with an isolated HOME so the real state file is untouched.

    `session` is the payload's session_id. Left out on purpose in most cases
    below, which exercises the "unknown" fallback bucket: a payload with no
    session id must still be CHARGED, never handed a free budget.
    """
    env = {**os.environ, "HOME": state_dir}
    body = {"tool_name": tool, "tool_input": {"to": to}}
    if transcript is not None:
        body[transcript_key] = transcript
    if session is not None:
        body["session_id"] = session
    p = subprocess.run([sys.executable, GATE], input=json.dumps(body),
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, (p.stdout + p.stderr)


def budget_home(state_dir):
    return os.path.join(state_dir, ".cache", "carr", "peer-broadcast-gate")


def budgets(state_dir):
    try:
        return sorted(os.listdir(budget_home(state_dir)))
    except OSError:
        return []


def age(path, seconds):
    """Backdate a file's mtime, which is the clock sweep() reads."""
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def spawn_record(agent_id):
    """One transcript line as the harness actually writes it when the Agent tool
    launches a subagent — verified against a real session transcript
    (db8cb4af, 2026-08-14): the spawn is a toolUseResult carrying agentId."""
    return json.dumps({
        "type": "user",
        "toolUseResult": {"isAsync": True, "status": "async_launched",
                          "agentId": agent_id, "description": "worker"},
    })


def write_transcript(d, lines):
    path = os.path.join(d, "transcript.jsonl")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


print("peer-broadcast-gate")

# 1. The core case: two named peers pass, the third is refused.
with tempfile.TemporaryDirectory() as d:
    rc1, _ = run("carr-ai-01 [aaa]", d)
    rc2, _ = run("carr-ai-02 [bbb]", d)
    rc3, out3 = run("carr-ai-03 [ccc]", d)
    check("first named peer is allowed", rc1 == 0, f"rc={rc1}")
    check("second named peer is allowed", rc2 == 0, f"rc={rc2}")
    check("THIRD named peer is refused", rc3 == 2, f"rc={rc3}")
    check("the refusal names the cheaper route", "git diff origin/main" in out3)
    check("the refusal says replies are not blocked", "Replies are never blocked" in out3)

# 2. Replies must NEVER be blocked, however many are sent.
with tempfile.TemporaryDirectory() as d:
    codes = [run(f"uds:/tmp/cc-socks/{n}.sock", d)[0] for n in range(6)]
    check("six replies in a row are all allowed", codes == [0] * 6, f"got {codes}")

# 3. A reply must not consume the named-peer budget either.
with tempfile.TemporaryDirectory() as d:
    run("uds:/tmp/cc-socks/1.sock", d)
    run("uds:/tmp/cc-socks/2.sock", d)
    rc, _ = run("carr-ai-01 [aaa]", d)
    check("replies do not use up the named-peer allowance", rc == 0, f"rc={rc}")

# 4. Re-messaging the SAME peer is a thread, not fan-out — never blocked.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    codes = [run("carr-ai-01 [aaa]", d)[0] for _ in range(3)]
    check("continuing a thread with a reached peer stays allowed", codes == [0, 0, 0],
          f"got {codes}")

# 4b. THE SAME PEER UNDER BOTH SPELLINGS IS STILL ONE PEER.
#
# The deadlock this closes, hit 2026-08-14. SendMessage refuses a bare name it
# cannot resolve unambiguously — "'x' is not an agent in this conversation.
# Re-send with the ref to confirm you mean: x [d7e9b2]" — so the caller must use
# the ref form. The gate keyed on the raw string, so `x` and `x [d7e9b2]` counted
# as two different sessions. A caller who had already reached `x` was then told
# `x [d7e9b2]` was "another one", while the refusal text listed `x` as already
# messaged in the same sentence. Neither spelling could be sent: bare was refused
# by the tool, ref-qualified by the gate.
#
# Identity is the NAME. The ref is a disambiguator the tool asks for, not part of
# who the peer is.
# Asserted with the budget ALREADY FULL, which is the only form that proves
# anything. With one peer reached there is spare allowance, so a second spelling
# passes whether or not the gate understands it is the same session — the first
# draft of these two cases passed against the unfixed gate for exactly that
# reason. Filling the budget first makes the assertion about identity.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01", d)                       # reached bare
    run("carr-ai-02 [bbb]", d)                 # budget now full
    rc, out = run("carr-ai-01 [d7e9b2]", d)    # same peer as the first, ref-qualified
    check("the ref-qualified form of a peer already reached by bare name is allowed",
          rc == 0, f"rc={rc}: {out[:200]}")

with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)                 # reached ref-qualified
    run("carr-ai-02 [bbb]", d)                 # budget now full
    rc, out = run("carr-ai-01", d)             # same peer, bare
    check("and the reverse: bare name after the ref-qualified form is allowed",
          rc == 0, f"rc={rc}: {out[:200]}")

# The budget must not be spent twice by one peer under two spellings, or two
# genuine peers become unreachable after one.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01", d)
    run("carr-ai-01 [aaa]", d)      # same peer, must not consume a second slot
    rc, out = run("carr-ai-02 [bbb]", d)
    check("two spellings of one peer consume ONE slot, not two", rc == 0,
          f"rc={rc}: {out[:160]}")

# And the gate must still count genuinely different peers, ref or not — the fix
# must not become a way to spend an unlimited budget by varying the suffix.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, _ = run("carr-ai-03", d)
    check("a genuinely third peer is still refused, bare or not", rc == 2, f"rc={rc}")

with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, out = run("carr-ai-03 [ccc]", d)
    check("the refusal lists peers by name, without the ref noise",
          rc == 2 and "carr-ai-01" in out and "[aaa]" not in out,
          f"rc={rc}: {out[:200]}")

# 4c. A SESSION'S OWN SUBAGENTS ARE ORCHESTRATION, NOT BROADCAST.
#
# The false positive, hit 2026-08-14 (session db8cb4af): an orchestrating
# session spawned workers via the Agent tool and messaged them by agentId. The
# gate counted those workers as "sessions", filled the budget with them, and
# then refused the session's OWN next worker — listing one of its own subagents
# as an already-messaged peer in the refusal.
#
# The gate's rationale does not apply here. Its whole justification is the
# EXTERNALISED cost — the asker pays one message, every other session on the
# machine pays for reading and answering. A subagent's reply lands in the
# asker's own context; the asker pays the full cost itself. There is nobody to
# protect.
#
# Identity must be PROVEN, not pattern-matched: a recipient is exempt only when
# THIS session's transcript records spawning that agentId. A recipient merely
# SHAPED like an agentId is still a peer — otherwise naming a session
# 'a0123456789abcdef' would spend an unlimited budget.

# Own subagent, budget already full — the incident, replayed. With spare budget
# this passes against the unfixed gate, so it is asserted at the point the old
# gate refused.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307"),
                             spawn_record("aabf255ee92301b74")])
    run("carr-ai-01 [aaa]", d, transcript=t)
    run("carr-ai-02 [bbb]", d, transcript=t)   # budget now full
    rc, out = run("aabf255ee92301b74", d, transcript=t)
    check("an own subagent is allowed even with the peer budget full",
          rc == 0, f"rc={rc}: {out[:200]}")

# Own subagents must not consume the budget either — the other direction of the
# same incident: two worker messages first, then two genuine peers.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307"),
                             spawn_record("aabf255ee92301b74")])
    run("a4da4a7e30149a307", d, transcript=t)
    run("aabf255ee92301b74", d, transcript=t)
    rc1, out1 = run("carr-ai-01 [aaa]", d, transcript=t)
    rc2, out2 = run("carr-ai-02 [bbb]", d, transcript=t)
    rc3, _ = run("carr-ai-03 [ccc]", d, transcript=t)
    check("own subagents do not consume the named-peer allowance",
          rc1 == 0 and rc2 == 0, f"rc1={rc1} rc2={rc2}: {(out1 + out2)[:160]}")
    check("and a genuinely third PEER is still refused alongside subagent traffic",
          rc3 == 2, f"rc={rc3}")

# The exemption is earned by the transcript, not by the shape of the name. A
# recipient that merely looks like an agentId but was never spawned by this
# session is a peer and spends the budget like one.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307")])
    run("carr-ai-01 [aaa]", d, transcript=t)
    run("carr-ai-02 [bbb]", d, transcript=t)
    rc, _ = run("a0123456789abcdef", d, transcript=t)
    check("an agentId-shaped recipient NOT spawned by this session is still refused",
          rc == 2, f"rc={rc}")

# An agentId that appears in the transcript only as TEXT — quoted in a message,
# pasted in a tool result — is not a spawn record. Only a structural
# toolUseResult.agentId proves ownership; anything less is spoofable by content.
with tempfile.TemporaryDirectory() as d:
    mention = json.dumps({"type": "assistant", "message": {
        "content": 'peer said its id is "agentId": "adecoy0123456789a"'}})
    t = write_transcript(d, [spawn_record("a4da4a7e30149a307"), mention])
    run("carr-ai-01 [aaa]", d, transcript=t)
    run("carr-ai-02 [bbb]", d, transcript=t)
    rc, _ = run("adecoy0123456789a", d, transcript=t)
    check("an agentId mentioned only in message text earns no exemption",
          rc == 2, f"rc={rc}")

# No transcript in the payload, or a transcript that cannot be read, means no
# exemption can be proven — the recipient counts as a peer, the gate keeps
# working, and nothing crashes.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, _ = run("aabf255ee92301b74", d)
    check("without a transcript_path the recipient counts as a peer",
          rc == 2, f"rc={rc}")

with tempfile.TemporaryDirectory() as d:
    gone = os.path.join(d, "no-such-transcript.jsonl")
    run("carr-ai-01 [aaa]", d, transcript=gone)
    run("carr-ai-02 [bbb]", d, transcript=gone)
    rc, _ = run("aabf255ee92301b74", d, transcript=gone)
    check("an unreadable transcript denies the exemption without crashing",
          rc == 2, f"rc={rc}")

# The camelCase payload spelling some hook events use must work too.
with tempfile.TemporaryDirectory() as d:
    t = write_transcript(d, [spawn_record("aabf255ee92301b74")])
    run("carr-ai-01 [aaa]", d, transcript=t, transcript_key="transcriptPath")
    run("carr-ai-02 [bbb]", d, transcript=t, transcript_key="transcriptPath")
    rc, out = run("aabf255ee92301b74", d, transcript=t,
                  transcript_key="transcriptPath")
    check("the camelCase transcriptPath key is honoured as well",
          rc == 0, f"rc={rc}: {out[:160]}")

# 5. It must not touch any other tool.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d)
    run("carr-ai-02 [bbb]", d)
    rc, _ = run("carr-ai-03 [ccc]", d, tool="Bash")
    check("a non-SendMessage tool is never gated", rc == 0, f"rc={rc}")

# 6. A malformed payload must fail OPEN. A gate that crashes on bad input
#    becomes an outage in every session that sends a message.
p = subprocess.run([sys.executable, GATE], input="not json at all",
                   capture_output=True, text=True, timeout=60)
check("a malformed payload fails open, never blocks", p.returncode == 0,
      f"rc={p.returncode}")

# 7. An empty recipient is not a broadcast.
with tempfile.TemporaryDirectory() as d:
    rc, _ = run("", d)
    check("an empty recipient is ignored", rc == 0, f"rc={rc}")

# 8. THE BUDGET BELONGS TO THE ASKER, NOT TO THE MACHINE.
#
# The incident, 2026-08-23, observed live from worktree session
# magical-vaughan-5dc83f: that session had sent ZERO peer messages, and its
# FIRST SendMessage was refused — "You have already messaged 2 different
# sessions in the last 30 minutes (confident-dewdney-db5a60-b6,
# hopeful-colden-822136-71)". Both of those were another session's messages.
# State lived in one machine-wide file and MAX_NAMED_PEERS was counted across
# all of it, so with ~34 concurrent sessions on this Mac two messages from any
# one session exhausted the allowance for every other one. The gate refused the
# single targeted message it exists to encourage — the REF_SUFFIX deadlock
# again, by a different road.
#
# Asserted with the FIRST session's budget already full, which is the only form
# that proves anything: with spare allowance the second session passes whether
# or not the gate scopes anything.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01 [aaa]", d, session="s-one")
    run("carr-ai-02 [bbb]", d, session="s-one")      # s-one's budget is now full
    rc1, out1 = run("carr-ai-11", d, session="s-two")
    rc2, out2 = run("carr-ai-12", d, session="s-two")
    check("a second session's first peer is allowed while another session's budget is full",
          rc1 == 0, f"rc={rc1}: {out1[:200]}")
    check("and its second peer too — the budget is its own, not the machine's",
          rc2 == 0, f"rc={rc2}: {out2[:200]}")

    # The protection is unchanged in the direction that matters.
    rc3, out3 = run("carr-ai-13", d, session="s-two")
    check("a SINGLE session's third distinct peer is still refused",
          rc3 == 2, f"rc={rc3}")
    check("and that refusal names only that session's own peers",
          rc3 == 2 and "carr-ai-11" in out3 and "carr-ai-01" not in out3,
          f"out={out3[:300]}")

    rc4, out4 = run("carr-ai-03", d, session="s-one")
    check("the first session is still held to its own full budget",
          rc4 == 2, f"rc={rc4}")
    check("and IS told about its own peers, not the other session's",
          rc4 == 2 and "carr-ai-01" in out4 and "carr-ai-11" not in out4,
          f"out={out4[:300]}")

    check("each session's budget is a separate file",
          budgets(d) == ["s-one.json", "s-two.json"], f"got {budgets(d)}")

# One session's own messages still accumulate across calls — per-session scoping
# must not become per-CALL scoping, which would disable the gate entirely.
with tempfile.TemporaryDirectory() as d:
    codes = [run(f"carr-ai-{n}", d, session="s-one")[0] for n in range(1, 5)]
    check("one session's peers accumulate across calls: allow, allow, deny, deny",
          codes == [0, 0, 2, 2], f"got {codes}")

# A session id becomes a PATH. It must not be able to leave its directory.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01", d, session="../../../escape")
    check("a traversal-shaped session id is confined to the budget directory",
          budgets(d) == ["escape.json"], f"got {budgets(d)}")
    check("and nothing was written outside it",
          not os.path.exists(os.path.join(d, "escape.json"))
          and not os.path.exists(os.path.join(d, ".cache", "escape.json")))

# A payload with NO session id is charged to one shared bucket. The conservative
# end of the trade, stated: an unidentifiable caller must not collect a fresh
# budget by simply omitting the field.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01", d)
    run("carr-ai-02", d)
    rc, _ = run("carr-ai-03", d)
    check("a payload with no session_id is charged, not handed a free budget",
          rc == 2, f"rc={rc}")
    check("and its state is the shared unknown bucket",
          budgets(d) == ["unknown.json"], f"got {budgets(d)}")

# 9. ABANDONED BUDGETS ARE SWEPT — one file per session, forever, is litter.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-01", d, session="live")
    home = budget_home(d)
    # makedirs, not an assumption that the run above created it: a gate that
    # writes its state somewhere else should report a clean FAIL here, not a
    # FileNotFoundError traceback that buries every check after this one.
    os.makedirs(home, exist_ok=True)
    abandoned = os.path.join(home, "abandoned.json")
    with open(abandoned, "w") as fh:
        json.dump({"carr-ai-99": time.time()}, fh)
    age(abandoned, WINDOW * 3)
    run("carr-ai-02", d, session="live")
    check("a budget nothing has touched for two windows is swept",
          not os.path.exists(abandoned), f"still there: {budgets(d)}")
    check("and the running session's own budget survives its own sweep",
          os.path.exists(os.path.join(home, "live.json")), f"got {budgets(d)}")

# The sweep must NEVER touch a live session's file, or it becomes a way to clear
# another session's budget — the collision this whole change removes, rebuilt.
with tempfile.TemporaryDirectory() as d:
    run("carr-ai-11", d, session="other")
    run("carr-ai-12", d, session="other")           # other's budget is full
    run("carr-ai-01", d, session="live")            # live sweeps on the way past
    check("a freshly-written budget belonging to another session is left alone",
          sorted(budgets(d)) == ["live.json", "other.json"], f"got {budgets(d)}")
    rc, _ = run("carr-ai-13", d, session="other")
    check("so that session is still correctly refused its third peer",
          rc == 2, f"rc={rc}")

# The retired machine-wide file is litter too, and is swept on the same terms.
with tempfile.TemporaryDirectory() as d:
    legacy = os.path.join(d, ".cache", "carr", "peer-broadcast-gate.json")
    os.makedirs(os.path.dirname(legacy), exist_ok=True)
    with open(legacy, "w") as fh:
        json.dump({"carr-ai-99": time.time()}, fh)
    age(legacy, WINDOW * 3)
    run("carr-ai-01", d, session="live")
    check("the retired machine-wide state file is swept as well",
          not os.path.exists(legacy))

print()
if failures:
    print(f"FAIL {len(failures)} check(s): {', '.join(failures)}")
    sys.exit(1)
print("OK all checks passed")

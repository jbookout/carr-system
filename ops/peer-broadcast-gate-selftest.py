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

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GATE = os.path.join(REPO, "hooks", "peer-broadcast-gate.py")

failures: list[str] = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


def run(to, state_dir, tool="SendMessage"):
    """Invoke the gate with an isolated HOME so the real state file is untouched."""
    env = {**os.environ, "HOME": state_dir}
    payload = json.dumps({"tool_name": tool, "tool_input": {"to": to}})
    p = subprocess.run([sys.executable, GATE], input=payload,
                       capture_output=True, text=True, env=env, timeout=60)
    return p.returncode, (p.stdout + p.stderr)


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

print()
if failures:
    print(f"FAIL {len(failures)} check(s): {', '.join(failures)}")
    sys.exit(1)
print("OK all checks passed")

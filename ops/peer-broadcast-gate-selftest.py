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

failures = []


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

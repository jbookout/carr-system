#!/usr/bin/env python3
"""gate_ledger-selftest.py — prove the gate decision ledger records what it claims.

Every case drives hooks/hook-meter-run.py as the harness does: a subprocess fed
a hook payload on stdin, wrapping a small fixture gate. The ledger is read back
from disk. Nothing is imported from the wrapper, so a ledger that is not wired
into the wrapper fails here rather than passing on a direct function call.

    .venv/bin/python ops/gate_ledger-selftest.py [-v]
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WRAPPER = os.path.join(REPO, "hooks", "hook-meter-run.py")

SECRET = "sk-fixture-NOT-A-REAL-KEY-7Q2"
BRIEF = ("Builder brief for the GitHub App lane. Its private key was downloaded "
         "to Downloads as the newest matching file. Never print key material; "
         f"the token {SECRET} is a fixture. Install it with the intake script, "
         "then wire the watchdog to the app's own allowance and open one PR.")

# A fixture gate: verdict from the payload, so one file plays every role.
FIXTURE_GATE = r'''
import json, sys
p = json.load(sys.stdin)
verdict = (p.get("tool_input") or {}).get("fixture_verdict") or p.get("fixture_verdict") or "allow"
if verdict == "deny":
    print("fixture rule (detail 42) — blocked: " + str(p.get("tool_input"))[:40], file=sys.stderr)
    sys.exit(2)
if verdict == "ask":
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                      "permissionDecision": "ask", "permissionDecisionReason": "hold for a human"}}))
if verdict == "reopen":
    print(json.dumps({"decision": "block", "reason": "reply needs fresh verification"}))
sys.exit(0)
'''

FAILS: list[str] = []
VERBOSE = "-v" in sys.argv[1:]


def check(name, ok, detail=""):
    if VERBOSE or not ok:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"  :: {detail}"))
    if not ok:
        FAILS.append(name)


class Lab:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="gate-ledger-selftest-")
        self.ledger = os.path.join(self.dir, "gate-decisions.jsonl")
        self.gates = {}
        for name in ("guard-fixture.py", "other-fixture.py", "stop-fixture.py"):
            path = os.path.join(self.dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(FIXTURE_GATE)
            self.gates[name] = path

    def env(self, **extra):
        env = dict(os.environ)
        env.update({"CARR_GATE_LEDGER": self.ledger, "CARR_HOOK_FIXTURE": "1",
                    "CARR_HOOK_TELEMETRY": os.path.join(self.dir, "telemetry.jsonl")})
        env.update(extra)
        return env

    def fire(self, gate, payload, **env):
        p = subprocess.run([sys.executable, WRAPPER, self.gates[gate]],
                           input=json.dumps(payload), capture_output=True, text=True,
                           timeout=30, env=self.env(**env))
        return p.returncode, p.stderr

    def rows(self, kind=None):
        try:
            with open(self.ledger, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            return []
        return [r for r in rows if kind is None or r.get("type") == kind]

    def all_bytes(self):
        blob = []
        for root, _, files in os.walk(self.dir):
            for f in files:
                if f.endswith(".py") or f.startswith("telemetry"):
                    continue  # the fixture gates and the meter's own stream
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    blob.append(fh.read())
        return "\n".join(blob)


def pre(session, tool_use_id, tool_input, tool="Bash"):
    return {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": tool,
            "tool_use_id": tool_use_id, "tool_input": tool_input}


def post(session, tool_use_id, tool_input, tool="Bash"):
    return {**pre(session, tool_use_id, tool_input, tool), "hook_event_name": "PostToolUse"}


def stop(session, prompt_id, message, verdict="allow"):
    return {"hook_event_name": "Stop", "session_id": session, "prompt_id": prompt_id,
            "last_assistant_message": message, "fixture_verdict": verdict}


def heredoc(body):
    return f"mkdir -p out/orch/ghapp; cat > out/orch/ghapp/brief.md <<'EOF'\n{body}\nEOF"


# ── 1. A deny is one decision line carrying no raw text ──────────────────────
lab = Lab()
cmd = {"command": heredoc(BRIEF), "fixture_verdict": "deny"}
rc, err = lab.fire("guard-fixture.py", pre("S1", "t1", cmd))
check("the gate's own deny passes through unchanged", rc == 2 and "fixture rule" in err, f"rc={rc}")
decisions = lab.rows("decision")
check("a deny appends exactly one decision", len(decisions) == 1, decisions)
d = decisions[0] if decisions else {}
check("the decision names gate, rule, digest, session and time",
      d.get("gate") == "guard-fixture.py" and d.get("rule") == "fixture rule (…)"
      and str(d.get("input_digest", "")).startswith("sha256:") and d.get("session") == "S1"
      and d.get("ts") and d.get("id"), d)
check("the decision is classed as a block", d.get("kind") == "block", d)
blob = lab.all_bytes()
check("no raw command text or secret reaches the ledger or its state",
      SECRET not in blob and "Builder brief" not in blob and "intake script" not in blob)

# ── 2. An allow writes nothing ───────────────────────────────────────────────
lab = Lab()
lab.fire("guard-fixture.py", pre("S1", "t1", {"command": "git status"}))
check("an allow writes no decision", lab.rows() == [], lab.rows())

# ── 3. A hold (ask) and a Stop reopen are decisions too ──────────────────────
lab = Lab()
lab.fire("other-fixture.py", pre("S1", "t1", {"command": "x", "fixture_verdict": "ask"}))
lab.fire("stop-fixture.py", stop("S1", "p1", "Done.", verdict="reopen"))
kinds = sorted(r.get("kind") for r in lab.rows("decision"))
check("an ask is recorded as a hold and a Stop block as a reopen", kinds == ["hold", "reopen"], kinds)
rules = {r.get("rule") for r in lab.rows("decision")}
check("a JSON-only refusal still gets a rule", "reply needs fresh verification" in rules, rules)

# ── 4. Same session completes the same brief through Write: auto-wrong ──────
lab = Lab()
lab.fire("guard-fixture.py", pre("S1", "t1", {"command": heredoc(BRIEF), "fixture_verdict": "deny"}))
write = {"file_path": "/tmp/brief.md", "content": BRIEF}
lab.fire("other-fixture.py", pre("S1", "t2", write, tool="Write"))
check("a matching call that has only been ALLOWED is not yet a completion",
      lab.rows("verdict") == [], lab.rows("verdict"))
lab.fire("other-fixture.py", post("S1", "t2", write, tool="Write"))
verdicts = lab.rows("verdict")
did = lab.rows("decision")[0]["id"] if lab.rows("decision") else None
check("completing the same substance via Write auto-labels the block wrong",
      len(verdicts) == 1 and verdicts[0].get("label") == "wrong"
      and verdicts[0].get("by") == "auto" and verdicts[0].get("decision_id") == did, verdicts)
lab.fire("other-fixture.py", post("S1", "t2", write, tool="Write"))
check("a second hook on the same completion adds no second label",
      len(lab.rows("verdict")) == 1, lab.rows("verdict"))

# ── 5. Not 'immediately': the session did other work first ──────────────────
lab = Lab()
lab.fire("guard-fixture.py", pre("S1", "t1", {"command": heredoc(BRIEF), "fixture_verdict": "deny"}))
lab.fire("other-fixture.py", pre("S1", "t2", {"command": "ls bin tools ops | grep -i intake"}))
lab.fire("other-fixture.py", post("S1", "t3", write, tool="Write"))
check("a different call in between means the block changed the course: no label",
      lab.rows("verdict") == [], lab.rows("verdict"))

# ── 6. Changed substance is not the same intent ─────────────────────────────
lab = Lab()
lab.fire("guard-fixture.py", pre("S1", "t1", {"command": heredoc(BRIEF), "fixture_verdict": "deny"}))
lab.fire("other-fixture.py", post("S1", "t2", {"file_path": "/tmp/b.md",
          "content": "A completely different and much shorter note about lunch."}, tool="Write"))
check("a completion with different substance leaves the block unlabelled",
      lab.rows("verdict") == [], lab.rows("verdict"))

# ── 7. Another session never labels this one's block ────────────────────────
lab = Lab()
lab.fire("guard-fixture.py", pre("S1", "t1", {"command": heredoc(BRIEF), "fixture_verdict": "deny"}))
lab.fire("other-fixture.py", post("S2", "t9", write, tool="Write"))
check("a completion in another session leaves the block unlabelled",
      lab.rows("verdict") == [], lab.rows("verdict"))

# ── 8. Stop: a reopen whose reply comes back unchanged and is accepted ───────
REPLY = ("Merged the guard fix and re-ran the selftest: 230 of 230 pass. The replay "
         "of real commands shows no verdict changes beyond the refusal text.")
lab = Lab()
lab.fire("stop-fixture.py", stop("S1", "p1", REPLY, verdict="reopen"))
lab.fire("stop-fixture.py", stop("S1", "p1", REPLY, verdict="allow"))
v = lab.rows("verdict")
check("the same gate accepting the same reply after a reopen labels the reopen wrong",
      len(v) == 1 and v[0].get("label") == "wrong", v)
lab = Lab()
lab.fire("stop-fixture.py", stop("S1", "p1", REPLY, verdict="reopen"))
lab.fire("other-fixture.py", pre("S1", "t5", {"command": "python3 ops/guard-selftest.py"}))
lab.fire("stop-fixture.py", stop("S1", "p1", REPLY, verdict="allow"))
check("a reopen answered by running a check first is not labelled",
      lab.rows("verdict") == [], lab.rows("verdict"))

# ── 9. The window is bounded ─────────────────────────────────────────────────
lab = Lab()
lab.fire("guard-fixture.py", pre("S1", "t1", {"command": heredoc(BRIEF), "fixture_verdict": "deny"}),
         CARR_GATE_LEDGER_WINDOW_S="0")
time.sleep(1.1)
lab.fire("other-fixture.py", post("S1", "t2", write, tool="Write"), CARR_GATE_LEDGER_WINDOW_S="0")
check("a completion outside the window is not 'immediately'", lab.rows("verdict") == [], lab.rows("verdict"))

# ── 10. The ledger can never change a verdict ───────────────────────────────
lab = Lab()
blocked = os.path.join(lab.dir, "not-a-file")
os.makedirs(blocked)
rc, err = lab.fire("guard-fixture.py", pre("S1", "t1", {"command": "x", "fixture_verdict": "deny"}),
                   CARR_GATE_LEDGER=blocked)
check("an unwritable ledger leaves the deny and its message intact", rc == 2 and "fixture rule" in err,
      f"rc={rc} err={err[:80]}")

# ── 10b. One ledger per machine: a linked worktree writes to canonical out/ ─
sys.path.insert(0, os.path.join(REPO, "hooks"))
import gate_ledger  # noqa: E402
_env = os.environ.pop("CARR_GATE_LEDGER", None)
try:
    common = subprocess.run(["git", "-C", REPO, "rev-parse", "--path-format=absolute",
                             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
    canonical = os.path.dirname(common) if common.endswith("/.git") else REPO
    got = gate_ledger.ledger_path(REPO, "live")
    check("the live ledger is the canonical checkout's, from any worktree",
          got == os.path.join(canonical, "out", "gate-decisions.jsonl"), got)
finally:
    if _env is not None:
        os.environ["CARR_GATE_LEDGER"] = _env

# ── 11. The verdict CLI and the precision report ────────────────────────────
spec = importlib.util.spec_from_file_location("gate_verdict", os.path.join(REPO, "tools", "gate_verdict.py"))
assert spec is not None and spec.loader is not None
gv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gv)


def cli(lab, *args):
    p = subprocess.run([sys.executable, os.path.join(REPO, "tools", "gate_verdict.py"), *args],
                       capture_output=True, text=True, timeout=30, env=lab.env())
    return p.returncode, p.stdout + p.stderr


lab = Lab()
for i in range(4):
    lab.fire("guard-fixture.py", pre("S1", f"g{i}", {"command": f"blocked {i}", "fixture_verdict": "deny"}))
lab.fire("other-fixture.py", pre("S1", "o1", {"command": "held", "fixture_verdict": "ask"}))
ids = [r["id"] for r in lab.rows("decision") if r["gate"] == "guard-fixture.py"]
rc, out = cli(lab, "label", ids[0], "wrong", "--reason", "prompt text, not a command")
check("label marks a decision wrong", rc == 0, out)
cli(lab, "label", ids[1], "wrong", "--reason", "grep pattern")
cli(lab, "label", ids[2], "right", "--reason", "real deploy")
rc, out = cli(lab, "label", "nope", "wrong", "--reason", "x")
check("label refuses an unknown decision id", rc != 0 and "unknown" in out.lower(), out)
rc, out = cli(lab, "label", ids[3], "maybe", "--reason", "x")
check("label refuses anything but right or wrong", rc != 0, out)
rc, out = cli(lab, "label", ids[3], "wrong")
check("label requires a reason", rc != 0, out)
stats = gv.precision(lab.ledger, days=7)
g = stats.get("guard-fixture.py", {})
check("report counts blocks, labels and wrong per gate",
      (g.get("blocks"), g.get("labelled"), g.get("wrong")) == (4, 3, 2), g)
check("false-alarm rate is wrong over labelled", abs(g.get("fa_rate", -1) - 2 / 3) < 1e-9, g)
check("top wrong patterns name the rule", g.get("top_wrong") == [("fixture rule (…)", 2)], g)
check("a gate with no labels has no rate rather than zero",
      stats.get("other-fixture.py", {}).get("fa_rate") is None, stats.get("other-fixture.py"))
rc, out = cli(lab, "report")
check("report prints the gate with its rate", rc == 0 and "guard-fixture.py" in out and "67%" in out, out)
rc, out = cli(lab, "list", "--unlabelled")
check("list --unlabelled shows only the unlabelled", ids[3] in out and ids[0] not in out, out)

# A human label overrides an automatic one, latest wins.
lab = Lab()
lab.fire("guard-fixture.py", pre("S1", "t1", {"command": heredoc(BRIEF), "fixture_verdict": "deny"}))
lab.fire("other-fixture.py", post("S1", "t2", write, tool="Write"))
did = lab.rows("decision")[0]["id"]
cli(lab, "label", did, "right", "--reason", "the brief really did name a live key path")
g = gv.precision(lab.ledger, days=7)["guard-fixture.py"]
check("a human label overrides the automatic one", (g["labelled"], g["wrong"]) == (1, 0), g)

# ── 12. The health row and its bound action ─────────────────────────────────
now = time.time()
rows = []
for i in range(5):
    rows.append({"type": "decision", "id": f"d{i}", "ts": now - 60, "gate": "noisy.py",
                 "rule": "deploy words", "kind": "block"})
    rows.append({"type": "verdict", "decision_id": f"d{i}", "ts": now - 30,
                 "label": "wrong" if i < 3 else "right", "by": "test"})
rows.append({"type": "decision", "id": "q1", "ts": now - 60, "gate": "quiet.py", "rule": "r", "kind": "block"})
path = os.path.join(Lab().dir, "ledger.jsonl")
with open(path, "w", encoding="utf-8") as fh:
    fh.write("".join(json.dumps(r) + "\n" for r in rows))
line, noisy = gv.health_row(path)
check("a gate over the false-alarm threshold is named noisy", [n["gate"] for n in noisy] == ["noisy.py"], noisy)
check("the health row prints its bound action inline", "on breach:" in line and "loop" in line, line)
check("the health row names the noisy gate and its top wrong rule",
      "noisy.py" in line and "deploy words" in line, line)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(rows[-1]) + "\n")
line, noisy = gv.health_row(path)
check("a quiet ledger is OK and still names its bound action",
      noisy == [] and line.startswith("OK") and "on breach:" in line, line)
line, noisy = gv.health_row(os.path.join(Lab().dir, "missing.jsonl"))
check("a missing ledger is not an all-clear", not line.startswith("OK"), line)

# Backfill from the meter's own telemetry: live refusals only, idempotent.
lab = Lab()
tele = os.path.join(lab.dir, "telemetry-history.jsonl")
with open(tele, "w", encoding="utf-8") as fh:
    for row in (
        {"ts": "2026-10-05T10:00:00Z", "hook": "guard-unattended.py", "event": "PreToolUse",
         "tool": "Bash", "session": "S1", "tool_use_id": "t1", "outcome": "deny", "source": "live",
         "deny_headline": f"private key material — blocked :: cat {SECRET}"},
        {"ts": "2026-10-05T10:01:00Z", "hook": "conduct-stop-gate.py", "event": "Stop",
         "session": "S1", "prompt_id": "p1", "outcome": "deny", "source": "live",
         "deny_class": "bare_id"},
        {"ts": "2026-10-05T10:02:00Z", "hook": "guard-unattended.py", "event": "PreToolUse",
         "session": "S1", "tool_use_id": "t2", "outcome": "allow", "source": "live"},
        {"ts": "2026-10-05T10:03:00Z", "hook": "guard-unattended.py", "event": "PreToolUse",
         "session": "S9", "tool_use_id": "t3", "outcome": "deny", "source": "fixture"},
    ):
        fh.write(json.dumps(row) + "\n")
rc, out = cli(lab, "backfill", tele)
rc2, out2 = cli(lab, "backfill", tele)
got = lab.rows("decision")
check("backfill records live refusals only, once",
      rc == 0 and rc2 == 0 and sorted((r["gate"], r["kind"]) for r in got)
      == [("conduct-stop-gate.py", "reopen"), ("guard-unattended.py", "block")], (out, out2, got))
check("backfilled decisions carry the rule, never the command",
      {r["rule"] for r in got} == {"private key material", "bare_id"}
      and SECRET not in open(lab.ledger, encoding="utf-8").read(), got)

calls: list[tuple[str, dict]] = []


def fake_verb(name, payload):
    calls.append((name, payload))
    return {"ok": True, "loop_id": "L-1"} if name == "add-loop" else {"ok": True}


state = os.path.join(Lab().dir, "loops.json")
noisy_one = [{"gate": "noisy.py", "blocks": 5, "labelled": 5, "wrong": 3, "fa_rate": 0.6,
              "top_wrong": [("deploy words", 3)]}]
first = gv.reconcile_loops(noisy_one, fake_verb, state)
again = gv.reconcile_loops(noisy_one, fake_verb, state)
check("a newly noisy gate opens one loop owned by the orchestrator",
      first == {"noisy.py": "opened"} and calls[0][0] == "add-loop"
      and calls[0][1]["owner"] == "claude" and "deploy words" in calls[0][1]["body"], (first, calls))
check("a gate still noisy keeps its loop without a second one",
      again == {"noisy.py": "open"} and len(calls) == 1, (again, calls))
cleared = gv.reconcile_loops([], fake_verb, state)
check("a gate that recovered closes its loop",
      cleared == {"noisy.py": "closed"} and calls[-1][0] == "close-loop"
      and calls[-1][1]["loop_id"] == "L-1", (cleared, calls))

print(f"\ngate_ledger-selftest: {'FAIL' if FAILS else 'all passed'}"
      + (f" — {len(FAILS)} failed: " + "; ".join(FAILS) if FAILS else ""))
sys.exit(1 if FAILS else 0)

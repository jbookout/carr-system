#!/usr/bin/env python3
"""
costar-lane-gate-selftest.py — fixtures for hooks/costar-lane-gate.py, written
before it (rule e65efc68).

THE RULE, f5d97b4a, in Joe's own words: "CoStar is driven ONLY in the Claude
desktop app's own Browser pane. NEVER Chrome, never the Chrome extension, which
CoStar blocks on the first click."

THE HOLE, from the 2026-08-14 enforceability audit (bucket U, enforcement
specified and not built) and re-probed still open the hour this was written.
Every route a session could take to CoStar through the Chrome extension was
allowed by the installed hook chain: navigate to the URL, type it into the
address bar, set location.href from javascript_tool, or bundle any of those
into browser_batch. The one matcher that already covers these tools,
mcp__claude-in-chrome__.*, belongs to model-floor-gate.py and is about model
tiers, not destinations.

WHY THIS ONE IS ENFORCEABLE WHERE MOST OF ITS NEIGHBOURS ARE NOT. The binding
condition is a HOST, which is a predicate (rule 5e89c211). Most of the audit's
partial rows are honestly advisory because their condition needs judgment;
"is the target host costar.com" needs none.

THE LANE SPLIT IS THE WHOLE DESIGN, and it is what makes a deny safe here. The
sanctioned lane and the banned lane are different tool NAMESPACES:

    mcp__Claude_Browser__*    the desktop app's own Browser pane — sanctioned
    mcp__claude-in-chrome__*  the Chrome extension — what the rule forbids

So a refusal never wedges a session. The work is always still doable, one
namespace over, which is why this gate ships with no escape hatch where its
neighbours have one: there is no legitimate case, only a different tool name.

MATCHING IS ON THE HOST, NOT ON THE SUBSTRING, and that is deliberate. Two
false DENYs shipped into live gates on 2026-08-14, both from a gate answering a
question it could not answer. Searching Google for "costar comps" has host
google.com and must pass; only a URL whose host IS costar.com or a subdomain of
it is the banned lane.

WHAT MUST STAY TRUE:
  1. Every Chrome route to CoStar is refused — url, typed text, javascript,
     and batched forms of each.
  2. The SANCTIONED lane is never touched, on the same URL. This is the
     direction a carve-out tested only on the permitting side loses first.
  3. Ordinary Chrome use of other sites is never touched.
  4. A costar SUBSTRING that is not a host (a search query, prose, a filename)
     is not a refusal.
  5. The refusal names the lane to use instead, so the next move is obvious.
  6. It fails OPEN on every malformed input.

RUNNING IT. No database, no network, no vault:

    .venv/bin/python ops/costar-lane-gate-selftest.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "costar-lane-gate.py"

passed = 0
failures: list[str] = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(tool, tool_input):
    """Drive the real hook exactly as the harness would."""
    payload = {"tool_name": tool, "session_id": "selftest",
               "transcript_path": "", "cwd": str(REPO),
               "tool_input": tool_input}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                       capture_output=True, text=True)
    return p.returncode == 2, (p.stdout or "") + (p.stderr or "")


print("\nhooks/costar-lane-gate.py — CoStar is driven in the Browser pane, "
      "never Chrome (f5d97b4a)")

if not HOOK.exists():
    print(f"  FAIL  the gate does not exist at {HOOK}")
    print("\n1 check(s) failed: not implemented")
    sys.exit(1)

CHROME = "mcp__claude-in-chrome__"
PANE = "mcp__Claude_Browser__"

# ── 1. the hole this closes: every Chrome route to CoStar ───────────────────
ROUTES = [
    (CHROME + "navigate", {"url": "https://product.costar.com/home"},
     "navigate with a full URL"),
    (CHROME + "navigate", {"url": "costar.com"},
     "navigate with a bare host"),
    (CHROME + "navigate", {"url": "HTTPS://PRODUCT.COSTAR.COM/Search"},
     "navigate, shouted"),
    (CHROME + "computer", {"action": "type",
                           "text": "https://product.costar.com/search"},
     "typed into the address bar"),
    (CHROME + "javascript_tool",
     {"action": "javascript_exec",
      "text": "location.href='https://product.costar.com'"},
     "set from javascript_tool"),
    (CHROME + "browser_batch",
     {"actions": [{"tool": "navigate", "url": "https://product.costar.com"}]},
     "bundled into browser_batch"),
    (CHROME + "navigate", {"url": "https://www.costar.com/"},
     "the www host"),
    (CHROME + "tabs_create_mcp", {"url": "https://product.costar.com"},
     "opened as a new tab"),
]
for tool, tool_input, label in ROUTES:
    blocked, _ = run(tool, tool_input)
    check(f"Chrome, {label} — refused", blocked)

blocked, out = run(CHROME + "navigate", {"url": "https://product.costar.com"})
check("the refusal names the Browser pane as the lane to use",
      "browser pane" in out.lower(), out[:160])
check("the refusal names the rule", "f5d97b4a" in out, out[:160])

# ── 2. the direction a one-sided carve-out loses first ──────────────────────
# The SANCTIONED lane, on the very same URLs. If this ever starts refusing,
# the gate has eaten the only route to CoStar that exists.
for tool_input, label in [({"url": "https://product.costar.com/home"}, "full URL"),
                          ({"url": "costar.com"}, "bare host")]:
    blocked, _ = run(PANE + "navigate", tool_input)
    check(f"the Browser pane on CoStar ({label}) is ALLOWED", not blocked,
          "the sanctioned lane must never be gated")

for tool in ("get_page_text", "read_page", "computer", "javascript_tool"):
    blocked, _ = run(PANE + tool, {"text": "https://product.costar.com"})
    check(f"Browser-pane {tool} on CoStar is ALLOWED", not blocked)

# ── 3. ordinary Chrome use is never touched ─────────────────────────────────
for url in ("https://www.sunbiz.org", "https://www.google.com",
            "https://npiregistry.cms.hhs.gov", "back", "forward"):
    blocked, _ = run(CHROME + "navigate", {"url": url})
    check(f"Chrome to {url} is allowed", not blocked)

for tool in ("read_page", "get_page_text", "tabs_context_mcp"):
    blocked, _ = run(CHROME + tool, {})
    check(f"Chrome {tool} with no target is allowed", not blocked)

# ── 4. a costar SUBSTRING that is not a host is not a refusal ───────────────
# This is the class that produced two false DENYs in live gates on 2026-08-14:
# a gate answering a question it could not answer. Host position or nothing.
NOT_HOSTS = [
    (CHROME + "navigate", {"url": "https://www.google.com/search?q=costar+comps"},
     "a Google search FOR costar"),
    (CHROME + "navigate", {"url": "https://www.bing.com/search?q=costar.com"},
     "a search whose query string contains the host"),
    (CHROME + "computer", {"action": "type",
                           "text": "the costar export lands in source-exports"},
     "prose mentioning costar"),
    (CHROME + "navigate", {"url": "https://example.com/costar.com/notes"},
     "costar.com sitting in a PATH, not the host"),
    (CHROME + "computer", {"action": "type", "text": "costar-export-2026-08.csv"},
     "a filename containing costar"),
    (CHROME + "navigate", {"url": "https://notcostar.com"},
     "a different domain that merely ends in the same letters"),
    (CHROME + "navigate", {"url": "https://costar.com.evil.example"},
     "costar.com as a LABEL inside another domain"),
]
for tool, tool_input, label in NOT_HOSTS:
    blocked, out = run(tool, tool_input)
    check(f"not a refusal: {label}", not blocked, out[:140])

# ── 5. tools outside the Chrome namespace are never gated ───────────────────
for tool in ("Read", "Bash", "WebFetch", "mcp__carr__add-loop"):
    blocked, _ = run(tool, {"url": "https://product.costar.com"})
    check(f"{tool} is out of scope", not blocked)

# ── 6. fail open ────────────────────────────────────────────────────────────
p = subprocess.run([sys.executable, str(HOOK)], input="not json",
                   capture_output=True, text=True)
check("malformed input fails open", p.returncode == 0)

p = subprocess.run([sys.executable, str(HOOK)], input="",
                   capture_output=True, text=True)
check("empty input fails open", p.returncode == 0)

blocked, _ = run(CHROME + "navigate", {})
check("a navigate with no url at all fails open", not blocked)

print(f"\n{passed} check(s) passed"
      + (f", {len(failures)} FAILED: {failures}" if failures else ""))
sys.exit(1 if failures else 0)

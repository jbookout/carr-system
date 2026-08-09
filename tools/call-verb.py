#!/usr/bin/env python3
"""
call-verb.py — fire ONE record verb from a terminal on this Mac.

WHY THIS EXISTS (2026-08-08, monthly resurface session). Joe tried to close four
idea rows from the shell and there was no path: `run.sh` has no verb subcommand,
so a session can call a verb through the MCP connector but a human at a prompt
cannot. The capability already existed — mcp-server/local-verb.mjs imports the
real src/tools.js registry and runs any registered verb — but it was invisible
from run.sh and it needs a DATABASE_URL that a human has to produce by hand.
This wires the two together: db-tap's DSN derivation feeds local-verb's runner.

WHAT IT DOES NOT DO, deliberately. It does NOT open a production write path.
local-verb.mjs refuses the production endpoint unless
CARR_LOCAL_VERB_ALLOW_PRODUCTION=1 is set, on the stated grounds that
"production writes are a human's tap; this tool exists for branch rehearsal and
must not become a side door." That rail is two days older than this file and it
is left exactly where it stands — this script never sets that variable, it only
passes through an environment that already carries it. Reaching production is
therefore still a deliberate act by the human, not a side effect of a
convenience wrapper.

The DSN is derived by tools/db-tap.py, not re-implemented here, so the branch
targeting and the neonctl invocation stay one piece of code.

Usage:
  ./run.sh call <verb> '<json args>' [actor-slug]
  ./run.sh call --branch rehearse-0031 add-loop '{"kind":"idea", ...}' joe
  ./run.sh call list-verbs '{}'

Never prints the DSN.
"""
import importlib.util
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_VERB = os.path.join(REPO, "mcp-server", "local-verb.mjs")
NODE_CANDIDATES = [
    "/opt/homebrew/bin/node",
    "/usr/local/opt/node@22/bin/node",
    "node",
]


def _db_tap():
    # db-tap.py's hyphen makes it un-importable by name; load it by path so the
    # DSN logic has exactly one home (rule a8c55a47 — the manual path and the
    # automated path that do the same job must BE the same code).
    path = os.path.join(REPO, "tools", "db-tap.py")
    spec = importlib.util.spec_from_file_location("db_tap", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def node_bin() -> str:
    for p in NODE_CANDIDATES:
        if os.path.sep not in p or os.path.exists(p):
            return p
    return "node"


def main() -> None:
    argv = sys.argv[1:]
    branch = "production"
    if argv and argv[0] == "--branch":
        if len(argv) < 2:
            sys.exit("--branch needs a name")
        branch, argv = argv[1], argv[2:]
    if not argv:
        sys.exit(__doc__)
    verb, rest = argv[0], argv[1:]
    args_json = rest[0] if rest else "{}"
    actor = rest[1] if len(rest) > 1 else "joe"

    if not os.path.exists(LOCAL_VERB):
        sys.exit(f"no such file: {LOCAL_VERB}")

    url = _db_tap().dsn(branch)
    env = {**os.environ, "DATABASE_URL": url}
    os.chdir(os.path.join(REPO, "mcp-server"))
    rc = subprocess.run(
        [node_bin(), LOCAL_VERB, verb, args_json, actor], env=env
    ).returncode
    sys.exit(rc)


if __name__ == "__main__":
    main()

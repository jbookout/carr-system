#!/usr/bin/env python3
"""Run one headless `claude -p` on THIS machine under this machine's own login.

The prompt arrives on stdin; every argument is passed through to `claude -p`.
bin/remote-claude.sh calls this over SSH so a session on one Mac can hand a job
to another partner's Claude subscription.

It refuses when this machine has no long-lived login (CLAUDE_CODE_OAUTH_TOKEN in
~/.config/carr/tokens.env). Falling back to whatever else is signed in could
bill the wrong account, so a missing token is an error, never a fallback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from credential_env import claude_child_env  # noqa: E402

SETUP_HINT = ("run `claude setup-token`, then "
              "`.venv/bin/python tools/store-token.py CLAUDE_CODE_OAUTH_TOKEN`")


def main(argv: list[str]) -> int:
    env, warning = claude_child_env()
    if warning:
        print(f"claude-headless: {warning}; {SETUP_HINT}", file=sys.stderr)
        return 3
    # A non-login SSH shell has no ~/.local/bin, where the native installer puts claude.
    env["PATH"] = os.pathsep.join([str(Path.home() / ".local" / "bin"), env.get("PATH", "/usr/bin:/bin")])
    os.chdir(REPO)
    os.execvpe("claude", ["claude", "-p", *argv], env)
    return 127  # unreachable: execvpe replaces this process or raises


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Proof for bin/claude-headless.py, the remote side of bin/remote-claude.sh.

Covers: refusing (exit 3, no claude started) when the machine has no
long-lived login, and, when it has one, running `claude -p` with the caller's
flags, the prompt on stdin, the token in the child env, and ~/.local/bin on PATH.

Run:  python3 tools/test_claude_headless.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "claude-headless.py"
FAKE_CLAUDE = """#!/bin/sh
echo "args:$*"
echo "token:${CLAUDE_CODE_OAUTH_TOKEN:-missing}"
echo "stdin:$(cat)"
"""


def run(home: Path, *args: str, prompt: str = "hello") -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=prompt,
                          capture_output=True, text=True, env=env, timeout=30)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        fake = home / ".local" / "bin" / "claude"
        fake.parent.mkdir(parents=True)
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)

        refused = run(home)
        assert refused.returncode == 3, refused
        assert "args:" not in refused.stdout, "claude must not start without a login"
        assert "claude setup-token" in refused.stderr, refused.stderr

        tokens = home / ".config" / "carr" / "tokens.env"
        tokens.parent.mkdir(parents=True)
        tokens.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test\n")
        tokens.chmod(0o600)

        ran = run(home, "--output-format", "json", prompt="do the task")
        assert ran.returncode == 0, ran
        assert "args:-p --output-format json" in ran.stdout, ran.stdout
        assert "token:sk-ant-oat-test" in ran.stdout, "token must reach the child env"
        assert "stdin:do the task" in ran.stdout, ran.stdout
        assert "sk-ant-oat-test" not in ran.stderr
    print("claude-headless: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

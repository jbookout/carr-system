#!/usr/bin/env python3
"""Exercise the Codex-only continuity installer through its CLI dispatch."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from typing import Any
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "config_as_code_for_codex_continuity", REPO / "ops" / "config-as-code.py"
)
assert spec and spec.loader
mod: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _hook(command: str, matcher: str | None = None) -> dict:
    group: dict[str, Any] = {"hooks": [{"type": "command", "command": command, "timeout": 10}]}
    if matcher is not None:
        group["matcher"] = matcher
    return group


def main() -> int:
    continuity = "/carr-system/ops/codex-continuity-hook.py"
    source = {"hooks": {
        "PreCompact": [_hook(continuity)],
        "PostCompact": [_hook(continuity)],
        "SessionStart": [_hook(continuity, "^(startup|resume|compact)$")],
        "UserPromptSubmit": [_hook(continuity)],
    }}
    live: dict[str, Any] = {
        "hooks": {
            "PreCompact": [
                _hook("/other/project/keep.py"),
                _hook(continuity),
            ],
            "Stop": [_hook("/other/project/stop.py")],
        },
        "permissions": {"keep": True},
    }
    with tempfile.TemporaryDirectory(prefix="carr-codex-continuity-") as temp:
        root = Path(temp)
        repo_source = root / "tracked-codex-hooks.json"
        codex_live = root / ".codex" / "hooks.json"
        claude_settings = root / ".claude" / "settings.json"
        codex_config = root / ".codex" / "config.toml"
        repo_source.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
        codex_live.parent.mkdir()
        original = json.dumps(live, indent=2) + "\n"
        codex_live.write_text(original, encoding="utf-8")

        mod.CODEX_HOOKS_REPO = str(repo_source)
        mod.CODEX_HOOKS_SRC = str(codex_live)
        mod.SETTINGS = str(claude_settings)
        mod.CODEX_CONFIG = str(codex_config)

        mod.sys.argv = ["config-as-code.py", "install-codex-continuity"]
        assert mod.main() == 0
        assert codex_live.read_text(encoding="utf-8") == original

        mod.sys.argv = ["config-as-code.py", "install-codex-continuity", "--apply"]
        assert mod.main() == 0
        installed = json.loads(codex_live.read_text(encoding="utf-8"))
        assert installed["permissions"] == {"keep": True}
        assert installed["hooks"]["Stop"] == live["hooks"]["Stop"]
        assert installed["hooks"]["PreCompact"] == [_hook("/other/project/keep.py"), _hook(continuity)]
        assert installed["hooks"]["PreCompact"][1:] == source["hooks"]["PreCompact"]
        assert all(installed["hooks"].get(event) == source["hooks"][event]
                   for event in mod.CODEX_CONTINUITY_EVENTS if event != "PreCompact")
        assert not claude_settings.exists()
        assert not codex_config.exists()

        after_first = codex_live.read_bytes()
        assert mod.main() == 0
        assert codex_live.read_bytes() == after_first

        # Rollback is selective; it must not restore an old whole-file backup
        # over unrelated hooks added after installation.
        updated = json.loads(after_first)
        updated["hooks"]["PostCompact"].append(_hook("/other/new-hook.py"))
        codex_live.write_text(json.dumps(updated, indent=2) + "\n")
        before_remove = codex_live.read_bytes()
        mod.sys.argv = ["config-as-code.py", "remove-codex-continuity"]
        assert mod.main() == 0
        assert codex_live.read_bytes() == before_remove
        mod.sys.argv.append("--apply")
        assert mod.main() == 0
        removed = json.loads(codex_live.read_text())
        assert removed["hooks"]["PostCompact"] == [_hook("/other/new-hook.py")]
        assert removed["hooks"]["PreCompact"] == [_hook("/other/project/keep.py")]
        assert removed["hooks"]["Stop"] == live["hooks"]["Stop"]
        assert removed["permissions"] == {"keep": True}
        assert not claude_settings.exists() and not codex_config.exists()
        after_remove = codex_live.read_bytes()
        assert mod.main() == 0
        assert codex_live.read_bytes() == after_remove

    print("codex-continuity-installer-selftest: 1/1 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

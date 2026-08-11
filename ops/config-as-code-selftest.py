#!/usr/bin/env python3
"""Regression fixtures for non-destructive Codex global-hook reconciliation."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import plistlib
import tempfile
from types import SimpleNamespace
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "config_as_code", os.path.join(REPO, "ops", "config-as-code.py")
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(mod)

DESIRED = {"hooks": {
    "Stop": [{"hooks": [{
        "type": "command", "command": "/Users/booko/carr-system/hooks/completion-evidence-gate.py", "timeout": 15,
    }]}],
}}
LIVE = {
    "hooks": {
        "Stop": [
            {"hooks": [{"type": "command", "command": "/Users/booko/other/hooks/keep.py", "timeout": 5}]},
            {"hooks": [{"type": "command", "command": "/Users/booko/carr-system/hooks/old.py", "timeout": 10}]},
            {"hooks": [
                {"type": "command", "command": "/Users/booko/other/hooks/mixed.py", "timeout": 5},
                {"type": "command", "command": "/Users/booko/carr-system/hooks/old2.py", "timeout": 10},
            ]},
        ],
        "PostToolUse": [{"matcher": "Other", "hooks": [{
            "type": "command", "command": "/Users/booko/other/hooks/post.py", "timeout": 5,
        }]}],
    },
    "user_setting": {"keep": True},
}


def commands(doc):
    return [hook.get("command") for groups in doc["hooks"].values()
            for group in groups for hook in group.get("hooks", []) if isinstance(hook, dict)]


def main():
    merged = mod.merge_codex_carr_hooks(LIVE, DESIRED)
    names = commands(merged)
    again = mod.merge_codex_carr_hooks(merged, DESIRED)
    live_permissions = (
        'default_permissions = "carr_drive_readonly"\n\n'
        f'{mod.CODEX_PERMISSIONS_BEGIN}\n'
        '[permissions.carr_drive_readonly.filesystem]\n'
        f'"{mod.REPO}" = "write"\n'
        f'{mod.CODEX_PERMISSIONS_END}\n'
    )
    portable_permissions = mod.canonical_codex_permissions(live_permissions)
    empty_codex_snapshot = mod.carr_owned_hooks_document({}, ["Stop"])
    call_mode_source = mod.launchd_repo_path("com.carr.call-mode.plist")
    ordinary_launchd_source = mod.launchd_repo_path("com.carr.example.plist")
    with tempfile.TemporaryDirectory(prefix="carr-claude-only-") as temp_home:
        home = Path(temp_home)
        repo = home / "carr-system"
        config = repo / "ops" / "config"
        launchd = repo / "ops" / "launchd"
        tasks = repo / "ops" / "scheduled-tasks"
        config.mkdir(parents=True)
        launchd.mkdir(parents=True)
        tasks.mkdir(parents=True)
        hooks_source = {"PreToolUse": []}
        (config / "hooks.json").write_text(
            json.dumps(hooks_source, indent=2) + "\n", encoding="utf-8"
        )
        mod.REPO = str(repo)
        mod.SETTINGS = str(home / ".claude" / "settings.json")
        mod.TASKS_SRC = str(home / ".claude" / "scheduled-tasks")
        mod.TASKS_REPO = str(tasks)
        mod.LAUNCHD_SRC = str(home / "Library" / "LaunchAgents")
        mod.LAUNCHD_REPO = str(launchd)
        mod.LAUNCHD_ALT_REPO = {}
        mod.HOOKS_REPO = str(config / "hooks.json")
        mod.CODEX_HOOKS_SRC = str(home / ".codex" / "hooks.json")
        mod.CODEX_CONFIG = str(home / ".codex" / "config.toml")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            claude_only_rc = mod.cmd_install(True)
            claude_only_check = mod.cmd_check()
        claude_only_output = output.getvalue()
        settings_created = Path(mod.SETTINGS).is_file()
        installed_settings = json.loads(Path(mod.SETTINGS).read_text(encoding="utf-8"))
        codex_created = (home / ".codex").exists()
        Path(mod.CODEX_HOOKS_SRC).parent.mkdir(parents=True)
        Path(mod.CODEX_HOOKS_SRC).write_text("{}\n", encoding="utf-8")
        partial_state = mod.codex_configuration_state()
        with contextlib.redirect_stdout(io.StringIO()):
            partial_install_rc = mod.cmd_install(False)
            partial_check_rc = mod.cmd_check()
            partial_pull_rc = mod.cmd_pull(False)
        desired_codex = {"hooks": {"Stop": []}}
        (config / "codex-hooks.json").write_text(
            json.dumps(desired_codex, indent=2) + "\n", encoding="utf-8"
        )
        (config / "codex-permissions.toml").write_text(
            'default_permissions = "carr_drive_readonly"\n\n'
            '[permissions.carr_drive_readonly.filesystem]\n'
            '"{{REPO}}" = "write"\n',
            encoding="utf-8",
        )
        Path(mod.CODEX_CONFIG).write_text('model = "test"\n', encoding="utf-8")
        mod.CODEX_HOOKS_REPO = str(config / "codex-hooks.json")
        mod.CODEX_PERMISSIONS_REPO = str(config / "codex-permissions.toml")
        with contextlib.redirect_stdout(io.StringIO()):
            configured_rc = mod.cmd_install(True)
            configured_check = mod.cmd_check()
        configured_hooks = json.loads(Path(mod.CODEX_HOOKS_SRC).read_text(encoding="utf-8"))
        configured_toml = Path(mod.CODEX_CONFIG).read_text(encoding="utf-8")
        failing_plist = {
            "Label": "com.carr.synthetic-load-failure",
            "ProgramArguments": ["/usr/bin/true"],
            "RunAtLoad": False,
        }
        (launchd / "com.carr.synthetic-load-failure.plist").write_bytes(
            plistlib.dumps(failing_plist)
        )
        real_run = mod.subprocess.run
        load_attempts = []

        def fail_launchctl(args, *call_args, **call_kwargs):
            if args[:2] == ["launchctl", "unload"]:
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            if args[:2] == ["launchctl", "load"]:
                load_attempts.append(args[-1])
                return SimpleNamespace(returncode=1, stderr="synthetic load failure", stdout="")
            return real_run(args, *call_args, **call_kwargs)

        mod.subprocess.run = fail_launchctl
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                launchd_failure_rc = mod.cmd_install(True)
                launchd_retry_rc = mod.cmd_install(True)
        finally:
            mod.subprocess.run = real_run
        launchd_dir_created = Path(mod.LAUNCHD_SRC).is_dir()
    cases = [
        ("unrelated top-level key preserved", merged.get("user_setting") == {"keep": True}),
        ("unrelated event preserved", "PostToolUse" in merged["hooks"] and "/Users/booko/other/hooks/post.py" in commands(merged)),
        ("unrelated Stop hook preserved", "/Users/booko/other/hooks/keep.py" in names),
        ("mixed group keeps unrelated hook", "/Users/booko/other/hooks/mixed.py" in names),
        ("stale CARR hook removed", all("/carr-system/hooks/old" not in name for name in names)),
        ("desired CARR hook installed once", names.count("/Users/booko/carr-system/hooks/completion-evidence-gate.py") == 1),
        ("second merge is idempotent", again == merged),
        ("live Codex permission paths become portable tokens",
         portable_permissions is not None and '"{{REPO}}" = "write"' in portable_permissions),
        ("Call Mode LaunchAgent resolves to its tracked tool source",
         call_mode_source.endswith(
             "/tools/dictation-rig/launchd/com.carr.call-mode.plist")),
        ("ordinary LaunchAgent resolves to ops launchd",
         ordinary_launchd_source.endswith(
             "/ops/launchd/com.carr.example.plist")),
        ("missing Codex hooks document is an empty managed snapshot",
         empty_codex_snapshot == {"hooks": {"Stop": []}}),
        ("fresh Claude-only apply creates settings and succeeds",
         claude_only_rc == 0 and settings_created),
        ("fresh Claude-only apply installs the tracked Claude hooks",
         installed_settings.get("hooks") == hooks_source),
        ("Claude-only post-install drift check is clean",
         claude_only_check == 0),
        ("Claude-only install explicitly skips Codex",
         "SKIP  Codex configuration" in claude_only_output
         and "Codex was not configured and was left absent" in claude_only_output),
        ("Claude-only install does not create a Codex home",
         not codex_created),
        ("partial Codex configuration is distinguished from absence",
         partial_state == "partial"),
        ("partial Codex configuration fails install and check",
         partial_install_rc == 1 and partial_check_rc == 1 and partial_pull_rc == 1),
        ("configured Codex install remains supported and drift-clean",
         configured_rc == 0 and configured_check == 0),
        ("configured Codex writes only the desired managed hooks",
         configured_hooks == desired_codex),
        ("configured Codex preserves existing config and adds managed permissions",
         'model = "test"' in configured_toml
         and mod.CODEX_PERMISSIONS_BEGIN in configured_toml
         and mod.CODEX_PERMISSIONS_END in configured_toml),
        ("LaunchAgent load failure and idempotent retry both stay nonzero",
         launchd_failure_rc == 1 and launchd_retry_rc == 1 and len(load_attempts) == 2),
        ("fresh install creates the LaunchAgents directory",
         launchd_dir_created),
    ]
    for label, passed in cases:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
    print(f"config-as-code-selftest: {sum(ok for _, ok in cases)}/{len(cases)} passed")
    return 0 if all(ok for _, ok in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())

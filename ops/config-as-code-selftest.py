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
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
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


PLIST_DIR = Path(__file__).resolve().parents[1] / "ops" / "launchd"


def _plist_parses(path):
    """True when Python can read the plist, not merely when plutil can.

    launchd uses CoreFoundation's lenient parser and accepts files that
    plistlib rejects.  Every repo tool that inspects these jobs uses plistlib,
    so a file only launchd can read is a file the tooling silently skips.
    """
    try:
        with open(path, "rb") as fh:
            plistlib.load(fh)
        return True
    except Exception as exc:
        print(f"  UNPARSEABLE  {path.name}: {type(exc).__name__} {exc}")
        return False


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
        mod.TASKS_QUARANTINE = str(
            home / ".claude" / "scheduled-tasks-quarantine" / "carr-primary-only"
        )
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

        # Scheduled task *definitions* are CARR-managed only when their name
        # exists in the repo registry.  Primary install must render every
        # tracked definition; secondary install must move an exact tracked
        # render out of Claude's active directory without touching a personal
        # task.  The 16-task fixture mirrors Dell's reproduced current state.
        primary_tasks = {
            f"task-{number:02d}": f"---\nname: task-{number:02d}\n---\nCARR managed {number}\n"
            for number in range(1, 17)
        }
        for name, body in primary_tasks.items():
            (tasks / f"{name}.SKILL.md").write_text(body, encoding="utf-8")
        original_primary = mod.IS_PRIMARY
        mod.IS_PRIMARY = True
        with contextlib.redirect_stdout(io.StringIO()) as primary_task_out:
            primary_task_install_rc = mod.cmd_install(True)
            primary_task_check_rc = mod.cmd_check()
        primary_task_rendered = all(
            (Path(mod.TASKS_SRC) / name / "SKILL.md").read_text(encoding="utf-8") == body
            for name, body in primary_tasks.items()
        )

        mod.IS_PRIMARY = False
        secondary_task_source = Path(mod.TASKS_SRC)
        with contextlib.redirect_stdout(io.StringIO()) as secondary_dry_out:
            secondary_dry_rc = mod.cmd_install(False)
        secondary_dry_output = secondary_dry_out.getvalue()
        secondary_dry_preserves_active = all(
            (secondary_task_source / name / "SKILL.md").is_file()
            for name in primary_tasks
        ) and not Path(mod.TASKS_QUARANTINE).exists()
        with contextlib.redirect_stdout(io.StringIO()) as secondary_clean_out:
            secondary_install_rc = mod.cmd_install(True)
            secondary_clean_check_rc = mod.cmd_check()
        secondary_clean_output = secondary_clean_out.getvalue()
        secondary_quarantine = Path(mod.TASKS_QUARANTINE)
        active_carr_tasks_after = [
            path for path in secondary_task_source.glob("*/SKILL.md")
            if path.parent.name in primary_tasks
        ]
        quarantined_carr_tasks = [
            secondary_quarantine / name / "SKILL.md" for name in primary_tasks
        ]
        secondary_quarantine_complete = (
            secondary_install_rc == 0 and len(active_carr_tasks_after) == 0
            and all(path.is_file() for path in quarantined_carr_tasks)
            and "QUARANTINE  scheduled task task-01" in secondary_clean_output
        )
        with contextlib.redirect_stdout(io.StringIO()) as secondary_retry_out:
            secondary_retry_rc = mod.cmd_install(True)
        secondary_retry_output = secondary_retry_out.getvalue()

        # Personal task names do not belong to CARR's registry: neither the
        # health denominator nor apply is permitted to claim or move them.
        personal_task = secondary_task_source / "personal-reminder" / "SKILL.md"
        personal_task.parent.mkdir(parents=True)
        personal_task.write_text("personal task\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as personal_task_out:
            personal_task_check_rc = mod.cmd_check()
            personal_task_install_rc = mod.cmd_install(True)
        personal_task_output = personal_task_out.getvalue()
        personal_task_preserved = personal_task.is_file()

        # A tracked CARR name with a changed body is potentially user work.
        # It blocks safely and remains active rather than being overwritten or
        # quarantined.
        modified_task = secondary_task_source / "task-01" / "SKILL.md"
        modified_task.parent.mkdir(parents=True)
        modified_task.write_text("modified CARR task\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as secondary_bad_out:
            secondary_bad_check_rc = mod.cmd_check()
            secondary_bad_pull_rc = mod.cmd_pull(True)
            secondary_bad_install_rc = mod.cmd_install(True)
        secondary_bad_output = secondary_bad_out.getvalue()
        modified_task_preserved = modified_task.read_text(encoding="utf-8") == "modified CARR task\n"
        # PIN THE MACHINE ROLE FOR THE LAUNCHD CASES. This used to restore the
        # real machine's role here, which silently made the three launchd
        # assertions below mean different things on different Macs: the synthetic
        # plists are primary-scope, so on a SECONDARY machine (Dell's, actor slug
        # dell) they are never loaded, load_attempts stays empty, and two cases
        # fail for a reason that has nothing to do with what they test. They pass
        # on Joe's Mac and on the GitHub runner, both of which resolve primary, so
        # the split stayed invisible until pre-push CI was run on the secondary.
        # A selftest must assert the same thing everywhere; the real role is
        # restored after the block instead.
        mod.IS_PRIMARY = True
        definition_only_plist = {
            "Label": "com.carr.control-plane-tick",
            "ProgramArguments": ["/usr/bin/true"],
            "RunAtLoad": False,
        }
        (launchd / "com.carr.control-plane-tick.plist").write_bytes(
            plistlib.dumps(definition_only_plist)
        )
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
            with contextlib.redirect_stdout(io.StringIO()) as launchd_out:
                launchd_failure_rc = mod.cmd_install(True)
                launchd_retry_rc = mod.cmd_install(True)
        finally:
            mod.subprocess.run = real_run
        mod.IS_PRIMARY = original_primary
        launchd_dir_created = Path(mod.LAUNCHD_SRC).is_dir()
        definition_only_absent = not (
            Path(mod.LAUNCHD_SRC) / "com.carr.control-plane-tick.plist"
        ).exists()

        # THE TWO SCHEDULED-TASK GUARDS LANDED WITHOUT COVERAGE (PR 430). Both
        # decide whether a file is CARR configuration at all, so a silent
        # regression in either does not fail loudly — it re-arms the chronically
        # red pre-push gate those guards were written to stop, and that failure
        # arrives on somebody else's unrelated push.
        #
        # The frontmatter restriction is the part worth pinning. The marker is
        # honoured ONLY inside the opening frontmatter block, so a task cannot
        # exempt itself by mentioning the phrase in prose, and a genuine CARR
        # task that someone forgot to commit still shows up as drift.
        ephemeral_cases = {
            "spaced marker in frontmatter": "---\nname: t\nephemeral: true\n---\nbody\n",
            "unspaced marker in frontmatter": "---\nname: t\nephemeral:true\n---\nbody\n",
            "capitalised marker in frontmatter": "---\nname: t\nEphemeral: True\n---\nbody\n",
        }
        non_ephemeral_cases = {
            # THE CASE THE GUARD EXISTS TO GET RIGHT, and it has to be a line
            # that is EXACTLY the marker. The comparison is line equality, so a
            # marker used mid-sentence never matches and would pass even with
            # the frontmatter boundary deleted — such a fixture tests nothing.
            # A task documenting the convention is the realistic shape.
            "marker alone on a prose line below frontmatter":
                "---\nname: t\n---\nTo mark scaffolding, add to the frontmatter:\n\n"
                "ephemeral: true\n\nThat is the only place it counts.\n",
            "marker in prose mid-sentence":
                "---\nname: t\n---\nThis task is ephemeral: true only in spirit.\n",
            "marker with no frontmatter at all": "ephemeral: true\n",
            "marker before the frontmatter opens": "ephemeral: true\n---\nname: t\n---\n",
            "ordinary tracked task": "---\nname: t\n---\nCARR managed\n",
            "empty body": "",
        }
        ephemeral_marker_honoured = all(
            mod.is_ephemeral_scheduled_task(body) for body in ephemeral_cases.values()
        )
        ephemeral_prose_ignored = not any(
            mod.is_ephemeral_scheduled_task(body) for body in non_ephemeral_cases.values()
        )
        definition_only_detected = (
            mod.is_definition_only_task(
                "---\nname: t\n---\nThis definition is disabled. Do not create it.\n")
            and not mod.is_definition_only_task("---\nname: t\n---\nCARR managed\n")
            and not mod.is_definition_only_task("")
        )

        # END TO END, because a correct predicate wired into the wrong place is
        # the failure this pair actually had: the checker honoured
        # "definition only" while the installer did not, and would have written
        # four contracts whose own text forbids creating them.
        mod.IS_PRIMARY = True
        machine_ephemeral = Path(mod.TASKS_SRC) / "handoff-continuation" / "SKILL.md"
        machine_ephemeral.parent.mkdir(parents=True, exist_ok=True)
        machine_ephemeral.write_text(
            "---\nname: handoff-continuation\nephemeral: true\n---\n"
            "Session scaffolding, not tracked configuration.\n",
            encoding="utf-8",
        )
        (tasks / "calendar-prebrief-am.SKILL.md").write_text(
            "---\nname: calendar-prebrief-am\n---\n"
            "This definition is disabled. Do not create, enable, or invoke any scheduler.\n",
            encoding="utf-8",
        )
        guard_labels = [label for label, _live, _repo in mod.pairs()]
        with contextlib.redirect_stdout(io.StringIO()) as guard_out:
            guard_install_rc = mod.cmd_install(True)
        guard_output = guard_out.getvalue()
        mod.IS_PRIMARY = original_primary
        ephemeral_excluded_from_drift = (
            not any("handoff-continuation" in label for label in guard_labels)
            and machine_ephemeral.is_file()
        )
        definition_only_excluded_from_drift = not any(
            "calendar-prebrief-am" in label for label in guard_labels
        )
        definition_only_not_installed = (
            guard_install_rc == 0
            and "SKIP  scheduled task calendar-prebrief-am (definition only:" in guard_output
            and not (Path(mod.TASKS_SRC) / "calendar-prebrief-am" / "SKILL.md").exists()
        )
    cases = [
        ("ephemeral marker is honoured inside frontmatter",
         ephemeral_marker_honoured),
        ("ephemeral marker in prose does not exempt a tracked task",
         ephemeral_prose_ignored),
        ("a disabled definition is recognised, an ordinary body is not",
         definition_only_detected),
        ("an ephemeral machine task is excluded from drift and left alone",
         ephemeral_excluded_from_drift),
        ("a definition-only task is not reported as missing from the machine",
         definition_only_excluded_from_drift),
        ("the installer skips a definition-only scheduled task",
         definition_only_not_installed),
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
        ("fresh primary install renders all tracked scheduled-task definitions",
         primary_task_install_rc == 0 and primary_task_rendered
         and "WRITE  scheduled task task-01" in primary_task_out.getvalue()),
        ("fresh primary scheduled-task install leaves check clean",
         primary_task_check_rc == 0),
        ("secondary dry run names quarantine work without moving active tasks",
         secondary_dry_rc == 0 and secondary_dry_preserves_active
         and "would quarantine  scheduled task task-01" in secondary_dry_output),
        ("secondary quarantines all 16 exact managed definitions",
         secondary_quarantine_complete),
        ("secondary exact-task retry is idempotent",
         secondary_retry_rc == 0 and "QUARANTINE" not in secondary_retry_output),
        ("secondary ignores absent primary scheduled-task definitions as drift",
         secondary_clean_check_rc == 0),
        ("unrelated personal scheduled tasks are preserved and excluded",
         personal_task_check_rc == 0 and personal_task_install_rc == 0
         and personal_task_preserved and "personal-reminder" not in personal_task_output),
        ("modified CARR secondary scheduled task fails visibly",
         secondary_bad_check_rc == 1
         and "NOT ALLOWED ON SECONDARY" in secondary_bad_output),
        ("secondary refuses to pull a modified CARR scheduled task into the repo",
         secondary_bad_pull_rc == 1
         and "refusing to capture" in secondary_bad_output
         and modified_task_preserved),
        ("secondary refuses to quarantine or overwrite a modified CARR task",
         secondary_bad_install_rc == 1 and "refusing to move or overwrite" in secondary_bad_output
         and modified_task_preserved),
        ("LaunchAgent load failure and idempotent retry both stay nonzero",
         launchd_failure_rc == 1 and launchd_retry_rc == 1 and len(load_attempts) == 2),
        ("definition-only control-plane tick is not installed before cutover",
         definition_only_absent
         and "SKIP  com.carr.control-plane-tick.plist (definition only:" in launchd_out.getvalue()),
        ("fresh install creates the LaunchAgents directory",
         launchd_dir_created),
        # EVERY REPO PLIST MUST PARSE WITH plistlib, not merely with plutil.
        # com.carr.partner-ping.plist named a flag literally inside an XML
        # comment on 2026-08-18, and XML forbids the two-dash sequence there.
        # plutil and launchd both accepted the file; Python's parser did not.
        # The cost was silent: _missing_paths in ops/config-as-code.py catches
        # the parse error and returns [] — "no missing paths" — so the check
        # that stops a job installing when its program was never built did
        # nothing at all for that job, and nothing said so. A text-only drift
        # comparison cannot see this, which is why it needs its own case.
        ("every repo launchd plist parses with plistlib, not just plutil",
         all(_plist_parses(f) for f in sorted(PLIST_DIR.glob("*.plist")))),
    ]
    for label, passed in cases:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
    print(f"config-as-code-selftest: {sum(ok for _, ok in cases)}/{len(cases)} passed")
    return 0 if all(ok for _, ok in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())

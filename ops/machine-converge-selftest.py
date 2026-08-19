#!/usr/bin/env python3
"""Fixtures for hooks/machine-converge.py — the SessionStart secondary-machine
converge.

THE ONE ASSERTION THIS FILE EXISTS FOR (decision #442): a converge must come
out the other side with the dictation-consent wrapper INTACT. The live plist
is seeded here exactly as the 2026-08-17 incident found it — unwrapped, no
StartInterval — and the test passes only if the converge rewrites it from the
tracked template WITH the four bin/run-scheduled.sh wrapper lines and the
still-recording reminder. This is also the regression trap for the verb
choice: if a future edit swaps the hook's `install --apply` for `pull`, the
seeded unwrapped plist gets captured INTO the fixture repo instead of being
repaired, the live copy stays unwrapped, and this test fails.

Everything runs against throwaway git repos, a throwaway $HOME and a stubbed
launchctl on $PATH, so nothing here can touch real machine config — the same
discipline as ops/fleet-sync-selftest.py.
"""
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

SELF_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The checkout under test supplies the real hook, the real installer and the
# real tracked template — a hand-copied template here would let the template
# and the test drift apart, which is the two-homes disease.
COPIES = [
    "hooks/machine-converge.py",
    "ops/config-as-code.py",
    "ops/git_env.py",
    "ops/githooks/pre-push",
    "ops/config/hooks.json",
    "ops/launchd/com.carr.dictation-consent.plist",
]

# The pre-#442 live state: wrapper stripped, no StartInterval. {repo} is
# filled per fixture.
DRIFTED_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.carr.dictation-consent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>{repo}/tools/dictation-rig/bin/consent-watch.sh</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def sh(args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)


def git(repo, *args, env=None):
    r = sh(["git", "-C", repo, *args], env=env)
    if r.returncode != 0:
        raise RuntimeError(f"fixture git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def owner_email():
    with open(os.path.join(SELF_REPO, "ops", "githooks", "pre-push"),
              encoding="utf-8") as fh:
        return re.search(r'^OWNER_EMAIL="([^"]+)"', fh.read(), re.M).group(1)


def write_exec(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_fixture(root, email, actor_slug=None, behind=True, dirty=False,
                  seed_drifted_plist=True):
    """A canonical-checkout stand-in, its origin, a fake $HOME, a stub PATH."""
    repo = os.path.join(root, "repo")
    home = os.path.join(root, "home")
    stubs = os.path.join(root, "stubs")

    for rel in COPIES:
        dst = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(SELF_REPO, rel), dst)
    write_exec(os.path.join(repo, "bin", "run-scheduled.sh"), "#!/bin/zsh\nexit 0\n")
    write_exec(os.path.join(repo, "tools", "dictation-rig", "bin", "consent-watch.sh"),
               "#!/bin/zsh\nexit 0\n")
    with open(os.path.join(repo, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("fixture\n")

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", email)
    git(repo, "config", "user.name", "Fixture")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")

    origin = os.path.join(root, "origin.git")
    sh(["git", "clone", "-q", "--bare", repo, origin])
    git(repo, "remote", "add", "origin", origin)
    git(repo, "fetch", "-q", "origin")

    if behind:
        work = os.path.join(root, "work")
        sh(["git", "clone", "-q", origin, work])
        git(work, "config", "user.email", email)
        git(work, "config", "user.name", "Fixture")
        with open(os.path.join(work, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("origin moved\n")
        git(work, "commit", "-qam", "origin ahead")
        git(work, "push", "-q", "origin", "main")

    if dirty:
        with open(os.path.join(repo, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("LOCAL EDIT THAT MUST SURVIVE\n")

    if actor_slug:
        actor = os.path.join(home, ".config", "carr", "local-actor.json")
        os.makedirs(os.path.dirname(actor), exist_ok=True)
        with open(actor, "w", encoding="utf-8") as fh:
            json.dump({"actor_slug": actor_slug}, fh)

    agents = os.path.join(home, "Library", "LaunchAgents")
    os.makedirs(agents, exist_ok=True)
    if seed_drifted_plist:
        with open(os.path.join(agents, "com.carr.dictation-consent.plist"),
                  "w", encoding="utf-8") as fh:
            fh.write(DRIFTED_PLIST.format(repo=repo))

    write_exec(os.path.join(stubs, "launchctl"),
               "#!/bin/zsh\necho \"$@\" >> \"$LAUNCHCTL_LOG\"\nexit 0\n")
    return repo, home, stubs


def run_hook(repo, home, stubs, root):
    env = dict(os.environ)
    env["HOME"] = home
    env["PATH"] = stubs + os.pathsep + env.get("PATH", "")
    env["LAUNCHCTL_LOG"] = os.path.join(root, "launchctl.log")
    for var in list(env):
        if var.startswith("GIT_") or var == "CLAUDE_PROJECT_DIR":
            env.pop(var)
    return sh([sys.executable, os.path.join(repo, "hooks", "machine-converge.py")],
              cwd=root, env=env)


def installed_plist(home):
    path = os.path.join(home, "Library", "LaunchAgents",
                        "com.carr.dictation-consent.plist")
    with open(path, "rb") as fh:
        return plistlib.load(fh)


def wrapper_intact(home):
    """The four deliberate wrapper lines of commit 4fb58a8a, in order, plus
    the still-recording StartInterval PR #328 carried into the template."""
    try:
        d = installed_plist(home)
    except FileNotFoundError:
        return False
    args = d.get("ProgramArguments") or []
    return (len(args) == 5
            and args[0] == "/bin/zsh"
            and args[1].endswith("/bin/run-scheduled.sh")
            and args[2] == "dictation-consent"
            and args[3] == "launchd.run"
            and args[4].endswith("/tools/dictation-rig/bin/consent-watch.sh")
            and d.get("StartInterval") == 300)


def case_secondary_converges(tmp):
    """Dell's machine: behind origin, drifted plist -> ff + wrapper restored."""
    repo, home, stubs = build_fixture(tmp, "dell@example.com", actor_slug="dell")
    r = run_hook(repo, home, stubs, tmp)
    checks = {
        "hook exits 0": r.returncode == 0,
        "checkout fast-forwarded to origin/main":
            git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main"),
        "dictation-consent wrapper SURVIVES the converge": wrapper_intact(home),
        "launchd job reloaded through launchctl":
            "load" in open(os.path.join(tmp, "launchctl.log")).read(),
        "Claude settings rendered":
            os.path.isfile(os.path.join(home, ".claude", "settings.json")),
        "no ~/.codex created on a Claude-only machine":
            not os.path.exists(os.path.join(home, ".codex")),
        "brief line reports the apply": "re-applied" in r.stdout,
    }
    return checks


def case_primary_noop(tmp):
    """Joe's machine by actor slug: nothing moves, nothing prints."""
    repo, home, stubs = build_fixture(tmp, "dell@example.com", actor_slug="joe")
    before = git(repo, "rev-parse", "HEAD")
    r = run_hook(repo, home, stubs, tmp)
    return {
        "hook exits 0": r.returncode == 0,
        "prints nothing": r.stdout.strip() == "",
        "checkout not moved": git(repo, "rev-parse", "HEAD") == before,
        "no config written":
            not os.path.exists(os.path.join(home, ".claude", "settings.json")),
        "drifted plist deliberately untouched": not wrapper_intact(home),
    }


def case_owner_fallback_noop(tmp):
    """No local-actor.json: the owner's git identity means primary -> no-op."""
    repo, home, stubs = build_fixture(tmp, owner_email(), actor_slug=None)
    before = git(repo, "rev-parse", "HEAD")
    r = run_hook(repo, home, stubs, tmp)
    return {
        "hook exits 0": r.returncode == 0,
        "prints nothing": r.stdout.strip() == "",
        "checkout not moved": git(repo, "rev-parse", "HEAD") == before,
        "no config written":
            not os.path.exists(os.path.join(home, ".claude", "settings.json")),
    }


def case_dirty_tree_refuses_ff_but_still_applies(tmp):
    """Local tracked edits: the pull refuses (fleet-sync's rule), the edit
    survives, and the config STILL converges from the local checkout."""
    repo, home, stubs = build_fixture(tmp, "dell@example.com", actor_slug="dell",
                                      dirty=True)
    before = git(repo, "rev-parse", "HEAD")
    r = run_hook(repo, home, stubs, tmp)
    readme = open(os.path.join(repo, "README.md"), encoding="utf-8").read()
    return {
        "hook exits 0": r.returncode == 0,
        "pull refused and says so": "SKIP pull" in r.stdout,
        "checkout not moved": git(repo, "rev-parse", "HEAD") == before,
        "local edit survives": "LOCAL EDIT THAT MUST SURVIVE" in readme,
        "wrapper still restored from the LOCAL template": wrapper_intact(home),
    }


def main():
    cases = [
        ("secondary machine converges", case_secondary_converges),
        ("primary machine (actor slug) is a no-op", case_primary_noop),
        ("primary machine (owner-email fallback) is a no-op", case_owner_fallback_noop),
        ("dirty tree refuses ff, keeps the edit, still applies", case_dirty_tree_refuses_ff_but_still_applies),
    ]
    passed = failed = 0
    for name, fn in cases:
        with tempfile.TemporaryDirectory(prefix="machine-converge-") as tmp:
            try:
                checks = fn(tmp)
            except Exception as exc:
                print(f"FAIL  {name}: raised {exc!r}")
                failed += 1
                continue
        bad = [label for label, ok in checks.items() if not ok]
        if bad:
            print(f"FAIL  {name}: " + "; ".join(bad))
            failed += 1
        else:
            print(f"PASS  {name} ({len(checks)} checks)")
            passed += 1
    print(f"machine-converge-selftest: {passed}/{passed + failed} cases passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

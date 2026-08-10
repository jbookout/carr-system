#!/usr/bin/env python3
"""config-as-code.py — the machine config belongs in the repo, same as the code.

WHY (Joe, 2026-08-03: "shouldnt all code be in the repo? .json is code").

He is right, and the exposure was worse than the one file he was pointing at.
Measured that night:

    ~/.claude/settings.json      1 on disk,  0 in the repo   <- fires all 5 hooks
    launchd plists               2 on disk,  0 in the repo   <- incl. the hourly
                                                                rule refresh
    scheduled tasks             15 on disk,  4 in the repo

So the five hook SCRIPTS were version-controlled and the thing that makes them
run was not. That is the two-homes disease the whole system is built to avoid:
the same night, hooks/SETTINGS-BLOCK.md was found to have silently drifted — it
documented two hooks and the live file had four. A document DESCRIBING config
drifts. Config in the repo does not.

WHAT THIS DOES NOT DO, and the reason is not squeamishness. It does not put
`~/.claude/settings.json` in the repo wholesale. That file also carries 77
permission entries and notification prefs that are Joe's, machine-shaped, and
churn weekly — committing them would create a second home for something Claude
Code only ever reads from ~/.claude/, and a baseline would go stale exactly the
way SETTINGS-BLOCK.md did. Only the CARR-OWNED `hooks` block is tracked, and
`install` merges it back leaving every other key untouched.

THE PATTERN IS THE ONE THE SYSTEM ALREADY USES: source in the repo, render on
the machine. settings.json becomes a render, the same way clients-active.md is.

PORTABILITY IS THE POINT, NOT A BONUS. Repo copies store {{HOME}}, {{REPO}} and
{{VAULT}} instead of /Users/booko. That is what lets the same source install on
Dell's machine — and 54 of the 70 active rules are SHARED scope, binding him
exactly as they bind Joe, with zero mechanical enforcement on his side today.

    ops/config-as-code.py check      # drift report; exit 1 if any. THE DEFAULT.
    ops/config-as-code.py pull       # machine -> repo (capture what is live)
    ops/config-as-code.py install    # repo -> machine (deploy; needs --apply)

`check` is what belongs in run.sh health: it answers "is the live config still
the config we think we have", which is the question nobody could answer tonight.
"""

import json
import os
import re
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
def _find_vault():
    """The vault sits under a Drive mount named for the ACCOUNT, so a hardcoded
    default is Joe-only by construction — the exact defect this file exists to
    fix, one level down. Glob for it instead, so Dell's machine resolves his own
    mount. CARR_VAULT overrides."""
    env = os.environ.get("CARR_VAULT")
    if env:
        return env
    import glob
    hits = sorted(glob.glob(os.path.join(
        HOME, "Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI")))
    return hits[0] if hits else ""


VAULT = _find_vault()

SETTINGS = os.path.join(HOME, ".claude", "settings.json")
TASKS_SRC = os.path.join(HOME, ".claude", "scheduled-tasks")
TASKS_REPO = os.path.join(REPO, "ops", "scheduled-tasks")
LAUNCHD_SRC = os.path.join(HOME, "Library", "LaunchAgents")
LAUNCHD_REPO = os.path.join(REPO, "ops", "launchd")
HOOKS_REPO = os.path.join(REPO, "ops", "config", "hooks.json")

# Longest first: REPO and VAULT both sit under HOME, so substituting HOME first
# would leave "{{HOME}}/carr-system" and the REPO token would never match.
# EMPTY VALUES ARE FILTERED OUT, and that is not defensive padding: str.replace
# with an empty needle inserts the token between every character of the file.
# _find_vault() returns "" when no Drive mount matches, so one unmounted Drive
# would otherwise shred every tracked config on the next pull.
TOKENS = [(tok, real) for tok, real in
          (("{{VAULT}}", VAULT), ("{{REPO}}", REPO), ("{{HOME}}", HOME)) if real]

# Joe-only by nature; Dell has no video pipeline. Tracked so it is recoverable,
# never installed on another machine.
JOE_ONLY = {"com.carr.videopipeline.plist"}


def portable(text):
    for tok, real in TOKENS:
        text = text.replace(real, tok)
    return text


def concrete(text):
    for tok, real in TOKENS:
        text = text.replace(tok, real)
    return text


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def hook_scripts_untracked():
    """Hook scripts that settings.json points at but git does not track.

    THE GAP THIS CLOSES, found 2026-08-09. This tool verifies that the hooks
    BLOCK inside settings.json matches the repo. It never checked whether the
    SCRIPTS that block points at are committed. Those are different failures and
    only one of them was covered: on this machine the live settings referenced
    hooks/conduct-stop-gate.py, hooks/conduct_patterns.py and
    hooks/escalation-gate.py, all created the same afternoon and none of them in
    git. The check reported "OK — repo matches machine" the whole time, and it
    was telling the truth about the only thing it was looking at.

    WHY IT MATTERS RATHER THAN BEING TIDINESS. A hook that exists only in one
    working tree is one `git clean`, one disk failure or one fresh clone from
    being gone, and the settings block that survives will then point at files
    that are not there. It also cannot reach Dell: he pulls the repo, so an
    untracked gate binds Joe's sessions and silently binds nothing of his, which
    is the twin-parity failure rule 61c64d91 exists to prevent. The 2026-08-08
    wipe proved the settings block is worth versioning; the scripts it invokes
    are the other half of the same control.

    Returns a list of (path, why) — repo-relative where possible.
    """
    block = live_hooks_block()
    if not block:
        return []
    paths = set()
    for groups in block.values():
        if not isinstance(groups, list):
            continue
        for grp in groups:
            for hook in (grp or {}).get("hooks", []) or []:
                for tok in re.findall(r"(/[^\s'\"]+\.py)", hook.get("command", "") or ""):
                    paths.add(os.path.realpath(concrete(tok)))
    out = []
    for p in sorted(paths):
        if not os.path.exists(p):
            out.append((p, "settings.json invokes it and IT DOES NOT EXIST"))
            continue
        # Ask git about the file IN ITS OWN checkout, not in this script's.
        # The live settings point at the primary checkout (~/carr-system), while
        # this script may be running from a worktree — comparing against the
        # script's own REPO made every hook look "outside the repo" and the
        # check silently found nothing, which is worse than not having it.
        d = os.path.dirname(p)
        inside = subprocess.run(["git", "-C", d, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            continue                       # not in any git checkout: not ours to version
        # `git ls-files --error-unmatch` is the exact question: is this path in
        # the index? A file that is merely present is not a file that survives.
        rc = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", p],
                            capture_output=True).returncode
        if rc != 0:
            top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True).stdout.strip()
            rel = os.path.relpath(p, top) if top else p
            out.append((rel, "settings.json invokes it but git DOES NOT TRACK it"))
    return out


def carr_plists():
    try:
        return sorted(f for f in os.listdir(LAUNCHD_SRC)
                      if f.startswith("com.carr.") and f.endswith(".plist"))
    except FileNotFoundError:
        return []


def live_hooks_block():
    raw = read(SETTINGS)
    if raw is None:
        return None
    return json.loads(raw).get("hooks")


def pairs():
    """(label, live_text, repo_path) for every tracked item. live_text is
    already portable; repo contents are compared verbatim against it."""
    out = []

    hooks = live_hooks_block()
    out.append(("hooks block (settings.json)",
                None if hooks is None else portable(json.dumps(hooks, indent=2) + "\n"),
                HOOKS_REPO))

    seen = set()
    for name in sorted(os.listdir(TASKS_SRC)) if os.path.isdir(TASKS_SRC) else []:
        skill = os.path.join(TASKS_SRC, name, "SKILL.md")
        if os.path.isfile(skill):
            seen.add(f"{name}.SKILL.md")
            out.append((f"scheduled-task {name}", portable(read(skill)),
                        os.path.join(TASKS_REPO, f"{name}.SKILL.md")))
    # A task deleted from the machine but still in the repo is drift too — the
    # repo would otherwise quietly claim a job that no longer runs anywhere.
    for f in sorted(os.listdir(TASKS_REPO)) if os.path.isdir(TASKS_REPO) else []:
        if f.endswith(".SKILL.md") and f not in seen:
            out.append((f"scheduled-task {f[:-9]} (IN REPO, NOT ON MACHINE)",
                        None, os.path.join(TASKS_REPO, f)))

    for f in carr_plists():
        out.append((f"launchd {f}", portable(read(os.path.join(LAUNCHD_SRC, f))),
                    os.path.join(LAUNCHD_REPO, f)))
    return out


def cmd_check():
    # SEVERITY IS NOT COSMETIC HERE, and the 2026-08-08 incident is why.
    # A tracked item MISSING from the machine means a protection that was
    # supposed to be running is not running. A tracked item merely DIFFERENT
    # usually means the repo baseline lagged a deliberate live change. Those are
    # not the same event and must not print the same line.
    #
    # WHAT ACTUALLY FAILED. On 2026-08-06 the guard's matcher was widened to
    # "Bash|WebFetch" on the machine and nobody pulled, so this check went red
    # for a benign reason and STAYED red. On 2026-08-08 15:46 a plugin install
    # rewrote ~/.claude/settings.json and deleted the entire hooks block — all
    # five gates off. The headline both days: "DRIFT — 2 of 28 items". Identical
    # string, and the health row prints only that first line, so the difference
    # between a matcher tweak and total gate annihilation was invisible. It went
    # unnoticed for a day and was found by accident.
    #
    # The lesson is rule 590b11e1's, arriving the other way round: a check that
    # is chronically red detects nothing, because a reader who has learned to
    # skip a red row will skip the one that matters. Keeping this row green when
    # nothing is wrong is therefore part of the control, not tidiness.
    missing, untracked, different = [], [], []
    for label, live, repo_path in pairs():
        have = read(repo_path)
        if live is None:
            missing.append((label, "on disk: MISSING; in repo: present"))
        elif have is None:
            untracked.append((label, "on disk: present; in repo: NOT TRACKED"))
        elif have != live:
            different.append((label, "TRACKED BUT DIFFERENT from the live copy"))
    # A hook script the settings block invokes but git does not track is a
    # separate failure from a settings mismatch, and it used to be invisible
    # because this tool only ever compared the block itself. It is reported as
    # UNVERSIONED rather than folded into `untracked`, because the remedy is a
    # commit rather than a `pull`.
    unversioned = hook_scripts_untracked()
    drift = missing + untracked + different
    if not drift and not unversioned:
        print(f"config-as-code: OK — {len(pairs())} items, repo matches machine")
        return 0
    if not drift and unversioned:
        print(f"config-as-code: UNVERSIONED HOOKS — {len(unversioned)} script(s) the live "
              f"settings invoke are not in git: " + ", ".join(p for p, _ in unversioned))
        for p, why in unversioned:
            print(f"  {p}\n      {why}")
        print("\n  A gate that exists in one working tree only is one `git clean` or one\n"
              "  fresh clone from gone, and it can never reach Dell. Commit them:\n"
              "      git -C ~/carr-system add " + " ".join(p for p, _ in unversioned))
        return 1
    # The headline carries the severity, because callers that summarise this
    # tool (tools/health-check.py) read the FIRST LINE ONLY.
    headline = f"config-as-code: DRIFT — {len(drift)} of {len(pairs())} items"
    if missing:
        headline += f" — {len(missing)} MISSING FROM MACHINE: " + ", ".join(
            label for label, _ in missing)
    print(headline)
    for label, why in drift:
        print(f"  {label}\n      {why}")
    if missing:
        print("\n  MISSING means a tracked config is NOT ON THE MACHINE. If it is the\n"
              "  hooks block, every gate is currently off — restore it FIRST:\n"
              "      python3 ops/config-as-code.py install --apply\n"
              "  then prove it with a denial that should fail, e.g. a WebFetch to a\n"
              "  host outside KNOWN_HOSTS.")
    if untracked or different:
        print("\n  `ops/config-as-code.py pull` to capture the machine into the repo.")
    # Reported even when settings drift is also present: the two have different
    # remedies (a pull versus a commit), so folding them together would hide one.
    if unversioned:
        print(f"\n  ALSO — {len(unversioned)} hook script(s) the live settings invoke are not in git:")
        for p, why in unversioned:
            print(f"  {p}\n      {why}")
        print("      git -C ~/carr-system add " + " ".join(p for p, _ in unversioned))
    return 1


def cmd_pull(apply):
    wrote = 0
    for label, live, repo_path in pairs():
        if live is None:
            print(f"  SKIP  {label} (not on this machine; left in the repo)")
            continue
        if read(repo_path) == live:
            continue
        print(f"  {'WRITE' if apply else 'would write'}  {os.path.relpath(repo_path, REPO)}")
        if apply:
            os.makedirs(os.path.dirname(repo_path), exist_ok=True)
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write(live)
        wrote += 1
    print(f"\n{wrote} item(s) {'written' if apply else 'would be written'}."
          + ("" if apply else " Re-run with --apply."))
    return 0


def cmd_install(apply):
    """repo -> machine. The half that makes a second machine possible."""
    if not os.path.exists(SETTINGS):
        print(f"ERROR: no settings file at {SETTINGS}")
        return 1
    raw = read(SETTINGS)
    try:
        cfg = json.loads(raw)
    except Exception as exc:
        print(f"ERROR: {SETTINGS} is not valid JSON ({exc}) — refusing to touch it.")
        return 1

    src = read(HOOKS_REPO)
    if src is None:
        print(f"ERROR: no tracked hooks block at {HOOKS_REPO}. Run `pull` first.")
        return 1

    planned = json.loads(concrete(src))
    if cfg.get("hooks") == planned:
        print("  hooks block already matches the repo")
    else:
        print("  hooks block: WILL BE REPLACED with the repo's version")
        print(f"    preserved top-level keys: {sorted(k for k in cfg if k != 'hooks')}")
        p = cfg.get("permissions", {})
        print(f"    preserved permission entries: "
              f"{sum(len(v) for v in p.values() if isinstance(v, list))}")
        cfg["hooks"] = planned

    for f in sorted(os.listdir(LAUNCHD_REPO)) if os.path.isdir(LAUNCHD_REPO) else []:
        if f in JOE_ONLY:
            print(f"  SKIP  {f} (Joe-only; never installed elsewhere)")
            continue
        dest = os.path.join(LAUNCHD_SRC, f)
        body = concrete(read(os.path.join(LAUNCHD_REPO, f)))
        if read(dest) == body:
            continue
        print(f"  {'WRITE' if apply else 'would write'}  {dest}")
        if apply:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(body)
            # Load it, do not print a command for a human to paste (rule
            # e313a3ca). Writing the plist and stopping leaves the job on disk
            # and dead: on a fresh machine that means the nightly never runs,
            # so the record-derived fetch allowlist is generated once by the
            # migration and then never refreshed as clients are added. unload
            # is expected to fail when the job was never loaded; that is not
            # an error, which is why only the load result is reported.
            subprocess.run(["launchctl", "unload", "-w", dest],
                           capture_output=True, check=False)
            r = subprocess.run(["launchctl", "load", "-w", dest],
                               capture_output=True, text=True, check=False)
            if r.returncode == 0:
                print("      loaded")
            else:
                print(f"      LOAD FAILED ({(r.stderr or r.stdout).strip()[:80]}) "
                      f"— run: launchctl load -w {dest}")

    # Git hooks. Added 2026-08-03, when Dell was granted WRITE and it turned out
    # branch protection is unavailable on a private free-plan repo — so the pull
    # request review team-loops T39 relied on has no server-side replacement.
    # ops/githooks/pre-push refuses a direct push to main from any identity but
    # the owner's. It installs HERE rather than being a step in the runbook,
    # because a guard that depends on someone remembering a config command is
    # not a guard. Machine config ships with the code; that is what this file is.
    hooks_dir = os.path.join(REPO, "ops", "githooks")
    if os.path.isdir(hooks_dir):
        current = subprocess.run(
            ["git", "-C", REPO, "config", "--get", "core.hooksPath"],
            capture_output=True, text=True).stdout.strip()
        if current == "ops/githooks":
            print("  git hooksPath already points at ops/githooks")
        else:
            print(f"  git hooksPath: {current or '(unset)'} -> ops/githooks"
                  + ("" if apply else "   [would set]"))
        if apply:
            subprocess.run(["git", "-C", REPO, "config", "core.hooksPath", "ops/githooks"],
                           check=False)
            for h in sorted(os.listdir(hooks_dir)):
                p = os.path.join(hooks_dir, h)
                if os.path.isfile(p):
                    os.chmod(p, os.stat(p).st_mode | 0o111)
            print("  git hooks installed (pre-push guards main)")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    backup = SETTINGS + ".bak-config-as-code"
    shutil.copy2(SETTINGS, backup)
    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    try:
        json.loads(read(SETTINGS))
    except Exception as exc:
        shutil.copy2(backup, SETTINGS)
        print(f"ERROR: write produced unparseable JSON ({exc}) — restored {backup}")
        return 1
    # NO RESTART NEEDED, and the old message here said otherwise for months.
    # Live-tested 2026-08-09 with two independent confirmations: git-writer-gate
    # and gate-edit-gate were both installed MID-SESSION and both fired in a
    # session that started before either existed. Claude Code reads the hooks
    # block per tool call, not once at session start. This matters — the old
    # wording implied every other running session stayed unguarded until it was
    # restarted, which would have made a gate install nearly useless on a machine
    # running five sessions. The opposite is true: an install takes effect
    # everywhere immediately. Rule 97326357 — a claim about a surface becomes
    # doctrine only after a live test from that surface.
    print(f"\nWROTE OK (backup: {backup}). Live immediately — Claude Code reads "
          f"the hooks block per tool call, so every running session is covered "
          f"without a restart (verified 2026-08-09).")
    return 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    apply = "--apply" in sys.argv
    if mode == "check":
        return cmd_check()
    if mode == "pull":
        return cmd_pull(apply)
    if mode == "install":
        return cmd_install(apply)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Which tolls does THIS change owe, read off the diff before it is pushed.

WHY THIS EXISTS. Hosted CI on one branch refused three times in a row on
2026-09-18, and not one refusal was about whether the code worked. Each was a
TOLL this repository charges for a kind of change, paid late or not at all:

  · a gate was edited and its baseline was re-blessed -- correctly -- but the
    source-inventory seal was derived BEFORE the last edit, so the seal
    described a file that no longer existed by the time it was committed.
  · the branch fell behind main and the pull request went CONFLICTING, which
    draws ZERO checks, so "no checks reported" looked like a slow runner for
    twenty minutes.
  · the conflict was in two derived files, where taking either side whole
    silently drops the other side's rows and the seal still verifies.

These are knowable from the changed-file list. They were not caught because
knowing them depends on a session remembering nine separate lessons at the
moment it types git push, and a rule that asks for memory fires sometimes.

THE SHAPE. Each toll is an INDEPENDENT yes-or-no about one change, so each is
its own question and all of them go in ONE request -- independent questions
about a single subject are asked together, and each is still scored on its own.
There is no ranking here and nothing competes, so this is not a pick-one.

IT ADVISES AND DOES NOT DECIDE. It prints what looks owed and why. It cannot
block a push, because it returns probabilities and the deterministic checks are
the ones allowed to refuse. Being wrong here costs a reader thirty seconds;
being authoritative here would cost a correct change that could not ship.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT_SECONDS = 60.0
VERIFY_AT = 0.60   # a check worth RUNNING is a lower bar than a remedy worth printing
WARN_AT = 0.55

# Every toll below was paid late or missed at least once, and each names the
# concrete remedy rather than the principle, because a session reading this
# output is about to push and needs the command, not the lesson.
TOLLS = {
    "inventory_reseal": (
        "The changed files are in `change.files`. Does this change edit a file "
        "that the sealed source inventory tracks as a script entrypoint -- "
        "anything under hooks/, bin/, tools/, pipelines/, ops/ that carries a "
        "shebang or a main guard, or any file under mcp-server/src/? Editing "
        "one changes its digest and the seal must be re-derived.",
        "union the row into current_source_review.upsert in ops/config/"
        "scac-registry-source-inventory-fixtures.v1.json, then pin the digest "
        "the ASSERTION reports, not the generator's. They are different "
        "numbers: the generator prints a digest of the raw rows, while the "
        "seal covers the REVIEWED set, which is the base version with the "
        "upserts applied. Run it and read the observed value back: node "
        "--input-type=module -e \"import "
        "{assertCurrentSourceInventoryMatchesFixture} from "
        "'./ops/scac-mutation-inventory.mjs'; import {TOOLS} from "
        "'./mcp-server/src/tools.js'; "
        "assertCurrentSourceInventoryMatchesFixture(TOOLS)\" -- and run it "
        "AFTER your last edit, never before"),

    "new_ingress_admitted": (
        "Does this change ADD a new file that carries a shebang line or an "
        "`if __name__ == \"__main__\"` guard, under hooks/, ops/, bin/, tools/ "
        "or pipelines/? A new file with either of those is a new sealed "
        "ingress, which moves the frontier and costs a registry successor "
        "rather than a re-digest. A new module with neither is a library and "
        "costs nothing. TWO EXCEPTIONS THAT ARE NOT INGRESSES no matter what "
        "they contain, because the generator excludes them by name: any file "
        "whose name contains `selftest`, and anything under a `test/` "
        "directory. A new selftest carrying a main guard costs nothing.",
        "make it a library -- no shebang, no main guard -- and host the "
        "dispatch inside an entrypoint that is already inventoried, or open a "
        "registry successor for it"),

    "gate_rebless": (
        "Does this change edit a file under hooks/ that is one of the gates the "
        "baseline tracks? A gate's bytes are pinned, so an edited gate and its "
        "baseline must move in the SAME commit.",
        "run `hooks/gate-integrity.py --bless hooks/<the-gate>.py` and commit "
        "ops/config/gate-baseline.json alongside the gate"),

    "paired_selftest": (
        "Does this change edit a gate under hooks/ WITHOUT changing the "
        "matching ops/<same-name>-selftest.py? A gate and its paired suite are "
        "meant to change together; a gate that gains behaviour its suite does "
        "not exercise has stopped being covered.",
        "add cases to ops/<gate-name>-selftest.py for the behaviour you added"),

    "ci_sh_reseal": (
        "Does this change edit ops/ci.sh itself? Editing it pulls further "
        "obligations than editing an ordinary script, because other checks read "
        "its contents back out.",
        "a COUNT change in ops/ci.sh pulls three more places -- run "
        "ops/ci-selftest.py and read what it names"),

    "judgment_without_caller": (
        "Does this change ADD a module under ops/ that reaches a model -- one "
        "importing typesafe_client or jev_judge -- without any file under "
        "hooks/, bin/, tools/, pipelines/ or mcp-server/src/ calling it?",
        "wire it to a door that already fires, or add it to DECLARED_INERT in "
        "ops/judgment-wiring-selftest.py with a reason and a loop number"),

    "derived_file_hand_merged": (
        "Do the changed files include a DERIVED artifact -- a seal, a baseline, "
        "a generated registry or fixture -- alongside a merge of another "
        "branch? Resolving one of those by choosing a side silently discards "
        "the other side's rows, and the file still verifies afterwards, so "
        "nothing catches it.",
        "union the rows from both sides, then re-derive EVERY row against the "
        "merged tree rather than carrying any of them over"),

    "settings_matcher_change": (
        "Does this change touch a settings file that registers hooks, or add a "
        "hook that needs a tool matcher it does not yet have? A hook registered "
        "on no matcher never fires, and a bad path there blocks every tool at "
        "once.",
        "check the matcher actually names the tool, and pair matchers to hooks "
        "programmatically rather than reading the two lists in order"),
}


def _client():
    spec = importlib.util.spec_from_file_location(
        "typesafe_client", os.path.join(REPO, "ops", "typesafe_client.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def change(base="origin/main", repo=REPO):
    """The changed-file list, plus the two facts the questions cannot see.

    Measured against origin/<base> rather than HEAD: on a shared checkout a
    local base drifts behind constantly, and diffing against a stale one
    reports every line those commits added as though it were this change.
    """
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, timeout=120)
    # COMMITTED AND UNCOMMITTED BOTH. The first version diffed only
    # origin/main...HEAD, which is right at push time and useless while
    # working: on 2026-09-18 a session edited mcp-server/src/tools.js, asked
    # what the change owed, and was told about two other files -- because the
    # edit it was asking about had not been committed yet. The toll it needed
    # was the expensive one: 100 of the 864 inventory rows name that file as
    # their source, so its bytes moving re-digests every one of them.
    # NOT .strip() BEFORE .splitlines(). Porcelain's status is two COLUMNS,
    # so an unstaged edit is " M path" with a leading space -- and stripping
    # the whole blob eats that space on the FIRST line only, shifting its
    # path by one character. Caught 2026-09-18 when the advisory reported an
    # edit to "cp-server/src/tools.js", a file that does not exist: one row
    # silently mis-parsed while every row after it was fine.
    names = subprocess.run(["git", "diff", "--name-status", f"{base}...HEAD"],
                           capture_output=True, text=True, cwd=repo,
                           timeout=60).stdout.splitlines()
    names += subprocess.run(["git", "status", "--porcelain=v1"],
                            capture_output=True, text=True, cwd=repo,
                            timeout=60).stdout.splitlines()
    added, edited = [], []
    for row in names:
        # Two shapes reach here. `git diff --name-status` gives "M\tpath";
        # `git status --porcelain` gives "?? path" or " M path". Untracked and
        # added both count as ADDED, because what decides the toll is whether
        # the file is new to the repository, not how it got there.
        if "\t" in row:
            parts = row.split("\t")
            status, path = parts[0], parts[-1]
        else:
            status, path = row[:2].strip() or "M", row[3:].strip()
        if not path:
            continue
        target = added if status.startswith(("A", "??")) else edited
        if path not in target:
            target.append(path)
    # Whether a file is an entrypoint is a fact the questions CANNOT see, so
    # it is gathered here for every touched file rather than inferred from a
    # path. Two mistakes in the first version of this function, both of which
    # made the model answer a question about facts it had not been given:
    # only ADDED files were classified, so an edited entrypoint looked like an
    # ordinary edit and the reseal toll went unflagged on a branch that owed
    # it; and only the first 4096 bytes were read, so a main guard -- which
    # sits at the BOTTOM of every file that has one -- was invisible in any
    # file longer than that.
    def entrypoint(rel):
        try:
            with open(os.path.join(repo, rel), encoding="utf-8",
                      errors="ignore") as fh:
                body = fh.read()
        except OSError:
            return False
        return body.startswith("#!") or '__name__ == "__main__"' in body \
            or "__name__ == '__main__'" in body

    shebangs = [rel for rel in added if entrypoint(rel)]
    edited_entrypoints = [rel for rel in edited if entrypoint(rel)]
    merged = subprocess.run(
        ["git", "log", "--merges", "--oneline", f"{base}..HEAD"],
        capture_output=True, text=True, cwd=repo, timeout=60).stdout.strip()
    return {"files": {"added": added, "edited": edited},
            "added_files_with_a_shebang_or_main_guard": shebangs,
            "edited_files_that_are_script_entrypoints": edited_entrypoints,
            "this_branch_merged_another_branch": bool(merged)}


# ── what proves a toll paid ──────────────────────────────────────────────────
#
# WHY THIS EXISTS, and it is a specific failure on 2026-09-18 rather than a
# general worry. This advisory scored `inventory_reseal` at 0.94 on a push, in
# the session's own terminal, naming the exact remedy. The session read it and
# pushed anyway. Hosted CI failed 25 minutes later on precisely that, and the
# whole cycle had to be spent again. The judgment was not missing, not wrong
# and not quiet -- it simply had no consequence attached to it.
#
# THE SHAPE OF THE FIX, which keeps the model out of the blocking decision.
# Rule 'judgment advises beside the deterministic layer, never decides inside
# it' still holds: a probability must not be what refuses a push. So the model
# does not block anything. It CHOOSES WHICH DETERMINISTIC CHECK IS WORTH
# RUNNING, and that check's own exit code decides. A toll the model scores
# high is a check that gets run; the check passes or fails on bytes.
#
# This also settles what a false positive costs. The advisory has scored
# `gate_rebless` at 0.71 on changes that owed no rebless. Under this design
# that costs running gate-integrity.py, which takes under a second and passes.
# A wrong judgment buys a few seconds of checking, never a blocked afternoon,
# which is the trade that makes running these at all safe.
#
# A toll with no verifier stays purely advisory. Printing a remedy nobody can
# check mechanically is still worth doing; it just cannot hold a push.
VERIFIERS = {
    "inventory_reseal": (
        ["node", "--input-type=module", "-e",
         "import {assertCurrentSourceInventoryMatchesFixture} from "
         "'./ops/scac-mutation-inventory.mjs'; import {TOOLS} from "
         "'./mcp-server/src/tools.js'; "
         "assertCurrentSourceInventoryMatchesFixture(TOOLS);"],
        "the sealed source inventory does not match this tree"),
    "gate_rebless": (
        [".venv/bin/python", "hooks/gate-integrity.py"],
        "a gate's bytes no longer match ops/config/gate-baseline.json"),
    "judgment_without_caller": (
        [".venv/bin/python", "ops/judgment-wiring-selftest.py"],
        "a module that reaches the model has no caller and is not declared inert"),
    "ci_sh_reseal": (
        [".venv/bin/python", "ops/ci-selftest.py"],
        "ops/ci.sh changed and something that reads it back out disagrees"),
}


def verify(state=None, *, floor=None, client=None, api_key=None, repo=REPO):
    """Run the deterministic check behind every toll the model scores high.

    Returns [(name, probability, reason, output)] for checks that FAILED. An
    empty list means either nothing was flagged or everything flagged is
    already paid.

    `floor` defaults to VERIFY_AT rather than the advisory's own warning floor:
    a check worth running is a lower bar than a remedy worth printing, so this
    deliberately runs more checks than the advisory prints warnings.
    """
    floor = VERIFY_AT if floor is None else floor
    state = change(repo=repo) if state is None else state
    failures = []
    for probability, name, _fix in owed(state, client=client, api_key=api_key,
                                        floor=floor):
        entry = VERIFIERS.get(name)
        if not entry:
            continue
        argv, reason = entry
        try:
            done = subprocess.run(argv, cwd=repo, capture_output=True,
                                  text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            # A verifier that cannot RUN is not a verifier that failed. Saying
            # otherwise would block a push on a missing interpreter, which is
            # the fail-closed trap this advisory is not allowed to become.
            failures.append((name, probability, f"{reason} (check could not run: {exc})", ""))
            continue
        if done.returncode != 0:
            failures.append((name, probability, reason,
                             ((done.stdout or "") + (done.stderr or ""))[-1200:]))
    return failures


def owed(state=None, *, client=None, api_key=None, floor=WARN_AT):
    tsc = client or _client()
    state = state if state is not None else change()
    questions = {name: tsc.noul(text) for name, (text, _) in TOLLS.items()}
    answer = tsc.ask({"change": state}, questions, timeout=TIMEOUT_SECONDS,
                     api_key=api_key)
    out = []
    for name, body in (answer.get("answers") or {}).items():
        probability = float(body.get(body.get("type"), 0.0))
        if probability >= floor:
            out.append((probability, name, TOLLS[name][1]))
    out.sort(reverse=True)
    return out

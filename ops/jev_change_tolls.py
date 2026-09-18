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
        "run `node ops/scac-mutation-inventory.mjs`, union the row into "
        "current_source_review.upsert in ops/config/"
        "scac-registry-source-inventory-fixtures.v1.json, and pin the derived "
        "digest -- AFTER your last edit, never before it"),

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
    names = subprocess.run(["git", "diff", "--name-status", f"{base}...HEAD"],
                           capture_output=True, text=True, cwd=repo,
                           timeout=60).stdout.strip().splitlines()
    added, edited = [], []
    for row in names:
        parts = row.split("\t")
        if len(parts) < 2:
            continue
        (added if parts[0].startswith("A") else edited).append(parts[-1])
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

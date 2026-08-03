#!/usr/bin/env python3
"""record-home-gate.py — the PreToolUse DENY gate on records written to markdown.

WHY THIS EXISTS. Joe, 2026-08-03: "you cant write a .md file you have to write to
the database... it is one of the worst mistakes that can be made in this database
system. writing a .md file creates a big issue. its undetectable to the system."

He is right about undetectable, and that is the whole defect. Every OTHER control
in this system that matters has a mechanism behind it: destructive Bash has
guard-unattended.py, taught-rule shape has rule-shape-gate.py, prose quality has
lint-gate.py, the ledger rules have ledger-sweep.py. Shared rule "findings and
record updates go into the DATABASE, never into a markdown report" is the one
major rule enforced ONLY by a session remembering it at the moment of writing.

It failed twice in one sitting on 2026-08-03, in a session that had loaded the
rule and recited it back at session start. That is the same failure ledger-sweep
was built for, and its docstring already carries the finding: "The only control
that worked unassisted all night was a hook."

THE FOUR CAUSES, because a gate that treats only the symptom moves the leak:

  1. THE RULE'S OWN CARVE-OUT IS A TRAP. It ends "Narrative and doctrine files
     stay markdown; this rule is about RECORDS and FINDINGS." A handoff packet,
     a status writeup, an audit report and a checkpoint all LOOK like narrative
     and are made almost entirely of records: findings, decisions, open items.
     The rule as written permits the reading that broke it.
  2. NOTHING DETECTS IT. Write and Edit on a .md are completely unguarded. A
     wrong DB write throws; a wrong markdown write succeeds silently and reaches
     nobody — no query finds it, no export renders it, the other partner's
     sessions never see it. Silent success is the worst possible failure shape.
  3. A SKILL MANDATES IT. Both /handoff skills exist to produce "a drop-in
     markdown packet" and file it into CARR. The system instructs sessions to do
     the thing. That contradiction is tracked separately and is not this file's
     job, but no gate fixes a workflow that tells sessions to break the rule.
  4. FRICTION ASYMMETRY. Write always works. A verb needs a schema load, a fresh
     UUID and a base_version read. Under time pressure the cheap path wins, which
     is a design fact about the tooling, not a character fact about the session.

This file addresses cause 2 only, and only cause 2. That is honest scoping, not
a claim to have solved the problem.

WHAT IT DENIES, and every rule here is PATH-STRUCTURAL rather than content-
sniffing, on purpose. lint-gate.py's own doctrine says a gate that fires on
everything is a gate people learn to ignore, and a content heuristic on prose
this varied would misfire constantly and burn the gate's credibility in a day.

  A. ANY write or edit to a GENERATED RENDER. Already a hard rule with no
     mechanism until now. Pure path match, zero ambiguity, zero false positives.
  B. CREATING A NEW .md under 00_Context/, the vault ROOT, or out/. New-file
     creation is the tell: narrative and doctrine files already EXIST and get
     edited, while records arrive as brand-new files. This is the exact shape of
     every instance of the defect on record, including the cloud session that
     hand-wrote a DECISIONS.md duplicating what add-loop already does.
  C. ANY write or edit under 00_Context/handoffs/. A handoff is the purest form
     of records wearing narrative clothing, and it is the shape that failed on
     2026-08-03. The record layer already has the receiving end: catch-me-up and
     today-triage brief a new session from the DB, with no file to point at.
  D. Content carrying a RULE UUID next to a rule verb (teach/activate/retire/
     amend/supersede). That combination is never narrative — it is a record. This
     is the one content check, kept deliberately narrow.

WHAT IT DOES NOT TOUCH. Anything outside the CARR vault (the repo, scratchpads,
Life AI). Non-markdown files. Edits to doctrine and playbooks under DNA/, to
CLAUDE.md, INDEX.md, skills and agents — all of which are genuine narrative that
is edited in place, which is why rule B keys on CREATION rather than on writing.

KNOWN LIMIT, STATED PLAINLY. A session determined to write records into an
EXISTING narrative file under DNA/ can still do it. Rules A-D catch where the
defect has actually occurred, not everywhere it could. Tightening beyond this
needs Joe's call, because the cost lands on his own writing surface.

FAILS CLOSED ON DENY, OPEN ON ERROR. Exit 2 + stderr is the deny path, matching
guard-unattended.py and for the same reason: the structured JSON contract needs
exit 0, so on any build that does not parse it, exit 0 reads as ALLOW and the
gate fails open silently. Any INTERNAL error allows the call, because a gate that
wedges a session costs more than the marginal safety of failing closed on a
single-operator machine.
"""

import json
import os
import re
import sys

VAULT = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")

# --- A. generated renders (from exporters/targets.py, 2026-08-03) ------------
GENERATED = {
    "00_Context/compiled-rules-joe.md",
    "00_Context/decision-history.md",
    "00_Context/idea-bank.md",
    "00_Context/open-loops-backlog.md",
    "00_Context/open-loops.md",
    "DNA/Clients/client-roster.xlsx",
    "DNA/Clients/clients-active.md",
    "DNA/Deal Management/panhandle-team-deals.json",
    "DNA/Leads/lead-registry.xlsx",
    "DNA/Leads/lead-router-2026-07-13.xlsx",
    "DNA/Network/introduction-rules.md",
    "DNA/Network/vendors.xlsx",
    "DNA/Team/action-required.md",
    "DNA/Team/team-loops.md",
    "DNA/compiled-rules-dell.md",
    "DNA/compiled-rules-shared.md",
}

# --- B. directories where a NEW .md is a record masquerading as a note -------
NEW_MD_DENIED_DIRS = ("00_Context/", "out/", "")   # "" = the vault root itself

# --- C. the handoff shape ----------------------------------------------------
HANDOFF_DIR = "00_Context/handoffs/"

# --- D. the one content check ------------------------------------------------
RULE_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
RULE_VERB = re.compile(r"\b(teach|activate-rule|retire-rule|amend-rule|supersedes?|"
                       r"proposed rule|active rule)\b", re.I)

USE_INSTEAD = ("Use a verb: log-decision (a settled call and why), add-loop (an open item), "
               "record-finding (research/verification), teach (a standing rule), "
               "update-loop / close-loop. The record layer is the memory; a markdown file "
               "reaches nobody and no query finds it.")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(f"record-home-gate {msg}\n")
    except Exception:
        pass


def rel_to_vault(path):
    """Vault-relative POSIX path, or None when the file is outside the vault."""
    try:
        ap = os.path.realpath(os.path.expanduser(path))
        av = os.path.realpath(VAULT)
        if ap == av or not ap.startswith(av + os.sep):
            return None
        return os.path.relpath(ap, av).replace(os.sep, "/")
    except Exception:
        return None


def check(tool, ti):
    path = ti.get("file_path") or ti.get("filePath") or ""
    if not path:
        return None
    rel = rel_to_vault(path)
    if rel is None:
        return None                                   # outside the vault: not ours

    # A. generated renders — any tool, any extension
    if rel in GENERATED:
        return (f"'{rel}' is a GENERATED render — hand-editing it is overwritten by the next "
                f"export and the change is lost. Change the record through a verb, or change "
                f"the exporter in ~/carr-system/exporters/. {USE_INSTEAD}")

    if not rel.lower().endswith(".md"):
        return None                                   # B-D are markdown-only

    # C. the handoff shape, new or existing
    if rel.startswith(HANDOFF_DIR):
        return (f"'{rel}' is a HANDOFF — the shape that broke this rule on 2026-08-03. A handoff "
                f"is records (findings, decisions, open items) wearing narrative clothing, and "
                f"markdown strands every one of them: undetectable to the system, invisible to "
                f"Dell's sessions, findable only if someone remembers to point at the file. The "
                f"record layer already has the receiving end — catch-me-up and today-triage brief "
                f"a cold session straight from the DB. {USE_INSTEAD}")

    # B. creating a NEW .md where records masquerade as notes
    if tool == "Write" and not os.path.exists(os.path.expanduser(path)):
        parent = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
        if parent in NEW_MD_DENIED_DIRS:
            return (f"creating a NEW markdown file at '{rel}'. New-file creation is the tell: "
                    f"narrative and doctrine files already exist and get edited, while RECORDS "
                    f"arrive as brand-new files. If this genuinely is new doctrine rather than a "
                    f"record, it belongs under DNA/ and Joe decides that. {USE_INSTEAD}")

    # D. a rule id next to a rule verb is a record, never narrative
    body = ti.get("content") or ti.get("new_string") or ""
    if isinstance(body, str) and RULE_UUID.search(body) and RULE_VERB.search(body):
        return (f"'{rel}' carries a rule id alongside a rule verb, which makes it a RECORD, not "
                f"narrative. Rule text lives in the rule store and renders through the "
                f"compiled-rules exports. {USE_INSTEAD}")

    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                          # fail OPEN
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        ti = payload.get("tool_input") or payload.get("toolInput") or {}
        if tool not in ("Write", "Edit", "MultiEdit") or not isinstance(ti, dict):
            sys.exit(0)

        reason = check(tool, ti)
        if reason:
            log(f"DENY {tool} :: {reason[:220]}")
            print(f"BLOCKED by the CARR record-home gate: {reason}", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    except Exception as exc:                          # fail OPEN
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()

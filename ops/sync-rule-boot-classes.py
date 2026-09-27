#!/usr/bin/env python3
"""sync-rule-boot-classes.py — regenerate mcp-server/src/rule-boot-classes.js
from ops/config/rule-classes.v1.json, and guard the rule boot budget.

WHY A GENERATED MODULE. standing-context's `detail: "boot"` mode (the gated
rule boot, see mcp-server/src/rule-boot.js) renders an index of every active
rule plus the full text of the always-on set. The class, the <=20-word
summary and the "when it applies" line for each rule are committed data in
ops/config/rule-classes.v1.json; the rule STATEMENTS are never committed and
are read from the store at request time. A Cloudflare Worker has no
filesystem at request time, so the class data ships as this checked-in
module, the same pattern as ops/sync-core-rule-ids.py.

THIS SCRIPT IS THE ONLY WAY THE MODULE IS MEANT TO BE PRODUCED, and --check is
the same code path, so the write and the parity check cannot drift apart
(rule a8c55a47).

THE BUDGET GUARD. The boot text is delivered to every session and every
subagent before its first ordinary tool call, so its size is paid on every
context. --check also estimates the rendered size from the committed `chars`
(statement length at classification; a length, never text) and FAILS when the
index plus the always-on text exceeds `budget_tokens` (chars / chars_per_token),
naming the largest always-on rules. It never suggests truncating: an overage
is resolved by consolidating rules or by a budget decision that is Joe's.

EVERY SCOPE IS MEASURED: unsponsored, every partner named in `sponsors`
(Dell and Joe today) and any partner a classified rule is personal to. And
EVERY ACTIVE RULE MUST BE CLASSIFIED: an active id in
ops/config/rule-enforcement-map.json that is not in the class file would
render as class U in full text, outside this estimate, so --check fails on it.
The one exclusion is the rule_surface "intro_politics" rules, which the verb's
query leaves out of the boot.

Usage:
    ./.venv/bin/python ops/sync-rule-boot-classes.py            # regenerate
    ./.venv/bin/python ops/sync-rule-boot-classes.py --check    # parity + budget; exit 1 on either
"""
import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASSES_PATH = os.path.join(REPO, "ops", "config", "rule-classes.v1.json")
MAP_PATH = os.path.join(REPO, "ops", "config", "rule-enforcement-map.json")
EXCLUDED_SURFACES = {"intro_politics"}
OUT_PATH = os.path.join(REPO, "mcp-server", "src", "rule-boot-classes.js")
CLASSES = {"a", "b", "c", "d", "e"}
MAX_SUMMARY_WORDS = 20
# Must match mcp-server/src/rule-boot.js's layout: one always-on entry is
# "### <id>[ (personal)]\n<statement>\n\n", one index line is
# "<id> | <CLASS> | <summary> | <when>\n". PREAMBLE_CHARS is a fixed allowance
# for the headers and instructions the renderer prints once.
PREAMBLE_CHARS = 2400


def load(path=CLASSES_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate(doc):
    problems = []
    rules = doc.get("rules")
    if not isinstance(rules, dict) or not rules:
        return ["rules must be a non-empty object"]
    for rid, row in sorted(rules.items()):
        if len(rid) != 8 or any(ch not in "0123456789abcdef" for ch in rid):
            problems.append(f"{rid}: id must be the 8-char lowercase hex short form")
        if row.get("class") not in CLASSES:
            problems.append(f"{rid}: class must be one of {sorted(CLASSES)}")
        summary = row.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append(f"{rid}: summary missing")
        elif len(summary.split()) > MAX_SUMMARY_WORDS:
            problems.append(f"{rid}: summary is {len(summary.split())} words (max {MAX_SUMMARY_WORDS})")
        if not isinstance(row.get("when"), str) or not row["when"].strip():
            problems.append(f"{rid}: when missing")
        if not isinstance(row.get("always_on"), bool):
            problems.append(f"{rid}: always_on must be a boolean")
        if row.get("class") == "a" and not row.get("always_on"):
            problems.append(f"{rid}: class a is always on by definition")
        if not isinstance(row.get("chars"), int) or row["chars"] <= 0:
            problems.append(f"{rid}: chars must be a positive integer")
        if "statement" in row or "human_quote" in row:
            problems.append(f"{rid}: rule text must never be committed here")
    return problems


def classes_digest(doc):
    body = json.dumps(doc.get("rules"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def render(doc):
    rules = doc["rules"]
    entries = []
    for rid in sorted(rules):
        row = rules[rid]
        value = {"cls": row["class"], "on": bool(row["always_on"]),
                 "summary": row["summary"], "when": row["when"]}
        if row.get("personal_to"):
            value["personal_to"] = row["personal_to"]
        entries.append(f"  {json.dumps(rid)}: Object.freeze({json.dumps(value, ensure_ascii=False, sort_keys=True)}),")
    lines = [
        "// GENERATED -- do not hand-edit.",
        "// Source: ops/config/rule-classes.v1.json",
        "// Regenerate with: ./.venv/bin/python ops/sync-rule-boot-classes.py",
        "// Drift + budget check (CI): ./.venv/bin/python ops/sync-rule-boot-classes.py --check",
        "//",
        "// The gated rule boot (standing-context detail \"boot\", mcp-server/src/rule-boot.js)",
        "// reads this for each rule's class, always-on flag, <=20-word summary and",
        "// when-it-applies line. Rule statements are NOT here: they come from the store",
        "// at request time. A Worker has no filesystem, so this is a checked-in module.",
        "",
        "export const RULE_BOOT_CLASSES_SOURCE = \"ops/config/rule-classes.v1.json\";",
        f"export const RULE_BOOT_CLASSES_DIGEST = {json.dumps(classes_digest(doc))};",
        f"export const RULE_BOOT_BUDGET_TOKENS = {int(doc.get('budget_tokens', 40000))};",
        f"export const RULE_BOOT_CHARS_PER_TOKEN = {float(doc.get('chars_per_token', 3.6))};",
        "",
        "export const RULE_BOOT_CLASSES = Object.freeze({",
        *entries,
        "});",
        "",
    ]
    return "\n".join(lines)


def estimate(doc, sponsor=None):
    """(total_chars, tokens, always_on rows sorted largest first) for one sponsor's view."""
    rules = doc["rules"]
    total = PREAMBLE_CHARS
    big = []
    for rid in sorted(rules):
        row = rules[rid]
        owner = row.get("personal_to")
        if owner and owner != sponsor:
            continue
        total += len(f"{rid} | {row['class'].upper()} | {row['summary']} | {row['when']}\n")
        if row["always_on"]:
            header = f"### {rid}{' (personal)' if owner else ''}\n"
            total += len(header) + row["chars"] + 2
            big.append((row["chars"], rid))
    big.sort(reverse=True)
    per_token = float(doc.get("chars_per_token", 3.6))
    return total, total / per_token, big


def budget_findings(doc):
    limit = int(doc.get("budget_tokens", 40000))
    sponsors = sorted({r.get("personal_to") for r in doc["rules"].values() if r.get("personal_to")}
                      | set(doc.get("sponsors") or []))
    findings = []
    for sponsor in [None, *sponsors]:
        chars, tokens, big = estimate(doc, sponsor)
        label = sponsor or "unsponsored"
        if tokens > limit:
            largest = ", ".join(f"{rid} ({n} chars)" for n, rid in big[:10])
            findings.append(
                f"RULE BOOT OVER BUDGET for {label}: ~{tokens:,.0f} tokens ({chars:,} chars) "
                f"> {limit:,}. Largest always-on rules: {largest}. Consolidate or retire rules "
                "(amend-rule / retire-rule); never truncate the boot text.")
        else:
            findings.append(f"ok  {label}: ~{tokens:,.0f} of {limit:,} tokens ({chars:,} chars)")
    return findings


def coverage_findings(doc, map_path=MAP_PATH):
    """Every active rule in the enforcement map is classified, in its scope."""
    try:
        with open(map_path, encoding="utf-8") as fh:
            mapping = json.load(fh)
    except (OSError, ValueError) as exc:
        return [f"RULE BOOT COVERAGE: cannot read {os.path.relpath(map_path, REPO)}: {exc}"]
    controls = mapping.get("rule_controls") or {}
    excluded = {k[:8] for k, v in controls.items()
                if isinstance(v, dict) and v.get("rule_surface") in EXCLUDED_SURFACES}
    rules = doc["rules"]
    known = set(doc.get("sponsors") or [])
    findings = []
    for scope, ids in sorted((mapping.get("active_rule_ids") or {}).items()):
        owner = None if scope == "shared" else scope
        if owner and owner not in known:
            findings.append(f"RULE BOOT COVERAGE: partner {owner!r} has active rules but is not in "
                            "`sponsors`, so its scope is not measured against the budget")
        loose = sorted(i[:8] for i in ids if i[:8] not in excluded
                       and (i[:8] not in rules or (rules[i[:8]].get("personal_to") or None) != owner))
        if loose:
            findings.append(f"RULE BOOT COVERAGE: {len(loose)} active {scope} rule(s) are not classified "
                            f"for that scope and would render as class U in full text, outside the budget: "
                            f"{', '.join(loose[:20])}. Classify them in ops/config/rule-classes.v1.json.")
    return findings


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--classes", default=CLASSES_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args(argv)
    doc = load(args.classes)
    problems = validate(doc)
    if problems:
        print("rule-classes.v1.json INVALID:\n  " + "\n  ".join(problems))
        return 1
    rendered = render(doc)
    if not args.check:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"wrote {os.path.relpath(args.out, REPO)} ({len(doc['rules'])} rules)")
        return 0
    rc = 0
    try:
        with open(args.out, encoding="utf-8") as fh:
            current = fh.read()
    except OSError:
        current = None
    if current != rendered:
        print(f"STALE: {os.path.relpath(args.out, REPO)} does not match "
              "ops/config/rule-classes.v1.json. Regenerate: "
              "./.venv/bin/python ops/sync-rule-boot-classes.py")
        rc = 1
    for line in budget_findings(doc) + coverage_findings(doc):
        print(line)
        if line.startswith(("RULE BOOT OVER BUDGET", "RULE BOOT COVERAGE")):
            rc = 1
    if rc == 0:
        print(f"rule-boot classes: module in parity with {len(doc['rules'])} classified rules")
    return rc


if __name__ == "__main__":
    sys.exit(main())

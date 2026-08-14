#!/usr/bin/env python3
"""Account for security tests required by changed high-risk source files.

This is intentionally a candidate gate, not a test runner.  It asks Git for the
range supplied by CI, maps only changed high-risk paths to declarative policy,
and checks supplied evidence names.  It never imports project code, executes a
test command, contacts a network service, or writes a record.
"""

import argparse
import fnmatch
import json
import os
import pathlib
import subprocess
import sys
import time

MAX_FILES = 100
MAX_DIFF_LINES = 5000
TIME_LIMIT_SECONDS = 20
BASE_ENVIRONMENT = ("CARR_CI_BASE_SHA", "CI_BASE_SHA", "GITHUB_BASE_SHA", "GITHUB_EVENT_BEFORE")
DEADLINE = None


class Refusal(Exception):
    """An unknown or unsafe candidate state that must block promotion."""


def git(repo, args):
    """Run the only external program this gate permits: local git."""
    remaining = DEADLINE - time.monotonic()
    if remaining <= 0:
        raise Refusal("inspection exceeded the 20s time cap")
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True,
            timeout=remaining, check=False,
        )
    except subprocess.TimeoutExpired:
        raise Refusal("git inspection exceeded the 20s time cap")
    except OSError as exc:
        raise Refusal(f"cannot inspect local git repository: {exc}")


def choose_base(explicit):
    if explicit:
        return explicit, "--base"
    for name in BASE_ENVIRONMENT:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None, None


def trusted_base(repo, value):
    resolved = git(repo, ["rev-parse", "--verify", "--end-of-options", f"{value}^{{commit}}"])
    if resolved.returncode:
        return None, "base ref does not resolve to a local commit"
    commit = resolved.stdout.strip()
    shared = git(repo, ["merge-base", commit, "HEAD"])
    if shared.returncode or not shared.stdout.strip():
        return None, "base ref shares no trusted ancestry with HEAD"
    return shared.stdout.strip(), None


def read_policy(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Refusal(f"cannot read policy: {exc}")
    except json.JSONDecodeError as exc:
        raise Refusal(f"policy is not valid JSON: {exc.msg}")

    if data.get("version") != 1:
        raise Refusal("policy version must be 1")
    high_risk = data.get("high_risk_patterns")
    rules = data.get("rules")
    if not isinstance(high_risk, list) or not high_risk or not all(isinstance(x, str) and x for x in high_risk):
        raise Refusal("policy needs a non-empty high_risk_patterns string list")
    if not isinstance(rules, list):
        raise Refusal("policy rules must be a list")

    parsed = []
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise Refusal("each policy rule must be an object")
        ident = rule.get("id")
        patterns = rule.get("patterns")
        tests = rule.get("required_tests")
        if (not isinstance(ident, str) or not ident or ident in seen or
                not isinstance(patterns, list) or not patterns or
                not all(isinstance(x, str) and x for x in patterns) or
                not isinstance(tests, list) or not tests or
                not all(isinstance(x, str) and x for x in tests)):
            raise Refusal("each policy rule needs unique id plus non-empty patterns and required_tests")
        seen.add(ident)
        parsed.append((ident, patterns, tests))
    return high_risk, parsed


def changed_files(repo, base):
    result = git(repo, ["diff", "--name-status", "-z", "--find-renames",
                        "--diff-filter=ACMRTD", f"{base}..HEAD"])
    if result.returncode:
        raise Refusal("could not list changed files for base..HEAD")
    fields = [field for field in result.stdout.split("\0") if field]
    names = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        width = 2 if status.startswith(("R", "C")) else 1
        if index + width > len(fields):
            raise Refusal("malformed changed-file status stream")
        names.extend(fields[index:index + width])
        index += width
    names = sorted(set(names))
    if len(names) > MAX_FILES:
        raise Refusal(f"changed-file limit exceeded: {len(names)} > {MAX_FILES}")

    stats = git(repo, ["diff", "--numstat", "--find-renames", "--diff-filter=ACMRTD", f"{base}..HEAD"])
    if stats.returncode:
        raise Refusal("could not count changed diff lines")
    lines = 0
    for row in stats.stdout.splitlines():
        fields = row.split("\t", 2)
        if len(fields) < 2 or not fields[0].isdigit() or not fields[1].isdigit():
            raise Refusal("binary or malformed diff cannot be safely inspected")
        lines += int(fields[0]) + int(fields[1])
    if lines > MAX_DIFF_LINES:
        raise Refusal(f"diff-line limit exceeded: {lines} > {MAX_DIFF_LINES}")
    return names, lines


def matches(path, patterns):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def account(paths, high_risk, rules, evidence):
    candidates = [path for path in paths if matches(path, high_risk)]
    if not candidates:
        return None, []

    missing_mapping = []
    required = set()
    matched_rules = []
    for path in candidates:
        for ident, patterns, tests in rules:
            if matches(path, patterns):
                matched_rules.append((path, ident))
                required.update(tests)
        if not any(p == path for p, _ in matched_rules):
            missing_mapping.append(path)
    if missing_mapping:
        raise Refusal("high-risk path has no mapped tests: " + ", ".join(sorted(missing_mapping)))
    if not required:
        raise Refusal("high-risk candidate has no mapped tests")
    absent = sorted(required - evidence)
    if absent:
        raise Refusal("required test evidence missing: " + ", ".join(absent))
    return candidates, matched_rules


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="trusted local ancestor commit supplied by CI")
    parser.add_argument("--evidence", action="append", default=[], metavar="TEST_ID",
                        help="a completed required-test identifier; repeatable")
    parser.add_argument("--strict", action="store_true", help="refuse when no usable base is supplied")
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent,
                        help=argparse.SUPPRESS)
    parser.add_argument("--policy", type=pathlib.Path, help="policy JSON (default: <repo>/ops/config/semantic-security-policy.json)")
    return parser.parse_args(argv)


def main(argv=None):
    global DEADLINE
    DEADLINE = time.monotonic() + TIME_LIMIT_SECONDS
    args = parse_args(argv or sys.argv[1:])
    repo = args.repo.resolve()
    policy = (args.policy or repo / "ops" / "config" / "semantic-security-policy.json").resolve()
    try:
        base_value, source = choose_base(args.base)
        if not base_value:
            if args.strict:
                raise Refusal("no base supplied (--base or CI base-SHA environment)")
            print("SEMANTIC_SECURITY: NO_CANDIDATES — no base supplied outside strict mode")
            return 0
        base, problem = trusted_base(repo, base_value)
        if problem:
            if args.strict:
                raise Refusal(f"untrusted base from {source}: {problem}")
            print(f"SEMANTIC_SECURITY: NO_CANDIDATES — unusable base from {source} outside strict mode")
            return 0
        high_risk, rules = read_policy(policy)
        paths, diff_lines = changed_files(repo, base)
        candidates, mapping = account(paths, high_risk, rules, set(args.evidence))
        if candidates is None:
            print(f"SEMANTIC_SECURITY: NO_CANDIDATES — {len(paths)} changed file(s), {diff_lines} diff line(s)")
        else:
            rules_text = ", ".join(sorted({ident for _, ident in mapping}))
            print("SEMANTIC_SECURITY: ACCOUNTED — "
                  f"{len(candidates)} candidate file(s), rules: {rules_text}, "
                  f"evidence: {', '.join(sorted(set(args.evidence)))}")
        return 0
    except Refusal as exc:
        print(f"SEMANTIC_SECURITY: REFUSED — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

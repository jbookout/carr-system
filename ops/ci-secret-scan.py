#!/usr/bin/env python3
"""ci-secret-scan.py — refuse a tree that carries a live credential.

WHY THIS IS HAND-WRITTEN RATHER THAN gitleaks/trufflehog. The scan has to run
identically in two places: a GitHub runner and Joe's Mac at push time. rule
a8c55a47 says a manual path and an automated path doing the same job must be the
same code, and the cheapest way to guarantee that is a scanner with no install
step, no network fetch and no version to drift. It is stdlib only, so it cannot
be the reason a check is skipped.

WHAT IT LOOKS FOR. High-confidence, shaped credentials only — the ones whose
format is distinctive enough that a match is almost never a false positive. It
deliberately does NOT do generic entropy scoring. An entropy scanner in a repo
holding 947 markdown files, migration SQL and doctrine prose produces noise, and
a noisy gate is one people learn to bypass, which is worse than no gate.

WHAT IT DOES NOT PROTECT AGAINST. It reads the working tree, not git history. A
credential already committed and later removed is still in the history and this
will not find it. That is a separate, one-time job (history rewrite), not a
per-push check, and saying so here is the point: a gate that quietly implies
coverage it does not have is how loop #276 happened in another form.

ALLOWLISTING, because a repo about credentials must be able to write about them.
Two mechanisms, both deliberately narrow:
  * an inline `ci-secret-scan: allow` marker on the same line, which forces the
    author to make the claim at the exact site rather than in a distant config;
  * ops/config/secret-scan-allow.json, for whole paths that are documentation or
    fixtures by construction.
Neither can allowlist a real private-key block — that pattern is unconditional,
because there is no legitimate reason for one to be in this tree at all.

Usage:
    ops/ci-secret-scan.py                 # scan tracked files, exit 1 on a hit
    ops/ci-secret-scan.py --list-patterns # what it looks for, and why
"""

import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ALLOW_FILE = REPO / "ops" / "config" / "secret-scan-allow.json"
INLINE_ALLOW = "ci-secret-scan: allow"

# (name, regex, why it is high-confidence, allowlistable)
PATTERNS = [
    ("private-key-block",
     re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),
     "A private key block has no legitimate reason to be in this tree.",
     False),
    ("aws-access-key",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
     "AWS access key IDs have a fixed, unmistakable prefix and length.",
     True),
    ("google-api-key",
     re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
     "Google API keys are fixed-length with an AIza prefix.",
     True),
    ("google-oauth-client-secret",
     re.compile(r"\bGOCSPX-[0-9A-Za-z\-_]{28}\b"),
     "The Worker's Google Sign-In secret has this exact shape.",
     True),
    ("github-token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b"),
     "GitHub personal/OAuth/app tokens are prefixed and fixed-length.",
     True),
    ("slack-token",
     re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
     "Slack tokens carry an xox<type>- prefix.",
     True),
    ("anthropic-key",
     re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"),
     "Anthropic API keys are prefixed sk-ant-.",
     True),
    ("openai-key",
     re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"),
     "OpenAI API keys are prefixed sk- or sk-proj-.",
     True),
    ("dsn-with-password",
     re.compile(r"\bpostgres(?:ql)?://[^\s:/@]+:[^\s@]{6,}@"),
     "A Postgres DSN carrying an inline password is the Neon credential shape.",
     True),
]

# Binary and vendored trees are skipped by extension/path rather than sniffed:
# a scanner that reads a 1.6GB model file to find no secrets in it is a scanner
# nobody leaves enabled.
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".xlsx", ".docx", ".pptx",
    ".zip", ".gz", ".tgz", ".bin", ".ico", ".woff", ".woff2", ".ttf",
    ".mp3", ".mp4", ".wav", ".m4a", ".webp", ".heic",
}
SKIP_PATH_PARTS = {"node_modules", ".venv", "vendor", ".git"}


def tracked_files():
    """Only files git tracks. An untracked scratch file is not shipping."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO,
        capture_output=True, text=True, check=True,
    ).stdout
    for name in out.split("\0"):
        if not name:
            continue
        path = REPO / name
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if SKIP_PATH_PARTS & set(pathlib.PurePath(name).parts):
            continue
        yield name, path


def load_allowed_paths():
    if not ALLOW_FILE.exists():
        return set()
    try:
        data = json.loads(ALLOW_FILE.read_text())
    except json.JSONDecodeError as exc:
        print(f"secret-scan: {ALLOW_FILE} is not valid JSON — {exc}", file=sys.stderr)
        sys.exit(2)
    return set(data.get("paths", []))


def scan():
    allowed_paths = load_allowed_paths()
    findings = []

    for name, path in tracked_files():
        if name in allowed_paths:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # not text; nothing a shaped-credential regex can match

        for lineno, line in enumerate(text.splitlines(), 1):
            for pname, rx, _why, allowlistable in PATTERNS:
                m = rx.search(line)
                if not m:
                    continue
                if allowlistable and INLINE_ALLOW in line:
                    continue
                # Never print the value. The finding names the location and the
                # shape; a scanner that echoes the credential into a CI log has
                # published it to a second place.
                findings.append((name, lineno, pname, len(m.group(0))))

    return findings


def main():
    if "--list-patterns" in sys.argv:
        print("ci-secret-scan patterns:\n")
        for pname, _rx, why, allowlistable in PATTERNS:
            flag = "" if allowlistable else "  [NOT allowlistable]"
            print(f"  {pname}{flag}\n      {why}")
        print(f"\nInline allow marker: {INLINE_ALLOW!r}")
        print(f"Path allowlist:      {ALLOW_FILE.relative_to(REPO)}")
        return 0

    findings = scan()
    if not findings:
        print("secret scan: clean")
        return 0

    print(f"secret scan: {len(findings)} finding(s)\n", file=sys.stderr)
    for name, lineno, pname, length in findings:
        print(f"  {name}:{lineno}  {pname}  ({length} chars, value not printed)",
              file=sys.stderr)
    print(
        "\nIf a finding is documentation or a fixture, mark it at the site with"
        f"\n  {INLINE_ALLOW}"
        f"\non the same line, or add the path to {ALLOW_FILE.relative_to(REPO)}."
        "\nIf it is a real credential: rotate it first, then remove it. Removing"
        "\nit from the working tree does NOT remove it from git history.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

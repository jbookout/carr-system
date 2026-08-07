#!/usr/bin/env python3
"""guard-unattended.py — the PreToolUse deny gate (idea-bank #32, second job).

WHY THIS EXISTS. Everything else that constrains a session in this system is
compliance-dependent: doctrine, taught rules, the compiled-rules recitation, the
writing-lint gate. All of them work only if the session chooses to obey. On
2026-08-02 an audit found ten claims relayed as verified that were not, which is
what compliance-dependent control looks like when it fails. A hook is the only
mechanism here the model cannot reason its way around, because the harness runs
it, not the model.

WHAT IT BLOCKS. Five classes that are never correct for a session to do on its
own, unattended or not:
  1. Destructive filesystem writes outside the sanctioned scratch zones.
  2. History-rewriting or force git operations.
  3. Reads of private key material.
  4. Network sends to hosts outside the known set (the exfiltration guard).
  5. Destructive SQL (DROP / TRUNCATE / unqualified DELETE or UPDATE).

WHAT IT DELIBERATELY DOES NOT BLOCK. Ordinary database WRITES. Step 1 of the
nightly chain is `cadence_engine.py --apply`, which writes by design. A naive
"no writes when unattended" rule would kill the chain at 2am. The distinction
this gate draws is destructive-or-exfiltrating, never write-versus-read.

THE NIGHTLY CHAIN IS EXPLICITLY SAFE. Its command string is
`cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo "direct script exit=$?"`
and it carries a persisted permission approval that must stay byte-identical.
It matches no pattern below. It is also allowlisted by exact string as a second
belt, so a future pattern cannot accidentally catch it.

KNOWN LIMIT, STATED HONESTLY. This sees the command the SESSION issues, not what
a script does internally. `nightly.sh` runs git, pg_dump and R2 uploads inside
itself and this gate never sees them. That is the correct scope: the threat model
is a session taking a destructive action, not the reviewed scripts in this repo.

FAILS OPEN, ON PURPOSE. Any internal error allows the call. A gate that wedges
the 2am chain costs more than the marginal safety of failing closed, on a
single-operator machine that is not a hostile environment. Every allow-on-error
is logged so a silently-degraded gate is still discoverable.

DISABLE FAST: remove the hooks block from settings.json, or `chmod -x` this file
(a non-executable hook errors, and this gate fails open).
"""

import json
from datetime import datetime, timezone
import os
import re
import sys

LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")

# The nightly chain, verbatim. Belt and braces: it matches nothing below, and it
# is also allowlisted so it can never be caught by a pattern added later.
ALLOW_EXACT = {
    'cd ~/carr-system && ./bin/nightly.sh >/dev/null 2>&1; echo "direct script exit=$?"',
    "cd ~/carr-system && ./run.sh health",
}

# Paths a session may freely destroy things inside.
SAFE_ZONES = (
    "/private/tmp/", "/tmp/", "/var/folders/",
    "carr-system/out/", "carr-system/_to_delete/", "_to_delete/",
    "scratchpad", "Graph.tmp",
)

# Hosts this system legitimately talks to.
KNOWN_HOSTS = (
    # api.doctorcre.com is the SAME Worker as api.practicecre.com — both are custom
    # domains on carr-mcp. It became the PRIMARY name on Joe's 2026-08-01 domain
    # ruling, which reached wrangler.toml and the Worker routes but never reached
    # this list, so calls to the primary domain were blocked until 2026-08-03.
    # Microsoft identity + Graph: the carr.us mailbox is Microsoft 365, and the
    # draft transport talks to these two and nothing else on Microsoft's side.
    "login.microsoftonline.com", "graph.microsoft.com", "outlook.office365.com",
    "api.practicecre.com", "api.doctorcre.com", "api.anthropic.com", "console.neon.tech",
    "neon.tech", "cloudflareapi.com", "cloudflare.com", "r2.cloudflarestorage.com",
    "googleapis.com", "github.com", "api.github.com", "hc-ping.com",
    "npiregistry.cms.hhs.gov", "download.cms.gov",
    # raw.githubusercontent.com: loop #163 named its absence as the gap forcing
    # the gh-api workaround for plain changelog reads. Added 2026-08-06 with the
    # WebFetch widening.
    "raw.githubusercontent.com",
    # Research-read hosts, added 2026-08-06 night on Joe's direct order to read
    # the architecture-research shortlist ("read each one 1 at a time") — the
    # first legitimate tuning of the widened WebFetch gate, hours after it
    # shipped. All are read-only documentation/paper hosts.
    "arxiv.org", "anthropic.com", "humanlayer.dev", "mem0.ai",
    "langchain.com", "emergentmind.com",
    # huggingface.co: whisper.cpp model downloads for the dictation rig
    # (ggml-large-v3-turbo). Added 2026-08-07 on Joe's explicit go in the
    # dictation-rig build session; read-only model fetches into ~/.cache.
    "huggingface.co",
)

# ── render-write protection over Bash (2026-08-06, Joe: "Fix both now") ──────
# The record-home gate denies Edit/Write on generated renders, but an ordinary
# shell redirect walked around it: the #214 audit proved `echo >> open-loops.md`
# ALLOWED while Edit on the same file was DENIED. This closes the second door.
# The protected-path list is record-home-gate's own (parsed live from
# exporters/targets.py) — one list, two doors. BEST-EFFORT PARSER, stated
# plainly: it catches the ordinary write shapes the audit demonstrated
# (>, >>, tee, cp/mv/rsync targets, sed -i, truncate, python open(...,'w')).
# It does not chase adversarial obfuscation; the gate degrades open on its own
# errors like the rest of this file.

_VAULT_SPELLINGS = (
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/",
    "/Users/booko/My Drive/CARR AI/",
)

_render_paths_cache = None


def _protected_abs_paths():
    """Absolute protected render paths under BOTH vault spellings, from
    record-home-gate's generated_paths(). Cached per invocation; [] on any
    error (fail open, logged by caller)."""
    global _render_paths_cache
    if _render_paths_cache is not None:
        return _render_paths_cache
    try:
        import importlib.util
        gate_py = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "record-home-gate.py")
        spec = importlib.util.spec_from_file_location("_rhg", gate_py)
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        exact, dirs = g.generated_paths()
        paths = []
        for rel in list(exact):
            for v in _VAULT_SPELLINGS:
                paths.append(v + rel)
        _render_paths_cache = (paths, [v + d.rstrip("/") + "/" for d in dirs for v in _VAULT_SPELLINGS])
    except Exception:
        _render_paths_cache = ([], [])
    return _render_paths_cache


# sed -i: any protected path inside a `sed -i` clause IS the in-place write
# target, so the pattern accepts anything between -i and the path short of a
# clause separator (macOS sed carries a backup-suffix arg the first version
# of this regex missed).
_WRITE_BEFORE_CTX = re.compile(
    r"(>>?|\btee(\s+-a)?|\bsed\s+-i[^|;&]*|\btruncate\b[^|;&]*)\s*[\"']?$")


def render_write_target(cmd):
    """Reason string when the command writes onto a protected render, else None."""
    exact, gen_dirs = _protected_abs_paths()
    if not exact and not gen_dirs:
        return None
    hits = [p for p in exact if p in cmd]
    hits += [d for d in gen_dirs if d in cmd]
    if not hits:
        return None
    for p in hits:
        idx = 0
        while True:
            idx = cmd.find(p, idx)
            if idx < 0:
                break
            before = cmd[max(0, idx - 60):idx]
            if _WRITE_BEFORE_CTX.search(before):
                return (f"write onto a generated render via shell ({os.path.basename(p.rstrip('/'))}) — "
                        f"renders are written by the exporter only; use the record verb instead "
                        f"(blocked by the CARR guard, second door of record-home-gate)")
            idx += len(p)
    # cp/mv/rsync: protected path as the DESTINATION (last path argument of the clause)
    for clause in re.split(r"[;&|]", cmd):
        toks = clause.strip().split()
        if not toks:
            continue
        if toks[0] in ("cp", "mv", "rsync"):
            tail = clause.strip()
            for p in hits:
                if tail.rstrip("\"' ").endswith(p.rstrip("/")):
                    return (f"{toks[0]} onto a generated render ({os.path.basename(p.rstrip('/'))}) — "
                            f"renders are written by the exporter only; use the record verb instead "
                            f"(blocked by the CARR guard, second door of record-home-gate)")
    # python inline write onto a protected path
    for p in hits:
        if re.search(r"open\(\s*[\"']" + re.escape(p) + r"[\"']\s*,\s*[\"'][wa]", cmd):
            return (f"python write onto a generated render ({os.path.basename(p)}) — "
                    f"use the record verb instead (blocked by the CARR guard)")
    return None

RULES = [
    # 1. destructive filesystem
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+", re.I), "recursive/forced delete"),
    (re.compile(r"\b(shred|srm)\b", re.I), "secure delete"),
    (re.compile(r">\s*/dev/(sd|disk|rdisk)", re.I), "raw device write"),
    (re.compile(r"\bmkfs\b|\bdiskutil\s+erase", re.I), "filesystem format"),
    # 2. git history rewrite
    (re.compile(r"git\s+push\b[^|;&]*(--force\b|--force-with-lease\b|\s-f\b)", re.I), "force push"),
    (re.compile(r"git\s+reset\s+--hard\b", re.I), "hard reset"),
    (re.compile(r"git\s+(filter-repo|filter-branch)\b", re.I), "history rewrite"),
    (re.compile(r"git\s+clean\s+-[a-zA-Z]*f", re.I), "forced clean"),
    # 3. private key material
    #
    # `\.age\b` REMOVED 2026-08-07, on Joe's ruling: "loosen the gate so the work
    # can actually be done." The extension matched every ENCRYPTED BACKUP in
    # backups/, which is ciphertext, not key material — without the identity it
    # is noise. What it actually blocked was ordinary custodial work on the
    # backups: listing them, comparing sizes, quarantining a corrupt one into
    # _to_delete/, naming an R2 object key. It fired twice on 2026-08-07 against
    # correct operations during the recovery from a corrupt backup — including
    # the attempt to move that corrupt backup out of restore range. A guard that
    # blocks the cleanup of the incident it was watching is costing more than it
    # protects.
    #
    # The private key itself is still covered, by name and by path: it lives at
    # ~/.config/carr/age-key.txt, which `age-key` matches, with `identity.txt`
    # behind it for the --identity override.
    (re.compile(r"(id_rsa|id_ed25519|\.ssh/|age-key|identity\.txt|\.pem\b|\.p12\b)", re.I),
     "private key material"),
    # 3b. THE REAL EXPOSURE THE EXTENSION WAS STANDING IN FOR, now named directly.
    # Handling a .age file is harmless; DECRYPTING one spills production PII in
    # cleartext. So the dangerous verb is blocked instead of the file type, which
    # is both tighter and less obstructive than what it replaces.
    # bin/restore-rehearse.sh is unaffected: it decrypts INSIDE the script, into a
    # mode-700 mktemp outside the repo, and shreds it on every exit path including
    # a Ctrl-C. The gate sees `./run.sh restore-rehearse ...`, never the age call.
    # LIMIT, stated rather than pretended away: this matches the flags real usage
    # spells out (-d, --decrypt, -i, --identity). It does not chase bundled short
    # flags or obfuscation, same as every other rule here.
    (re.compile(r"\bage\s+[^|;&]*(--?d\b|--decrypt\b|--?i\b|--identity\b)", re.I),
     "raw decrypt of an encrypted backup — production data in cleartext. Use "
     "`./run.sh restore-rehearse --verify-only [--date YYYYMMDD]`, which handles the "
     "key and shreds the plaintext"),
    # 4. destructive SQL
    (re.compile(r"\bdrop\s+(table|schema|database|view|index)\b", re.I), "DROP"),
    (re.compile(r"\btruncate\s+(table\s+)?\w", re.I), "TRUNCATE"),
    (re.compile(r"\bdelete\s+from\s+\w+\s*(;|$)", re.I), "unqualified DELETE"),
    (re.compile(r"\bupdate\s+\w+\s+set\b(?![\s\S]*\bwhere\b)", re.I), "unqualified UPDATE"),
]

# Any http(s) URL in a sending context.
SEND_CTX = re.compile(r"\b(curl|wget|http|https)\b", re.I)
URL_RE = re.compile(r"https?://([A-Za-z0-9._-]+)")


def log(msg):
    """Timestamped and self-identifying. Before 2026-08-03 no hook stamped its
    lines, so out/hook-guard.log could not answer "when did this fire" or even
    "which gate wrote this" — 51 lines with test fixtures indistinguishable from
    production denials. A log you cannot read chronologically is an artifact,
    not a check."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        with open(LOG, "a") as fh:
            fh.write(f"{ts} guard-unattended {msg.rstrip()}\n")
    except Exception:
        pass


def in_safe_zone(cmd):
    return any(z in cmd for z in SAFE_ZONES)


def check(cmd):
    """Return a reason string to block, or None to allow."""
    if cmd.strip() in ALLOW_EXACT:
        return None

    for pat, label in RULES:
        if pat.search(cmd):
            # Destructive-fs rules are waived inside the sanctioned scratch zones.
            if label in ("recursive/forced delete", "secure delete") and in_safe_zone(cmd):
                continue
            return f"{label} — blocked by the CARR unattended guard"

    if SEND_CTX.search(cmd):
        for host in URL_RE.findall(cmd):
            if not any(host == k or host.endswith("." + k) for k in KNOWN_HOSTS):
                return (f"network send to an unrecognised host ({host}) — blocked by the "
                        f"CARR unattended guard. Add it to KNOWN_HOSTS if it is legitimate.")

    reason = render_write_target(cmd)
    if reason:
        return reason
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                       # fail OPEN
        log(f"ALLOW(parse-error) {exc}")
        sys.exit(0)

    try:
        tool = payload.get("tool_name") or payload.get("toolName") or ""
        ti = payload.get("tool_input") or payload.get("toolInput") or {}

        # [2026-08-06, loop #163 closed on Joe's "Fix both now"] WebFetch joins
        # the egress allowlist. Before this, `if tool != "Bash": sys.exit(0)`
        # meant WebFetch reached ANY host while the identical curl was blocked —
        # demonstrated live on 2026-08-03. Same KNOWN_HOSTS list, same tuning
        # path (a block names the host to add). WebSearch is deliberately NOT
        # gated: it reaches a search API, not an arbitrary host. Requires the
        # settings matcher to include WebFetch — changed the same sitting.
        if tool == "WebFetch":
            url = (ti.get("url", "") if isinstance(ti, dict) else "") or ""
            m = URL_RE.search(url if url.startswith("http") else f"https://{url}")
            host = m.group(1) if m else ""
            if host and not any(host == k or host.endswith("." + k) for k in KNOWN_HOSTS):
                reason = (f"WebFetch to an unrecognised host ({host}) — blocked by the CARR "
                          f"guard (loop #163 widening). Add it to KNOWN_HOSTS if it is legitimate.")
                log(f"DENY {reason} :: {url[:200]}")
                print(reason, file=sys.stderr)
                sys.exit(2)
            sys.exit(0)

        if tool != "Bash":
            sys.exit(0)
        cmd = ti.get("command", "") if isinstance(ti, dict) else ""
        if not cmd:
            sys.exit(0)

        reason = check(cmd)
        if reason:
            log(f"DENY {reason} :: {cmd[:300]}")
            # EXIT 2, NOT JSON, AND THE CHOICE MATTERS. The structured contract
            # (exit 0 + hookSpecificOutput.permissionDecision) is richer, but it
            # requires exit 0 — so on any build that does not parse the JSON, exit
            # 0 reads as ALLOW and the gate fails open silently. Exit 2 blocks on
            # every build and hands stderr back to the session as the reason. For
            # a guard, degrading toward "blocked" beats degrading toward "allowed".
            print(reason, file=sys.stderr)
            sys.exit(2)
        sys.exit(0)
    except Exception as exc:                       # fail OPEN
        log(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()

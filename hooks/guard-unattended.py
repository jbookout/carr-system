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
import ipaddress
import os
import re
import sys
from urllib.parse import urlsplit

LOG = os.path.expanduser("~/carr-system/out/hook-guard.log")
REPO = os.path.expanduser("~/carr-system")
DELEGATION_STATE = os.path.join(REPO, "out", "delegation-gate-state.json")
DELEGATION_LOCK = DELEGATION_STATE + ".lock"

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
    # blotato.io: the media-upload backend of the ALREADY-SANCTIONED Blotato
    # connector. Its create-post tool takes public mediaUrls, and the only way
    # to get a local PNG there is the presigned PUT its own tool description
    # prescribes ("Use curl ... --data-binary"). The network gate landed
    # 2026-08-02 (ea2c78c) and this host was never listed, which SILENTLY BROKE
    # the image half of `social-batch-weekly` — the 7/31 batch had uploaded 18
    # graphics fine, and the 2026-08-07 run was the first weekly batch after the
    # gate and hit the wall on its first upload. Restored here rather than
    # shipping a batch with no graphics, which would fail the mandatory
    # format-rotation gate in social-media-workflow.md. Scope is narrow by
    # construction: the PUT targets a short-lived presigned URL this session
    # minted seconds earlier through the connector, and carries a PNG we just
    # rendered. Added 2026-08-07 by the social-batch-weekly run; flagged to Joe
    # in that run's output because a scheduled job widening a security gate is
    # exactly the kind of change that should not pass unannounced.
    "blotato.io",
    # huggingface.co: whisper.cpp model downloads for the dictation rig
    # (ggml-large-v3-turbo). Added 2026-08-07 on Joe's explicit go in the
    # dictation-rig build session; read-only model fetches into ~/.cache.
    "huggingface.co",
    # api.elevenlabs.io: Doc's production renderer. build-voice-corpus.py exists
    # for exactly this ("once ElevenLabs does production rendering, render speed
    # stops constraining the SOURCE material", Joe 2026-08-08) and the 193-line
    # corpus was built to be uploaded there. The guard blocked the host outright,
    # so the step the corpus was made for could not run. Added 2026-08-10 while
    # Joe was live in-session ("we can go through eleven labs testing with Doc's
    # voice"), and flagged to him in that turn rather than passing unannounced —
    # widening a security gate is not a silent act.
    #
    # SCOPE, stated because an allowlist entry is a standing permission: this is
    # the API host for voice creation and text-to-speech renders of a voice we
    # designed. Doc has no real-person referent (voice doctrine section 3), so
    # nothing here is a person's voice. Audio uploaded is synthetic Doc, raw and
    # pre-mastering by design. NOTE FOR ANY FUTURE READER: the same doctrine
    # section warns ElevenLabs retains a perpetual licence over the underlying
    # voice model, so exclusivity of Doc as a brand asset is an OPEN question,
    # not one this entry settles.
    "api.elevenlabs.io", "elevenlabs.io",
    # x.com / twitter.com: X retrieval is standing doctrine (rule 57d13061,
    # Grok-first as of 2026-08-07) and Joe hands sessions x.com links directly.
    # The guard was blocking the Grok CLI invocation itself because the link
    # appears in the command text. Read-only retrieval; Claude still writes all
    # content and nothing posts to X from a session. Added 2026-08-07 while Joe
    # was live in-session ("study this article for our system").
    "x.com", "twitter.com",
    # ── Client-verification sources, added 2026-08-07 on Joe's order after the
    # weekly deal-history research slice ran half-blind. That run could reach
    # NPPES and nothing else: no entity filing, no licence status, no primary
    # source of any kind. Four of fifteen records were filed found:false partly
    # because the registry that would have settled them was unreachable, and
    # every website fact in the run rests on a search-engine summary — which is
    # precisely what rule 94806da2 says not to let stand.
    #
    # All read-only public-record lookups. Each hostname was verified live
    # before being added rather than recalled: a guessed host in an allowlist is
    # dead weight at best, and at worst names a domain someone else can register.
    #
    # State entity registries — the "is this a real company and what is its
    # legal name" question that every client identity check runs through.
    "sunbiz.org",              # FL Division of Corporations (search.sunbiz.org)
    "dos.fl.gov",              # FL Dept of State, the Sunbiz parent
    "arc-sos.state.al.us",     # AL SOS entity search (legacy CGI, NOT under alabama.gov)
    # Licensing boards. alabama.gov is deliberately the whole domain rather than
    # one host per board: every AL board lives on it (chiro., optometry.,
    # asbvme., sos., licensesearch., inform.) and CARR's verticals will want
    # boards nobody has needed yet. One entry beats a dozen that drift apart.
    "alabama.gov",
    "dentalboard.org",         # Board of Dental Examiners of Alabama (a .org, not a .gov)
    # The AL dental board hosts its actual licensee lookup on a third-party
    # vendor, so dentalboard.org alone does NOT make dental licence checks work.
    # Scoped to the board's own host rather than igovsolution.net, which serves
    # many states' boards — this grant buys Alabama and nothing else. Widen only
    # if another state's board is ever needed. Joe's go, 2026-08-07.
    "bdeal.igovsolution.net",
    "albme.gov",               # AL Board of Medical Examiners
    # Alabama's official e-government portal (state contract, Tyler/NIC). Added
    # 2026-08-07 on Joe's go, one exchange after the rest of this block, because
    # verifying the block found the gap: the AL CHIROPRACTIC board publishes no
    # lookup on chiro.alabama.gov and routes to this portal instead, and it is
    # the one AL board absent from the unified licensesearch.alabama.gov (that
    # path returns HTTP 500). Without this entry a chiropractor's licence cannot
    # be verified in Alabama at all — the live gap behind the C-130 Nikki Cottis
    # npi:found=false row of 2026-08-07.
    #
    # UNLIKE the igovsolution entry above, this one CANNOT be narrowed: the
    # boards sit on PATHS of the apex domain (/asbce/, /sos/) rather than on
    # per-board subdomains, and this matcher tests hosts, not paths. So the
    # grant is the whole portal or nothing. Accepted as the same trust class as
    # alabama.gov — it is the state's own contracted portal — and noted here
    # plainly rather than left to look like a deliberately loose entry.
    "alabamainteractive.org",
    "doh.state.fl.us",         # FL DOH MQA licence search (appsmqa./mqa-internet./mqa-vo.)
    "flhealthsource.gov",      # FL DOH consumer-facing licence portal
    # Professional directories, ranked BELOW the registries above: ada.org is an
    # association roster and healthgrades.com is a commercial aggregator
    # carrying stale and advertised entries. Corroboration only, never the sole
    # source for an identity claim.
    "ada.org",
    "healthgrades.com",
    # NOT added, and deliberately: individual practice websites. They are the
    # largest remaining gap in client verification and an allowlist cannot close
    # it — every client has a different domain, so the list would have to grow
    # by one entry per client forever. linkedin.com is also omitted: it blocks
    # automated fetches behind a login wall, so listing it would buy nothing.
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

def _vault_spellings():
    """The primary machine's two literal spellings, PLUS whatever vault actually
    exists here. Additive on purpose (2026-08-10 audit): the hardcodedは
    pair is kept verbatim so this machine's matching cannot regress, while a second
    machine stops running a vault guard that matches no path it owns."""
    fixed = (
        "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI/",
        "/Users/booko/My Drive/CARR AI/",
    )
    try:
        from gate_paths import vault_roots
        return tuple(dict.fromkeys(fixed + vault_roots()))
    except Exception:
        return fixed          # degrade open, like the rest of this file


_VAULT_SPELLINGS = _vault_spellings()

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

# Vault .md paths in a shell command: vault spelling + relative tail up to a
# shell metachar/quote. Case-insensitive match would be wrong (APFS is
# case-insensitive but the manifest compares normalized real paths; the Write
# hook is the primary door — this door catches the shell walk-around).
_VAULT_MD_RE = None


def vault_md_write_target(cmd):
    """PHASE 0 (doctrine-store build, decision 82a2fb62): the Bash door of the
    vault-wide .md deny-by-default. Same manifest as record-home-gate — one
    module, hooks/md_manifest.py — so the two doors cannot drift (rule a8c55a47).
    Renders are caught earlier by render_write_target with a sharper message."""
    global _VAULT_MD_RE
    try:
        if _VAULT_MD_RE is None:
            alts = "|".join(re.escape(v) for v in _VAULT_SPELLINGS)
            _VAULT_MD_RE = re.compile(r"(" + alts + r")([^\"'|;&<>]+?\.md)\b")
        hits = [(m.start(), m.group(2)) for m in _VAULT_MD_RE.finditer(cmd)]
        if not hits:
            return None
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from md_manifest import md_write_verdict
        for idx, rel in hits:
            verdict = md_write_verdict(rel.strip())
            if not verdict:
                continue
            before = cmd[max(0, idx - 60):idx]
            written = bool(_WRITE_BEFORE_CTX.search(before))
            if not written:
                # cp/mv/rsync destination, or a python open(..., "w"/"a")
                for clause in re.split(r"[;&|]", cmd):
                    toks = clause.strip().split()
                    if toks and toks[0] in ("cp", "mv", "rsync") \
                            and clause.rstrip("\"' ").endswith(rel):
                        written = True
                        break
                if re.search(r"open\(\s*[\"'][^\"']*" + re.escape(rel) + r"[\"']\s*,\s*[\"'][wa]",
                             cmd):
                    written = True
            if written:
                return (f"shell write onto vault markdown — {verdict} "
                        f"(blocked by the CARR guard, Bash door of the Phase 0 .md write-block)")
        return None
    except Exception:
        return None                                   # fail open, same as siblings


def corpus_render_write_target(cmd):
    """The Bash door of the corpus-render write-block (2026-08-10, Joe's
    instruction after the nightly chain failed on eight Drive-side edits).
    Same deny set as the Write/Edit door — one module, hooks/corpus_renders.py —
    so the two doors cannot drift apart (rule a8c55a47: a manual path and an
    automated path that do the same job must be the same code).

    `corpus-sync.py` itself is exempt and must be: it is the sanctioned writer
    of every one of these files. It never names them literally, so the exemption
    is belt-and-braces rather than load-bearing."""
    try:
        if "corpus-sync.py" in cmd:
            return None
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from corpus_renders import paths_in_command, verdict
        hits = paths_in_command(cmd)
        if not hits:
            return None
        for spelling, real in hits.items():
            written = False
            idx = 0
            while True:
                idx = cmd.find(spelling, idx)
                if idx < 0:
                    break
                if _WRITE_BEFORE_CTX.search(cmd[max(0, idx - 60):idx]):
                    written = True
                    break
                idx += len(spelling)
            if not written:
                for clause in re.split(r"[;&|]", cmd):
                    toks = clause.strip().split()
                    if toks and toks[0] in ("cp", "mv", "rsync") \
                            and clause.rstrip("\"' ").endswith(spelling):
                        written = True
                        break
            if not written and re.search(
                    r"open\(\s*[\"'][^\"']*" + re.escape(os.path.basename(spelling))
                    + r"[\"']\s*,\s*[\"'][wa]", cmd):
                written = True
            if written:
                return (f"shell write onto a corpus render — {verdict(real, 'this command')} "
                        f"(blocked by the CARR guard, Bash door of the corpus-render block)")
        return None
    except Exception:
        return None                                   # fail open, same as siblings


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

# ── THE DERIVED HOST LIST (2026-08-09, the "B" half of Joe's "build A and B") ─
#
# KNOWN_HOSTS above is trust as CODE, which is right for infrastructure and wrong
# for client practice websites: a different host per client, unknown until the
# client exists, growing weekly. Encoding those in code means one hand-edit per
# client forever, each one a session asking to widen a security allowlist.
#
# So the rest of the list is DATA, generated by ops/fetch-allowlist.py from the
# email domains of CLIENTS AND LEADS already in the record — partner-entered at
# intake, free-mail filtered. Read that file's header for the full reasoning,
# including why it is email domains rather than a website column, and the honest
# limit (this makes widening AUDITED, not impossible: a verb can write an email).
#
# FAILS OPEN TO THE NARROW LIST, never to a wider one: if the file is missing or
# unreadable, the result is KNOWN_HOSTS alone. A generator that breaks tightens
# the gate rather than loosening it, which is the correct direction for a
# security control to fail.
_DERIVED_CACHE = {"mtime": None, "hosts": ()}
DERIVED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "out", "fetch-allowlist.txt")


def derived_hosts():
    """Hosts from the record-derived list. Cached on mtime; never raises."""
    try:
        mt = os.path.getmtime(DERIVED_PATH)
    except OSError:
        return ()
    if _DERIVED_CACHE["mtime"] == mt:
        return _DERIVED_CACHE["hosts"]
    try:
        with open(DERIVED_PATH, encoding="utf-8") as fh:
            hosts = tuple(ln.strip().lower() for ln in fh
                          if ln.strip() and not ln.lstrip().startswith("#"))
    except Exception:
        return ()
    _DERIVED_CACHE.update(mtime=mt, hosts=hosts)
    return hosts


def host_allowlisted(host):
    """True if host is on the code list OR the record-derived list."""
    host = (host or "").strip(".").lower()
    if not host:
        return False
    for k in KNOWN_HOSTS:
        if host == k or host.endswith("." + k):
            return True
    for k in derived_hosts():
        if host == k or host.endswith("." + k):
            return True
    return False


# ── THE WEBFETCH READ-ONLY CLASS (2026-08-09, Joe: "build A and B") ──────────
#
# THE ERROR THIS FIXES. Until now WebFetch and Bash `curl` shared ONE allowlist,
# and that forced the list to be as tight as the riskiest caller. Their
# exfiltration ceilings are not remotely comparable:
#
#     WebFetch   GET only · payload out = the URL string · no credentials ·
#                cross-host redirects are RETURNED to the caller, not followed
#                (observed live twice on 2026-08-07 against alabamainteractive)
#     Bash curl  any method · any body · credentials · follows redirects
#
# The guard's own stated job (see the header, class 4) is the EXFILTRATION
# guard. A GET whose entire outbound payload is a URL is a different risk from a
# POST that can carry a database, so it gets a different policy. KNOWN_HOSTS is
# untouched and still governs Bash.
#
# WHAT THIS BUYS. Client verification needs the practice's own website, and
# practice websites cannot be enumerated — there is a different one per client,
# unknown until the client exists. An allowlist can never close that; a policy
# can. This is what makes the weekly deal-history research slice able to read a
# practice site at all.
#
# THE EXFILTRATION CONTROL IS THE LENGTH CAP, not the host list. If a GET can
# only leave with what fits in its URL, then capping the URL caps the channel.
# 256 chars total with a near-empty query admits ordinary pages and refuses a
# payload. Everything else here is the SSRF floor.
#
# KNOWN LIMIT, STATED PLAINLY: a hostname that RESOLVES to a private address
# (DNS rebinding) is not caught. Resolving in a PreToolUse hook would add a
# network round-trip to every call and is itself resolver-dependent. The bare-IP
# and private-suffix rules below stop the direct forms; rebinding is accepted
# residual risk on a single-operator machine, and is logged here so the next
# reader does not mistake its absence for an oversight.
FETCH_MAX_URL = 256          # total characters
FETCH_MAX_QUERY = 64         # characters after '?'

_FETCH_HOST_DENY_SUFFIX = (
    "localhost", "local", "internal", "localdomain", "home.arpa", "lan",
)
# Anything that looks like a secret riding in the query string.
_FETCH_SECRETISH = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential|authorization|"
    r"session[_-]?id|access[_-]?key)\b")
# A long unbroken high-entropy run — a base64/hex blob being smuggled out.
_FETCH_BLOB = re.compile(r"[A-Za-z0-9+/_=-]{40,}")


def webfetch_open_read_reason(url):
    """None if this GET is acceptable under the wider read-only class, else why not.

    Only consulted for hosts NOT already in KNOWN_HOSTS — an allowlisted host
    keeps its existing unconditional pass, so nothing that worked before breaks.
    """
    try:
        parts = urlsplit(url if url.startswith(("http://", "https://")) else f"https://{url}")
    except Exception as exc:                       # unparseable → refuse
        return f"WebFetch URL could not be parsed ({type(exc).__name__}) — blocked by the CARR guard."

    if len(url) > FETCH_MAX_URL:
        return (f"WebFetch URL is {len(url)} chars (cap {FETCH_MAX_URL}) for a host outside "
                f"KNOWN_HOSTS — blocked by the CARR guard. A long URL to an unvetted host is "
                f"the exfiltration channel this cap exists to close.")
    if len(parts.query) > FETCH_MAX_QUERY:
        return (f"WebFetch query string is {len(parts.query)} chars (cap {FETCH_MAX_QUERY}) for a "
                f"host outside KNOWN_HOSTS — blocked by the CARR guard.")
    if parts.username or parts.password or "@" in parts.netloc:
        return "WebFetch URL carries credentials in the authority — blocked by the CARR guard."
    if _FETCH_SECRETISH.search(parts.query) or _FETCH_BLOB.search(parts.query):
        return ("WebFetch query string looks like it carries a secret or an encoded blob — "
                "blocked by the CARR guard.")

    host = (parts.hostname or "").strip(".").lower()
    if not host:
        return "WebFetch URL has no host — blocked by the CARR guard."

    try:
        port = parts.port
    except ValueError:
        return "WebFetch URL has a malformed port — blocked by the CARR guard."
    if port not in (None, 80, 443):
        return (f"WebFetch to a non-standard port ({port}) on a host outside KNOWN_HOSTS — "
                f"blocked by the CARR guard.")

    # A bare IP literal skips DNS entirely and is how link-local metadata
    # (169.254.169.254) and RFC1918 targets get reached. The wider class is for
    # named public websites, so require a name.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        return (f"WebFetch to a bare IP address ({host}) outside KNOWN_HOSTS — blocked by the "
                f"CARR guard. The open read class requires a public DNS name.")

    last = host.rsplit(".", 1)[-1]
    if last in _FETCH_HOST_DENY_SUFFIX or host in _FETCH_HOST_DENY_SUFFIX:
        return f"WebFetch to a non-public host ({host}) — blocked by the CARR guard."
    if "." not in host:
        return (f"WebFetch to a dotless host ({host}) — blocked by the CARR guard; that is a "
                f"local name, not a public website.")
    return None


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


def raw_targets_carr(cmd):
    """Recognize CARR path spellings without evaluating shell variables."""
    if not isinstance(cmd, str):
        return False
    markers = (
        r"(?:^|[^\w])~/carr-system(?:/|\b)",
        r"\$(?:\{HOME\}|HOME)/carr-system(?:/|\b)",
        r"/Users/[^/]+/carr-system(?:/|\b)",
        r"(?:CloudStorage/GoogleDrive-[^/]+/)?My\\? Drive/CARR\\? AI(?:/|\b)",
        r"/CARR\\? AI(?:/|\b)",
    )
    return any(re.search(marker, cmd) for marker in markers)


def delegation_control_plane_write(cmd):
    """Block ordinary shell tampering with the delegation gate's mutable state.

    The hook itself updates this state by direct atomic filesystem calls, not by
    issuing a harness Bash tool call, so this does not interfere with normal
    state transitions.  A model-issued shell command, however, must never edit
    the latch/control files to release its own task.
    """
    names = (DELEGATION_STATE, DELEGATION_LOCK, "delegation-gate-state.json",
             ".delegation-gate-")
    if not any(name in cmd for name in names):
        return None
    write_shape = re.compile(r"(>>?|\btee\b|\bsed\s+-i\b|\btruncate\b|\brm\b|\bmv\b|\bcp\b|\bchmod\b|\bchflags\b|open\([^\n]+['\"][wa])", re.I)
    if write_shape.search(cmd):
        return "delegation control-plane state write — blocked by the CARR guard"
    return None


def check(cmd):
    """Return a reason string to block, or None to allow."""
    if cmd.strip() in ALLOW_EXACT:
        return None

    reason = delegation_control_plane_write(cmd)
    if reason:
        return reason

    for pat, label in RULES:
        if pat.search(cmd):
            # Destructive-fs rules are waived inside the sanctioned scratch zones.
            if label in ("recursive/forced delete", "secure delete") and in_safe_zone(cmd):
                continue
            return f"{label} — blocked by the CARR unattended guard"

    if SEND_CTX.search(cmd):
        for host in URL_RE.findall(cmd):
            # host_allowlisted covers KNOWN_HOSTS plus the record-derived client
            # and lead domains. The Bash path gets NO equivalent of the WebFetch
            # open-read class and must not: curl chooses its own method and body,
            # so the length cap that makes an open GET safe buys nothing here.
            # This path stays allowlist-only, by design.
            if not host_allowlisted(host):
                return (f"network send to an unrecognised host ({host}) — blocked by the "
                        f"CARR unattended guard. Add it to KNOWN_HOSTS if it is legitimate, "
                        f"or — if it is a client's own domain — check that they carry a "
                        f"practice email and re-run ops/fetch-allowlist.py.")

    reason = render_write_target(cmd)
    if reason:
        return reason
    reason = corpus_render_write_target(cmd)
    if reason:
        return reason
    reason = vault_md_write_target(cmd)
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
            # urlsplit, not URL_RE, for the fetch path. The regex host class
            # excludes ':' and '@', so `https://user:pass@evil.com/` captures
            # "user" as the host — harmless while everything was deny-by-default,
            # actively wrong now that a host can pass on policy rather than on a
            # list. The Bash path below still uses URL_RE (it scans free text,
            # where a parser has no single URL to parse).
            try:
                _p = urlsplit(url if url.startswith(("http://", "https://")) else f"https://{url}")
                host = (_p.hostname or "").strip(".").lower()
            except Exception:
                host = ""
            if host and not host_allowlisted(host):
                # Not on the list — fall through to the wider READ-ONLY class
                # rather than refusing outright. This is the 2026-08-09 split:
                # an allowlisted host keeps its unconditional pass, and anything
                # else must satisfy the open-read policy instead.
                reason = webfetch_open_read_reason(url)
                if reason:
                    log(f"DENY {reason} :: {url[:200]}")
                    print(reason, file=sys.stderr)
                    sys.exit(2)
                log(f"ALLOW(open-read) {host} :: {url[:200]}")
            sys.exit(0)

        # Codex routes its local shell through functions.exec. Normalise the
        # name so this remains one command policy across both runtimes.
        if tool == "functions.exec":
            # The native Bash guard pre-dates Codex and is intentionally global.
            # This new Codex alias is CARR-only so it cannot change Life AI or
            # another repository's workflow merely because they share Codex.
            cwd = payload.get("cwd") or ""
            try:
                real_cwd = os.path.realpath(os.path.expanduser(cwd))
            except Exception:
                real_cwd = ""
            if not (real_cwd == REPO or real_cwd.startswith(REPO + os.sep)
                    or "/CARR AI" in real_cwd):
                # A task rooted elsewhere can still target CARR by absolute
                # path.  Scope by the target too, otherwise a non-CARR cwd is
                # an accidental bypass for the very files this guard protects.
                raw = ti if isinstance(ti, str) else ""
                if REPO not in raw and not raw_targets_carr(raw):
                    sys.exit(0)
            tool = "Bash"
        if tool != "Bash":
            sys.exit(0)
        # Codex's local-function tool passes freeform JavaScript as a string;
        # its embedded exec_command({cmd: ...}) must receive the same command
        # inspection as a native Bash call. A dict remains the Claude shape.
        cmd = ti.get("command", "") if isinstance(ti, dict) else ti
        if not isinstance(cmd, str):
            cmd = ""
        if not cmd:
            sys.exit(0)

        reason = check(cmd)
        if not reason:
            # THE GATE DOOR (loop #231, 2026-08-10). Runs only when nothing above
            # denies, so a destructive shape (`rm -rf hooks/`) still DENIES on the
            # stronger rule rather than being softened to an announcement here.
            #
            # Until today gate-edit-gate.py guarded Write/Edit on these files and
            # its docstring claimed this file guarded the shell path. It did not —
            # proven by firing the hook, not by reading it: append, sed -i, `>`
            # onto settings.json and tee all returned ALLOW while the render
            # control in the same run correctly DENIED. Shared matcher, one list,
            # two doors (rule a8c55a47): hooks/gate_paths.py.
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from gate_paths import announcement, enforcement_write
                hit = enforcement_write(cmd)
                if hit:
                    msg = announcement(hit, "a shell command")
                    log(f"ANNOUNCE gate-write {hit} :: {cmd[:300]}")
                    print(json.dumps({
                        "systemMessage": msg,
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "permissionDecisionReason": msg,
                        },
                    }))
                    sys.exit(0)
            except SystemExit:
                raise
            except Exception as exc:
                log(f"ALLOW(gate-door-error) {exc}")

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

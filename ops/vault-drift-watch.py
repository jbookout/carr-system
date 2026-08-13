#!/usr/bin/env python3
"""
vault-drift-watch.py — vault-write door detection, v2 (Phase 1, 2026-08-13).

The vault ("/Users/booko/My Drive/CARR AI") accepts writes from more than one
actor, and the client-write door from Codex has been demonstrated open. v1 of
this watch was a plain manifest diff: sha256+mtime per tracked .md file,
compared against the previous run. That catches a hand-edit sitting on disk
between two runs, but it CANNOT catch a generated file being clobbered and then
immediately re-clobbered by the same night's export — the exact failure mode
this v2 build was commissioned to close: a nightly chain that rewrites every
generated file is a nightly chain that erases the evidence of any tamper that
happened to a generated file earlier that same day, before this watch ever saw
it. v2 adds a second, independent baseline that survives the rewrite.

THREE THINGS THIS SCRIPT NOW DOES, run as two separate modes:

  --rebaseline (run LAST, after the nightly export chain finishes)
      Snapshots the POST-EXPORT state of every file the live exporters/targets.py
      TARGETS registry knows about: sha256 always, plus a content copy (so a
      later diff has something to diff against) unless the total registered
      content exceeds a size cap, in which case only .md registry files get
      content-copied and everything else (xlsx, json) is hash-only. Written to
      out/vault-drift-baseline/.

  --check (run FIRST, before anything else in the nightly chain)
      Two independent comparisons:
        1. TAMPER — every file in the last --rebaseline's registry is re-hashed
           now. ANY hash difference (or the file having vanished) since that
           baseline is TAMPER: something touched a generated file outside the
           export that is supposed to be its only writer. This is the check v1
           could not do, because v1 only ever compared manifest-to-manifest and
           every manifest already reflected a post-export state.
        2. UNEXPECTED — same walk-and-classify structural diff v1 did, over
           tracked .md files only: any add/modify/delete of a .md file that is
           neither a registered export target nor one of the 54 files already
           content-hash-checked by the doctrine-mirror drift check (see
           tools/corpus-sync.py / corpus/corpus-set.tsv — that check owns those
           54 files; this watch excludes them so the same drift is never
           reported twice by two different scripts) is UNEXPECTED.
      Every TAMPER and UNEXPECTED finding gets salvaged before this run ends:
      quarantine the current file plus a diff against the last known-good
      content, append a well-formed intake payload to a manifest.jsonl, and
      (only if CARR_DRIFT_INGEST=1 is set — OFF by default) POST that payload
      to the Worker's /ingest socket, so a hand-edit that a nightly export is
      about to overwrite has a real chance of reaching the record layer instead
      of just disappearing. See "SALVAGE-THEN-HEAL" below.

A first --check run with no --rebaseline baseline yet skips the tamper
comparison (nothing to compare against) rather than reporting false tamper; a
first --check run with no previous check-manifest is a SEED for the structural
.md comparison, same semantics as v1. Neither counts as drift.

Exit codes (--check): 0 clean, 2 at least one TAMPER or UNEXPECTED finding,
1 FATAL (bad --root). Exit codes (--rebaseline): 0 on success, 1 FATAL.

SALVAGE-THEN-HEAL. The nightly chain's whole design is: overwrite every
generated file with a fresh export every night. If a human (or Codex) hand-
edited a registry file in between, the next export blows that edit away with
no record it ever existed — unless something captures it FIRST. That is what
--check's salvage step is for: quarantine + diff + a manifest line exist for
every finding whether or not live routing is turned on, so nothing is lost even
with CARR_DRIFT_INGEST unset. Turning that flag on is a separate, visible,
human decision — the capability ships disabled.

Run:
  python3 ops/vault-drift-watch.py --check
  python3 ops/vault-drift-watch.py --rebaseline
Test:
  python3 ops/vault-drift-watch.py --check --root <scratch> --manifest <scratch> \\
      --baseline-dir <scratch> --quarantine-dir <scratch> --salvage-manifest <scratch> \\
      --summary <scratch>
  (never point --root at the real vault from a test)
"""
import argparse
import difflib
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = "/Users/booko/My Drive/CARR AI"
DEFAULT_MANIFEST = REPO_ROOT / "out" / "vault-md-manifest.json"
DEFAULT_BASELINE_DIR = REPO_ROOT / "out" / "vault-drift-baseline"
DEFAULT_QUARANTINE_DIR = REPO_ROOT / "out" / "vault-drift-quarantine"
DEFAULT_SALVAGE_MANIFEST = REPO_ROOT / "out" / "vault-drift-salvage-manifest.jsonl"
DEFAULT_SUMMARY = REPO_ROOT / "out" / "vault-drift-check-summary.json"
CORPUS_SET_FILE = REPO_ROOT / "corpus" / "corpus-set.tsv"

# ~50MB: above this, --rebaseline stops content-copying xlsx/binary registry
# files (hash-only for those) and content-copies .md registry files only.
CONTENT_COPY_CAP_BYTES = 50 * 1024 * 1024

TEXT_DIFF_EXTS = (".md", ".json", ".txt", ".tsv", ".csv")
DIFF_EXCERPT_CHARS = 8000

# Same skip-list convention as pipelines/build-section-index.py's SKIP_DIRS:
# matched by directory NAME anywhere in the tree, exact case, plus dotdirs.
SKIP_DIRS = {"Source Material", "Output", ".obsidian", ".claude", ".git",
             "source-exports", "photos", "Prospects", "_asset_staging", "_to_delete"}

# Also from build-section-index.py (THE ARCHIVE LEAK, 2026-08-03): the exporter
# keeps per-file version snapshots in SUFFIX-named directories
# ("CLAUDE.md.generations", "decision-history.md.generations", 46 of them in
# the vault today, 249 .md files inside them). Those are byte-copies of files
# already covered by their live path, not new content — walking them would
# make every exporter refresh look like a wall of UNEXPECTED adds.
GENERATIONS_SUFFIX = ".generations"

# Belt-and-suspenders per the build brief: these three ride the hourly rules
# refresh and the nightly chain. compiled-rules-shared.md and
# compiled-rules-joe.md are TARGETS entries too (so the live-registry parse
# below already carries them); listed again here so a broken import of
# exporters/targets.py still does not misclassify any of the three as
# UNEXPECTED — it degrades to "everything else is UNEXPECTED", never the
# reverse.
def load_target_paths_by_key_prefix(prefix):
    """The vault paths written by every TARGETS entry whose KEY starts with
    `prefix` — i.e. exactly the files `run.sh export --only <prefix>` writes.

    WHY BY KEY AND NOT BY PATH. bin/refresh-rules.sh first shipped this with a
    hardcoded list of the three compiled-rules render paths, and it was wrong
    within minutes: `--only compiled-rules` also prefix-matches
    compiled-rules-gist-index (CLAUDE.md) and compiled-rules-intro
    (DNA/Network/introduction-rules.md), so the hourly job rewrote five files
    and the rebaseline only re-snapshotted three. The next check reported the
    other two as TAMPER. A hand-kept list beside a registry is the drift this
    whole change exists to remove (rule a8c55a47 — a manual path and an
    automated path that do the same job must be the same code), so the caller
    now names the EXPORT SELECTOR and the registry resolves it. Adding a sixth
    compiled-rules target needs no edit here.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from exporters import targets as _targets  # noqa: PLC0415 (deliberate late import)
        return {rel for key, (rel, _b) in _targets.TARGETS.items()
                if key.startswith(prefix)}, None
    except Exception as exc:  # pragma: no cover - exercised via a real import failure only
        return set(), f"{type(exc).__name__}: {exc}"


ALWAYS_EXPECTED = {
    "DNA/compiled-rules-shared.md",
    "00_Context/compiled-rules-joe.md",
    "DNA/compiled-rules-dell.md",
}

# ---------------------------------------------------------------- ingest wiring
# Same house pattern as bin/notes-sweep-post.sh and bin/pull-gmail-calendar.py:
# one shared credential file, one bearer token per source, never composed into
# a command line or printed. Reused here rather than reinvented.
INGEST_ENVFILE = os.path.expanduser("~/.config/carr/ingest.env")
DEFAULT_INGEST_URL = "https://api.practicecre.com/ingest"
INGEST_TOKEN_VAR = "CARR_INGEST_TOKEN_VAULT_DRIFT"


def load_expected_writers():
    """Registered export target relpaths, parsed from the LIVE exporters/targets.py
    registry (its TARGETS dict) rather than hand-copied, so this can't drift from
    the real registry the way a pasted list would. Returns (expected_set, error);
    error is None on a clean import. On failure this falls back to
    ALWAYS_EXPECTED only — conservative in the safe direction: more UNEXPECTED
    false positives, never a false "expected" on an unverified path. Note this
    registry is NOT .md-only (it also carries the xlsx/json registry targets) —
    both --check's tamper pass and --rebaseline hash every path in it regardless
    of extension.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from exporters import targets as _targets  # noqa: PLC0415 (deliberate late import)
        paths = {rel for rel, _builder in _targets.TARGETS.values()}
        paths |= ALWAYS_EXPECTED
        return paths, None
    except Exception as exc:  # pragma: no cover - exercised via a real import failure only
        return set(ALWAYS_EXPECTED), f"{type(exc).__name__}: {exc}"


def load_corpus_mirror_set():
    """Vault-relative paths already covered by the EXISTING doctrine-mirror
    drift check (tools/corpus-sync.py, driven off corpus/corpus-set.tsv) — that
    script content-hashes these 54 files against its own manifest and prints a
    loud CONFLICT by name on a hand-edit. Excluded here so this watch never
    double-reports the same file two different scripts already watch.
    """
    if not CORPUS_SET_FILE.exists():
        return set()
    paths = set()
    try:
        for line in CORPUS_SET_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if parts and parts[0]:
                paths.add(parts[0])
    except Exception:
        return set()
    return paths


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(root):
    """Return {relpath: {"sha256":..., "mtime":...}} for every tracked .md file."""
    root = Path(root)
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                              if d not in SKIP_DIRS
                              and not d.startswith(".")
                              and not d.endswith(GENERATIONS_SUFFIX))
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            abspath = Path(dirpath) / name
            relpath = str(abspath.relative_to(root))
            try:
                st = abspath.stat()
                digest = sha256_of(abspath)
            except OSError as exc:
                print(f"  ⚠︎ could not read {relpath}: {exc}", file=sys.stderr)
                continue
            files[relpath] = {"sha256": digest, "mtime": st.st_mtime}
    return files


# A THIRD legitimate writer class the first two missed. `expected` covers the
# exporters/targets.py TARGETS registry and `corpus_mirror` covers
# tools/corpus-sync.py, but the vault has a third machine that writes .md into
# it every night: `./run.sh graph-system` (bin/nightly.sh:233) regenerates the
# whole derived Graph-System/ tree. Nothing registers those files, because they
# are not export targets and not corpus mirrors — so every one of them landed in
# the UNEXPECTED pile. Measured cost, run 20260813T201608Z: 97 of that run's 99
# UNEXPECTED findings were Graph-System/ paths. A drift report that is 98% one
# known generator is a report nobody reads, which is how the two REAL findings
# in that same run (two deliberately retired renders) nearly went unnoticed.
# ops/vault-duplicate-sweep.py already skips this directory by name for exactly
# this reason; this is that same knowledge, applied to the second scanner.
# Prefix-matched rather than set-matched on purpose: the tree is regenerated
# wholesale, so its members change nightly and enumerating them would drift.
DERIVED_DIRS = ("Graph-System/",)


def classify(relpath, expected, corpus_mirror):
    if relpath in expected:
        return "expected-writer: exporter"
    if relpath in corpus_mirror:
        return "expected-writer: corpus-mirror (see tools/corpus-sync.py)"
    if relpath.startswith(DERIVED_DIRS):
        return "expected-writer: derived (see ./run.sh graph-system)"
    return "UNEXPECTED"


def write_manifest(manifest_path, root, files, unexpected_count, seed):
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "seed": seed,
        "unexpected_count": unexpected_count,
        "file_count": len(files),
        "files": files,
    }
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(manifest_path)


def load_baseline(baseline_dir):
    manifest_path = Path(baseline_dir) / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except Exception:
        return None


def load_ingest_env():
    if not os.path.exists(INGEST_ENVFILE):
        return {}
    out = {}
    with open(INGEST_ENVFILE) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def read_text_or_none(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, OSError):
        return None


def build_diff(relpath, baseline_path, current_path):
    """Unified diff of baseline content copy vs current file, when both are
    diffable text. Registry binaries (xlsx) and any file this watch has no
    content copy for (non-registry .md — only rebaselined registry files get
    content-copied) fall back to an honest note plus sizes rather than a fake
    diff.
    """
    if not relpath.endswith(TEXT_DIFF_EXTS):
        b_size = os.path.getsize(baseline_path) if baseline_path and os.path.exists(baseline_path) else None
        c_size = os.path.getsize(current_path) if current_path and os.path.exists(current_path) else None
        return (f"# {relpath} is not a diffable text type for this watch (binary/spreadsheet).\n"
                f"# baseline size: {b_size if b_size is not None else 'n/a'} bytes\n"
                f"# current size:  {c_size if c_size is not None else 'n/a (file missing)'} bytes\n")
    baseline_text = read_text_or_none(baseline_path)
    current_text = read_text_or_none(current_path)
    if baseline_text is None and current_text is None:
        return (f"# no content available for {relpath} — no baseline content copy on file "
                f"(only files rebaselined after the size-cap check get one) and the current "
                f"file is also missing or unreadable.\n")
    if baseline_text is None:
        return (f"# no baseline content copy on file for {relpath} (new registry path, or "
                f"baselined as hash-only past the size cap). Showing current content only:\n\n"
                + current_text)
    b_lines = baseline_text.splitlines(keepends=True)
    c_lines = (current_text or "").splitlines(keepends=True)
    diff_lines = difflib.unified_diff(b_lines, c_lines,
                                       fromfile=f"baseline/{relpath}", tofile=f"current/{relpath}")
    text = "".join(diff_lines)
    if not text:
        text = (f"# hash differs but no line-level diff found for {relpath} — likely a "
                f"whitespace/encoding-only change, or the file is gone (see quarantine_file).\n")
    return text


def post_ingest(payload, url, token, dry_run):
    external_id = f"vault-drift:{payload['path']}:{payload.get('current_sha256') or 'deleted'}:{payload['run_id']}"
    body = dict(payload)
    body["external_id"] = external_id
    if dry_run:
        print(f"  [DRY-RUN] would POST {payload['path']} -> {url} "
              f"(external_id={external_id}, token read from {INGEST_ENVFILE}::{INGEST_TOKEN_VAR}, "
              f"never printed)")
        return
    if not token:
        print(f"  ⚠︎ ingest: CARR_DRIFT_INGEST=1 but {INGEST_TOKEN_VAR} is not set in "
              f"{INGEST_ENVFILE} — {payload['path']} stays in the manifest + quarantine only. "
              f"That token is Joe's to create; see DNA/Deal Management/record-layer/"
              f"ingest-tokens-setup.md")
        return
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            # NOT cosmetic — see bin/pull-gmail-calendar.py: Cloudflare's edge
            # refuses urllib's default UA with error code 1010 before the
            # Worker ever sees the request.
            "user-agent": "carr-capture/1 (+carr-system ops/vault-drift-watch.py)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode() or "{}")
            print(f"  ingest POST {payload['path']} -> {resp.status} "
                  f"(id={data.get('id')}, duplicate={data.get('duplicate')})")
    except urllib.error.HTTPError as e:
        print(f"  ⚠︎ ingest POST {payload['path']} -> HTTP {e.code}: "
              f"{e.read().decode(errors='replace')[:200]}")
    except Exception as e:
        print(f"  ⚠︎ ingest POST {payload['path']} -> FAILED: {type(e).__name__}: {e}")


def salvage_finding(finding, root, baseline_content_dir, qroot, run_id, salvage_manifest_path,
                     ingest_enabled, dry_run_ingest, ingest_url, ingest_token):
    """Quarantine the current file + a diff, append the intake payload, and
    (only if enabled) route it live. Runs on EVERY finding — TAMPER and
    UNEXPECTED alike — because both are about to be silently lost: a TAMPERed
    registry file is about to be overwritten by tonight's export, and an
    UNEXPECTED .md file is exactly the kind of hand-edit nobody logged.
    """
    relpath = finding["path"]
    current_path = os.path.join(root, relpath)
    current_exists = os.path.isfile(current_path)

    qfile_rel = None
    if current_exists:
        dest = os.path.join(qroot, relpath)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(current_path, dest)
        qfile_rel = relpath

    baseline_path = (os.path.join(baseline_content_dir, relpath)
                      if baseline_content_dir else None)
    diff_text = build_diff(relpath, baseline_path, current_path if current_exists else None)

    diff_dest = os.path.join(qroot, relpath + ".diff")
    os.makedirs(os.path.dirname(diff_dest), exist_ok=True)
    with open(diff_dest, "w", encoding="utf-8") as f:
        f.write(diff_text)

    truncated = len(diff_text) > DIFF_EXCERPT_CHARS
    excerpt = diff_text[:DIFF_EXCERPT_CHARS]

    payload = {
        "kind": "vault-drift-salvage",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "path": relpath,
        "finding_type": finding["type"],
        "reason": finding["reason"],
        "baseline_sha256": finding.get("baseline_sha256"),
        "current_sha256": finding.get("current_sha256"),
        "quarantine_dir": os.path.relpath(qroot, REPO_ROOT),
        "quarantine_file": qfile_rel,
        "diff_excerpt": excerpt,
        "diff_truncated": truncated,
    }

    with open(salvage_manifest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")

    print(f"  SALVAGED  {finding['type']:<10} {relpath}  (quarantine: "
          f"{qfile_rel or 'file gone, diff only'})")

    if ingest_enabled:
        post_ingest(payload, ingest_url, ingest_token, dry_run=dry_run_ingest)

    return payload


# ---------------------------------------------------------------- --rebaseline

def run_rebaseline(args):
    root = args.root
    baseline_dir = Path(args.baseline_dir)
    content_dir = baseline_dir / "content"

    if not os.path.isdir(root):
        print(f"FATAL: vault root not found or not a directory: {root}", file=sys.stderr)
        return 1

    expected, expected_err = load_expected_writers()
    if expected_err:
        # FAIL CLOSED on a full rebaseline. This used to warn and carry on,
        # writing a baseline of just the 3 hardcoded compiled-rules paths —
        # which REPLACES the full one, silently deleting the tamper baseline for
        # every registered export target. It happened for real: the baseline
        # found on disk today was stamped 2026-08-13T18:21:01Z with file_count
        # 3, because someone ran `python3 ops/vault-drift-watch.py --rebaseline`
        # instead of nightly.sh's `./.venv/bin/python`, psycopg was missing, and
        # the tool degraded quietly. From then until the next nightly, tamper
        # detection covered 3 files instead of the whole registry, and nothing
        # in the artifact said so — the warning went to stderr and the run still
        # exited 0. A baseline that silently covers less than it claims is worse
        # than a missing one, because --check reports "OK" off it.
        #
        # --only is exempt: it MERGES, so it can only ever add or refresh the
        # paths it names, and ALWAYS_EXPECTED is hardcoded rather than imported.
        # --only-target falls through to its own resolver below, which fails
        # closed with a message naming the selector the caller actually passed.
        if not args.only and not args.only_target:
            print(f"FATAL: could not load exporters/targets.py's TARGETS registry "
                  f"({expected_err}). Refusing to write a full baseline covering only the "
                  f"{len(ALWAYS_EXPECTED)} hardcoded compiled-rules paths — that would drop "
                  f"tamper coverage for every export target. Run it the way bin/nightly.sh "
                  f"does: ./.venv/bin/python ops/vault-drift-watch.py --rebaseline",
                  file=sys.stderr)
            return 1
        print(f"⚠︎ could not load the TARGETS registry ({expected_err}) — proceeding "
              f"because --only merges and its paths are hardcoded.", file=sys.stderr)

    # --only: a PARTIAL rebaseline, merged into whatever is already on file.
    #
    # WHY THIS EXISTS. --rebaseline is documented to run LAST, after the nightly
    # export chain, and it re-snapshots everything at once. That is correct for
    # every writer that runs on the nightly beat — but bin/refresh-rules.sh
    # re-renders the three compiled-rules files HOURLY, 7:00-20:00, so a rule
    # taught at noon reaches the binding surface the same day. Those two beats
    # do not agree, and the tamper pass believed the slower one: on run
    # 20260813T201608Z all three compiled-rules files reported TAMPER
    # ("rewritten outside the nightly export") against an 18:04Z baseline they
    # had legitimately moved past at 20:00Z. It is not an occasional race. The
    # render carries an `Exported: <timestamp>` line, so the bytes change on
    # EVERY hourly pass even when no rule changed — DNA/compiled-rules-dell.md's
    # diff in that run was that one line and nothing else. Three guaranteed
    # false TAMPER findings a day, on the three files whose integrity matters
    # most, is worse than no check at all: it teaches the reader to skip them.
    #
    # WHY MERGE RATHER THAN A FULL RE-SNAPSHOT ON THE HOURLY BEAT. Running the
    # whole --rebaseline hourly would silently bless any OTHER registered file
    # that had been rewritten outside its export — which is the exact tampering
    # this tool exists to catch. --only keeps the blast radius to the paths the
    # caller names and leaves every other baseline entry exactly as it was.
    only_prefixes = None
    if args.only_target:
        resolved, resolve_err = load_target_paths_by_key_prefix(args.only_target)
        if resolve_err:
            print(f"FATAL: --only-target needs exporters/targets.py's TARGETS registry to "
                  f"resolve '{args.only_target}', and it could not be loaded "
                  f"({resolve_err}). Run it the way bin/nightly.sh does: "
                  f"./.venv/bin/python ops/vault-drift-watch.py --rebaseline …",
                  file=sys.stderr)
            return 1
        if not resolved:
            print(f"FATAL: --only-target {args.only_target} matched no export target key. "
                  f"Nothing to rebaseline — refusing rather than writing an empty slice.",
                  file=sys.stderr)
            return 1
        only_prefixes = tuple(sorted(resolved))
        expected = {p for p in expected if p in resolved} | resolved
    elif args.only:
        only_prefixes = tuple(p.strip() for p in args.only.split(",") if p.strip())
        if not only_prefixes:
            print("FATAL: --only was given but parsed to no prefixes", file=sys.stderr)
            return 1
        expected = {p for p in expected if p.startswith(only_prefixes)}
        if not expected:
            print(f"FATAL: --only {args.only} matched no registered path — refusing to "
                  f"write a baseline that would drop every entry.", file=sys.stderr)
            return 1

    print(f"vault drift watch --rebaseline — {datetime.now(timezone.utc).isoformat()}")
    print(f"root: {root}")
    if only_prefixes:
        print(f"--only {', '.join(only_prefixes)} — partial rebaseline, merging into "
              f"the existing baseline")
    print(f"registered file(s): {len(expected)}")

    sizes = {}
    total_bytes = 0
    missing = []
    for relpath in sorted(expected):
        full = os.path.join(root, relpath)
        if os.path.isfile(full):
            sz = os.path.getsize(full)
            sizes[relpath] = sz
            total_bytes += sz
        else:
            missing.append(relpath)

    print(f"total registered content: {total_bytes:,} bytes (~{total_bytes / 1024 / 1024:.2f} MB) "
          f"across {len(sizes)} file(s) on disk; {len(missing)} registered path(s) not present")

    content_copy_all = total_bytes <= CONTENT_COPY_CAP_BYTES
    cap_mb = CONTENT_COPY_CAP_BYTES / 1024 / 1024
    if content_copy_all:
        print(f"under the ~{cap_mb:.0f}MB cap — content-copying every registered file "
              f"(enables a real diff for all of them, not just .md)")
    else:
        print(f"over the ~{cap_mb:.0f}MB cap — content-copying .md registry files only; "
              f"xlsx/binary registry files get a hash-only baseline")

    # A partial run inherits the prior manifest's copy decision instead of
    # recomputing it from its own small subset — otherwise rebaselining three
    # .md files would see "well under the cap" and start content-copying every
    # xlsx on the next full run's terms, quietly changing an unrelated policy.
    prior = {}
    manifest_path = baseline_dir / "manifest.json"
    if only_prefixes:
        if manifest_path.exists():
            try:
                prior = json.loads(manifest_path.read_text())
            except (OSError, ValueError) as exc:
                print(f"FATAL: --only needs a readable existing baseline to merge into, "
                      f"but {manifest_path} could not be parsed ({exc}). Run a full "
                      f"--rebaseline first.", file=sys.stderr)
                return 1
            content_copy_all = bool(prior.get("content_copy_all", content_copy_all))
        else:
            print(f"FATAL: --only needs an existing baseline to merge into, but "
                  f"{manifest_path} does not exist. Run a full --rebaseline first.",
                  file=sys.stderr)
            return 1
    elif content_dir.exists():
        # Full rebaseline still starts clean; a partial one must NOT, or it
        # would delete the content copies of every path it is not touching.
        shutil.rmtree(content_dir)

    files = {}
    copied = 0
    hash_only = 0
    for relpath in sorted(expected):
        full = os.path.join(root, relpath)
        if not os.path.isfile(full):
            files[relpath] = {"sha256": None, "size": None, "content_copied": False}
            continue
        sha = sha256_of(full)
        do_copy = content_copy_all or relpath.endswith(".md")
        if do_copy:
            dest = content_dir / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(full, dest)
            copied += 1
        else:
            hash_only += 1
        files[relpath] = {"sha256": sha, "size": sizes[relpath], "content_copied": do_copy}

    baseline_dir.mkdir(parents=True, exist_ok=True)
    touched = len(files)
    if only_prefixes:
        # Merge: every path this run did not name keeps the entry it already
        # had. The counts below are recomputed over the MERGED set so the
        # manifest keeps describing the whole baseline, not just this slice.
        merged = dict(prior.get("files") or {})
        merged.update(files)
        files = merged
        copied = sum(1 for e in files.values() if e.get("content_copied"))
        hash_only = sum(1 for e in files.values()
                        if e.get("sha256") is not None and not e.get("content_copied"))
        missing = [p for p, e in files.items() if e.get("sha256") is None]
        total_bytes = sum(e.get("size") or 0 for e in files.values())

    manifest = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "total_bytes": total_bytes,
        "content_copy_cap_bytes": CONTENT_COPY_CAP_BYTES,
        "content_copy_all": content_copy_all,
        "file_count": len(files),
        "content_copied_count": copied,
        "hash_only_count": hash_only,
        "missing_count": len(missing),
        "files": files,
    }
    if only_prefixes:
        # Provenance, so a later reader can tell a merged baseline from a full
        # one without diffing timestamps.
        manifest["partial_rebaseline"] = {
            "prefixes": list(only_prefixes),
            "paths_touched": touched,
            "prior_generated_at": prior.get("generated_at"),
        }
    tmp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    tmp.replace(manifest_path)

    print(f"\nbaseline written: {manifest_path}")
    if only_prefixes:
        print(f"  {touched} path(s) re-snapshotted, "
              f"{len(files) - touched} carried forward untouched")
    print(f"  {copied} content-copied, {hash_only} hash-only, {len(missing)} missing")
    return 0


# ---------------------------------------------------------------- --check

def run_check(args):
    root = args.root
    manifest_path = Path(args.manifest)
    baseline_dir = Path(args.baseline_dir)
    baseline_content_dir = baseline_dir / "content"
    quarantine_root = Path(args.quarantine_dir)
    salvage_manifest_path = Path(args.salvage_manifest)

    if not os.path.isdir(root):
        print(f"FATAL: vault root not found or not a directory: {root}", file=sys.stderr)
        return 1

    run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z")

    expected, expected_err = load_expected_writers()
    if expected_err:
        # FAIL CLOSED, for the same reason --rebaseline does. This used to warn
        # and continue, and its own warning stated the consequence: "Everything
        # else will read UNEXPECTED until this import is fixed." That is not a
        # degraded check, it is a wrong one. `expected` IS the classifier, so a
        # check without the registry reclassifies every legitimate export target
        # as UNEXPECTED and emits dozens of false findings -- each of which
        # quarantines a copy, writes a salvage row, and, because bin/nightly.sh
        # runs this step with CARR_DRIFT_INGEST=1, POSTs to the ingest endpoint.
        # The run then exits 2 (findings), which reads as "drift detected"
        # rather than "the scanner was blind", and the only thing distinguishing
        # them is one stderr line nobody reads in a nightly log.
        #
        # This is the same defect the --rebaseline side already had and the same
        # one that produced the 3-path baseline on 2026-08-13T18:21Z. Fixing one
        # caller and leaving the other is how a class survives its own fix.
        print(f"FATAL: could not load exporters/targets.py's TARGETS registry "
              f"({expected_err}). Refusing to run a check whose classifier knows only "
              f"{len(ALWAYS_EXPECTED)} of the registered paths — every export target "
              f"would be reported as UNEXPECTED, quarantined and ingested. Run it the "
              f"way bin/nightly.sh does: ./.venv/bin/python ops/vault-drift-watch.py "
              f"--check", file=sys.stderr)
        return 1

    corpus_mirror = load_corpus_mirror_set()

    print(f"vault drift watch --check — {datetime.now(timezone.utc).isoformat()}")
    print(f"root: {root}")
    print(f"run id: {run_id}")

    # ---- Pillar 1: registry tamper check against the last --rebaseline ----
    baseline = load_baseline(baseline_dir)
    tamper_findings = []
    if baseline is None:
        print("\nregistry tamper check: NO BASELINE — run --rebaseline once (nightly's LAST "
              "step, after exports) to seed post-export hashes. Skipping the tamper comparison "
              "this run; there is nothing to compare against yet.")
    else:
        bfiles = baseline.get("files", {})
        baseline_age_h = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(baseline["generated_at"])).total_seconds() / 3600
        print(f"\nregistry tamper check: baseline {baseline_age_h:.1f}h old, "
              f"{len(bfiles)} registered file(s)")
        for relpath in sorted(bfiles):
            binfo = bfiles[relpath]
            if binfo.get("sha256") is None:
                continue  # wasn't on disk at rebaseline time either — nothing to compare
            full = os.path.join(root, relpath)
            if not os.path.isfile(full):
                tamper_findings.append({
                    "path": relpath, "type": "tamper",
                    "reason": "generated file deleted since last rebaseline",
                    "baseline_sha256": binfo["sha256"], "current_sha256": None,
                })
                continue
            cur_sha = sha256_of(full)
            if cur_sha != binfo["sha256"]:
                tamper_findings.append({
                    "path": relpath, "type": "tamper",
                    "reason": "hash changed since last rebaseline (rewritten outside "
                              "the nightly export)",
                    "baseline_sha256": binfo["sha256"], "current_sha256": cur_sha,
                })
        if tamper_findings:
            print(f"  {len(tamper_findings)} TAMPERED file(s):")
            for tf in tamper_findings:
                print(f"    TAMPER  {tf['path']}  ({tf['reason']})")
        else:
            print("  no tamper detected — every registered file matches its post-export "
                  "baseline hash")
        new_since_baseline = sorted(p for p in expected if p not in bfiles
                                     and os.path.isfile(os.path.join(root, p)))
        if new_since_baseline:
            print(f"  {len(new_since_baseline)} registered file(s) not yet in any baseline "
                  f"(will be captured at the next --rebaseline, not tamper): "
                  + ", ".join(new_since_baseline))

    # ---- structural .md pass: add/modify/delete vs the previous --check manifest ----
    new = scan(root)
    seeding = not manifest_path.exists()
    old = {}
    if not seeding:
        try:
            old_data = json.loads(manifest_path.read_text())
            old = old_data.get("files", {})
        except Exception as exc:
            print(f"⚠︎ could not read previous check manifest at {manifest_path} "
                  f"({exc}) — treating this run as a reseed rather than guessing at "
                  f"drift against unreadable state.", file=sys.stderr)
            seeding = True
            old = {}

    print(f"\ntracked .md files: {len(new)}")

    unexpected_entries = []
    if seeding:
        print(f"\nSEED RUN for the .md structural manifest — no previous check manifest at "
              f"{manifest_path}. All {len(new)} file(s) report ADDED; this is expected seed "
              f"behavior, not drift.")
        for p in sorted(new):
            print(f"  ADDED  {classify(p, expected, corpus_mirror):<55} {p}")
    else:
        added = sorted(p for p in new if p not in old)
        deleted = sorted(p for p in old if p not in new)
        modified = sorted(p for p in new if p in old and new[p]["sha256"] != old[p]["sha256"])

        def report_group(label, paths):
            if not paths:
                print(f"\n{label}: none")
                return
            print(f"\n{label}: {len(paths)}")
            for p in paths:
                tag = classify(p, expected, corpus_mirror)
                print(f"  {tag:<55} {p}")
                if tag == "UNEXPECTED":
                    unexpected_entries.append((label, p))

        report_group("ADDED", added)
        report_group("MODIFIED", modified)
        report_group("DELETED", deleted)

    write_manifest(manifest_path, root, new, unexpected_count=len(unexpected_entries), seed=seeding)

    unexpected_findings = []
    for label, p in unexpected_entries:
        unexpected_findings.append({
            "path": p, "type": "unexpected",
            "reason": f"{label.lower()} — non-registry, non-corpus-mirror .md file",
            "baseline_sha256": old.get(p, {}).get("sha256"),
            "current_sha256": new.get(p, {}).get("sha256"),
        })

    if unexpected_findings:
        print(f"\n{'=' * 78}\nUNEXPECTED VAULT MARKDOWN CHANGE(S) DETECTED".center(78))
        print("=" * 78)
    findings = tamper_findings + unexpected_findings

    # ---- Pillar 3: salvage-then-heal ----
    ingest_enabled = os.environ.get("CARR_DRIFT_INGEST") == "1"
    ingest_env = load_ingest_env()
    ingest_url = ingest_env.get("CARR_INGEST_URL", DEFAULT_INGEST_URL)
    ingest_token = ingest_env.get(INGEST_TOKEN_VAR, "")

    if findings:
        qroot = os.path.join(str(quarantine_root), run_id)
        os.makedirs(qroot, exist_ok=True)
        print(f"\n{'=' * 78}")
        print(f"SALVAGE-THEN-HEAL — {len(findings)} finding(s), quarantining to {qroot}")
        print("=" * 78)
        for f_ in findings:
            salvage_finding(f_, root,
                             str(baseline_content_dir) if baseline is not None else None,
                             qroot, run_id, str(salvage_manifest_path),
                             ingest_enabled, args.dry_run_ingest, ingest_url, ingest_token)
        print(f"\nsalvage manifest: {salvage_manifest_path}")
        print(f"quarantine dir:   {qroot}")
    else:
        print("\nno findings — nothing to salvage")

    if not ingest_enabled:
        print(f"\ningest: disabled by default (set CARR_DRIFT_INGEST=1 to enable live routing "
              f"to {ingest_url}) — manifest + quarantine + loud health flag only")
    elif args.dry_run_ingest:
        print("\ningest: CARR_DRIFT_INGEST=1 and --dry-run-ingest set — payloads built and "
              "printed, no network call made")

    # ---- summary for tools/health-check.py ----
    summary = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "tamper_count": len(tamper_findings),
        "unexpected_count": len(unexpected_findings),
        "total_findings": len(findings),
        "ingest_enabled": ingest_enabled,
        "baseline_present": baseline is not None,
    }
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = summary_path.with_suffix(summary_path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, indent=2, sort_keys=True))
    tmp.replace(summary_path)

    if findings:
        print(f"\n{len(tamper_findings)} TAMPER + {len(unexpected_findings)} UNEXPECTED — exit 2")
        return 2
    print("\nclean — 0 tamper, 0 unexpected")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_VAULT,
                     help="Vault root to scan (default: the real CARR AI vault). "
                          "Override for tests — never point this at the real vault.")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                     help="Structural .md check-to-check manifest "
                          "(default: out/vault-md-manifest.json)")
    ap.add_argument("--baseline-dir", default=str(DEFAULT_BASELINE_DIR),
                     help="--rebaseline snapshot dir (default: out/vault-drift-baseline)")
    ap.add_argument("--quarantine-dir", default=str(DEFAULT_QUARANTINE_DIR),
                     help="Per-run quarantine root (default: out/vault-drift-quarantine)")
    ap.add_argument("--salvage-manifest", default=str(DEFAULT_SALVAGE_MANIFEST),
                     help="Append-only salvage intake log "
                          "(default: out/vault-drift-salvage-manifest.jsonl)")
    ap.add_argument("--summary", default=str(DEFAULT_SUMMARY),
                     help="Machine-readable last-check summary for health-check.py "
                          "(default: out/vault-drift-check-summary.json)")
    ap.add_argument("--check", action="store_true",
                     help="Tamper + structural drift check (run FIRST, before exports)")
    ap.add_argument("--rebaseline", action="store_true",
                     help="Snapshot the post-export registry state (run LAST, after exports)")
    ap.add_argument("--only-target", default=None, metavar="EXPORT_KEY_PREFIX",
                     help="With --rebaseline: re-snapshot exactly the paths written by "
                          "`run.sh export --only <EXPORT_KEY_PREFIX>`, resolved through "
                          "exporters/targets.py so the two selections cannot drift apart. "
                          "Preferred over --only for anything that shadows an export.")
    ap.add_argument("--only", default=None, metavar="PREFIX[,PREFIX...]",
                     help="With --rebaseline: re-snapshot ONLY registered paths starting "
                          "with one of these comma-separated prefixes, MERGING them into "
                          "the existing baseline and leaving every other path's baseline "
                          "untouched. For a writer that runs on its own beat between "
                          "nightly chains (bin/refresh-rules.sh). Without it, --rebaseline "
                          "re-snapshots the whole registry as before.")
    ap.add_argument("--dry-run-ingest", action="store_true",
                     help="With --check and CARR_DRIFT_INGEST=1: build and print payloads, "
                          "never actually POST. No effect when CARR_DRIFT_INGEST is unset.")
    ap.add_argument("--run-id", default=None,
                     help="Override the run id used for the quarantine folder name and "
                          "salvage payloads (default: current UTC timestamp). Tests only.")
    args = ap.parse_args(argv)

    if (args.only or args.only_target) and args.check:
        print("FATAL: --only/--only-target apply to --rebaseline only; --check always "
              "scans the whole registry.", file=sys.stderr)
        return 1
    if args.only and args.only_target:
        print("FATAL: --only and --only-target are two ways to name the same slice — "
              "pass one. Prefer --only-target when the slice shadows an export.",
              file=sys.stderr)
        return 1
    if args.check and args.rebaseline:
        print("FATAL: --check and --rebaseline are mutually exclusive — run them as two "
              "separate steps (--check first, --rebaseline last).", file=sys.stderr)
        return 1
    if not args.check and not args.rebaseline:
        args.check = True
        print("(no mode flag given — defaulting to --check; pass --rebaseline explicitly "
              "to snapshot the post-export baseline)")

    if args.rebaseline:
        return run_rebaseline(args)
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())

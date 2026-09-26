#!/usr/bin/env python3
"""ops/session-trace-archive.py -- nightly local archive of agent session
transcripts, so old sessions are no longer lost.

WHY THIS EXISTS. Claude Code keeps session transcripts at
~/.claude/projects/<project-dir>/*.jsonl, and Codex keeps its sessions under
~/.codex/sessions if that directory exists. Both clients prune old sessions,
and for this project only 8 transcripts survived, the oldest from Sept 23.
That is raw agent-trace history -- worth keeping for a later curator and
search slice -- and there was no durable copy anywhere. This job makes one.

DATA-CLASS BOUNDARY. This script copies files byte-for-byte. It never reads,
parses, prints, or summarises transcript CONTENT -- it only ever looks at a
path, a size, an mtime, and (to fill the manifest) a sha256 digest computed by
streaming the file through hashlib without ever holding or printing its text.

WHERE THE ARCHIVE LIVES. ~/carr-local/session-trace-archive/, outside this
repo -- the same convention vendor-patches/README.md documents for local,
machine-specific material that should not be committed. Layout:

    ~/carr-local/session-trace-archive/
        claude/<project-dir>/<file>.jsonl
        codex/<same relative path under ~/.codex/sessions>
        manifest.jsonl        <- one line per file ever archived

COPY RULE. A file is copied only when it is new (no archive copy exists yet)
or has grown (its current size is larger than the archive copy's size). An
unchanged file -- same size as its archive copy -- is skipped, cheaply, without
touching its bytes or reading a manifest. A file whose size somehow shrank
versus its archive copy is left alone too: the archive already holds at least
as much as the source does, and this job never truncates or rewrites downward
what it already preserved.

THE 10-MINUTE QUIET WINDOW. A session file can still be open and being
written by a live client. Copying mid-write risks archiving a torn line, and
re-checking it on the very next nightly run is free, so any file whose mtime
is under 10 minutes old is skipped for this run and picked up once it goes
quiet.

PERMISSIONS. The archive is meant to be readable by nobody but the owning
user: every directory this script creates is chmod 0700 and every file it
writes (the copy, and the manifest) is chmod 0600.

FAILURE CONTRACT. Exit nonzero only for a real I/O failure (a copy that
raised, a chmod that raised, a manifest write that raised). A missing
~/.codex/sessions directory is an expected, supported state on a Claude-only
machine and is not a failure. Nothing this script does ever deletes or
rewrites an existing archived file's bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import TypedDict


class ArchiveSummary(TypedDict):
    copied: int
    skipped_unchanged: int
    skipped_recent: int
    skipped_shrunk: int
    errors: list[str]

DIR_MODE = 0o700
FILE_MODE = 0o600
QUIET_WINDOW_SECONDS = 10 * 60
COPY_CHUNK = 1024 * 1024

DEFAULT_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
DEFAULT_ARCHIVE_ROOT = Path.home() / "carr-local" / "session-trace-archive"


class ArchiveError(RuntimeError):
    """A real I/O failure while archiving -- distinct from "nothing to do"."""


def _ensure_dir(path: Path) -> None:
    """mkdir -p with 0700 on every directory this call creates.

    An already-existing directory is left with whatever mode it already has
    (never loosened, never a failure) -- only directories THIS call creates
    are forced to DIR_MODE, same shape as os.makedirs but without silently
    tightening a directory some other tool made on purpose.
    """
    if path.is_dir():
        return
    parent = path.parent
    if parent != path:
        _ensure_dir(parent)
    try:
        path.mkdir(mode=DIR_MODE)
    except FileExistsError:
        return
    except OSError as exc:
        raise ArchiveError(f"mkdir failed for {path}: {exc}") from exc
    try:
        os.chmod(path, DIR_MODE)
    except OSError as exc:
        raise ArchiveError(f"chmod failed for {path}: {exc}") from exc


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(COPY_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise ArchiveError(f"hashing failed for {path}: {exc}") from exc
    return digest.hexdigest()


def _copy_bytes(src: Path, dest: Path) -> None:
    """Copy src -> dest byte-for-byte, then chmod dest to FILE_MODE.

    Writes to a sibling temp file first and renames into place, so a crash
    mid-copy never leaves a half-written file sitting at the final path --
    the manifest and any later size comparison only ever see a complete copy
    or no copy at all.
    """
    tmp = dest.with_name(dest.name + f".partial-{os.getpid()}")
    try:
        with open(src, "rb") as rf, open(tmp, "wb") as wf:
            while True:
                chunk = rf.read(COPY_CHUNK)
                if not chunk:
                    break
                wf.write(chunk)
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise ArchiveError(f"copy failed {src} -> {dest}: {exc}") from exc


def _iter_source_files(root: Path, pattern: str = "*.jsonl"):
    """Yield every matching file under root, deterministically ordered."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob(pattern)):
        if path.is_file():
            yield path


def _archive_one(
    src: Path,
    dest: Path,
    manifest_fh,
    *,
    now: float,
    source_label: str,
) -> str:
    """Archive a single source file if it qualifies. Returns a status string:
    "copied", "skipped_unchanged", "skipped_recent", or "skipped_shrunk"."""
    try:
        st = src.stat()
    except OSError as exc:
        raise ArchiveError(f"stat failed for {src}: {exc}") from exc

    if (now - st.st_mtime) < QUIET_WINDOW_SECONDS:
        return "skipped_recent"

    dest_exists = dest.exists()
    if dest_exists:
        dest_size = dest.stat().st_size
        if st.st_size == dest_size:
            return "skipped_unchanged"
        if st.st_size < dest_size:
            # The archive already holds at least as much as the source does.
            # Never modify or truncate what is already archived.
            return "skipped_shrunk"

    _ensure_dir(dest.parent)
    _copy_bytes(src, dest)

    digest = _sha256_of(dest)
    size = dest.stat().st_size
    manifest_fh.write(json.dumps({
        "source": source_label,
        "path": str(src),
        "archived_path": str(dest),
        "size": size,
        "sha256": digest,
        "archived_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
    }) + "\n")
    return "copied"


def run_archive(
    *,
    claude_projects_dir: Path,
    codex_sessions_dir: Path,
    archive_root: Path,
    now: float | None = None,
) -> ArchiveSummary:
    """Archive every qualifying Claude and Codex session file once.

    Returns a summary dict {"copied": n, "skipped_unchanged": n,
    "skipped_recent": n, "skipped_shrunk": n, "errors": [...]}. Raises
    ArchiveError only for a failure that stopped the whole run (opening the
    manifest, creating the archive root); per-file failures are collected in
    "errors" and the run continues, since one unreadable transcript should
    not cost the rest of the night's archiving.
    """
    now = time.time() if now is None else now
    _ensure_dir(archive_root)
    manifest_path = archive_root / "manifest.jsonl"
    manifest_is_new = not manifest_path.exists()

    # A plain counts dict rather than ArchiveSummary itself: the status
    # string _archive_one returns is not a literal mypy can match against a
    # TypedDict key, so counting happens here and the typed summary is
    # assembled once, below, from named fields.
    counts = {
        "copied": 0,
        "skipped_unchanged": 0,
        "skipped_recent": 0,
        "skipped_shrunk": 0,
    }
    errors: list[str] = []

    try:
        manifest_fh = open(manifest_path, "a", encoding="utf-8")
    except OSError as exc:
        raise ArchiveError(f"could not open manifest {manifest_path}: {exc}") from exc

    try:
        if manifest_is_new:
            os.chmod(manifest_path, FILE_MODE)

        jobs = []
        if claude_projects_dir.is_dir():
            for src in _iter_source_files(claude_projects_dir, "*.jsonl"):
                rel = src.relative_to(claude_projects_dir)
                dest = archive_root / "claude" / rel
                jobs.append((src, dest, "claude"))
        # A missing ~/.codex/sessions directory is an expected, supported
        # state -- Claude-only machines have no ~/.codex at all -- so this is
        # not gated on any prior existence check beyond the is_dir() below.
        if codex_sessions_dir.is_dir():
            for src in _iter_source_files(codex_sessions_dir, "*"):
                rel = src.relative_to(codex_sessions_dir)
                dest = archive_root / "codex" / rel
                jobs.append((src, dest, "codex"))

        for src, dest, label in jobs:
            try:
                status = _archive_one(
                    src, dest, manifest_fh, now=now, source_label=label,
                )
                counts[status] += 1
            except ArchiveError as exc:
                errors.append(str(exc))
    finally:
        manifest_fh.close()
        try:
            os.chmod(manifest_path, FILE_MODE)
        except OSError:
            pass

    return {
        "copied": counts["copied"],
        "skipped_unchanged": counts["skipped_unchanged"],
        "skipped_recent": counts["skipped_recent"],
        "skipped_shrunk": counts["skipped_shrunk"],
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-projects-dir", type=Path,
                         default=DEFAULT_CLAUDE_PROJECTS_DIR)
    parser.add_argument("--codex-sessions-dir", type=Path,
                         default=DEFAULT_CODEX_SESSIONS_DIR)
    parser.add_argument("--archive-root", type=Path,
                         default=DEFAULT_ARCHIVE_ROOT)
    args = parser.parse_args(argv)

    try:
        summary = run_archive(
            claude_projects_dir=args.claude_projects_dir,
            codex_sessions_dir=args.codex_sessions_dir,
            archive_root=args.archive_root,
        )
    except ArchiveError as exc:
        print(f"session-trace-archive: FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "session-trace-archive: copied={copied} unchanged={skipped_unchanged} "
        "recent={skipped_recent} shrunk={skipped_shrunk} errors={n_errors}".format(
            n_errors=len(summary["errors"]), **summary
        )
    )
    if summary["errors"]:
        for err in summary["errors"]:
            print(f"session-trace-archive: error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

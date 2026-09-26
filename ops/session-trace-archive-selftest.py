#!/usr/bin/env python3
"""ops/session-trace-archive-selftest.py -- selftest for
ops/session-trace-archive.py, entirely against synthetic temp directories.

Never touches ~/.claude, ~/.codex, or ~/carr-local: every case builds its own
tmp "claude projects", "codex sessions" and "archive root" and points
run_archive() at those, the same way the real launchd job points it at the
real ones via --claude-projects-dir / --codex-sessions-dir / --archive-root.

Each check is a plain function that returns True/False and prints one PASS/
FAIL line; main() also runs three MUTANTS -- deliberately broken variants of
the copy logic -- and asserts each one is caught by at least one check here,
so this suite is proven to fail on real regressions and not just pass by
construction.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "session_trace_archive",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "session-trace-archive.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
sta = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sta)


def _mkdirs():
    root = Path(tempfile.mkdtemp(prefix="session-trace-archive-selftest."))
    claude = root / "claude-projects"
    codex = root / "codex-sessions"
    archive = root / "archive"
    claude.mkdir()
    codex.mkdir()
    return root, claude, codex, archive


def _write(path: Path, content: bytes, mtime: float | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _mode_bits(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# Individual checks. Each returns (ok: bool, detail: str).
# ─────────────────────────────────────────────────────────────────────────

def check_new_file_copied(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    old_mtime = time.time() - 3600  # well outside the 10-minute quiet window
    src = claude / "proj-a" / "session1.jsonl"
    _write(src, b'{"line": 1}\n', mtime=old_mtime)

    summary = run_archive(
        claude_projects_dir=claude, codex_sessions_dir=codex,
        archive_root=archive, now=time.time(),
    )
    dest = archive / "claude" / "proj-a" / "session1.jsonl"
    ok = (
        summary["copied"] == 1
        and dest.exists()
        and dest.read_bytes() == src.read_bytes()
    )
    shutil.rmtree(root, ignore_errors=True)
    return ok, f"summary={summary}"


def check_unchanged_file_skipped(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    old_mtime = time.time() - 3600
    src = claude / "proj-a" / "session1.jsonl"
    _write(src, b'{"line": 1}\n', mtime=old_mtime)

    run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                archive_root=archive, now=time.time())
    dest = archive / "claude" / "proj-a" / "session1.jsonl"
    dest_bytes_before = dest.read_bytes()

    # Second run, nothing changed on the source side.
    summary2 = run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                            archive_root=archive, now=time.time())
    ok = (
        summary2["copied"] == 0
        and summary2["skipped_unchanged"] == 1
        and dest.read_bytes() == dest_bytes_before
    )
    shutil.rmtree(root, ignore_errors=True)
    return ok, f"summary2={summary2}"


def check_grown_file_recopied(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    old_mtime = time.time() - 3600
    src = claude / "proj-a" / "session1.jsonl"
    _write(src, b'{"line": 1}\n', mtime=old_mtime)

    run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                archive_root=archive, now=time.time())

    # Grow the source file, still outside the quiet window.
    _write(src, b'{"line": 1}\n{"line": 2}\n', mtime=old_mtime)
    summary2 = run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                            archive_root=archive, now=time.time())

    dest = archive / "claude" / "proj-a" / "session1.jsonl"
    ok = (
        summary2["copied"] == 1
        and dest.read_bytes() == src.read_bytes()
    )
    shutil.rmtree(root, ignore_errors=True)
    return ok, f"summary2={summary2}"


def check_recent_file_skipped(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    now = time.time()
    src = claude / "proj-a" / "session1.jsonl"
    _write(src, b'{"line": 1}\n', mtime=now - 60)  # 1 minute old: inside window

    summary = run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                           archive_root=archive, now=now)
    dest = archive / "claude" / "proj-a" / "session1.jsonl"
    ok = summary["copied"] == 0 and summary["skipped_recent"] == 1 and not dest.exists()
    shutil.rmtree(root, ignore_errors=True)
    return ok, f"summary={summary}"


def check_archive_never_deletes(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    old_mtime = time.time() - 3600
    src1 = claude / "proj-a" / "session1.jsonl"
    src2 = claude / "proj-b" / "session2.jsonl"
    _write(src1, b'{"line": 1}\n', mtime=old_mtime)
    _write(src2, b'{"line": 1}\n', mtime=old_mtime)

    run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                archive_root=archive, now=time.time())

    # Remove src1 from the source entirely; the archive copy must survive.
    src1.unlink()
    run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                archive_root=archive, now=time.time())

    dest1 = archive / "claude" / "proj-a" / "session1.jsonl"
    dest2 = archive / "claude" / "proj-b" / "session2.jsonl"
    ok = dest1.exists() and dest2.exists()
    shutil.rmtree(root, ignore_errors=True)
    return ok, f"dest1_exists={dest1.exists()} dest2_exists={dest2.exists()}"


def check_permissions(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    old_mtime = time.time() - 3600
    src = claude / "proj-a" / "session1.jsonl"
    _write(src, b'{"line": 1}\n', mtime=old_mtime)

    run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                archive_root=archive, now=time.time())

    dest = archive / "claude" / "proj-a" / "session1.jsonl"
    manifest = archive / "manifest.jsonl"
    dirs_ok = all(
        _mode_bits(p) == sta.DIR_MODE
        for p in (archive, archive / "claude", archive / "claude" / "proj-a")
    )
    files_ok = _mode_bits(dest) == sta.FILE_MODE and _mode_bits(manifest) == sta.FILE_MODE
    ok = dirs_ok and files_ok
    detail = (
        f"dirs_ok={dirs_ok} files_ok={files_ok} "
        f"dest_mode={oct(_mode_bits(dest))} manifest_mode={oct(_mode_bits(manifest))}"
    )
    shutil.rmtree(root, ignore_errors=True)
    return ok, detail


def check_manifest_correct(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    old_mtime = time.time() - 3600
    content = b'{"line": 1}\n{"line": 2}\n'
    src = claude / "proj-a" / "session1.jsonl"
    _write(src, content, mtime=old_mtime)

    run_archive(claude_projects_dir=claude, codex_sessions_dir=codex,
                archive_root=archive, now=time.time())

    manifest = archive / "manifest.jsonl"
    lines = [l for l in manifest.read_text().splitlines() if l.strip()]
    ok = len(lines) == 1
    detail = f"n_lines={len(lines)}"
    if ok:
        import json
        row = json.loads(lines[0])
        expected_sha = _sha256(content)
        ok = (
            row.get("size") == len(content)
            and row.get("sha256") == expected_sha
            and row.get("source") == "claude"
            and "archived_at" in row
            and str(src) == row.get("path")
        )
        detail = f"row={row} expected_sha={expected_sha}"
    shutil.rmtree(root, ignore_errors=True)
    return ok, detail


def check_missing_codex_dir_is_fine(run_archive=None) -> tuple[bool, str]:
    run_archive = run_archive or sta.run_archive
    root, claude, codex, archive = _mkdirs()
    # codex dir exists (mkdirs created it) but we point at a sibling path
    # that was never created, simulating a Claude-only machine with no
    # ~/.codex at all.
    missing_codex = root / "codex-sessions-does-not-exist"
    old_mtime = time.time() - 3600
    src = claude / "proj-a" / "session1.jsonl"
    _write(src, b'{"line": 1}\n', mtime=old_mtime)

    try:
        summary = run_archive(claude_projects_dir=claude,
                               codex_sessions_dir=missing_codex,
                               archive_root=archive, now=time.time())
        ok = summary["copied"] == 1 and not summary["errors"]
        detail = f"summary={summary}"
    except Exception as exc:  # noqa: BLE001 - a raise here IS the failure
        ok = False
        detail = f"raised: {exc}"
    shutil.rmtree(root, ignore_errors=True)
    return ok, detail


CHECKS = [
    ("new file copied", check_new_file_copied),
    ("unchanged file skipped", check_unchanged_file_skipped),
    ("grown file re-copied", check_grown_file_recopied),
    ("recent file (<10min) skipped", check_recent_file_skipped),
    ("archive never deletes", check_archive_never_deletes),
    ("permissions are 0600/0700", check_permissions),
    ("manifest correct", check_manifest_correct),
    ("missing codex dir is fine", check_missing_codex_dir_is_fine),
]


def run_checks(run_archive=None) -> tuple[int, int]:
    passed = 0
    failed = 0
    for name, fn in CHECKS:
        ok, detail = fn(run_archive)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name} ({detail})")
        if ok:
            passed += 1
        else:
            failed += 1
    return passed, failed


# ─────────────────────────────────────────────────────────────────────────
# Mutants: deliberately broken run_archive variants. Each must make at
# least one of the checks above fail, proving the suite actually detects
# the defect it is named for rather than passing by construction.
# ─────────────────────────────────────────────────────────────────────────

def _mutant_skip_age_check(**kwargs):
    """Ignores the 10-minute quiet window: copies everything regardless of
    mtime. Should be caught by check_recent_file_skipped."""
    claude_projects_dir = kwargs["claude_projects_dir"]
    codex_sessions_dir = kwargs["codex_sessions_dir"]
    archive_root = kwargs["archive_root"]
    now = kwargs.get("now") or time.time()

    sta._ensure_dir(archive_root)
    manifest_path = archive_root / "manifest.jsonl"
    summary = {"copied": 0, "skipped_unchanged": 0, "skipped_recent": 0,
               "skipped_shrunk": 0, "errors": []}
    with open(manifest_path, "a", encoding="utf-8") as manifest_fh:
        for src in sta._iter_source_files(claude_projects_dir, "*.jsonl"):
            rel = src.relative_to(claude_projects_dir)
            dest = archive_root / "claude" / rel
            dest_exists = dest.exists()
            if dest_exists and src.stat().st_size == dest.stat().st_size:
                summary["skipped_unchanged"] += 1
                continue
            sta._ensure_dir(dest.parent)
            sta._copy_bytes(src, dest)
            summary["copied"] += 1
    return summary


def _mutant_delete_on_sync(**kwargs):
    """Mirrors the source tree exactly: removes an archived file whose
    source disappeared. Should be caught by check_archive_never_deletes."""
    claude_projects_dir = kwargs["claude_projects_dir"]
    codex_sessions_dir = kwargs["codex_sessions_dir"]
    archive_root = kwargs["archive_root"]
    now = kwargs.get("now") or time.time()

    sta._ensure_dir(archive_root)
    manifest_path = archive_root / "manifest.jsonl"
    summary = {"copied": 0, "skipped_unchanged": 0, "skipped_recent": 0,
               "skipped_shrunk": 0, "errors": []}

    live_sources = set()
    with open(manifest_path, "a", encoding="utf-8") as manifest_fh:
        for src in sta._iter_source_files(claude_projects_dir, "*.jsonl"):
            rel = src.relative_to(claude_projects_dir)
            dest = archive_root / "claude" / rel
            live_sources.add(dest)
            if (now - src.stat().st_mtime) < sta.QUIET_WINDOW_SECONDS:
                summary["skipped_recent"] += 1
                continue
            if dest.exists() and src.stat().st_size == dest.stat().st_size:
                summary["skipped_unchanged"] += 1
                continue
            sta._ensure_dir(dest.parent)
            sta._copy_bytes(src, dest)
            summary["copied"] += 1

    claude_archive = archive_root / "claude"
    if claude_archive.is_dir():
        for existing in claude_archive.rglob("*.jsonl"):
            if existing not in live_sources:
                existing.unlink()  # THE DEFECT: pruning the archive.
    return summary


def _mutant_wrong_perms(**kwargs):
    """Leaves default (loose) permissions on the copy instead of 0600/0700.
    Should be caught by check_permissions."""
    claude_projects_dir = kwargs["claude_projects_dir"]
    codex_sessions_dir = kwargs["codex_sessions_dir"]
    archive_root = kwargs["archive_root"]
    now = kwargs.get("now") or time.time()

    archive_root.mkdir(parents=True, exist_ok=True)  # default mode, not 0700
    manifest_path = archive_root / "manifest.jsonl"
    summary = {"copied": 0, "skipped_unchanged": 0, "skipped_recent": 0,
               "skipped_shrunk": 0, "errors": []}
    with open(manifest_path, "a", encoding="utf-8") as manifest_fh:
        for src in sta._iter_source_files(claude_projects_dir, "*.jsonl"):
            rel = src.relative_to(claude_projects_dir)
            dest = archive_root / "claude" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)  # default mode
            if dest.exists() and src.stat().st_size == dest.stat().st_size:
                summary["skipped_unchanged"] += 1
                continue
            shutil.copyfile(src, dest)  # no chmod: THE DEFECT
            summary["copied"] += 1
    return summary


MUTANTS = [
    ("skip age check", _mutant_skip_age_check),
    ("delete-on-sync", _mutant_delete_on_sync),
    ("wrong perms", _mutant_wrong_perms),
]


def run_mutants() -> bool:
    """Each mutant must cause at least one check to FAIL. Returns True if
    every mutant was caught."""
    all_caught = True
    for name, mutant in MUTANTS:
        passed, failed = run_checks(run_archive=mutant)
        caught = failed > 0
        status = "CAUGHT" if caught else "NOT CAUGHT (BAD)"
        print(f"=== mutant '{name}': {status} ({failed} check(s) failed) ===")
        all_caught = all_caught and caught
    return all_caught


def main() -> int:
    print("=== real implementation ===")
    passed, failed = run_checks()
    print(f"real implementation: {passed} passed, {failed} failed")

    print()
    print("=== mutants (each must be caught) ===")
    mutants_ok = run_mutants()

    ok = failed == 0 and mutants_ok
    print()
    print("session-trace-archive-selftest: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

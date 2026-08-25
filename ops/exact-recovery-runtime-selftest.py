#!/usr/bin/env python3
"""Hermetic tests for exact-source runtime provisioning and cleanup."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "bin" / "deploy-worker.sh"
WRANGLER = ROOT / "mcp-server" / "node_modules" / ".bin" / "wrangler"
RUNTIME = ROOT / "mcp-server" / "node_modules"
RECOVERY_ARGS = (
    "--env", "staging",
    "--release-key", "candidate",
    "--recovery-attempt-id", "11111111-2222-4333-8444-555555555555",
    "--recovery-step", "prior",
    "--recovery-prior-release-key", "prior",
    "--staging-receipt-idempotency-key", "22222222-2222-4333-8444-555555555555",
)


def make_source(*, mismatch: bool = False, broken_attachment: bool = False) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
    holder = tempfile.TemporaryDirectory(prefix="exact-recovery-source-")
    root = Path(holder.name)
    shutil.copytree(ROOT / "mcp-server", root / "mcp-server",
                    ignore=shutil.ignore_patterns("node_modules"))
    shutil.copytree(ROOT / "dealroom", root / "dealroom")
    assert not (root / "mcp-server" / "node_modules").exists()
    if mismatch:
        lock = root / "mcp-server" / "package-lock.json"
        lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    if broken_attachment:
        config = root / "mcp-server" / "wrangler.toml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("routes = []\n", "", 1),
            encoding="utf-8",
        )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "selftest@example.invalid"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "selftest"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "add", "mcp-server", "dealroom"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "--detach", sha], check=True)
    if broken_attachment:
        subprocess.run(["git", "-C", str(root), "update-ref",
                        "refs/remotes/origin/main", sha], check=True)
    return holder, root, sha


def wrapper(root: Path, sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(DEPLOY), "--release-sha", sha,
         "--internal-exact-source-root", str(root), *RECOVERY_ARGS],
        cwd=ROOT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def test_wrangler_dry_run() -> None:
    with tempfile.TemporaryDirectory(prefix="exact-recovery-bundle-") as raw:
        root = Path(raw)
        shutil.copytree(ROOT / "mcp-server", root / "mcp-server",
                        ignore=shutil.ignore_patterns("node_modules"))
        shutil.copytree(ROOT / "dealroom", root / "dealroom")
        worker_deps = root / "mcp-server" / "node_modules"
        assert not worker_deps.exists()
        worker_deps.symlink_to(RUNTIME)
        result = subprocess.run(
            [str(WRANGLER), "deploy", "--dry-run", "--env", "staging",
             "--config", str(root / "mcp-server" / "wrangler.toml")],
            cwd=ROOT,
            env={**os.environ, "XDG_CONFIG_HOME": str(root / "config")},
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "--dry-run: exiting now." in result.stdout
        assert "Worker Version ID:" not in result.stdout
        worker_deps.unlink()
        assert not worker_deps.exists()


def test_cleanup_traps(source: str) -> None:
    start = source.index("cleanup_ephemeral() {")
    end = source.index("\ntrap cleanup_ephemeral EXIT", start)
    functions = source[start:end]
    with tempfile.TemporaryDirectory(prefix="exact-recovery-cleanup-") as raw:
        root = Path(raw)
        link = root / "node_modules"
        script = root / "cleanup.sh"
        script.write_text(
            "#!/bin/sh\nset -eu\n"
            f"REPO={ROOT!s}\nEXACT_RUNTIME_LINK={link!s}\nSTAGING_RECEIPT=\n"
            + functions
            + "\ntrap cleanup_ephemeral EXIT\n"
            + f"ln -s {RUNTIME!s} {link!s}\nexit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        success = subprocess.run([str(script)], capture_output=True, text=True, check=False)
        assert success.returncode == 0
        assert not link.exists()

        script.write_text(
            "#!/bin/sh\nset -eu\n"
            f"REPO={ROOT!s}\nEXACT_RUNTIME_LINK={link!s}\nSTAGING_RECEIPT=\n"
            + functions
            + "\ntrap cleanup_ephemeral EXIT\ntrap 'cleanup_on_signal 143' TERM\n"
            + f"ln -s {RUNTIME!s} {link!s}\nkill -TERM $$\n",
            encoding="utf-8",
        )
        signal_exit = subprocess.run([str(script)], capture_output=True, text=True, check=False)
        assert signal_exit.returncode == 143
        assert not link.exists()


def main() -> int:
    source = DEPLOY.read_text(encoding="utf-8")
    assert 'cmp -s "$CURRENT_PACKAGE_LOCK" "$EXACT_PACKAGE_LOCK"' in source
    assert source.index("validate-exact-recovery-source.py") < source.index("cmp -s")
    assert source.index("cmp -s") < source.index('ln -s "$REPO/mcp-server/node_modules"')
    assert "trap cleanup_ephemeral EXIT" in source
    assert "cleanup_on_signal 130" in source
    assert "cleanup_on_signal 143" in source

    mismatch_holder, mismatch_root, mismatch_sha = make_source(mismatch=True)
    try:
        refused = wrapper(mismatch_root, mismatch_sha)
        assert refused.returncode != 0
        assert "dependency lockfile differs" in (refused.stdout + refused.stderr), (
            refused.returncode, refused.stdout, refused.stderr
        )
        assert not (mismatch_root / "mcp-server" / "node_modules").exists()
    finally:
        mismatch_holder.cleanup()

    cleanup_holder, cleanup_root, cleanup_sha = make_source(broken_attachment=True)
    try:
        refused = wrapper(cleanup_root, cleanup_sha)
        assert refused.returncode != 0
        assert not (cleanup_root / "mcp-server" / "node_modules").exists(), (
            refused.stdout, refused.stderr
        )
    finally:
        cleanup_holder.cleanup()

    test_cleanup_traps(source)
    test_wrangler_dry_run()
    print("exact-recovery-runtime: lock gate, dry-run bundle, and cleanup passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fail-closed verification for governance inputs reviewed through Git."""
from __future__ import annotations

import subprocess
from pathlib import Path


class ReviewedArtifactError(ValueError):
    """A purported reviewed input is not the exact committed HEAD object."""


def assert_head_committed(repo: Path, path: Path, expected_relative_path: str) -> None:
    """Require one exact path to be tracked, clean, and byte-identical to HEAD."""
    repo = repo.resolve()
    path = path.resolve()
    expected = (repo / expected_relative_path).resolve()
    if path != expected:
        raise ReviewedArtifactError(
            f"reviewed artifact path must be {expected_relative_path}"
        )
    checks = (
        (["git", "ls-files", "--error-unmatch", "--", expected_relative_path],
         "is not tracked by Git"),
        (["git", "diff", "--quiet", "--", expected_relative_path],
         "has uncommitted working-tree changes"),
        (["git", "diff", "--cached", "--quiet", "--", expected_relative_path],
         "has staged but uncommitted changes"),
    )
    for command, message in checks:
        result = subprocess.run(
            command, cwd=repo, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode != 0:
            raise ReviewedArtifactError(
                f"reviewed artifact {expected_relative_path} {message}"
            )
    committed = subprocess.run(
        ["git", "show", f"HEAD:{expected_relative_path}"],
        cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    if committed.returncode != 0:
        raise ReviewedArtifactError(
            f"reviewed artifact {expected_relative_path} is absent from HEAD"
        )
    if committed.stdout != path.read_bytes():
        raise ReviewedArtifactError(
            f"reviewed artifact {expected_relative_path} differs from HEAD"
        )

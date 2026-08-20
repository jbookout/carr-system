"""Canonical/recovery boundary for the local video builders."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lib.drive_recovery import RecoveryArgumentError, RecoveryContext, parse_recovery_controls


@dataclass(frozen=True)
class VideoAssets:
    context: RecoveryContext
    brand_root: Path


def recovery_video_assets(argv: list[str], seam: str) -> VideoAssets:
    """Return Drive assets only for explicit, reasoned NONCANONICAL recovery."""
    try:
        context = parse_recovery_controls(argv, seam)
    except RecoveryArgumentError as exc:
        raise SystemExit(f"video-assets: {exc}") from exc
    if not context.recovery:
        raise SystemExit(
            f"video-assets: canonical asset seam missing: {seam}; normal mode refuses Drive assets"
        )
    assert context.vault is not None
    return VideoAssets(context, context.vault / "Marketing" / "Brand Assets")

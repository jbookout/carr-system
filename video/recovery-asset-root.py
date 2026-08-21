#!/usr/bin/env python3
"""Print a video asset root only for explicit reasoned recovery."""
import sys

from asset_boundary import recovery_video_assets

assets = recovery_video_assets(sys.argv[1:], "versioned b-roll and audio library")
if assets.context.args:
    raise SystemExit(f"video-assets: unexpected argument: {assets.context.args[0]}")
print(assets.brand_root)

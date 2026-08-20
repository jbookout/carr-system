#!/usr/bin/env python3
"""make-premiere-cut.py — generate a Premiere-importable timeline (FCP7 xmeml)
from a shot list. Premiere: File > Import the .xml, and the rough cut appears
as a sequence with clips in order, ready to trim. No plugins required.

Usage: python3 make-premiere-cut.py            (uses the built-in CARR shot list)
Edit SHOTS below or import this and call build_xmeml() with your own list.
"""
import pathlib
import sys
from xml.sax.saxutils import escape
from asset_boundary import recovery_video_assets

ASSETS = recovery_video_assets(sys.argv[1:], "versioned b-roll library")
if ASSETS.context.args:
    raise SystemExit(f"make-premiere-cut: unexpected argument: {ASSETS.context.args[0]}")
BROLL = ASSETS.brand_root / "Stock" / "broll"
OUT = pathlib.Path.home() / "Movies/CARR Video Pipeline/03_Output/carr_rough_cut.xml"

FPS = 30          # timebase (29.97 uses ntsc flag)
W, H = 1920, 1080

# (file, seconds to use from head of clip)
SHOTS = [
    (f"{BROLL}/lobby-blur-background_AS238299676.mov", 4.5),
    (f"{BROLL}/glass-tower-facade_AS367005503.mov", 4.5),
    (f"{BROLL}/professionals-consult-laptop_AS518646499.mov", 3.5),
]

def build_xmeml(shots, seq_name="CARR Rough Cut", fps=FPS, w=W, h=H):
    clips, t = [], 0
    for i, (path, dur_s) in enumerate(shots):
        p = pathlib.Path(path)
        if not p.exists():
            raise FileNotFoundError(path)
        frames = round(dur_s * fps)
        url = "file://localhost" + str(p).replace(" ", "%20")
        clips.append(f"""
        <clipitem id="clip-{i+1}">
          <name>{escape(p.stem)}</name>
          <duration>{frames}</duration>
          <rate><timebase>{fps}</timebase><ntsc>TRUE</ntsc></rate>
          <start>{t}</start><end>{t + frames}</end>
          <in>0</in><out>{frames}</out>
          <file id="file-{i+1}">
            <name>{escape(p.name)}</name>
            <pathurl>{url}</pathurl>
            <rate><timebase>{fps}</timebase><ntsc>TRUE</ntsc></rate>
            <media><video/></media>
          </file>
        </clipitem>""")
        t += frames
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence id="seq-1">
    <name>{escape(seq_name)}</name>
    <duration>{t}</duration>
    <rate><timebase>{fps}</timebase><ntsc>TRUE</ntsc></rate>
    <media>
      <video>
        <format>
          <samplecharacteristics>
            <rate><timebase>{fps}</timebase><ntsc>TRUE</ntsc></rate>
            <width>{w}</width><height>{h}</height>
            <pixelaspectratio>square</pixelaspectratio>
          </samplecharacteristics>
        </format>
        <track>{''.join(clips)}
        </track>
      </video>
    </media>
  </sequence>
</xmeml>
"""

if __name__ == "__main__":
    xml = build_xmeml(SHOTS)
    OUT.write_text(xml)
    print(f"wrote {OUT} ({len(SHOTS)} clips, {sum(d for _, d in SHOTS)}s)")

#!/usr/bin/env python3
"""Prove keep_generation can never abort the export it is protecting.

WHY THIS EXISTS. On 2026-08-25 the nightly export step wrote ZERO of its six
targets. It died two seconds in, inside keep_generation, on this:

    OSError: [Errno 11] Resource deadlock avoided

shutil.copy2 routes through macOS fcopyfile, and fcopyfile answers EDEADLK when
the source lives on a OneDrive CloudStorage (FileProvider) path — which is where
the live vault has been since Joe's 2026-08-22 ruling. keep_generation is called
from run_export BEFORE the atomic rename and OUTSIDE its try block, so one
failed BACKUP killed the exporter process on its first target and every vault
file went unwritten for the night. The export register still read OK, because
its window is 26 hours and the previous good run was inside it.

The rule is the inversion that bug embodied: losing a generation copy costs one
rollback point, losing the export costs the day. A backup is subordinate to the
thing it backs up, always.

Four properties:

  1. KEEPS A COPY      — the generation is byte-identical to the source.
  2. NO fcopyfile      — shutil.copy2/copyfile are never called, so the cloud
                         fast path that raised EDEADLK is not reachable at all.
  3. NEVER RAISES      — a genuinely broken destination is reported and
                         swallowed; the caller proceeds to write the export.
  4. PRUNES            — at most KEEP_GENERATIONS copies survive, oldest first.

Read-only: works in a temp dir, touches no vault file and no database.
Usage:  ./.venv/bin/python tools/test-export-keep-generation.py
"""

import errno
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exporters.common import KEEP_GENERATIONS, keep_generation  # noqa: E402

fails: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def gen_dir_for(target: Path) -> Path:
    return target.parent / (target.name + ".generations")


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        print("1. keeps a byte-identical copy of the file about to be replaced")
        target = tmp / "lead-board.md"
        target.write_bytes(b"first generation\n")
        keep_generation(target)
        kept = sorted(gen_dir_for(target).iterdir())
        check("one generation kept", len(kept) == 1, f"found {len(kept)}")
        if kept:
            check("copy is byte-identical", kept[0].read_bytes() == b"first generation\n")
            check("copy is stamped and named after the source",
                  kept[0].name.endswith("-lead-board.md") and kept[0].name[8] == "T")

        print("2. never routes through fcopyfile — shutil.copy2/copyfile unused")
        exploded = []

        def boom(*a, **k):
            exploded.append(a)
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")

        real_copy2, real_copyfile = shutil.copy2, shutil.copyfile
        shutil.copy2, shutil.copyfile = boom, boom
        try:
            target2 = tmp / "deal-room.md"
            target2.write_bytes(b"cloud path payload\n")
            raised = None
            try:
                keep_generation(target2)
            except BaseException as e:  # noqa: BLE001 — the point is that nothing escapes
                raised = e
            check("shutil copy helpers never called", not exploded,
                  f"called {len(exploded)}x" if exploded else "")
            check("no exception escaped", raised is None, repr(raised) if raised else "")
            kept2 = sorted(gen_dir_for(target2).iterdir()) if gen_dir_for(target2).exists() else []
            check("generation still written without them", len(kept2) == 1,
                  f"found {len(kept2)}")
        finally:
            shutil.copy2, shutil.copyfile = real_copy2, real_copyfile

        print("3. a broken destination is swallowed, not raised at the exporter")
        target3 = tmp / "vendors.md"
        target3.write_bytes(b"payload\n")
        # A regular FILE sitting where the .generations DIRECTORY must go: mkdir
        # raises FileExistsError, which is an OSError, and stands in for every
        # way a cloud-synced destination can refuse a write at 2am.
        gen_dir_for(target3).write_bytes(b"not a directory")
        raised3 = None
        try:
            keep_generation(target3)
        except BaseException as e:  # noqa: BLE001
            raised3 = e
        check("no exception escaped", raised3 is None, repr(raised3) if raised3 else "")
        check("source file left untouched", target3.read_bytes() == b"payload\n")

        print("4. a missing source is a no-op, and generations prune to the cap")
        keep_generation(tmp / "never-existed.md")
        check("missing source raises nothing and creates nothing",
              not (tmp / "never-existed.md.generations").exists())

        target4 = tmp / "renewal-feed.md"
        gd = gen_dir_for(target4)
        gd.mkdir()
        # Pre-seed more than the cap with lexically ordered stamps, then let one
        # real call prune. sorted() over the stamped names is oldest-first.
        for i in range(KEEP_GENERATIONS + 3):
            (gd / f"202608{10 + i:02d}T000000Z-renewal-feed.md").write_bytes(b"old\n")
        target4.write_bytes(b"newest\n")
        keep_generation(target4)
        survivors = sorted(gd.iterdir())
        check(f"pruned to the {KEEP_GENERATIONS}-generation cap",
              len(survivors) == KEEP_GENERATIONS, f"found {len(survivors)}")
        check("the newest copy survived the prune",
              any(p.read_bytes() == b"newest\n" for p in survivors))
        check("the oldest stamp was the one dropped",
              not (gd / "20260810T000000Z-renewal-feed.md").exists())

    print()
    if fails:
        print(f"{len(fails)} FAILED: {', '.join(fails)}")
        return 1
    print("keep_generation cannot abort the export it protects")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
corpus-sync.py — the doctrine corpus mirror (ORDER 30, Wave 4 phase 1, 2026-07-31).

WHAT THIS IS. `corpus/` holds a byte-for-byte copy of the vault's DOCTRINE tier
so that rules and craft gain what records already have: version history, diffs,
and a way to answer "when did we decide that, and what did it say before?"

WHAT THIS IS NOT, and phase 1 depends on the distinction holding:
**THE DRIVE IS CANONICAL.** The mirror is versioning only. Nothing reads the
mirror, nothing writes toward the Drive, and no human workflow changes. Joe and
Dell keep editing the Drive exactly as before; this tool copies one direction,
Drive -> mirror, and never the other way.

So the two drift directions mean opposite things:
  * source newer than the mirror  = NORMAL. Doctrine changed; re-mirror it.
  * mirror differs from what was mirrored = SOMEONE EDITED THE MIRROR. That is
    a loud error, because their edit is invisible to every reader (Cowork, Dell,
    every session reads the Drive) and the next --sync would silently erase it.

Drift is detected by CONTENT HASH against the manifest, never by mtime alone:
Google Drive File Stream rewrites mtimes on its own schedule, so an mtime test
would cry wolf nightly and, worse, could miss a real edit that preserved mtime.

Usage:
  python3 tools/corpus-sync.py               # check only; exit 0 clean / 1 drift / 2 mirror edited
  python3 tools/corpus-sync.py --sync        # re-mirror changed files (refuses to clobber an edited mirror)
  python3 tools/corpus-sync.py --sync --force  # ...and DISCARD mirror-side edits, restoring from the Drive
  python3 tools/corpus-sync.py --prune       # with --sync: delete mirror files no longer in the set
  python3 tools/corpus-sync.py --json        # machine-readable status

The set of files that ARE the corpus lives in corpus/corpus-set.tsv, one row per
file with the reason it counts as doctrine. Adding doctrine = adding a row.
Read-only against the vault. No network, no credential, no database.
"""
import hashlib
import json
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(REPO, "corpus")
SET_FILE = os.path.join(CORPUS, "corpus-set.tsv")
MANIFEST = os.path.join(CORPUS, "manifest.json")

VAULT = os.environ.get("CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

# Stop rules from the order: this is a TEXT corpus.
MAX_BYTES = 5 * 1024 * 1024
TEXT_EXT = {".md", ".txt", ".tsv", ".csv", ".json", ".sql", ".py", ".js", ".html", ".yml", ".yaml"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def looks_binary(path):
    """A null byte in the first 8 KB. Cheap, and right for the file types here."""
    with open(path, "rb") as fh:
        return b"\0" in fh.read(8192)


def load_set():
    """corpus-set.tsv -> [(vault_relative_path, class, why)]. '#' comments, blank lines ok."""
    rows = []
    with open(SET_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            while len(parts) < 3:
                parts.append("")
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return rows


def load_manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def status():
    """Classify every file in the set. Pure read; safe for the health check."""
    man = load_manifest()
    if man is None:
        return {"manifest": False, "files": [], "skipped": [], "orphans": [],
                "counts": {}, "mirrored": 0, "manifest_count": 0}

    recorded = {f["source"]: f for f in man.get("files", [])}
    skipped_rec = {s["source"]: s for s in man.get("skipped", [])}
    out = []
    in_set = set()

    for rel, klass, why in load_set():
        in_set.add(rel)
        src = os.path.join(VAULT, rel)
        dst = os.path.join(CORPUS, rel)
        rec = recorded.get(rel)

        if not os.path.exists(src):
            # Not "the mirror is wrong" — the vault file moved, was renamed, or the
            # Drive mount is not up. Named separately so nobody re-mirrors a ghost.
            out.append({"path": rel, "state": "SOURCE-GONE", "class": klass})
            continue

        try:
            if os.path.getsize(src) > MAX_BYTES or looks_binary(src):
                out.append({"path": rel, "state": "SKIP", "class": klass,
                            "bytes": os.path.getsize(src)})
                continue
        except OSError as e:
            out.append({"path": rel, "state": "UNREADABLE", "class": klass, "err": str(e)})
            continue

        src_h = sha256(src)
        if rec is None or not os.path.exists(dst):
            out.append({"path": rel, "state": "NEW" if rec is None else "MISSING-MIRROR",
                        "class": klass, "src_sha": src_h})
            continue

        mir_h = sha256(dst)
        was = rec.get("sha256")
        if mir_h != was and src_h != was:
            out.append({"path": rel, "state": "BOTH-CHANGED", "class": klass,
                        "src_sha": src_h, "mirror_sha": mir_h, "manifest_sha": was})
        elif mir_h != was:
            out.append({"path": rel, "state": "MIRROR-EDITED", "class": klass,
                        "src_sha": src_h, "mirror_sha": mir_h, "manifest_sha": was})
        elif src_h != was:
            out.append({"path": rel, "state": "DRIFT", "class": klass, "src_sha": src_h})
        else:
            out.append({"path": rel, "state": "OK", "class": klass})

    orphans = [p for p in recorded if p not in in_set]
    counts = {}
    for f in out:
        counts[f["state"]] = counts.get(f["state"], 0) + 1
    return {"manifest": True, "files": out, "orphans": orphans, "counts": counts,
            "skipped": list(skipped_rec.values()),
            "manifest_count": len(recorded), "generated_at": man.get("generated_at")}


def sync(force=False, prune=False):
    """Copy Drive -> mirror for everything that changed, then rewrite the manifest."""
    man = load_manifest() or {"files": [], "skipped": []}
    recorded = {f["source"]: f for f in man.get("files", [])}
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    files, skipped, refused, copied = [], [], [], []
    st_by_path = {f["path"]: f for f in status()["files"]} if os.path.exists(MANIFEST) else {}

    for rel, klass, why in load_set():
        src = os.path.join(VAULT, rel)
        dst = os.path.join(CORPUS, rel)
        if not os.path.exists(src):
            refused.append((rel, "source missing on the Drive"))
            if rel in recorded:            # keep the old row; do not lose the history pointer
                files.append(recorded[rel])
            continue
        size = os.path.getsize(src)
        if size > MAX_BYTES:
            skipped.append({"source": rel, "reason": f"over {MAX_BYTES // (1024*1024)} MB", "bytes": size})
            continue
        if looks_binary(src):
            skipped.append({"source": rel, "reason": "binary (this is a text corpus)", "bytes": size})
            continue

        state = st_by_path.get(rel, {}).get("state")
        if state in ("MIRROR-EDITED", "BOTH-CHANGED") and not force:
            refused.append((rel, f"{state}: the mirror was edited; the Drive is canonical. "
                                 "Move the edit to the Drive, or re-run with --force to discard it."))
            files.append(recorded[rel])
            continue

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        need = (not os.path.exists(dst)) or sha256(dst) != sha256(src)
        if need:
            shutil.copy2(src, dst)
            copied.append(rel)
        mt = os.path.getmtime(src)
        files.append({
            "source": rel,
            "mirror": os.path.relpath(dst, REPO),
            "sha256": sha256(src),
            "bytes": size,
            "mtime": round(mt, 3),
            "mtime_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mt)),
            "mirrored_at": now if need else recorded.get(rel, {}).get("mirrored_at", now),
            "class": klass,
            "why": why,
        })

    in_set = {f["source"] for f in files} | {s["source"] for s in skipped}
    orphans = [p for p in recorded if p not in in_set]
    pruned = []
    if prune:
        for p in orphans:
            fp = os.path.join(CORPUS, p)
            if os.path.exists(fp):
                os.remove(fp)
            pruned.append(p)

    out = {
        "generated_at": now,
        "generator": "tools/corpus-sync.py (ORDER 30, Wave 4 phase 1)",
        "vault_root": VAULT,
        "canonical": "THE DRIVE. This mirror is versioning only — never edit files under corpus/.",
        "max_bytes": MAX_BYTES,
        "count": len(files),
        "files": sorted(files, key=lambda f: f["source"]),
        "skipped": sorted(skipped, key=lambda s: s["source"]),
    }
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return {"copied": copied, "refused": refused, "skipped": skipped,
            "orphans": orphans, "pruned": pruned, "count": len(files)}


LOUD = ("MIRROR-EDITED", "BOTH-CHANGED")


def report(st):
    if not st["manifest"]:
        print("corpus: no manifest yet — run: python3 tools/corpus-sync.py --sync")
        return 1
    c = st["counts"]
    ok = c.get("OK", 0)
    loud = sum(c.get(k, 0) for k in LOUD)
    drift = c.get("DRIFT", 0) + c.get("NEW", 0) + c.get("MISSING-MIRROR", 0)
    other = c.get("SOURCE-GONE", 0) + c.get("UNREADABLE", 0)
    print(f"corpus mirror — {ok} clean, {drift} to re-mirror, {loud} mirror-side, "
          f"{other} unreadable/gone, {len(st['orphans'])} orphan(s)")
    for f in st["files"]:
        s = f["state"]
        if s == "OK":
            continue
        if s in LOUD:
            print(f"  !! {s:<14} {f['path']}")
            print("     THE MIRROR WAS EDITED. The Drive is canonical: nothing reads corpus/, so this")
            print("     edit reaches no session. Move it to the Drive copy, then re-sync. To throw the")
            print("     mirror-side change away instead: tools/corpus-sync.py --sync --force")
        else:
            print(f"  -- {s:<14} {f['path']}")
    for p in st["orphans"]:
        print(f"  -- ORPHAN         {p}  (in the manifest, no longer in corpus-set.tsv; --sync --prune removes it)")
    for s in st["skipped"]:
        print(f"  -- SKIPPED        {s['source']}  ({s['reason']}, {s['bytes']:,} bytes)")
    return 2 if loud else (1 if drift or other else 0)


def main(argv):
    do_sync = "--sync" in argv or "--apply" in argv
    force = "--force" in argv
    prune = "--prune" in argv
    if "--json" in argv:
        print(json.dumps(status(), indent=2))
        return 0
    if do_sync:
        r = sync(force=force, prune=prune)
        print(f"corpus sync — {r['count']} file(s) in the manifest, {len(r['copied'])} (re)mirrored")
        for p in r["copied"]:
            print(f"  ++ {p}")
        for p, why in r["refused"]:
            print(f"  !! REFUSED {p}\n     {why}")
        for s in r["skipped"]:
            print(f"  -- SKIPPED {s['source']} ({s['reason']}, {s['bytes']:,} bytes)")
        for p in r["pruned"]:
            print(f"  xx PRUNED  {p}")
        for p in r["orphans"]:
            if p not in r["pruned"]:
                print(f"  -- ORPHAN  {p} (left in place; --prune deletes it)")
        return 2 if r["refused"] else 0
    return report(status())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""corpus_renders.py — the deny set for CORPUS RENDERS: files git owns and the
Drive/home copies merely display.

WHY THIS EXISTS. Joe, 2026-08-10, after the nightly chain failed on it:
"create a hook that prevents anything on me or dells side from writing to the
drive files ever again."

THE INCIDENT IT CLOSES. On 2026-08-09 the chair-verdict-bars work was written
into the DRIVE copies of eight council files. Git has been canonical for the
doctrine corpus since 2026-08-06, so those edits landed on renders. Nothing
stopped them and nothing noticed until the nightly corpus push refused to
overwrite them the next morning and exited 2. Nothing was lost that time — the
push refuses rather than clobbering, which is the one thing standing between
this failure and silent data loss. Rely on that twice and the second time is a
`--push` running before anyone reads the conflict.

The cost was not the eight files. It was that the chain went red for a reason
nobody could distinguish from the mypy tripwire that had already been red for
three days, so the chain's exit code stopped carrying information.

WHY record-home-gate DIDN'T ALREADY COVER IT, and this is the interesting part.
That gate's docstring names the `~/.claude` and `My Drive/.claude` trees as
explicitly out of scope: "harness-required; repo-canonical from P6." The word
doing the work is repo-canonical — the reasoning was that because git owns
them, something else governs them. Nothing did. An exclusion justified by
another control's existence is only as good as that control, and here the other
control was an assumption.

WHAT IT DENIES. Every `source` path in corpus/manifest.json — 78 files across
three roots — at any extension:
  * `vault:` paths under CARR AI (doctrine, playbooks, SOPs)
  * `drive:` paths under My Drive/.claude (the agent roster, the skills)
  * `home:` paths under ~/.claude (the portable meta-skills)

THE SET IS READ FROM THE MANIFEST, NEVER RETYPED. Same discipline as
record-home-gate parsing exporters/targets.py, and for the same reason: a
duplicated list is the two-homes disease one layer down, and it goes stale on
the first file anyone adds to the corpus set. corpus-sync.py rewrites the
manifest on every sync and every push, so this gate widens itself automatically.

WHAT IT DOES NOT TOUCH. Anything not in the manifest. The repo's own
corpus/ copies — those are the canonical originals and editing them is the
CORRECT path this gate steers people toward. Scratchpads, Life AI, non-corpus
files anywhere.

DELL'S SIDE. The gate resolves its roots from `CARR_VAULT` when set and falls
back to a glob over `~/Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI`, so
it binds on his machine the moment he pulls, with no per-machine edit. His
settings already carry the two hooks this module plugs into.

FAILS OPEN ON ERROR, by deliberate inheritance from the other gates: a gate that
wedges a session costs more than the marginal safety of failing closed on a
single-operator machine. A parse failure logs and allows.
"""

import glob
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(REPO, "corpus", "manifest.json")


def _vault():
    """CARR AI's absolute path. Env first so Dell needs no edit; then Joe's
    literal path; then a glob, which is what makes this portable to any Drive
    account without the gate knowing whose it is."""
    env = os.environ.get("CARR_VAULT")
    if env and os.path.isdir(env):
        return env
    joe = ("/Users/booko/Library/CloudStorage/"
           "GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
    if os.path.isdir(joe):
        return joe
    for hit in sorted(glob.glob(os.path.expanduser(
            "~/Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI"))):
        if os.path.isdir(hit):
            return hit
    return joe                       # unreachable Drive: paths just won't match


def roots():
    v = _vault()
    return {"vault": v,
            "drive": os.path.dirname(v),          # "My Drive"
            "home": os.path.expanduser("~")}


def _abs(source, rt):
    """One manifest `source` -> absolute path. `drive:x` and `home:x` are
    root-prefixed; a bare path is vault-relative."""
    if ":" in source and source.split(":", 1)[0] in ("drive", "home"):
        kind, rel = source.split(":", 1)
        return os.path.join(rt[kind], rel)
    return os.path.join(rt["vault"], source)


def render_map():
    """{realpath: source-label} for every corpus render. Empty on any failure,
    which is the fail-open path."""
    try:
        man = json.load(open(MANIFEST))
        rt = roots()
        out = {}
        for f in man.get("files", []):
            src = f.get("source")
            if not src:
                continue
            try:
                out[os.path.realpath(_abs(src, rt))] = src
            except Exception:
                continue
        return out
    except Exception:
        return {}


def _fix(source):
    """The exact repo path to edit instead, so the denial names the next move
    rather than only the refusal. A refusal without the alternative is how a
    gate gets switched off."""
    if ":" in source and source.split(":", 1)[0] in ("drive", "home"):
        kind, rel = source.split(":", 1)
        return f"corpus/_{kind}/{rel}"
    return f"corpus/{source}"


def verdict(path, tool="this write"):
    """Deny reason for an absolute path, or None. `path` may be user-relative."""
    try:
        real = os.path.realpath(os.path.expanduser(path))
    except Exception:
        return None
    src = render_map().get(real)
    if not src:
        return None
    return (
        f"'{src}' is a CORPUS RENDER, not a source file. Git has owned the doctrine "
        f"corpus since 2026-08-06 and these copies exist only to be read — the next "
        f"`corpus-sync.py --push` either overwrites {tool} or refuses and turns the "
        f"nightly chain red, which is exactly what happened on 2026-08-10 with the "
        f"council chair files. "
        f"EDIT THIS INSTEAD: ~/carr-system/{_fix(src)} — then run "
        f"`python3 tools/corpus-sync.py --push` to publish it. That is the only path "
        f"that reaches BOTH partners; a Drive-side edit reaches neither, because "
        f"Dell's copy is pushed from git too."
    )


def command_spellings():
    """{literal path string: realpath} for every corpus render, under EVERY
    spelling a shell command might use it.

    This exists because `/Users/booko/My Drive` is a symlink to the CloudStorage
    mount, so realpath collapses two very different-looking strings into one.
    The Write/Edit door never notices — it resolves before comparing. The Bash
    door matches TEXT, so a command written against the CloudStorage spelling
    would sail past a deny set holding only realpaths. guard-unattended.py
    already carries `_VAULT_SPELLINGS` for exactly this reason; this is the same
    problem one directory up."""
    out = {}
    rt = roots()
    try:
        man = json.load(open(MANIFEST))
    except Exception:
        return out
    for f in man.get("files", []):
        src = f.get("source")
        if not src:
            continue
        try:
            raw = _abs(src, rt)
            real = os.path.realpath(raw)
            for spelling in {raw, real}:
                out[spelling] = real
        except Exception:
            continue
    return out


def paths_in_command(cmd):
    """{the literal string found in the command: its realpath}, for every corpus
    render the command names under either spelling.

    Keyed by the SPELLING rather than the realpath on purpose: the caller has to
    locate the write operator immediately before the path, and it can only do
    that by searching for the text the command actually contains."""
    hits = {}
    if not isinstance(cmd, str) or not cmd:
        return hits
    for spelling, real in command_spellings().items():
        if spelling in cmd:
            hits[spelling] = real
    return hits

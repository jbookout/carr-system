#!/usr/bin/env python3
"""Acceptance test for ops/no-client-names-gate.py.

Hermetic: it builds its own name list from INVENTED names (never a real one —
that is the point of the gate) and plants them in a throwaway git repository,
so it needs no network, no record layer and no state from this checkout. It
proves, in order:

  * the canonical form: case, apostrophes, punctuation, hyphens and underscores
    all normalise to the same hash, and matching stops at token boundaries;
  * a planted name FAILS the gate end to end (real `git ls-files`, real exit 1),
    in file contents and in a file path, and the output never prints the name;
  * an allowlisted (path, hash) pair passes, and covers only that one file;
  * the committed list itself is hashes only and loads.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from git_env import fixture_env  # noqa: E402

ENV = fixture_env()
spec = importlib.util.spec_from_file_location("no_client_names_gate", HERE / "no-client-names-gate.py")
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

PLANTED = ["Zebulon Quaxmire", "Orrin Fettlewick-Paine", "Glimmerstone Dental Arts"]


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


checks: list[tuple[str, bool]] = []

# ── canonical form ─────────────────────────────────────────────────────────
same = {gate.canonical(v) for v in ("Orrin Fettlewick-Paine", "ORRIN fettlewick_paine",
                                    "orrin  Fettlewick—Paine", "Orrin Fettlewick'-Paine")}
checks.append(("canonical form ignores case, punctuation and separators", same == {"orrin fettlewick paine"}))
checks.append(("an apostrophe is dropped, not split", gate.canonical("O'Quaxmire") == "oquaxmire"))

names = gate.NameList({h(gate.canonical(n)) for n in PLANTED},
                      {h(gate.canonical(n).split()[0]) for n in PLANTED}, 3)
checks.append(("a planted name is found mid-sentence",
               [ln for ln, _ in names.hits("x\nmet Zebulon Quaxmire today\n")] == [2]))
checks.append(("matching is token-bounded (no hit inside a longer word)",
               list(names.hits("zebulonquaxmire and Zebulon Quaxmires")) == []))
checks.append(("a hyphenated CamelCase path form is found",
               len(list(names.hits("DNA/Orrin-Fettlewick-Paine.md"))) == 1))
checks.append(("an unlisted name is not found", list(names.hits("Zebulon Smith")) == []))


# ── end to end, through a real git repository ───────────────────────────────
def run_gate(repo: Path, allow: list[dict]) -> tuple[int, str]:
    hashes = repo.parent / "hashes.json"
    hashes.write_text(json.dumps({"max_tokens": 3, "sha256": sorted(names.full),
                                  "first_token_sha256": sorted(names.first)}))
    allow_f = repo.parent / "allow.json"
    allow_f.write_text(json.dumps({"entries": allow}))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gate.main(["--repo", str(repo), "--hashes", str(hashes), "--allow", str(allow_f)])
    return rc, out.getvalue() + err.getvalue()


with tempfile.TemporaryDirectory(prefix="no-client-names-") as tmp:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    env = ENV
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "clean.md").write_text("A dentist in the panhandle, no names here.\n")
    subprocess.run(["git", "-C", str(repo), "add", "clean.md"], check=True, env=env)
    rc, _ = run_gate(repo, [])
    checks.append(("a clean tree passes", rc == 0))

    (repo / "notes.md").write_text("Follow up with glimmerstone DENTAL arts on Friday.\n")
    (repo / "Zebulon_Quaxmire-intake.txt").write_text("nothing\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    rc, text = run_gate(repo, [])
    checks.append(("a planted name in contents FAILS the gate", rc == 1 and "notes.md:1" in text))
    checks.append(("a planted name in a file path FAILS the gate",
                   "Zebulon_Quaxmire-intake.txt:path" in text))
    checks.append(("the failure output never prints the name",
                   "glimmerstone" not in text.lower() and "quaxmire" not in text.lower().replace(
                       "zebulon_quaxmire-intake.txt", "")))

    allow = [{"path": "notes.md", "sha256": h("glimmerstone dental arts"), "reason": "test"},
             {"path": "Zebulon_Quaxmire-intake.txt", "sha256": h("zebulon quaxmire"), "reason": "test"}]
    rc, _ = run_gate(repo, allow)
    checks.append(("allowlisted (path, name) pairs pass", rc == 0))

    (repo / "other.md").write_text("glimmerstone dental arts again\n")
    subprocess.run(["git", "-C", str(repo), "add", "other.md"], check=True, env=env)
    rc, text = run_gate(repo, allow)
    checks.append(("an allowlist pair covers only its own file", rc == 1 and "other.md:1" in text))

# ── the committed list ──────────────────────────────────────────────────────
committed = json.loads((HERE / "config" / "client-name-hashes.v1.json").read_text())
hexes = committed["sha256"] + committed["first_token_sha256"]
checks.append(("the committed list is hashes only",
               all(len(x) == 64 and all(c in "0123456789abcdef" for c in x) for x in hexes)
               and set(committed) >= {"sha256", "first_token_sha256", "max_tokens"}))
allow_doc = json.loads((HERE / "config" / "client-name-allowlist.v1.json").read_text())
checks.append(("every allowlist entry names a listed hash and gives a reason",
               all(e["sha256"] in set(committed["sha256"]) and e.get("reason")
                   for e in allow_doc["entries"])))

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
print(f"no-client-names-gate-selftest: {len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)

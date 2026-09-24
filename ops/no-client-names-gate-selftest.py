#!/usr/bin/env python3
"""Acceptance test for ops/no-client-names-gate.py.

Hermetic: it builds its name list from INVENTED names (never a real one; that
is the point of the gate), uses a throwaway test key generated here (never the
real CARR_NAME_GUARD_KEY, which this test clears from its environment), and
plants the names in a throwaway git repository. It proves, in order:

  * the canonical form: case, apostrophes, punctuation, hyphens and underscores
    all normalise the same way, and matching stops at token boundaries;
  * LOCAL mode: a planted name FAILS the gate end to end (real `git ls-files`,
    real exit 1), in file contents and in a file path, and the output never
    prints the name;
  * HMAC mode: the built file carries no plain digest of any name, a planted
    name fails the same way, and a WRONG key fails loudly (key_check);
  * with neither source the gate SKIPS LOUDLY (WARNING line, exit 0);
  * the allowlist covers FILES pinned by content: an edited pinned file is no
    longer covered, and an allowlist entry carrying a name digest is refused;
  * a planted name DIGEST fails in local mode: plain md5/sha1/sha256, a
    12-hex truncated prefix, and salted forms whose salt sits in the same file
    (JSON field or source assignment, either order, or an HMAC keyed by it),
    and the allowlist never covers one; an HMAC keyed by an external secret,
    and unrelated hex beside a salt field, pass;
  * the committed tree carries no plain name-hash list and no name digest.
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import importlib.util
import io
import json
import os
import secrets
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
TEST_KEY = "selftest-" + secrets.token_hex(16)   # throwaway; never the real key


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


checks: list[tuple[str, bool]] = []

# ── canonical form and matching ────────────────────────────────────────────
same = {gate.canonical(v) for v in ("Orrin Fettlewick-Paine", "ORRIN fettlewick_paine",
                                    "orrin  Fettlewick—Paine", "Orrin Fettlewick'-Paine")}
checks.append(("canonical form ignores case, punctuation and separators", same == {"orrin fettlewick paine"}))
checks.append(("an apostrophe is dropped, not split", gate.canonical("O'Quaxmire") == "oquaxmire"))

names = gate.NameList.from_names(PLANTED)
checks.append(("a planted name is found mid-sentence",
               [ln for ln, _ in names.hits("x\nmet Zebulon Quaxmire today\n")] == [2]))
checks.append(("matching is token-bounded (no hit inside a longer word)",
               list(names.hits("zebulonquaxmire and Zebulon Quaxmires")) == []))
checks.append(("a hyphenated CamelCase path form is found",
               len(list(names.hits("DNA/Orrin-Fettlewick-Paine.md"))) == 1))
checks.append(("an unlisted name is not found", list(names.hits("Zebulon Smith")) == []))

doc = gate.build_hmac_doc(PLANTED, TEST_KEY.encode())
blob = json.dumps(doc)
plain = [sha(gate.canonical(n)) for n in PLANTED] + [sha(gate.canonical(n).split()[0]) for n in PLANTED]
checks.append(("the HMAC file carries no plain sha256 of any name or first token",
               not any(p in blob for p in plain)))
hn = gate.NameList.from_hmacs(doc, TEST_KEY.encode())
checks.append(("HMAC mode finds the same names", len(list(hn.hits("glimmerstone DENTAL arts"))) == 1))


# ── end to end, through a real git repository ───────────────────────────────
def run_gate(repo: Path, allow: list[dict], *, local: bool = True, key: str | None = None,
             hmacs: dict | None = None) -> tuple[int, str]:
    names_f = repo.parent / "names.local.txt"
    names_f.write_text("\n".join(PLANTED) + "\n")
    hmacs_f = repo.parent / "hmacs.json"
    if hmacs is not None:
        hmacs_f.write_text(json.dumps(hmacs))
    elif hmacs_f.exists():
        hmacs_f.unlink()
    allow_f = repo.parent / "allow.json"
    allow_f.write_text(json.dumps({"entries": allow}))
    saved = {k: os.environ.get(k) for k in (gate.NAMES_ENV, gate.KEY_ENV, "GITHUB_ACTIONS")}
    # A path that does not exist disables the ~/carr-system fallback too.
    os.environ[gate.NAMES_ENV] = str(names_f) if local else str(repo.parent / "absent.txt")
    os.environ.pop("GITHUB_ACTIONS", None)
    if key is None:
        os.environ.pop(gate.KEY_ENV, None)
    else:
        os.environ[gate.KEY_ENV] = key
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = gate.main(["--repo", str(repo), "--hmacs", str(hmacs_f), "--allow", str(allow_f)])
            except SystemExit as e:
                rc = int(e.code or 0)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return rc, out.getvalue() + err.getvalue()


def no_name_in(text: str) -> bool:
    low = text.lower().replace("zebulon_quaxmire-intake.txt", "")
    return not any(t in low for t in ("glimmerstone", "quaxmire", "fettlewick"))


with tempfile.TemporaryDirectory(prefix="no-client-names-") as tmp:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    env = ENV
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / "clean.md").write_text("A dentist in the panhandle, no names here.\n")
    subprocess.run(["git", "-C", str(repo), "add", "clean.md"], check=True, env=env)
    rc, _ = run_gate(repo, [])
    checks.append(("a clean tree passes (local mode)", rc == 0))

    (repo / "notes.md").write_text("Follow up with glimmerstone DENTAL arts on Friday.\n")
    (repo / "Zebulon_Quaxmire-intake.txt").write_text("nothing\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    rc, text = run_gate(repo, [])
    checks.append(("local mode: a planted name in contents FAILS the gate", rc == 1 and "notes.md:1" in text))
    checks.append(("local mode: a planted name in a file path FAILS the gate",
                   "Zebulon_Quaxmire-intake.txt:path" in text))
    checks.append(("local mode: the failure output never prints the name", no_name_in(text)))

    rc, text = run_gate(repo, [], local=False, key=TEST_KEY, hmacs=doc)
    checks.append(("HMAC mode: a planted name FAILS the gate", rc == 1 and "notes.md:1" in text))
    checks.append(("HMAC mode: the failure output never prints the name", no_name_in(text)))

    rc, text = run_gate(repo, [], local=False, key="selftest-wrong-" + secrets.token_hex(8), hmacs=doc)
    checks.append(("HMAC mode: a wrong key fails loudly (key_check)", rc == 1 and "key_check" in text))

    rc, text = run_gate(repo, [], local=False, key=None)
    checks.append(("no local list and no key: SKIPS LOUDLY with exit 0",
                   rc == 0 and "WARNING no-client-names-gate SKIPPED" in text))
    rc, text = run_gate(repo, [], local=False, key=TEST_KEY, hmacs=None)
    checks.append(("key set but no HMAC file: SKIPS LOUDLY", rc == 0 and "SKIPPED" in text))

    pin = lambda p: hashlib.sha256((repo / p).read_bytes()).hexdigest()  # noqa: E731
    allow = [{"path": "notes.md", "file_sha256": pin("notes.md"), "reason": "test"},
             {"path": "Zebulon_Quaxmire-intake.txt", "file_sha256": pin("Zebulon_Quaxmire-intake.txt"),
              "reason": "test"}]
    rc, _ = run_gate(repo, allow)
    checks.append(("allowlisted, content-pinned files pass", rc == 0))

    (repo / "notes.md").write_text("Follow up with glimmerstone DENTAL arts on Monday.\n")
    rc, text = run_gate(repo, allow)
    checks.append(("an EDITED pinned file is no longer covered", rc == 1 and "notes.md:1" in text))

    (repo / "other.md").write_text("glimmerstone dental arts again\n")
    subprocess.run(["git", "-C", str(repo), "add", "other.md"], check=True, env=env)
    rc, text = run_gate(repo, [dict(allow[0], file_sha256=pin("notes.md")), allow[1]])
    checks.append(("an allowlist entry covers only its own file", rc == 1 and "other.md:1" in text))

    try:
        run_gate(repo, [{"path": "notes.md", "sha256": sha("glimmerstone dental arts"), "reason": "x"}])
        refused = False
    except ValueError:
        refused = True
    checks.append(("an allowlist entry carrying a name digest is refused", refused))

# ── planted name DIGESTS (the 2026-09-24 defect class, twice in one day) ────
salt = "s" + secrets.token_hex(15)
leak_cases = {
    "plain sha256 of a name is caught":
        ("plain.json", json.dumps({"hashes": [sha("zebulon quaxmire")]})),
    "a truncated (12-hex) sha256 prefix is caught":
        ("prefix.txt", "id = " + sha("orrin fettlewick paine")[:12] + "\n"),
    "md5 and sha1 are caught too":
        ("other.js", "const a='" + hashlib.md5(b"zebulon quaxmire").hexdigest() + "';\nconst b='"
         + hashlib.sha1(b"glimmerstone dental arts").hexdigest() + "';\n"),
    "a salt stored in the same file does not hide the name (salt\\0name, first 20 hex)":
        ("salted.json", json.dumps({"salt": salt, "hashes": [
            hashlib.sha256((salt + "\0" + "glimmerstone dental arts").encode()).hexdigest()[:20]]})),
    "name+salt order and an in-file HMAC 'key' are caught":
        ("salted2.json", json.dumps({"pepper": salt, "a": hashlib.sha256(("zebulon quaxmire:" + salt).encode()).hexdigest(),
                                     "b": hmac_mod.new(salt.encode(), b"orrin fettlewick paine", hashlib.sha256).hexdigest()})),
    "a salt assigned in source code does not hide the name":
        ("salted.py", f'NAME_SALT = "{salt}"\nKNOWN = ["' + hashlib.sha1((salt + "zebulon quaxmire").encode()).hexdigest() + '"]\n'),
}
with tempfile.TemporaryDirectory(prefix="no-client-names-digest-") as tmp:
    for label, (fname, body) in leak_cases.items():
        repo = Path(tmp) / fname.replace(".", "-")
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=ENV)
        (repo / fname).write_text(body)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=ENV)
        rc, text = run_gate(repo, [])
        checks.append((label, rc == 1 and "name DIGEST" in text and no_name_in(text)))
        file_pin = hashlib.sha256((repo / fname).read_bytes()).hexdigest()
        rc, _ = run_gate(repo, [{"path": fname, "file_sha256": file_pin, "reason": "test"}])
        if fname == "plain.json":
            checks.append(("the allowlist never covers a name digest", rc == 1))

    # The one allowed form: HMAC keyed by a secret held OUTSIDE the repo.
    repo = Path(tmp) / "external-hmac"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=ENV)
    (repo / "hmacs.json").write_text(json.dumps(doc))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=ENV)
    rc, text = run_gate(repo, [])
    checks.append(("an HMAC keyed by an external secret is NOT flagged", rc == 0))

    repo = Path(tmp) / "unrelated-hex"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=ENV)
    (repo / "lock.json").write_text(json.dumps({"salt": "abcd1234", "integrity": sha("left-pad 1.3.0"),
                                                "commit": secrets.token_hex(20)}))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=ENV)
    rc, _ = run_gate(repo, [])
    checks.append(("unrelated hex beside a salt field is not flagged", rc == 0))

# ── the committed tree ──────────────────────────────────────────────────────
checks.append(("no plain name-hash list is committed",
               not (HERE / "config" / "client-name-hashes.v1.json").exists()))
allow_doc = json.loads((HERE / "config" / "client-name-allowlist.v2.json").read_text())
checks.append(("every committed allowlist entry is a file with a reason, no name digest",
               all(set(e) <= {"path", "file_sha256", "reason"} and e.get("reason")
                   for e in allow_doc["entries"])))
hm = HERE / "config" / "client-name-hmacs.v1.json"
if hm.exists():
    hdoc = json.loads(hm.read_text())
    checks.append(("the committed HMAC file carries key_check and keyed digests only",
                   set(hdoc) >= {"key_check", "hmac_sha256", "first_token_hmac_sha256", "max_tokens"}
                   and "sha256" not in hdoc))

failed = [label for label, ok in checks if not ok]
for label, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
print(f"no-client-names-gate-selftest: {len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)

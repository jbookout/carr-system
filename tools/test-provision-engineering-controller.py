#!/usr/bin/env python3
"""Offline checks for tools/provision-engineering-controller.py. No Cloudflare, no controller run: wrangler and the
dispatch launcher are replaced by recording fakes, and the env file goes to a temporary folder."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("provision", os.path.join(HERE, "provision-engineering-controller.py"))
assert spec and spec.loader
pv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pv)
FAILED: list[str] = []


def check(label, fn):
    try:
        fn()
        print(f"  ok    {label}")
    except AssertionError as exc:
        FAILED.append(label)
        print(f"  FAIL  {label}: {exc}")


def done(code=0, out="", err=""):
    return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


def test_secret_goes_on_stdin_as_the_codex_map_never_argv():
    calls = []
    pv.put_secret("tok-123", run=lambda argv, **kw: calls.append((argv, kw)) or done())
    argv, kw = calls[0]
    assert argv[-3:] == ["secret", "put", "ENGINEERING_CONTROLLER_TOKENS"], argv
    assert "tok-123" not in " ".join(argv)
    assert json.loads(kw["input"]) == {"codex": "tok-123"}


def test_env_file_is_private_and_in_the_launchers_literal_form():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "carr", "engineering-controller.env")
        pv.write_env("tok-abc", path)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        lines = open(path).read().splitlines()
        assert lines == ["CARR_ENGINEERING_WORKER_URL=https://api.doctorcre.com/mcp",
                         "CARR_ENGINEERING_CONTROLLER_TOKEN=tok-abc"], lines
        assert pv.env_state(path) == "present, mode 600"


def test_env_state_flags_a_readable_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "e.env")
        pv.write_env("t", path)
        os.chmod(path, 0o644)
        assert "INVALID" in pv.env_state(path)
        assert pv.env_state(os.path.join(d, "none")) == "missing"


def test_failed_put_reports_one_line_without_the_value():
    try:
        pv.put_secret("tok-secret", run=lambda argv, **kw: done(1, err="Authentication error\nmore"))
    except RuntimeError as exc:
        assert "tok-secret" not in str(exc) and "Authentication error" in str(exc)
    else:
        raise AssertionError("expected a failure")


def test_verify_reads_the_controller_readback():
    ok, detail = pv.verify(run=lambda argv, **kw: done(0, out='{"ok": true, "claimed": 0}'))
    assert ok and "claimed 0" in detail
    ok, detail = pv.verify(run=lambda argv, **kw: done(78, err="engineering-dispatch: X is required"))
    assert not ok and "exit 78" in detail


def test_tokens_are_long_and_fresh():
    a, b = pv.new_token(), pv.new_token()
    assert len(a) >= 40 and a != b


def main():
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            check(name[5:].replace("_", " "), fn)
    print("provision-engineering-controller: " + (f"{len(FAILED)} failed" if FAILED else "every assertion held"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Hermetic adversarial checks for the historical source input validator."""
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
from pathlib import Path

path = Path(__file__).with_name("validate-exact-recovery-source.py")
spec = importlib.util.spec_from_file_location("source_validator", path)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw) / "source"
    (root / "mcp-server" / "src").mkdir(parents=True)
    (root / "dealroom").mkdir()
    (root / "mcp-server" / "wrangler.toml").write_text("name = 'test'\n", encoding="utf-8")
    (root / "mcp-server" / "src" / "tools.js").write_text("export const TOOLS = {};\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "source"], check=True)
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", "-q"], check=True)
    assert mod.validate(str(root), sha) == root.resolve()
    for bad_root, bad_sha in ((str(root), "f" * 40), (str(root / "mcp-server"), sha)):
        try:
            mod.validate(bad_root, bad_sha)
        except ValueError:
            pass
        else:
            raise AssertionError("unbound or non-root source must refuse")
    (root / "mcp-server" / "src" / "tools.js").write_text("dirty\n", encoding="utf-8")
    try:
        mod.validate(str(root), sha)
    except ValueError as exc:
        assert "uncommitted" in str(exc)
    else:
        raise AssertionError("dirty source must refuse")
    subprocess.run(["git", "-C", str(root), "checkout", "--", "mcp-server/src/tools.js"], check=True)
    (root / "mcp-server" / ".dev.vars").write_text("SECRET=not-shippable\n", encoding="utf-8")
    try:
        mod.validate(str(root), sha)
    except ValueError as exc:
        assert "ignored" in str(exc)
    else:
        raise AssertionError("ignored source input must refuse")

print("exact recovery source: detached, clean, exact roots only")

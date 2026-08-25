#!/usr/bin/env python3
# ci: unit
"""Self-test the Hermes execution-environment conformance probe."""

from __future__ import annotations

import importlib.util
import pathlib
import tempfile


SOURCE = pathlib.Path(__file__).with_name("hermes-execution-environment-check.py")
SPEC = importlib.util.spec_from_file_location("hermes_environment_check", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fake_tree(root: pathlib.Path, backend: str = "local") -> tuple[pathlib.Path, pathlib.Path]:
    binary = root / "hermes"
    binary.write_text(
        "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 'Hermes Agent v0.20.5 (fixture)'; exit 0; fi\n"
        f"if [ \"$1\" = \"config\" ]; then echo '{backend}'; exit 0; fi\nexit 1\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    source = root / "source" / "tools" / "environments"
    source.mkdir(parents=True)
    (source / "local.py").write_text(
        "class LocalEnvironment:\n    def cleanup(self): pass\n    def _kill_process(self, proc): pass\n",
        encoding="utf-8",
    )
    (source / "base.py").write_text("class BaseEnvironment:\n    pass\n", encoding="utf-8")
    return binary, root / "source"


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        binary, source = fake_tree(pathlib.Path(temporary))
        passed = MODULE.check(binary, source)
        assert passed["status"] == "passed"
        assert passed["contains_secrets"] is False
        assert passed["run_digest"].startswith("sha256:")
    with tempfile.TemporaryDirectory() as temporary:
        binary, source = fake_tree(pathlib.Path(temporary), "remote")
        failed = MODULE.check(binary, source)
        assert failed["status"] == "failed"
        assert failed["check_results"]["check:terminal-backend-local"] is False
    print("hermes-execution-environment-check selftest: 2 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

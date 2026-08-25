#!/usr/bin/env python3
# ci: unit
"""Self-test the Hermes execution-environment conformance probe."""

from __future__ import annotations

import importlib.util
import hashlib
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
        local_source = source / "tools" / "environments" / "local.py"
        expected = "sha256:" + hashlib.sha256(local_source.read_bytes()).hexdigest()
        passed = MODULE.check(binary, source, expected_implementation_digest=expected)
        assert passed["status"] == "passed"
        assert passed["contains_secrets"] is False
        assert passed["implementation_digest"] == expected
        assert passed["run_digest"].startswith("sha256:")
        original_run_digest = passed["run_digest"]
        local_source.write_text(
            "class LocalEnvironment:\n    def cleanup(self): pass\n    def _kill_process(self, proc): return 0\n",
            encoding="utf-8",
        )
        mutated = MODULE.check(binary, source, expected_implementation_digest=expected)
        assert mutated["status"] == "failed"
        assert mutated["check_results"]["check:implementation-digest-exact"] is False
        assert mutated["run_digest"] != original_run_digest
    with tempfile.TemporaryDirectory() as temporary:
        binary, source = fake_tree(pathlib.Path(temporary), "remote")
        local_source = source / "tools" / "environments" / "local.py"
        expected = "sha256:" + hashlib.sha256(local_source.read_bytes()).hexdigest()
        failed = MODULE.check(binary, source, expected_implementation_digest=expected)
        assert failed["status"] == "failed"
        assert failed["check_results"]["check:terminal-backend-local"] is False
    with tempfile.TemporaryDirectory() as temporary:
        binary, source = fake_tree(pathlib.Path(temporary))
        local_source = source / "tools" / "environments" / "local.py"
        expected = "sha256:" + hashlib.sha256(local_source.read_bytes()).hexdigest()
        with local_source.open("a", encoding="utf-8") as handle:
            handle.write("\napi_key = 'fixture-secret-material'\n")
        leaked = MODULE.check(binary, source, expected_implementation_digest=expected)
        assert leaked["status"] == "failed"
        assert leaked["contains_secrets"] is True
        assert leaked["check_results"]["check:source-secret-scan"] is False
    print("hermes-execution-environment-check selftest: 4 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

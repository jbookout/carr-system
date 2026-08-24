#!/usr/bin/env python3
"""Deterministic acceptance tests for doctorcre-production-smoke.py."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("smoke", HERE / "doctorcre-production-smoke.py")
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def reply(status: int, body: object = b"", location: str | None = None) -> Any:
    headers = {"Location": location} if location else {}
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return smoke.Reply(status, headers, raw)


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        wrangler = Path(directory) / "wrangler.toml"
        wrangler.write_text(
            'routes = [{ pattern = "api.practicecre.com" }, { pattern = "api.doctorcre.com" }, { pattern = "dealroom.doctorcre.com" }]\n',
            encoding="utf-8",
        )
        calls: list[str] = []

        def good_reader(url: str, timeout: int = 15) -> Any:
            calls.append(url)
            if url.endswith("/release"):
                return reply(200, {"ok": True, "env": {"value": "production"}, "verb_count": 12})
            return reply(302, location="/auth/login?return_to=%2F")

        failures = smoke.run("https://api.doctorcre.com", "https://dealroom.doctorcre.com",
                             wrangler, "production", 10, good_reader)
        check(not failures, f"healthy fixture failed: {failures}")
        check(calls == ["https://api.doctorcre.com/release", "https://dealroom.doctorcre.com/"],
              f"unexpected requests: {calls}")

        def bad_reader(url: str, timeout: int = 15) -> Any:
            if url.endswith("/release"):
                return reply(200, {"ok": True, "env": {"value": "staging"}, "verb_count": 0})
            return reply(200, {"html": "login"})

        failures = smoke.run("https://api.doctorcre.com", "https://dealroom.doctorcre.com",
                             wrangler, "production", 10, bad_reader)
        check(any("environment" in item for item in failures), "wrong environment was not caught")
        check(any("verb_count" in item for item in failures), "verb floor was not caught")
        check(any("auth" in item for item in failures), "missing auth redirect was not caught")

        failures = smoke.host_result(wrangler, "https://evil.invalid", "https://dealroom.doctorcre.com")
        check(any("API URL origin" in item for item in failures), "wrong API host was not caught")
        check(any("API URL origin" in item for item in smoke.host_result(
            wrangler, "http://api.doctorcre.com", "https://dealroom.doctorcre.com")),
            "insecure API origin was accepted")
        check(any("Deal Room URL origin" in item for item in smoke.host_result(
            wrangler, "https://api.doctorcre.com", "https://dealroom.doctorcre.com:8443")),
            "nonstandard Deal Room port was accepted")

        check(not smoke.auth_result(
            reply(302, location="https://dealroom.doctorcre.com/auth/login?return_to=%2F"),
            "https://dealroom.doctorcre.com"), "same-origin absolute auth redirect was refused")
        check(any("same-origin" in item for item in smoke.auth_result(
            reply(302, location="https://attacker.invalid/auth/login"),
            "https://dealroom.doctorcre.com")), "cross-origin auth redirect was accepted")
        check(any("same-origin" in item for item in smoke.auth_result(
            reply(302, location="https://dealroom.doctorcre.com:8443/auth/login"),
            "https://dealroom.doctorcre.com")), "nonstandard-port auth redirect was accepted")
        print("doctorcre-production-smoke-selftest: 3/3 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

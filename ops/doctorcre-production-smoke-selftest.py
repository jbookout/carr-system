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
            'routes = [{ pattern = "api.practicecre.com" }, { pattern = "api.doctorcre.com" }, { pattern = "dealroom.doctorcre.com" }, { pattern = "reports.doctorcre.com" }]\n',
            encoding="utf-8",
        )
        calls: list[str] = []

        def good_reader(url: str, timeout: int = 15) -> Any:
            calls.append(url)
            if url.endswith("/release"):
                return reply(200, {"ok": True, "env": {"value": "production"}, "verb_count": 12})
            if url.endswith("/app-release"):
                return reply(200, {
                    "service": "doctorcre-app",
                    "environment": "production",
                    "source_commit": "a" * 40,
                    "provider_version_id": "12345678-1234-4123-8123-123456789abc",
                    "carr_contract": {"schema": "doctorcre-carr-interface.v1", "version": "1.1.0"},
                    "route_contract": {"schema": "doctorcre-app-routes.v1", "version": "1.0.0"},
                })
            if url.startswith("https://app.doctorcre.com/"):
                return reply(302, location="/auth/login?return_to=%2F")
            return reply(302, location="https://app.doctorcre.com/deals")

        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, good_reader)
        check(not failures, f"healthy fixture failed: {failures}")
        check(calls == ["https://api.doctorcre.com/release", "https://app.doctorcre.com/app-release", "https://app.doctorcre.com/", "https://dealroom.doctorcre.com/?stale=1"],
              f"unexpected requests: {calls}")

        def bad_reader(url: str, timeout: int = 15) -> Any:
            if url.endswith("/release"):
                return reply(200, {"ok": True, "env": {"value": "staging"}, "verb_count": 0})
            return reply(200, {"html": "login"})

        failures = smoke.run("https://api.doctorcre.com", "https://app.doctorcre.com",
                             wrangler, "production", 10, bad_reader)
        check(any("environment" in item for item in failures), "wrong environment was not caught")
        check(any("verb_count" in item for item in failures), "verb floor was not caught")
        check(any("auth" in item for item in failures), "missing auth redirect was not caught")
        check(any("/app-release" in item for item in failures), "wrong app release was not caught")

        old_wrangler = Path(directory) / "old-wrangler.toml"
        old_wrangler.write_text(wrangler.read_text() + 'routes = [{ pattern = "app.doctorcre.com" }]\n', encoding="utf-8")
        check(any("still claims" in item for item in smoke.host_result(
            old_wrangler, "https://api.doctorcre.com", "https://app.doctorcre.com")),
            "the prior CARR-owned app host was accepted")

        failures = smoke.host_result(wrangler, "https://evil.invalid", "https://app.doctorcre.com")
        check(any("API URL origin" in item for item in failures), "wrong API host was not caught")
        check(any("API URL origin" in item for item in smoke.host_result(
            wrangler, "http://api.doctorcre.com", "https://app.doctorcre.com")),
            "insecure API origin was accepted")
        check(any("App URL origin" in item for item in smoke.host_result(
            wrangler, "https://api.doctorcre.com", "https://app.doctorcre.com:8443")),
            "nonstandard Deal Room port was accepted")

        check(not smoke.auth_result(
            reply(302, location="https://app.doctorcre.com/auth/login?return_to=%2F"),
            "https://app.doctorcre.com"), "same-origin absolute auth redirect was refused")
        check(any("same-origin" in item for item in smoke.auth_result(
            reply(302, location="https://attacker.invalid/auth/login"),
            "https://app.doctorcre.com")), "cross-origin auth redirect was accepted")
        check(any("same-origin" in item for item in smoke.auth_result(
            reply(302, location="https://app.doctorcre.com:8443/auth/login"),
            "https://app.doctorcre.com")), "nonstandard-port auth redirect was accepted")
        print("doctorcre-production-smoke-selftest: 3/3 passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

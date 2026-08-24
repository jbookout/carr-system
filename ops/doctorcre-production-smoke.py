#!/usr/bin/env python3
"""Read-only smoke check for the public DoctorCRE production surfaces.

The check makes only GET/HEAD requests.  It intentionally does not call
``/mcp`` or any mutation endpoint: its purpose is to answer whether the
configured Worker and Deal Room are the deployment we think they are.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API = "https://api.doctorcre.com"
DEFAULT_DEALROOM = "https://dealroom.doctorcre.com"
DEFAULT_ENVIRONMENT = "production"
# A Worker serving fewer than this is almost certainly an incomplete/old
# deployment.  This is a floor, not the exact count; use /release for identity.
DEFAULT_MIN_VERBS = 140
USER_AGENT = "doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)"


@dataclass
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def read(url: str, opener=_OPENER.open, timeout: int = 15) -> Reply:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with opener(request, timeout=timeout) as response:
            return Reply(response.status, dict(response.headers.items()), response.read())
    except urllib.error.HTTPError as error:
        # Redirects are disabled by the caller for the auth probe, but HTTP
        # errors still carry useful status/headers and should be judged.
        return Reply(error.code, dict(error.headers.items()), error.read())


def release_result(reply: Reply, expected_env: str, minimum_verbs: int) -> list[str]:
    failures: list[str] = []
    try:
        payload = json.loads(reply.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["/release returned non-JSON"]
    if reply.status != 200:
        failures.append(f"/release HTTP {reply.status}")
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        failures.append("/release ok is not true")
    env = payload.get("env") if isinstance(payload, dict) else None
    observed = env.get("value") if isinstance(env, dict) else env
    if observed != expected_env:
        failures.append(f"/release environment {observed!r}, expected {expected_env!r}")
    verbs = payload.get("verb_count") if isinstance(payload, dict) else None
    if not isinstance(verbs, int) or isinstance(verbs, bool) or verbs < minimum_verbs:
        failures.append(f"/release verb_count {verbs!r}, expected integer >= {minimum_verbs}")
    return failures


def auth_result(reply: Reply, dealroom: str) -> list[str]:
    failures: list[str] = []
    if reply.status not in (301, 302, 303, 307, 308):
        failures.append(f"Deal Room unauthenticated request returned HTTP {reply.status}, expected redirect")
        return failures
    location = reply.headers.get("Location", "")
    resolved = urllib.parse.urljoin(dealroom.rstrip("/") + "/", location)
    target = urllib.parse.urlparse(resolved)
    expected = urllib.parse.urlparse(dealroom)
    if not (target.scheme == "https" and target.hostname == expected.hostname
            and target.port in (None, 443) and target.path == "/auth/login"):
        failures.append(
            f"Deal Room redirect target {location!r}, expected same-origin /auth/login")
    return failures


def configured_hosts(wrangler_path: Path) -> list[str]:
    text = wrangler_path.read_text(encoding="utf-8")
    return re.findall(r'pattern\s*=\s*"([^"]+)"', text)


def host_result(wrangler_path: Path, api: str, dealroom: str) -> list[str]:
    expected = {"api.practicecre.com", "api.doctorcre.com", "dealroom.doctorcre.com"}
    configured = set(configured_hosts(wrangler_path))
    failures = [f"wrangler config missing expected host {host}" for host in sorted(expected - configured)]
    api_url = urllib.parse.urlparse(api)
    dealroom_url = urllib.parse.urlparse(dealroom)
    if not (api_url.scheme == "https" and api_url.hostname == "api.doctorcre.com"
            and api_url.port in (None, 443)):
        failures.append(
            f"API URL origin {api_url.scheme!r}, {api_url.hostname!r}, port {api_url.port!r}; "
            "expected canonical https://api.doctorcre.com")
    if not (dealroom_url.scheme == "https" and dealroom_url.hostname == "dealroom.doctorcre.com"
            and dealroom_url.port in (None, 443)):
        failures.append(
            f"Deal Room URL origin {dealroom_url.scheme!r}, {dealroom_url.hostname!r}, "
            "port {dealroom_url.port!r}; expected canonical https://dealroom.doctorcre.com")
    return failures


def run(api: str, dealroom: str, wrangler_path: Path, expected_env: str, minimum_verbs: int,
        reader=read) -> list[str]:
    failures = host_result(wrangler_path, api, dealroom)
    try:
        failures += release_result(reader(api.rstrip("/") + "/release"), expected_env, minimum_verbs)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"/release unreadable ({type(error).__name__}: {error})")
    try:
        # The root is the unauthenticated surface.  /auth/login itself starts
        # the upstream Google flow and therefore redirects somewhere else.
        failures += auth_result(reader(dealroom.rstrip("/") + "/", timeout=15), dealroom)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"Deal Room auth unreadable ({type(error).__name__}: {error})")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--dealroom", default=DEFAULT_DEALROOM)
    parser.add_argument("--wrangler", default="mcp-server/wrangler.toml")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--min-verbs", type=int, default=DEFAULT_MIN_VERBS)
    args = parser.parse_args(argv)
    try:
        failures = run(args.api, args.dealroom, Path(args.wrangler), args.environment, args.min_verbs)
    except (OSError, ValueError) as error:
        print(f"doctorcre-production-smoke: ERROR {error}", file=sys.stderr)
        return 2
    if failures:
        for failure in failures:
            print(f"doctorcre-production-smoke: FAIL {failure}", file=sys.stderr)
        return 1
    print("doctorcre-production-smoke: OK /release, Deal Room auth redirect, environment, verb floor, and host config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

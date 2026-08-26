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
DEFAULT_APP = "https://app.doctorcre.com"
DEFAULT_LEGACY = "https://dealroom.doctorcre.com"
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


def auth_result(reply: Reply, app: str) -> list[str]:
    failures: list[str] = []
    if reply.status not in (301, 302, 303, 307, 308):
        failures.append(f"Deal Room unauthenticated request returned HTTP {reply.status}, expected redirect")
        return failures
    location = reply.headers.get("Location", "")
    resolved = urllib.parse.urljoin(app.rstrip("/") + "/", location)
    target = urllib.parse.urlparse(resolved)
    expected = urllib.parse.urlparse(app)
    if not (target.scheme == "https" and target.hostname == expected.hostname
            and target.port in (None, 443) and target.path == "/auth/login"):
        failures.append(
            f"App redirect target {location!r}, expected same-origin /auth/login")
    return failures


def legacy_result(reply: Reply, app: str) -> list[str]:
    failures: list[str] = []
    if reply.status not in (301, 302, 303, 307, 308):
        return [f"legacy Deal Room request returned HTTP {reply.status}, expected redirect"]
    target = urllib.parse.urlparse(urllib.parse.urljoin(app.rstrip("/") + "/", reply.headers.get("Location", "")))
    expected = urllib.parse.urlparse(app)
    if not (target.scheme == "https" and target.hostname == expected.hostname
            and target.port in (None, 443) and target.path == "/deals" and not target.query):
        failures.append(f"legacy Deal Room redirect {reply.headers.get('Location', '')!r}, expected exact app /deals")
    return failures


def configured_hosts(wrangler_path: Path) -> list[str]:
    text = wrangler_path.read_text(encoding="utf-8")
    return re.findall(r'pattern\s*=\s*"([^"]+)"', text)


def host_result(wrangler_path: Path, api: str, app: str, legacy: str = DEFAULT_LEGACY) -> list[str]:
    expected = {"api.practicecre.com", "api.doctorcre.com", "app.doctorcre.com", "dealroom.doctorcre.com"}
    configured = set(configured_hosts(wrangler_path))
    failures = [f"wrangler config missing expected host {host}" for host in sorted(expected - configured)]
    api_url = urllib.parse.urlparse(api)
    app_url = urllib.parse.urlparse(app)
    if not (api_url.scheme == "https" and api_url.hostname == "api.doctorcre.com"
            and api_url.port in (None, 443)):
        failures.append(
            f"API URL origin {api_url.scheme!r}, {api_url.hostname!r}, port {api_url.port!r}; "
            "expected canonical https://api.doctorcre.com")
    if not (app_url.scheme == "https" and app_url.hostname == "app.doctorcre.com"
            and app_url.port in (None, 443)):
        failures.append(
            f"App URL origin {app_url.scheme!r}, {app_url.hostname!r}, "
            f"port {app_url.port!r}; expected canonical https://app.doctorcre.com")
    legacy_url = urllib.parse.urlparse(legacy)
    if not (legacy_url.scheme == "https" and legacy_url.hostname == "dealroom.doctorcre.com"
            and legacy_url.port in (None, 443)):
        failures.append(
            f"legacy URL origin {legacy_url.scheme!r}, {legacy_url.hostname!r}, "
            f"port {legacy_url.port!r}; expected https://dealroom.doctorcre.com")
    return failures


def run(api: str, app: str, wrangler_path: Path, expected_env: str, minimum_verbs: int,
        reader=read, legacy: str = DEFAULT_LEGACY) -> list[str]:
    failures = host_result(wrangler_path, api, app, legacy)
    try:
        failures += release_result(reader(api.rstrip("/") + "/release"), expected_env, minimum_verbs)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"/release unreadable ({type(error).__name__}: {error})")
    try:
        # The root is the unauthenticated surface.  /auth/login itself starts
        # the upstream Google flow and therefore redirects somewhere else.
        failures += auth_result(reader(app.rstrip("/") + "/", timeout=15), app)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"App auth unreadable ({type(error).__name__}: {error})")
    try:
        failures += legacy_result(reader(legacy.rstrip("/") + "/?stale=1", timeout=15), app)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        failures.append(f"legacy Deal Room unreadable ({type(error).__name__}: {error})")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--app", "--dealroom", dest="app", default=DEFAULT_APP)
    parser.add_argument("--legacy", default=DEFAULT_LEGACY)
    parser.add_argument("--wrangler", default="mcp-server/wrangler.toml")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--min-verbs", type=int, default=DEFAULT_MIN_VERBS)
    args = parser.parse_args(argv)
    try:
        failures = run(args.api, args.app, Path(args.wrangler), args.environment, args.min_verbs, legacy=args.legacy)
    except (OSError, ValueError) as error:
        print(f"doctorcre-production-smoke: ERROR {error}", file=sys.stderr)
        return 2
    if failures:
        for failure in failures:
            print(f"doctorcre-production-smoke: FAIL {failure}", file=sys.stderr)
        return 1
    print("doctorcre-production-smoke: OK /release, app auth redirect, legacy redirect, environment, verb floor, and host config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

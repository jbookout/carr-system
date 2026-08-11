#!/usr/bin/env python3
"""Post-cutoff connector/store parity for the Report Card.

Both paths compare the authenticated standing-context output with the direct
store. Primary uses the exporter's canonical rule query. Independent uses raw
SQL against the compiled-rules view. Neither path reads compiled Markdown.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

HTTP_STATUS_MARKER = "CARR_HTTP_STATUS:"


def _redact(value, secret):
    """Keep connector credentials out of every surfaced diagnostic."""
    text = "" if value is None else str(value)
    return text.replace(secret, "[REDACTED]") if secret else text


def _curl_config_quote(value):
    """Quote one curl-config value without placing it in process argv."""
    return ('"' + str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\r", "\\r").replace("\n", "\\n") + '"')


def _connector_request(api, token, payload):
    # The bearer header is supplied through curl's stdin config. It must never
    # appear in argv, where process listings and TimeoutExpired.cmd expose it.
    config = "\n".join((
        # Boolean curl-config options are standalone. `option = true` is not
        # portable across estate curl versions and exits 26 while parsing.
        "silent",
        "show-error",
        "fail-with-body",
        "max-time = 30",
        f"url = {_curl_config_quote(api)}",
        'request = "POST"',
        f"header = {_curl_config_quote('Authorization: Bearer ' + token)}",
        'header = "content-type: application/json"',
        f"data = {_curl_config_quote(payload)}",
        "",
    ))
    argv = [
        "curl", "--config", "-", "--write-out",
        f"\n{HTTP_STATUS_MARKER}%{{http_code}}\n",
    ]
    try:
        proc = subprocess.run(
            argv, input=config, capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        # Do not interpolate exc: its output/stderr fields are untrusted and a
        # future curl build or test fixture could echo the stdin config.
        raise RuntimeError("standing-context transport timed out after 40s") from None

    stdout = proc.stdout or ""
    marker = f"\n{HTTP_STATUS_MARKER}"
    if marker not in stdout:
        detail = _redact(proc.stderr or "no HTTP status returned", token)
        detail = detail.strip().splitlines()[-1] if detail.strip() else "no detail"
        raise RuntimeError(
            f"standing-context transport failed (rc={proc.returncode}): {detail}")
    body, raw_status = stdout.rsplit(marker, 1)
    status_text = raw_status.strip()
    if not (len(status_text) == 3 and status_text.isdigit()):
        raise RuntimeError("standing-context transport returned an invalid HTTP status")
    status = int(status_text)
    if status == 0:
        detail = _redact(proc.stderr or "no HTTP response", token)
        detail = detail.strip().splitlines()[-1] if detail.strip() else "no detail"
        raise RuntimeError(
            f"standing-context transport failed (rc={proc.returncode}): {detail}")
    if status == 401:
        raise RuntimeError("standing-context HTTP authentication failed (status=401)")
    if status == 403:
        raise RuntimeError("standing-context HTTP authorization failed (status=403)")
    if status < 200 or status >= 300:
        raise RuntimeError(f"standing-context HTTP failed (status={status})")
    if proc.returncode != 0:
        detail = _redact(proc.stderr or "no detail", token)
        detail = detail.strip().splitlines()[-1] if detail.strip() else "no detail"
        raise RuntimeError(
            f"standing-context transport failed (rc={proc.returncode}): {detail}")
    return body


def _env_value(name):
    value = os.environ.get(name)
    if value:
        return value
    env_path = Path(os.environ.get(
        "CARR_MCP_ENV", str(Path.home() / ".config/carr/mcp-tokens.env")))
    if not env_path.exists():
        return None
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, candidate = line.split("=", 1)
        if key.strip() == name:
            return candidate.strip().strip("'\"")
    return None


def connector_shared_count():
    token = _env_value("CARR_MCP_PROBE_TOKEN")
    if not token:
        raise RuntimeError("CARR_MCP_PROBE_TOKEN unavailable for standing-context parity")
    api = os.environ.get("CARR_MCP_URL", "https://api.practicecre.com/mcp")
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "standing-context", "arguments": {}},
    })
    # Use the estate's established curl transport. Cloudflare returns error
    # 1010 to Python urllib before the request reaches the Worker. The secret
    # travels only through curl-config stdin, never argv or surfaced errors.
    body = _connector_request(api, token, payload)
    envelope = json.loads(body)
    if envelope.get("error") is not None:
        detail = _redact(json.dumps(envelope["error"], sort_keys=True), token)
        raise RuntimeError(f"standing-context JSON-RPC error: {detail}")
    result_envelope = envelope.get("result")
    if not isinstance(result_envelope, dict):
        raise RuntimeError("standing-context returned no JSON-RPC result object")
    if result_envelope.get("isError") is True:
        details = [block.get("text", "") for block in result_envelope.get("content", [])
                   if isinstance(block, dict) and block.get("type") == "text"]
        detail = _redact(" ".join(details) or "no tool detail", token)
        raise RuntimeError(f"standing-context MCP tool error (isError=true): {detail}")
    for block in result_envelope.get("content", []):
        if block.get("type") != "text":
            continue
        try:
            result = json.loads(block.get("text", ""))
        except json.JSONDecodeError:
            continue
        if result.get("ok") is True and isinstance(result.get("shared_rules"), list):
            return len(result["shared_rules"])
    raise RuntimeError("standing-context returned no valid shared_rules payload")


def direct_store_count_primary():
    from exporters.common import connect
    from exporters.targets import _fetch_rules

    try:
        connection = connect()
    except SystemExit as exc:
        raise RuntimeError(f"rule store unavailable: {exc}") from exc
    with connection, connection.cursor() as cursor:
        return len(_fetch_rules(cursor, None))


def direct_store_count_independent():
    from exporters.common import connect

    try:
        connection = connect()
    except SystemExit as exc:
        raise RuntimeError(f"rule store unavailable: {exc}") from exc
    with connection, connection.cursor() as cursor:
        cursor.execute(
            "select count(*) from v_compiled_rules "
            "where personal_to is null "
            "and coalesce(scope->>'kind','') <> 'intro_politics'")
        row = cursor.fetchone()
    if not row or not isinstance(row[0], int):
        raise RuntimeError("direct store returned no numeric shared-rule count")
    return row[0]


def measure(independent=False):
    connector_count = connector_shared_count()
    store_count = (direct_store_count_independent() if independent
                   else direct_store_count_primary())
    return abs(connector_count - store_count)


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--primary", action="store_true")
    mode.add_argument("--independent", action="store_true")
    args = parser.parse_args()
    try:
        value = measure(independent=args.independent)
    except Exception as exc:
        print(f"UNKNOWN rules-context parity: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""provision-engineering-controller.py — give the Engineering Passport controller its Worker credential, without
the value ever being displayed, logged, or placed on a command line.

WHY. The Model Room bridge runs bin/run-engineering-dispatch.sh after every cycle, and on 2026-09-25 it had failed
on every cycle (954 logged failures) with "isolated Worker controller configuration is required": the controller's
bearer had never been provisioned, on either Mac, and the Worker had no ENGINEERING_CONTROLLER_TOKENS secret. So
admitted engineering jobs were never claimed unattended (loop #646).

WHAT IT DOES, in this order:
  1. generates a 48-character URL-safe token in-process;
  2. sets the Worker secret ENGINEERING_CONTROLLER_TOKENS = {"codex": "<token>"} through `wrangler secret put`,
     with the value on stdin (mcp-server/wrangler.toml documents the map; the Worker resolves it to the codex actor
     and opens only the four canonical-ownership operations);
  3. writes ~/.config/carr/engineering-controller.env (mode 0600, atomic replace) with the Worker's /mcp address
     and the token, in the literal KEY=value form bin/run-engineering-dispatch.sh reads without sourcing;
  4. unless --no-verify, runs bin/run-engineering-dispatch.sh once and reports only its outcome.

Running it again rotates: the Worker secret is replaced first, so the old token stops working at once.

    .venv/bin/python tools/provision-engineering-controller.py            # provision or rotate, then verify
    .venv/bin/python tools/provision-engineering-controller.py --check    # report state only; changes nothing

Needs Cloudflare access for the carr-mcp Worker (`wrangler whoami`). Exit 0 done, 1 a step failed, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(REPO, "mcp-server")
WRANGLER = os.path.join(MCP, "node_modules", ".bin", "wrangler")
ENV_FILE = os.environ.get("CARR_ENGINEERING_CONTROLLER_ENV_FILE",
                          os.path.expanduser("~/.config/carr/engineering-controller.env"))
WORKER_URL = "https://api.doctorcre.com/mcp"
SECRET_NAME = "ENGINEERING_CONTROLLER_TOKENS"
ACTOR = "codex"
DISPATCH = os.path.join(REPO, "bin", "run-engineering-dispatch.sh")


def new_token() -> str:
    return secrets.token_urlsafe(36)  # 48 characters


def secret_names(run=subprocess.run) -> set[str]:
    p = run([WRANGLER, "secret", "list", "--format", "json"], cwd=MCP, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError("wrangler secret list failed (is Cloudflare signed in? `wrangler whoami`)")
    return {row.get("name") for row in json.loads(p.stdout)}


def put_secret(token: str, run=subprocess.run) -> None:
    value = json.dumps({ACTOR: token}, separators=(",", ":"))
    p = run([WRANGLER, "secret", "put", SECRET_NAME], cwd=MCP, input=value, capture_output=True, text=True,
            timeout=180)
    if p.returncode != 0:
        # wrangler's own error text never contains the stdin value; still, report only the first line
        raise RuntimeError("wrangler secret put failed: " + ((p.stderr or p.stdout).strip().splitlines() or [""])[0][:300])


def write_env(token: str, path: str = ENV_FILE) -> None:
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    body = f"CARR_ENGINEERING_WORKER_URL={WORKER_URL}\nCARR_ENGINEERING_CONTROLLER_TOKEN={token}\n"
    fd, tmp = tempfile.mkstemp(prefix=".engineering-controller.", dir=os.path.dirname(path))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def env_state(path: str = ENV_FILE) -> str:
    if not os.path.exists(path):
        return "missing"
    mode = os.stat(path).st_mode & 0o777
    keys = set()
    with open(path) as fh:
        for line in fh:
            if "=" in line and not line.startswith("#"):
                keys.add(line.split("=", 1)[0].strip())
    ok = {"CARR_ENGINEERING_WORKER_URL", "CARR_ENGINEERING_CONTROLLER_TOKEN"} <= keys
    return f"present, mode {mode:o}" + ("" if ok and not mode & 0o077 else " (INVALID: needs both keys and mode 600)")


def verify(run=subprocess.run) -> tuple[bool, str]:
    """Run the controller once. It prints one JSON readback on success; its refusals are typed one-liners."""
    p = run(["/bin/zsh", DISPATCH], capture_output=True, text=True, timeout=1900, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        return False, f"exit {p.returncode}: " + ((p.stderr or "").strip().splitlines() or [""])[-1][:300]
    try:
        value = json.loads(p.stdout)
    except ValueError:
        return False, "controller returned unreadable output"
    if value.get("ok") is not True:
        return False, "controller readback was not ok"
    return True, f"controller ok, claimed {value.get('claimed')} job(s)"


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="report state only")
    ap.add_argument("--no-verify", action="store_true", help="skip the controller run afterwards")
    a = ap.parse_args(argv)
    try:
        on_worker = SECRET_NAME in secret_names()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Worker secret {SECRET_NAME}: {'set' if on_worker else 'not set'}")
    print(f"Mac file {ENV_FILE}: {env_state()}")
    if a.check:
        return 0
    token = new_token()
    try:
        put_secret(token)
        print(f"Worker secret {SECRET_NAME}: {'replaced' if on_worker else 'set'}")
        write_env(token)
        print(f"Mac file written (mode 600): {ENV_FILE}")
    except (RuntimeError, OSError) as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        del token  # drop the only reference as soon as both sides hold it
    if a.no_verify:
        return 0
    ok, detail = verify()
    print(("verified: " if ok else "verify FAILED: ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

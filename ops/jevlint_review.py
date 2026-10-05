#!/usr/bin/env python3
"""Advisory PR taste checks, through CARR's single Jev spend authority."""
import argparse
from contextlib import contextmanager
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading

import typesafe_client as ts
from git_env import fixture_env, scrubbed_env

ROOT = Path(__file__).resolve().parents[1]
INSTALL = json.loads((ROOT / "ops/config/jevlint.v1.json").read_text())
PIN = INSTALL["commit"]
MODEL = "jev-1.13.0"
PORT = 18741
LOCAL_MARKER = "carr-jevlint-loopback"


class Shim:
    def __init__(self, session, attribution):
        self.session = session
        self.attribution = attribution
        self.responses = {}
        self.lock = threading.Lock()
        self.requests = 0
        self.answered = 0
        self.refused = 0
        self.errors = 0
        self.cached = 0
        self.paid_attempts = 0

    def evaluate(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {"model", "state", "questions"}
                or payload["model"] != MODEL
                or ts.malformed_request(payload["state"], payload["questions"])):
            return 400, {"error": "invalid_systemone_request"}
        if not self.session or not self.attribution:
            return 403, {"error": "missing_attribution"}
        key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        # A timed-out jevlint request may retry. Serialize and reuse that answer,
        # including errors, so uncertainty never causes another paid attempt.
        with self.lock:
            self.requests += 1
            if key in self.responses:
                self.cached += 1
                return self.responses[key]
            try:
                result = ts.ask(payload["state"], payload["questions"], model=MODEL,
                                caller="jevlint_review", session_id=self.session,
                                facets=["code-taste", self.attribution], retries=0,
                                cache_ttl_seconds=0, timeout=8)
                if result.get("model") != MODEL:
                    raise ts.TypeSafeError("unexpected judgment model")
                result = {k: result[k] for k in ("model", "answers", "usage") if k in result}
                self.answered += 1
                response = (200, result)
            except ts.JevCallRefused as exc:
                self.refused += 1
                response = (403, {"error": exc.code})
            except ts.TypeSafeError:
                self.errors += 1
                # 502 would make upstream retry. An unavailable judgment is
                # terminal here and must never become an advisory clean pass.
                response = (424, {"error": "jev_unavailable"})
            self.responses[key] = response
            return response


@contextmanager
def serve(shim, port=PORT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            status, body = 400, {"error": "invalid_systemone_request"}
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if (self.path == "/v1/systemone" and 0 < size <= 512_000
                        and self.headers.get("Authorization") == "Bearer " + LOCAL_MARKER):
                    status, body = shim.evaluate(json.loads(self.rfile.read(size)))
            except (ValueError, UnicodeError):
                pass
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/systemone"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=scrubbed_env())


def materialize(repo, base, head, workspace, config):
    """Exact head blobs only; clean PR commits become dirty inputs in scratch."""
    paths = git(repo, "diff", "--name-only", "-z", "--diff-filter=ACMRT", base, head).split(b"\0")
    selected = []
    for raw in paths:
        if not raw:
            continue
        name = raw.decode()
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe git path")
        if path.suffix not in (".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".tsx"):
            continue
        mode = git(repo, "ls-tree", head, "--", name).split()[0]
        if mode != b"100644" and mode != b"100755":
            raise ValueError("PR source must be a regular blob")
        dest = workspace / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(git(repo, "show", f"{head}:{name}"))
        selected.append(name)
    (workspace / "jevlint.json").write_bytes(config.read_bytes())
    subprocess.run(["git", "init", "-q", str(workspace)], env=fixture_env(), check=True, capture_output=True)
    return selected


def run_jevlint(binary, workspace, shim, *, evals=None, port=PORT):
    workspace = workspace.resolve()
    env = {k: v for k, v in fixture_env().items() if not k.startswith(
        ("TYPESAFE_", "JEVLINT_", "OPENROUTER_", "CLOUDFLARE_", "CLEF_"))}
    day = datetime.now(timezone.utc).date()
    before = paid_reservations()
    with serve(shim, port) as endpoint:
        env.update(TYPESAFE_ENDPOINT=endpoint, TYPESAFE_API_KEY=LOCAL_MARKER,
                   TYPESAFE_DEFAULT_MODEL=MODEL, JEVLINT_PROVIDER="typesafe")
        args = [str(binary), "eval" if evals else "check", "--format", "json",
                "--concurrency", "1", "--config", str(workspace / "jevlint.json")]
        args += ["--evals", str(evals), "--verbose"] if evals else ["--changed"]
        result = subprocess.run(args, cwd=workspace, env=env, capture_output=True, text=True, timeout=1800)
    if datetime.now(timezone.utc).date() != day:
        raise ValueError("paid ledger rolled over; repeat the cached check for a bound count")
    shim.paid_attempts = paid_reservations() - before
    if result.returncode not in (0, 1, 2):
        raise ValueError("unexpected jevlint exit status")
    if result.returncode == 2 or shim.refused or shim.errors:
        return 2, {"error": "jevlint_unavailable", "refused": shim.refused, "errors": shim.errors,
                   "diagnostic": result.stderr[:2000]}
    return result.returncode, json.loads(result.stdout)


def paid_reservations():
    path = Path(ts._cap_db_path())
    if not path.exists():
        return 0
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='site_usage'").fetchone():
            return 0
        return db.execute("SELECT coalesce(sum(count),0) FROM site_usage WHERE site=?",
                          ("jevlint_review",)).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="go install the exact upstream commit")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--base", help="PR base commit/ref; merge-base with head is used")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--pr", help="repository/PR identity for spend receipts")
    parser.add_argument("--session", default=next((os.environ.get(k) for k in ts.SESSION_ID_ENV_KEYS if os.environ.get(k)), None))
    parser.add_argument("--evals", type=Path, help="bounded calibration cases, outside review check mode")
    parser.add_argument("--config", type=Path, default=ROOT / "jevlint.json")
    args = parser.parse_args()
    try:
        if args.install:
            return subprocess.run(["go", "install", f"github.com/codegirl-007/jevlint/cmd/jevlint@{PIN}"], check=True).returncode
        if not args.session or not args.pr or (not args.evals and not args.base):
            raise ValueError("--session, --pr and --base are required for review")
        binary = shutil.which("jevlint") or str(Path.home() / "go/bin/jevlint")
        info = subprocess.check_output(["go", "version", "-m", binary], text=True)
        if INSTALL["go_module_build"] not in info:
            raise ValueError("install the pinned jevlint with --install")
        repo = args.repo.resolve()
        head = git(repo, "rev-parse", "--verify", args.head + "^{commit}").decode().strip()
        base = git(repo, "merge-base", args.base, head).decode().strip() if args.base else None
        scope = hashlib.sha256(str(repo).encode()).hexdigest()[:24]
        parent = ROOT / "out/jevlint" / scope
        parent.mkdir(parents=True, exist_ok=True)
        with (parent / "run.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            workspace = parent / "workspace"
            if workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir()
            selected = materialize(repo, base, head, workspace, args.config) if base else []
            if not base:
                workspace = args.config.resolve().parent
            shim = Shim(args.session, f"pr:{args.pr}:{head}")
            code, report = run_jevlint(binary, workspace, shim, evals=args.evals.resolve() if args.evals else None)
            print(json.dumps({"schema": "carr-jevlint-review/v1", "advisory": True,
                              "base": base, "head": head, "pr": args.pr, "files": selected,
                              "shim": {"requests": shim.requests, "answered": shim.answered,
                                       "paid_attempts": shim.paid_attempts,
                                       "retry_cache_hits": shim.cached, "refused": shim.refused,
                                       "errors": shim.errors}, "exit_code": code, "report": report}, indent=2))
            return code
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        print(json.dumps({"error": "jevlint_setup_failed", "exit_code": 2}))
        return 2


if __name__ == "__main__":
    sys.exit(main())

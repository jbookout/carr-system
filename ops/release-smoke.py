#!/usr/bin/env python3
"""release-smoke.py — prove the live DoctorCRE system works, read-only.

ops/release-pipeline.py runs this before a release (the baseline) and again
after it (the proof). The pipeline compares the two runs: a journey that worked
before the release and fails after it is the release's fault. That journey
marks the release FAILED and rolls the lane back.

READ-ONLY, BY CONSTRUCTION. Every HTTP request is a GET with redirects off. Every
authenticated journey goes through /mcp as the `smoke-probe` actor (the
CARR_MCP_PROBE_TOKEN bearer in <credential-dir>/mcp-tokens.env). The Worker pins
that actor to the read-only `probe` profile on the server side, and every write
verb answers not_in_profile. This file calls only the read verbs in READ_VERBS.

THE JOURNEYS (JOURNEYS, in this order):
  release-identity  /release and /app-release answer; after a release the
                    lane's live SHA is the released SHA
  sign-in-gate      each core app page (deal board, Leads, invoices, progress
                    board, Dr. CRE chat) redirects to its own
                    /auth/login?return_to=<page>, and /status renders
  deal-board        deal-board answers
  leads-workspace   lead-board answers
  invoices-list     read-invoice-tracker answers
  progress-board    list-progress-boards answers
  dr-cre-chat       list-doc-conversations answers
  verb-registry     list-verbs serves every verb the released Worker SHA carries
                    (--worker-dir); before the release, the missing ones are
                    reported as the verbs this release adds
browser-journeys runs only when --app-dir holds an installed doctorcre-app
checkout. It runs that repository's production journeys
(smoke/production/*.e2e.ts, e2e.production.config.ts) and copies their
screenshots and traces into the evidence folder.

NOT EXERCISED, and it needs an identity: the probe actor is accepted only on
/mcp, so no journey here renders a signed-in app page. NOT_EXERCISED lists them,
and every summary repeats that list.

EVIDENCE. <out>/summary.json (one row per journey: status, milliseconds,
detail, evidence) and <out>/probes.jsonl. MCP evidence records the SHAPE of
each answer (its keys and list lengths), never the record values in it: a
production deal name never lands in out/.

  ops/release-smoke.py --lane worker --sha <sha> --phase post --out out/release-smoke/<sha>/post \
      [--worker-dir <release worktree>/mcp-server] [--app-dir <doctorcre-app checkout>] [--only id,id]

Exit 0 when every journey that ran passed, 1 otherwise, 2 on a usage error.
Tested offline by ops/release-smoke-selftest.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]

# The identity and redirect reader is the production smoke's own (GET, no
# redirects, HTTP errors returned as replies), not a second copy of it.
_SPEC = importlib.util.spec_from_file_location("doctorcre_production_smoke",
                                               Path(__file__).with_name("doctorcre-production-smoke.py"))
assert _SPEC is not None and _SPEC.loader is not None
_prod = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _prod   # its dataclass resolves its own module by name
_SPEC.loader.exec_module(_prod)
Reply: Any = _prod.Reply
read = _prod.read

DEFAULT_API = "https://api.doctorcre.com"
DEFAULT_APP = "https://app.doctorcre.com"
JOURNEYS = ("release-identity", "sign-in-gate", "deal-board", "leads-workspace", "invoices-list",
            "progress-board", "dr-cre-chat", "verb-registry")
BROWSER = "browser-journeys"
READ_VERBS = {"deal-board": "deal-board", "leads-workspace": "lead-board",
              "invoices-list": "read-invoice-tracker", "progress-board": "list-progress-boards",
              "dr-cre-chat": "list-doc-conversations"}
GATED_PAGES = ("/deals", "/leads", "/invoices", "/control-room/progress", "/doc-chats")
NOT_EXERCISED = (
    "signed-in deal board, Leads workspace, invoices list and progress board pages: the app gates "
    "every page on a CARR session cookie, and the only machine identity (smoke-probe) is accepted on "
    "/mcp alone, so the journeys above read the same verbs those pages call instead of rendering them",
    "Dr. CRE chat responding: a reply needs create-doc-conversation/add-doc-conversation-turn, which "
    "write records; the read-only probe profile refuses both, so only the chat's read path is proven",
)
USER_AGENT = "carr-release-smoke/1 (+ops/release-smoke.py)"
MCP_TIMEOUT = 30


def _shape(value: Any) -> Any:
    """Keys and lengths, never values: evidence without production data."""
    if isinstance(value, dict):
        return {k: (f"list[{len(v)}]" if isinstance(v, list) else type(v).__name__)
                for k, v in sorted(value.items())}
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


def _json(reply: Any) -> Any:
    try:
        return json.loads(reply.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _release_identity(ctx: dict) -> tuple[list[str], dict]:
    failures: list[str] = []
    worker = _json(ctx["http"](ctx["api"] + "/release"))
    app = _json(ctx["http"](ctx["app"] + "/app-release"))
    worker_sha = ((worker or {}).get("git_sha") or {}).get("value") if isinstance(worker, dict) else None
    app_sha = app.get("source_commit") if isinstance(app, dict) else None
    if not isinstance(worker, dict):
        failures.append("/release did not answer JSON")
    if not isinstance(app, dict):
        failures.append("/app-release did not answer JSON")
    if ctx["phase"] == "post":
        live = worker_sha if ctx["lane"] == "worker" else app_sha
        if live != ctx["sha"]:
            failures.append(f"{ctx['lane']} serves {live!r}, expected the released {ctx['sha']}")
    version = (worker or {}).get("worker_version") if isinstance(worker, dict) else None
    return failures, {"worker_sha": worker_sha, "app_sha": app_sha,
                      "worker_version_id": (version or {}).get("id") if isinstance(version, dict) else None,
                      "app_provider_version_id": app.get("provider_version_id") if isinstance(app, dict) else None}


def _sign_in_gate(ctx: dict) -> tuple[list[str], dict]:
    failures: list[str] = []
    pages: dict[str, dict] = {}
    expected = urllib.parse.urlparse(ctx["app"])
    for page in GATED_PAGES:
        reply = ctx["http"](ctx["app"] + page)
        location = reply.headers.get("Location", "")
        pages[page] = {"status": reply.status, "location": location}
        target = urllib.parse.urlparse(urllib.parse.urljoin(ctx["app"] + "/", location))
        return_to = urllib.parse.parse_qs(target.query).get("return_to", [None])[0]
        if reply.status not in (301, 302, 303, 307, 308):
            failures.append(f"{page} answered HTTP {reply.status}, expected a sign-in redirect")
        elif (target.scheme, target.hostname, target.path, return_to) != (
                expected.scheme, expected.hostname, "/auth/login", page):
            failures.append(f"{page} redirected to {location!r}, expected its own /auth/login?return_to={page}")
    status = ctx["http"](ctx["app"] + "/status")
    pages["/status"] = {"status": status.status, "content_type": status.headers.get("Content-Type", "")}
    if status.status != 200 or "text/html" not in status.headers.get("Content-Type", ""):
        failures.append(f"/status answered HTTP {status.status}, expected the ungated status page")
    return failures, {"pages": pages}


def _read_verb(verb: str) -> Callable[[dict], tuple[list[str], dict]]:
    def journey(ctx: dict) -> tuple[list[str], dict]:
        ok, answer = ctx["mcp"](verb, {})
        if not ok:
            return [f"{verb} failed: {str(answer)[:300]}"], {"verb": verb}
        if isinstance(answer, dict) and answer.get("ok") is False:
            return [f"{verb} answered ok:false ({str(answer.get('error'))[:200]})"], {"verb": verb}
        return [], {"verb": verb, "shape": _shape(answer)}
    return journey


def _verb_registry(ctx: dict) -> tuple[list[str], dict]:
    ok, answer = ctx["mcp"]("list-verbs", {"names_only": True})
    if not ok:
        return [f"list-verbs failed: {str(answer)[:300]}"], {}
    names = {v.get("name") for v in (answer or {}).get("verbs", []) if isinstance(v, dict)} \
        if isinstance(answer, dict) else set()
    evidence: dict[str, Any] = {"live_count": len(names)}
    if not names:
        return ["list-verbs listed no verbs"], evidence
    expected = ctx["expected_verbs"]
    if ctx["expected_verbs_error"]:
        return [ctx["expected_verbs_error"]], evidence
    if expected is None:
        evidence["expected"] = "not checked: no released Worker registry was given"
        return [], evidence
    missing = sorted(set(expected) - names)
    evidence["expected_count"] = len(expected)
    if ctx["phase"] == "baseline":
        evidence["new_verbs"] = missing
        return [], evidence
    evidence["missing"] = missing
    return ([f"{len(missing)} released verb(s) not served: {', '.join(missing[:20])}"] if missing else []), evidence


CHECKS: dict[str, Callable[[dict], tuple[list[str], dict]]] = {
    "release-identity": _release_identity,
    "sign-in-gate": _sign_in_gate,
    **{journey: _read_verb(verb) for journey, verb in READ_VERBS.items()},
    "verb-registry": _verb_registry,
}


def run_smoke(*, lane: str, sha: str, phase: str, api: str, app: str,
              http: Callable[..., Any], mcp: Callable[[str, dict], tuple[bool, Any]],
              expected_verbs: list[str] | None, browser: Callable[[], dict] | None,
              only: list[str] | None = None, expected_verbs_error: str | None = None) -> dict:
    ctx = {"lane": lane, "sha": sha, "phase": phase, "api": api.rstrip("/"), "app": app.rstrip("/"),
           "http": http, "mcp": mcp, "expected_verbs": expected_verbs,
           "expected_verbs_error": expected_verbs_error}
    probes: list[dict] = []
    selected = [j for j in JOURNEYS if only is None or j in only]
    for journey in selected:
        started = time.monotonic()
        try:
            failures, evidence = CHECKS[journey](ctx)
        except Exception as exc:  # noqa: BLE001 — a crashed journey is a failed journey, with its cause
            failures, evidence = [f"{type(exc).__name__}: {str(exc)[:300]}"], {}
        probes.append({"id": journey, "status": "fail" if failures else "pass",
                       "ms": int((time.monotonic() - started) * 1000),
                       "detail": "; ".join(failures), "evidence": evidence})
    if only is None or BROWSER in only:
        started = time.monotonic()
        if browser is None:
            probes.append({"id": BROWSER, "status": "skip", "ms": 0, "evidence": {},
                           "detail": "no installed doctorcre-app checkout was given (--app-dir)"})
        else:
            try:
                outcome = browser()
                failed = [t["title"] for t in outcome.get("tests", []) if t.get("status") not in ("passed", "flaky")]
                if not outcome.get("tests"):
                    failed = ["the browser run reported no journeys"]
                detail = "; ".join(failed)
            except Exception as exc:  # noqa: BLE001
                outcome, detail = {}, f"{type(exc).__name__}: {str(exc)[:300]}"
            probes.append({"id": BROWSER, "status": "fail" if detail else "pass",
                           "ms": int((time.monotonic() - started) * 1000), "detail": detail, "evidence": outcome})
    failed_ids = [p["id"] for p in probes if p["status"] == "fail"]
    return {"schema": "carr-release-smoke.v1", "lane": lane, "sha": sha, "phase": phase,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "ok": not failed_ids, "failed": failed_ids, "probes": probes,
            "not_exercised": list(NOT_EXERCISED)}


class McpProbe:
    """The smoke-probe door: JSON-RPC tools/call to <api>/mcp with the probe
    bearer. It refuses any verb outside the read journeys before a request is
    made, so this file cannot write even if the server-side lock were lifted."""

    ALLOWED = frozenset(READ_VERBS.values()) | {"list-verbs"}

    def __init__(self, api: str, token: str, *, opener: Callable[..., Any] = urllib.request.urlopen):
        self.url, self.token, self.opener, self.next_id = api.rstrip("/") + "/mcp", token, opener, 0

    def __call__(self, verb: str, args: dict) -> tuple[bool, Any]:
        if verb not in self.ALLOWED:
            return False, f"{verb} is not a read journey; release-smoke never calls it"
        self.next_id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": "tools/call",
                           "params": {"name": verb, "arguments": args}}).encode()
        request = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "User-Agent": USER_AGENT})
        try:
            with self.opener(request, timeout=MCP_TIMEOUT) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return False, f"HTTP {error.code} from /mcp"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            return False, f"{type(error).__name__} reading /mcp"
        if not isinstance(envelope, dict) or "error" in envelope:
            return False, self._clean(json.dumps((envelope or {}).get("error"))[:300])
        result = envelope.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
        if result.get("isError"):
            return False, self._clean(text[:300])
        try:
            return True, json.loads(text)
        except ValueError:
            return True, text

    def _clean(self, text: str) -> str:
        return text.replace(self.token, "[probe token]") if self.token else text


def probe_token(credential_dir: Path) -> str | None:
    """CARR_MCP_PROBE_TOKEN from mcp-tokens.env (NAME=value, optional export
    and quotes). Returned to the caller only; nothing here prints it."""
    try:
        text = (credential_dir / "mcp-tokens.env").read_text(encoding="utf-8")
    except OSError:
        return None
    value = None
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?CARR_MCP_PROBE_TOKEN\s*=\s*(.*?)\s*$", line)
        if m:
            v = m.group(1)
            value = (v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v) or None
    return value


def released_verbs(worker_dir: Path) -> list[str]:
    """Every verb name the released Worker registry exports, read by importing
    <worker_dir>/src/tools.js exactly as ops/verb-count.sh counts it."""
    script = ("import(process.argv[1]).then(m => console.log(JSON.stringify(Object.keys(m.TOOLS))))"
              ".catch(e => { console.error(e.message); process.exit(1); })")
    url = (worker_dir.resolve() / "src" / "tools.js").as_uri()
    proc = subprocess.run(["node", "--input-type=module", "-e", script, url], cwd=str(worker_dir),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"the released registry did not import: {proc.stderr.strip()[:300]}")
    names = json.loads(proc.stdout)
    if not names:
        raise RuntimeError("the released registry exported no verbs")
    return names


BROWSER_OUTPUT = ".e2e/production-smoke"   # e2e writes only inside its project root


def browser_runner(app_dir: Path, out: Path, app_url: str) -> Callable[[], dict] | None:
    """The doctorcre-app production journeys, or None when app_dir holds no
    installed runner. Its report, screenshots and traces are copied to
    <out>/browser; the outcome names each journey's artifacts there."""
    cli = app_dir / "node_modules" / "e2e" / "dist" / "cli" / "bin.js"
    if not cli.is_file() or not (app_dir / "e2e.production.config.ts").is_file():
        return None

    def run() -> dict:
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if os.environ.get(k)}
        env.update({"CI": "1", "E2E_TELEMETRY_DISABLED": "1", "DOCTORCRE_SMOKE_URL": app_url})
        proc = subprocess.run(["node", str(cli), "run", "smoke/production", "--config", "e2e.production.config.ts",
                               "--output", BROWSER_OUTPUT, "--reporter", "list,junit"], cwd=str(app_dir), env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
        dest = out / "browser"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app_dir / BROWSER_OUTPUT, dest, dirs_exist_ok=True)
        (dest / "run.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        report = json.loads((dest / "report.json").read_text(encoding="utf-8"))
        tests = []
        for row in report.get("run", {}).get("results", []):
            if not row.get("selected", True):
                continue
            artifacts = [str(dest / "artifacts" / a["path"]) for attempt in row.get("attempts", [])
                         for a in attempt.get("artifacts", []) if a.get("path")]
            tests.append({"title": " ".join(row.get("titlePath") or [row.get("id", "?")]),
                          "status": row.get("status"), "artifacts": artifacts,
                          "ms": sum(a.get("durationMs") or 0 for a in row.get("attempts", []))})
        return {"exit": proc.returncode, "report": str(dest / "report.json"), "tests": tests}
    return run


def main(argv: list[str] | None = None, *, http: Callable[..., Any] = read,
         mcp_factory: Callable[[str, str], Callable[[str, dict], tuple[bool, Any]]] = McpProbe,
         out_line: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lane", choices=("worker", "app"), required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--phase", choices=("baseline", "post"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--app", default=DEFAULT_APP)
    parser.add_argument("--credential-dir", default="~/.config/carr")
    parser.add_argument("--worker-dir", help="the released Worker's mcp-server/ (installed), for verb-registry")
    parser.add_argument("--app-dir", help="an installed doctorcre-app checkout, for browser-journeys")
    parser.add_argument("--only", help="comma-separated journey ids (a retry)")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        print("release-smoke: --sha must be a full 40-character SHA", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    token = probe_token(Path(os.path.expanduser(args.credential_dir)))
    if token:
        mcp = mcp_factory(args.api, token)
    else:
        def mcp(verb: str, _args: dict) -> tuple[bool, Any]:
            return False, "no CARR_MCP_PROBE_TOKEN in mcp-tokens.env: the authenticated journeys cannot run"
    expected, expected_error = None, None
    if args.worker_dir:
        try:
            expected = released_verbs(Path(args.worker_dir))
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            expected_error = str(error)
    browser = browser_runner(Path(args.app_dir), out, args.app) if args.app_dir else None
    only = [j.strip() for j in args.only.split(",") if j.strip()] if args.only else None

    summary = run_smoke(lane=args.lane, sha=args.sha, phase=args.phase, api=args.api, app=args.app,
                        http=http, mcp=mcp, expected_verbs=expected, browser=browser, only=only,
                        expected_verbs_error=expected_error)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out / "probes.jsonl").open("w", encoding="utf-8") as fh:
        for row in summary["probes"]:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    for row in summary["probes"]:
        out_line(f"release-smoke: {row['status'].upper():4} {row['id']} {row['ms']}ms {row['detail']}".rstrip())
    out_line(f"release-smoke: {'OK' if summary['ok'] else 'FAILED ' + ','.join(summary['failed'])} -> {out / 'summary.json'}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

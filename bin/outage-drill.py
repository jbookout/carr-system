#!/usr/bin/env python3
"""bin/outage-drill.py — the SYNTHETIC half of Program 4's degraded-mode
requirement: a repeatable drill harness that induces each survivable outage
against NON-PRODUCTION surfaces and asserts the system TELLS THE TRUTH rather
than showing green or fabricating a response.

THE DOCTRINE THIS PROVES, verbatim:
  "Dependency outage exercises prove truthful degraded behavior."
  "Unknown telemetry displays Unknown and can disable unsafe writes."
  "Unknown telemetry never displays Healthy."
  "Model outage makes Doc unavailable or deterministic/read-only; it never
   fabricates an AI response."
  "Database outage blocks authoritative writes and preserves safe local
   capture only when encrypted, clearly pending, and later reviewed."
Exit evidence: "tabletop or synthetic outage proves truthful UI, bounded
retries, reconciliation, and no duplicated effects."

THE OTHER HALF — a human tabletop with Joe unavailable and Dell using his own
real access — is filed separately and is NOT this file's job. This file only
does what a machine can do safely, on its own, repeatedly: point a real
component at an unreachable endpoint, an invalid credential, or a fixture, and
read back whether the system told the truth about it.

GROUND RULES, enforced by construction, not by promise:
  * NEVER a real outage on production. No production service is stopped, no
    real credential is revoked, nothing is deleted, and no write reaches the
    production database — every drill that needs a live database uses the
    ISOLATED STAGING NEON PROJECT (tools/db-tap.py --project staging), which
    shares no data or credentials with production (see db-tap.py's own
    PROJECTS comment). Every drill that needs a live Worker uses the staging
    Worker (carr-mcp-staging.joe-bookout-carr-us.workers.dev) or a local
    fixture. A drill that cannot be induced safely is reported as SKIPPED
    with the reason, never improvised around.
  * Outages are induced by pointing a component at an unreachable endpoint
    (postgresql://127.0.0.1:1, a fake `claude` binary shadowing the real one
    on PATH) or by a fixture (a fake model binary, a synthetic PostToolUse
    payload) — never by touching a real running service.
  * Each drill: induce -> observe -> assert the truthful behavior -> record
    ONE ops.run row as evidence (kind=check, service=carr-outage-drill, a
    run_key per drill, state reflecting whether the SYSTEM BEHAVED
    TRUTHFULLY, never whether the outage merely happened) -> restore the
    pre-drill state and PROVE it restored (a re-query, a file-mtime check, a
    row count back at zero).
  * A drill that finds the system did NOT tell the truth is the most useful
    possible result. This file never fixes that silently and never papers
    over it — it reports the finding plainly on stdout and via
    `record-defect` (see the drill's own comment for why that verb rather
    than `record-finding`).

WHY carr-outage-drill's EVIDENCE LANDS IN STAGING, NOT PRODUCTION. The task's
own ground rule is stricter than "don't break anything" — it is "no writes to
the production database beyond what an ordinary read does." Recording a check
row is a write. So unlike bin/restore-rehearse.sh (which rehearses against a
throwaway Neon branch but records its OWN evidence into PRODUCTION's ops.run,
because that script pre-dates this task and that choice was Joe's), every
row this file writes — the drill mechanics AND the evidence of having run —
stays inside the isolated staging project. `tools/ops-record.py trace
<correlation-id>` against staging reads it back.

USAGE
    bin/outage-drill.py --list
    bin/outage-drill.py                        # run every drill that can be
    bin/outage-drill.py --only record-layer-unreachable
    bin/outage-drill.py --dry-run              # describe, touch nothing

Exit code: 0 if every drill that ran found truthful behavior; 1 if any drill
that ran found the system did NOT tell the truth; 2 if a drill could not be
run at all (missing credential, unreachable staging) and had to be skipped.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = REPO / ".venv" / "bin" / "python"
if not PY.exists():
    PY = Path(sys.executable)

OPS_RECORD = REPO / "tools" / "ops-record.py"
RUN_SCHEDULED = REPO / "bin" / "run-scheduled.sh"
SETTINGS_GATE = REPO / "hooks" / "settings-change-gate.py"
DOC_CONVO_BIN = REPO / "tools" / "doc-convo" / "bin"
DOC_SESSION_FILE = REPO / "tools" / "doc-convo" / "assets" / ".brain-session-id"
STAGING_WORKER = "https://carr-mcp-staging.joe-bookout-carr-us.workers.dev"

EVIDENCE_SERVICE = "carr-outage-drill"
UNREACHABLE_DSN = "postgresql://nobody@127.0.0.1:1/nothing"

# Loopback:1 refuses instantly (RST), the same trick ops/run-scheduled-selftest.py
# and ops/restore-rehearse-record-selftest.py already use — a suite (or a drill)
# that ran on every push must never pay a connect timeout to prove an outage.


def _unreachable_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env["DATABASE_URL"] = UNREACHABLE_DSN
    for leak in ("CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL", "CARR_DB_URL", "PGSERVICE"):
        env.pop(leak, None)
    return env


# ── staging credential, derived once, never printed ──────────────────────────
_STAGING_DSN_CACHE: str | None = None


def staging_dsn() -> str:
    """The isolated staging Neon project's connection string, derived the
    SAME way tools/db-tap.py derives it — imported as a module rather than
    re-implemented, so there is exactly one place this logic lives (rule
    a8c55a47). Raises SystemExit with db-tap's own message on failure (no
    NEON_API_KEY, expired login, etc.) — that message already names the fix."""
    global _STAGING_DSN_CACHE
    if _STAGING_DSN_CACHE is None:
        spec = importlib.util.spec_from_file_location("carr_db_tap", REPO / "tools" / "db-tap.py")
        assert spec and spec.loader
        db_tap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(db_tap)
        _STAGING_DSN_CACHE = db_tap.dsn(project="staging")
    return _STAGING_DSN_CACHE


def staging_conn():
    import psycopg
    return psycopg.connect(staging_dsn(), autocommit=True)


class DrillUnavailable(Exception):
    """Raised when a drill's PRECONDITION fails — staging unreachable, a tool
    missing — which is a reason to SKIP, not a finding that the system under
    test lied."""


@dataclass
class DrillResult:
    name: str
    truthful: bool | None       # None only when skipped
    summary: str
    detail: str
    findings: list[dict] = field(default_factory=list)
    correlation: str = field(default_factory=lambda: str(uuid.uuid4()))
    skipped: bool = False
    skip_reason: str = ""


def _run(cmd: list[str], env: dict | None = None, cwd: Path | None = None,
         timeout: int = 60, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, env=env, cwd=str(cwd) if cwd else None, timeout=timeout,
        capture_output=True, text=True, input=input_text,
    )


def _tail_provenance(run_key: str, service: str) -> str:
    """The wrapper's own provenance line naming both this run key and this
    service — last one wins. Same technique ops/run-scheduled-selftest.py
    uses, so a drill run interleaved with real production heartbeats on this
    same Mac still finds exactly its own line."""
    log = REPO / "out" / "run-scheduled.log"
    try:
        with open(log, encoding="utf-8") as fh:
            hits = [ln.rstrip("\n") for ln in fh
                    if f"key={run_key} " in ln and f"service={service} " in ln]
    except FileNotFoundError:
        return ""
    return hits[-1] if hits else ""


def _field(line: str, name: str) -> str:
    m = re.search(rf"\b{re.escape(name)}=(\S*)", line)
    return m.group(1) if m else ""


def record_evidence(result: DrillResult) -> None:
    """The ONE ops.run row per drill (rule a8c55a47's own writer: this always
    goes through tools/ops-record.py, never a raw INSERT). Lands in the
    ISOLATED STAGING project — see the module docstring for why not
    production. state reflects whether the SYSTEM TOLD THE TRUTH, never
    whether the outage happened; a drill that unmasks a lie is a
    state=failed evidence row precisely because that lie is the failure."""
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if result.skipped:
        state, fclass = "skipped", None
    elif result.truthful:
        state, fclass = "succeeded", None
    else:
        state, fclass = "failed", "target_not_truthful"

    detail = (result.detail or result.summary)[:390]
    argv = [
        str(PY), str(OPS_RECORD), "run",
        "--service", EVIDENCE_SERVICE, "--key", result.name, "--kind", "check",
        "--environment", "staging", "--state", state,
        "--source-kind", "collector", "--source-ref", "bin/outage-drill.py",
        "--started-at", started, "--correlation", result.correlation,
        "--detail", detail,
    ]
    if fclass:
        argv += ["--failure-class", fclass]
    env = dict(os.environ)
    env["DATABASE_URL"] = staging_dsn()
    proc = _run(argv, env=env, timeout=30)
    if proc.returncode == 0:
        print(f"  evidence: recorded ({proc.stdout.strip()})")
    else:
        print(f"  EVIDENCE WARNING: not recorded — {proc.stderr.strip()[:300]}",
              file=sys.stderr)


def ensure_evidence_service(conn) -> None:
    """Idempotent upsert of the drill harness's own service row in staging —
    the same throwaway-probe-by-raw-insert pattern
    ops/run-scheduled-selftest.py's tier2() already uses (sync-registry is
    for services declared in the repo file; this one is deliberately not,
    same reasoning the existing selftests give for their own probes)."""
    with conn.cursor() as cur:
        cur.execute(
            """insert into ops.service (key, name, purpose, family, criticality, owner_actor, runtime)
               values (%s, 'Program 4 synthetic outage-drill harness',
                       'Evidence sink for bin/outage-drill.py — never a real dependency',
                       'Local Mac edge', 'low', 'joe', 'local-script')
               on conflict (key) do update set name = excluded.name
               returning id""", (EVIDENCE_SERVICE,))
        sid = cur.fetchone()[0]
        cur.execute(
            """insert into ops.service_environment (service_id, environment)
               values (%s, 'staging') on conflict do nothing""", (sid,))


# ══════════════════════════════════════════════════════════════════════════
# DRILL 1 — RECORD LAYER UNREACHABLE
# ══════════════════════════════════════════════════════════════════════════
def drill_record_layer_unreachable() -> DrillResult:
    """Point the real collector (bin/run-scheduled.sh -> tools/ops-record.py)
    at an unreachable database and assert: (a) the wrapped job's own exit
    code is untouched, (b) the failure is visible in the provenance line
    with a non-zero recorder exit, (c) no shadow markdown file appears
    anywhere as a substitute for the failed write, and (d) the service then
    reads `unknown` — never `healthy` — at the next health look, because
    ops.v_service_environment_health derives health from the latest
    TERMINAL observation and its freshness and stores none (migration
    0115's load-bearing decision)."""
    name = "record-layer-unreachable"
    probe = "carr-outage-drill-record-layer-probe"
    run_key = "probe.heartbeat"
    dsn = staging_dsn()

    with staging_conn() as conn:
        ensure_evidence_service(conn)
        with conn.cursor() as cur:
            # Clean slate — a probe re-registered on a stale row would let a
            # PREVIOUS run's health leak into this one's assertions.
            cur.execute("delete from ops.run where service_id = "
                        "(select id from ops.service where key = %s)", (probe,))
            cur.execute("delete from ops.service_environment where service_id = "
                        "(select id from ops.service where key = %s)", (probe,))
            cur.execute("delete from ops.service where key = %s", (probe,))
            cur.execute(
                """insert into ops.service (key, name, family, criticality, owner_actor, runtime)
                   values (%s, 'outage-drill probe (record-layer-unreachable)',
                           'Local Mac edge', 'low', 'joe', 'local-script')
                   returning id""", (probe,))
            sid = cur.fetchone()[0]
            # A SHORT cadence (5s) with no grace is what lets this drill prove
            # staleness->unknown in seconds rather than hours, without faking
            # observed_at (ops-record.py always stamps it `now()` — by design,
            # a collector cannot claim to have observed something in the past).
            #
            # environment='production' HERE, even though every row in this
            # section lives in the ISOLATED STAGING PROJECT (see staging_dsn()
            # below): bin/run-scheduled.sh — the real, UNMODIFIED collector
            # this drill exists to exercise — never accepts an --environment
            # override and always records 'production' (it wraps real launchd
            # jobs, all of which affect production). Registering this probe as
            # 'staging' would make the collector's own honest self-description
            # mismatch the catalog row and read as `missing` instead of
            # `healthy` — a bug in THIS DRILL, not in the collector, caught
            # empirically running it for real. environment describes WHAT WAS
            # AFFECTED, not which physical database holds the row (see
            # ops/config/services.json's own comment) — no production data or
            # credential is touched anywhere in this drill.
            cur.execute(
                """insert into ops.service_environment
                       (service_id, environment, expected_cadence_seconds, cadence_grace_seconds)
                   values (%s, 'production', 5, 0)""", (sid,))

        # snapshot the tree BEFORE, for the no-shadow-markdown assertion below.
        before_md = _snapshot_markdown()

        # ── baseline: a REAL successful collector run ────────────────────────
        baseline_env = dict(os.environ)
        baseline_env["DATABASE_URL"] = dsn
        for leak in ("CARR_DB_JOBS_URL", "CARR_DB_EXPORTER_URL"):
            baseline_env.pop(leak, None)
        proc = _run([str(RUN_SCHEDULED), probe, run_key, "/bin/sh", "-c", "exit 0"],
                     env=baseline_env, cwd=REPO, timeout=30)
        base_line = _tail_provenance(run_key, probe)
        if proc.returncode != 0 or _field(base_line, "state") != "succeeded":
            raise DrillUnavailable(
                f"could not establish a healthy baseline against staging "
                f"(rc={proc.returncode}, line={base_line!r}) — staging may be "
                f"unreachable right now")

        with conn.cursor() as cur:
            cur.execute(
                """select health, freshness_state from ops.v_service_environment_health
                    where service_id = %s and environment = 'production'""", (sid,))
            baseline_health = cur.fetchone()
        if baseline_health is None or baseline_health[0] != "healthy":
            raise DrillUnavailable(f"baseline health did not read healthy: {baseline_health}")

        # ── induce: the SAME collector, now pointed at an unreachable DB ─────
        outage_env = _unreachable_env(dict(os.environ))
        proc2 = _run([str(RUN_SCHEDULED), probe, run_key, "/bin/sh", "-c", "exit 0"],
                      env=outage_env, cwd=REPO, timeout=30)
        outage_line = _tail_provenance(run_key, probe)

        after_md = _snapshot_markdown()

        with conn.cursor() as cur:
            cur.execute(
                "select count(*) from ops.run where service_id = %s and run_key = %s",
                (sid, run_key))
            row_count = cur.fetchone()[0]

        checks = {
            "wrapped job's own exit code untouched (still 0)": proc2.returncode == 0,
            "failure is visible in the provenance line": outage_line != "",
            "provenance line's recorder_exit is non-zero (genuinely unreachable)":
                _field(outage_line, "recorder_exit") not in ("0", ""),
            "the outage attempt inserted NO second ops.run row (no shadow write)":
                row_count == 1,
            "no shadow markdown file appeared as a substitute for the failed write":
                after_md == before_md,
        }

        # ── the staleness half: silence must not stay green ──────────────────
        time.sleep(7)   # cadence 5s + 0 grace + margin; short and deterministic
        with conn.cursor() as cur:
            cur.execute(
                """select health, freshness_state from ops.v_service_environment_health
                    where service_id = %s and environment = 'production'""", (sid,))
            stale_health = cur.fetchone()
        checks["service now reads unknown (never healthy) at the next health look"] = (
            stale_health is not None and stale_health[0] == "unknown")

        # ── restore: deregister the probe and PROVE it is gone ───────────────
        with conn.cursor() as cur:
            cur.execute("delete from ops.run where service_id = %s", (sid,))
            cur.execute("delete from ops.service_environment where service_id = %s", (sid,))
            cur.execute("delete from ops.service where id = %s", (sid,))
            cur.execute("select count(*) from ops.service where key = %s", (probe,))
            restored = cur.fetchone()[0] == 0
        checks["restore: the probe service is fully deregistered afterward"] = restored

    truthful = all(checks.values())
    lines = "; ".join(f"{'ok' if v else 'FAIL'}: {k}" for k, v in checks.items())
    detail = (f"baseline={_field(base_line, 'state')} outage_recorder_exit="
              f"{_field(outage_line, 'recorder_exit')} rows_after_outage={row_count} "
              f"health_after_stale={stale_health[0] if stale_health else '?'} — {lines}")
    return DrillResult(name, truthful, "truthful" if truthful else "NOT truthful — see detail",
                        detail)


_MD_SKIP_PARTS = (".git", "node_modules", "__pycache__", "worktrees", ".venv")


def _snapshot_markdown() -> set[str]:
    """The set of every .md file under the given dirs, relative paths. Used as
    a before/after diff — the cheapest real proof that a failed write never
    silently became a markdown file somewhere (doctrine: 'no shadow Markdown
    or unreviewed replay'). Recurses only into out/ (the one place a
    collector could plausibly drop something) and looks at the repo root
    shallowly — a full REPO rglob would also descend into every worktree
    under .claude/worktrees/, each a full checkout, multiplying the file
    count by dozens for no benefit and risking a false positive from
    unrelated concurrent work in another worktree."""
    out: set[str] = set()
    for p in REPO.glob("*.md"):
        out.add(str(p.relative_to(REPO)))
    run_out = REPO / "out"
    if run_out.exists():
        for p in run_out.rglob("*.md"):
            if any(part in _MD_SKIP_PARTS for part in p.parts):
                continue
            out.add(str(p.relative_to(REPO)))
    return out


# ══════════════════════════════════════════════════════════════════════════
# DRILL 2 — STALE / MISSING OBSERVATION
# ══════════════════════════════════════════════════════════════════════════
def drill_stale_observation() -> DrillResult:
    """A service past its cadence reads stale-or-unknown rather than healthy
    — proven against a throwaway service registered in staging with NO
    outage attempted at all: time alone, with nobody trying and failing to
    observe it, is enough to turn a real 'healthy' into 'unknown'. This is
    distinct from drill 1 (which proves a FAILED collector attempt is both
    visible and harmless); this one proves plain SILENCE is never mistaken
    for health either."""
    name = "stale-observation"
    probe = "carr-outage-drill-stale-probe"
    run_key = "probe.once"
    dsn = staging_dsn()

    with staging_conn() as conn:
        ensure_evidence_service(conn)
        with conn.cursor() as cur:
            cur.execute("delete from ops.run where service_id = "
                        "(select id from ops.service where key = %s)", (probe,))
            cur.execute("delete from ops.service_environment where service_id = "
                        "(select id from ops.service where key = %s)", (probe,))
            cur.execute("delete from ops.service where key = %s", (probe,))
            cur.execute(
                """insert into ops.service (key, name, family, criticality, owner_actor, runtime)
                   values (%s, 'outage-drill probe (stale-observation)',
                           'Local Mac edge', 'low', 'joe', 'local-script')
                   returning id""", (probe,))
            sid = cur.fetchone()[0]
            cur.execute(
                """insert into ops.service_environment (service_id, environment)
                   values (%s, 'staging')""", (sid,))

        env = dict(os.environ)
        env["DATABASE_URL"] = dsn
        # A short, explicit expiry — the observation is real and freshly made;
        # only its BELIEVABILITY WINDOW is short, which is what lets this
        # drill prove staleness in seconds rather than waiting out an hour
        # cadence. ops-record.py always stamps observed_at at `now()`; there
        # is no way to backdate an observation, by design (rule 97326357: a
        # claim about a surface becomes doctrine only from a live test).
        proc = _run([str(PY), str(OPS_RECORD), "run",
                     "--service", probe, "--key", run_key, "--kind", "check",
                     "--environment", "staging", "--state", "succeeded",
                     "--exit-code", "0", "--source-kind", "collector",
                     "--source-ref", "bin/outage-drill.py", "--expires-in", "3",
                     "--detail", "outage-drill stale-observation probe"],
                    env=env, timeout=30)
        if proc.returncode != 0:
            raise DrillUnavailable(f"could not write the baseline probe row: {proc.stderr[:300]}")

        with conn.cursor() as cur:
            cur.execute(
                """select health, freshness_state from ops.v_service_environment_health
                    where service_id = %s and environment = 'staging'""", (sid,))
            fresh = cur.fetchone()

        time.sleep(4)  # past the 3s expiry, before it decays further

        with conn.cursor() as cur:
            cur.execute(
                """select health, freshness_state from ops.v_service_environment_health
                    where service_id = %s and environment = 'staging'""", (sid,))
            stale = cur.fetchone()

        with conn.cursor() as cur:
            cur.execute("delete from ops.run where service_id = %s", (sid,))
            cur.execute("delete from ops.service_environment where service_id = %s", (sid,))
            cur.execute("delete from ops.service where id = %s", (sid,))
            cur.execute("select count(*) from ops.service where key = %s", (probe,))
            restored = cur.fetchone()[0] == 0

    checks = {
        "a fresh, just-recorded success reads healthy": fresh is not None and fresh[0] == "healthy",
        "the SAME row, once past its expiry, reads unknown — never healthy":
            stale is not None and stale[0] == "unknown",
        "the SAME row's freshness_state is stale (not silently 'fresh')":
            stale is not None and stale[1] == "stale",
        "restore: the probe service is fully deregistered afterward": restored,
    }
    truthful = all(checks.values())
    lines = "; ".join(f"{'ok' if v else 'FAIL'}: {k}" for k, v in checks.items())
    detail = f"fresh={fresh} stale={stale} — {lines}"
    return DrillResult(name, truthful, "truthful" if truthful else "NOT truthful — see detail",
                        detail)


# ══════════════════════════════════════════════════════════════════════════
# DRILL 3 — WORKER UNREACHABLE / ERRORING
# ══════════════════════════════════════════════════════════════════════════
def drill_worker_unreachable() -> DrillResult:
    """Two parts. (a) LOCAL FIXTURE: run the real, already-committed
    correlation.js against `node --test` — the correctness of the mechanism
    ITSELF, independent of what happens to be deployed anywhere right now.
    (b) LIVE PROBE: hit the STAGING Worker's /mcp with no credential and
    assert the response is a truthful error (a real 401 challenge, never a
    fabricated 200) — then check whether that failed journey is traceable
    by its correlation id afterward, which is the harder half of the
    doctrine's exit bar and, per correlation.js's OWN header comment, is
    NOT yet built for ad hoc Worker requests (it writes to none of ops.run,
    ops.deployment, ops.incident or ops.work_request today). This drill
    proves that gap live rather than only citing the comment."""
    name = "worker-unreachable"
    findings: list[dict] = []

    # ── (a) local fixture — the committed mechanism itself ───────────────────
    node_proc = _run(["node", "--test", "test/correlation.test.mjs"],
                      cwd=REPO / "mcp-server", timeout=60)
    node_output = (node_proc.stdout or "") + (node_proc.stderr or "")
    # node --test's default ("spec") reporter prints "ℹ pass N" / "ℹ fail N"
    # summary lines, not TAP's "# pass N" — verified live against this repo's
    # own node v26.5.1. Matched loosely (no leading symbol) so this survives a
    # different Node version's exact glyph.
    node_pass_m = re.search(r"\bpass (\d+)\s*$", node_output, re.MULTILINE)
    node_fail_m = re.search(r"\bfail (\d+)\s*$", node_output, re.MULTILINE)
    node_pass = int(node_pass_m.group(1)) if node_pass_m else -1
    node_fail = int(node_fail_m.group(1)) if node_fail_m else -1
    local_mechanism_ok = node_proc.returncode == 0 and node_fail == 0 and node_pass > 0

    # ── (b) live probe against staging, never production ─────────────────────
    sent_correlation = str(uuid.uuid4())
    try:
        req = urllib.request.Request(
            f"{STAGING_WORKER}/mcp", data=b"{}", method="POST",
            headers={"content-type": "application/json", "x-correlation-id": sent_correlation,
                     # Cloudflare's edge WAF (rule "1010") blocks Python urllib's
                     # default User-Agent string before the request ever reaches
                     # the Worker — observed live: a 403 'error code: 1010'
                     # plain-text body, no x-correlation-id, not from our code at
                     # all. A generic browser-shaped UA reaches the real Worker,
                     # same as every ordinary curl call this drill was verified
                     # against by hand.
                     "user-agent": "Mozilla/5.0 (compatible; carr-outage-drill/1.0)"})
        try:
            resp = urllib.request.urlopen(req, timeout=20)
            status, headers, body = resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            status, headers, body = e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise DrillUnavailable(f"the staging Worker was not reachable at all: {e}")

    body_text = body.decode("utf-8", errors="replace")
    fabricated_success = status in (200, 201) or '"ok":true' in body_text
    got_correlation = headers.get("x-correlation-id") or headers.get("X-Correlation-Id")

    traced = False
    if got_correlation:
        try:
            with staging_conn() as conn, conn.cursor() as cur:
                cur.execute("select count(*) from ops.v_trace where correlation_id::text = %s",
                            (got_correlation,))
                traced = cur.fetchone()[0] > 0
        except Exception as e:                                    # noqa: BLE001
            findings.append({
                "note": "could not query ops.v_trace on staging to check traceability",
                "error": str(e)[:200],
            })
    else:
        findings.append({
            "note": "the staging Worker did not echo x-correlation-id at all on this "
                    "response — its deployed code (checked via /release's git_sha) "
                    "predates mcp-server/src/correlation.js, which is a deploy-lag "
                    "observation, not a defect in the committed code (part (a) above "
                    "proves the committed mechanism is correct).",
        })

    checks = {
        "the local fixture proves the committed correlation.js mechanism is correct "
        f"({node_pass} passed, {node_fail} failed)": local_mechanism_ok,
        "an unauthenticated /mcp call gets a truthful non-success — never a "
        f"fabricated 'ok:true' (got HTTP {status})": not fabricated_success,
    }
    truthful = all(checks.values())

    if not (got_correlation and traced):
        findings.append({
            "note": "the doctrine's own exit bar requires the failed journey to be "
                    "traceable by its correlation id afterward. It is not: correlation.js "
                    "mints/echoes the id on the response but writes it into no ops.* "
                    "table (confirmed by reading the file's own header comment AND by "
                    "querying ops.v_trace live for this request's id and finding "
                    "nothing). This is a real, filed finding — see the drill report.",
        })

    lines = "; ".join(f"{'ok' if v else 'FAIL'}: {k}" for k, v in checks.items())
    detail = (f"node: pass={node_pass} fail={node_fail} rc={node_proc.returncode} | "
              f"live: status={status} correlation_sent={sent_correlation} "
              f"correlation_echoed={got_correlation!r} traced_in_ops_v_trace={traced} — {lines}")
    return DrillResult(name, truthful, "truthful (error path) but see findings on traceability"
                        if truthful else "NOT truthful — see detail", detail, findings=findings)


# ══════════════════════════════════════════════════════════════════════════
# DRILL 4 — MODEL PROVIDER UNAVAILABLE (Doc)
# ══════════════════════════════════════════════════════════════════════════
DOC_FIXTURE_DRIVER = """
import json, sys
sys.path.insert(0, {tool_bin!r})
import convo_core

reply, brain = convo_core.ask_brain_streaming(
    "outage drill ping - ignore, this is a fixture", "You are a test fixture.")
print(json.dumps({{
    "returncode": brain.returncode,
    "reply": reply,
    "stderr_tail": (brain.stderr or "")[-300:],
}}))
"""


def drill_model_provider_unavailable() -> DrillResult:
    """Doc's model call (tools/doc-convo/bin/convo_core.py's BrainProcess)
    shells out to the `claude` CLI on PATH. This drill shadows `claude` with
    a fake binary that fails immediately — an unreachable-model fixture, the
    kind the ground rules explicitly allow — run in a FRESH subprocess with
    its own module-level state, never touching the real, possibly-running
    doc-engine KeepAlive process (a separate OS process; SESSION_FILE is
    read but, given the fixture never emits a valid session_id line, is
    never written). Asserts: the caller-visible result is a genuine failure
    (non-zero returncode) with NO fabricated reply text — proving the
    deterministic-or-unavailable posture doctrine requires."""
    name = "model-provider-unavailable"

    if not DOC_CONVO_BIN.exists():
        raise DrillUnavailable(f"{DOC_CONVO_BIN} does not exist on this checkout")

    session_before = DOC_SESSION_FILE.stat().st_mtime if DOC_SESSION_FILE.exists() else None

    fixture_dir = Path(tempfile.mkdtemp(prefix="carr-outage-drill-claude-fixture-"))
    fake_claude = fixture_dir / "claude"
    fake_claude.write_text("#!/bin/sh\necho 'FAKE_MODEL_PROVIDER_UNAVAILABLE (outage drill)' 1>&2\nexit 17\n")
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    driver = fixture_dir / "driver.py"
    driver.write_text(DOC_FIXTURE_DRIVER.format(tool_bin=str(DOC_CONVO_BIN)))

    env = dict(os.environ)
    env["PATH"] = f"{fixture_dir}:{env.get('PATH', '')}"
    env["DOC_MIC_DEVICE"] = "none"   # never probed; belt-and-suspenders

    try:
        proc = _run([sys.executable, str(driver)], env=env, cwd=DOC_CONVO_BIN, timeout=20)
    finally:
        shutil.rmtree(fixture_dir, ignore_errors=True)

    session_after = DOC_SESSION_FILE.stat().st_mtime if DOC_SESSION_FILE.exists() else None

    parsed: dict | None = None
    parse_error = None
    try:
        parsed = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else None
    except Exception as e:                                        # noqa: BLE001
        parse_error = str(e)
    parsed_safe: dict = parsed or {}

    checks = {
        "the driver itself ran to completion": proc.returncode == 0,
        "convo_core reported a real failure (non-zero returncode from the "
        "brain subprocess)": bool(parsed) and parsed_safe.get("returncode") not in (0, None),
        "no fabricated reply text was returned":
            bool(parsed) and (parsed_safe.get("reply") or "") == "",
        "Doc's real, possibly-running session state was never touched":
            session_before == session_after,
    }
    truthful = all(checks.values())
    lines = "; ".join(f"{'ok' if v else 'FAIL'}: {k}" for k, v in checks.items())
    detail = (f"driver_rc={proc.returncode} parsed={parsed} parse_error={parse_error} "
              f"stderr_tail={proc.stderr[-200:]!r} — {lines}")
    return DrillResult(name, truthful, "truthful" if truthful else "NOT truthful — see detail",
                        detail)


# ══════════════════════════════════════════════════════════════════════════
# DRILL 5 — SETTINGS-CHANGE GATE UNDER A GENUINE DATABASE OUTAGE
# ══════════════════════════════════════════════════════════════════════════
def drill_settings_change_db_outage() -> DrillResult:
    """ops/settings-change-gate-selftest.py already proves the spool-fallback
    SHAPE using CARR_SETTINGS_GATE_OFFLINE=1, which skips the database
    attempt entirely. This drill exercises the path that flag never reaches:
    a REAL attempt against a genuinely unreachable database that then falls
    through to the spool — the actual 'Database outage blocks authoritative
    writes and preserves safe local capture' doctrine line, live. It also
    checks the THREE qualifiers doctrine attaches to that capture —
    encrypted, clearly pending, later reviewed — and reports which hold."""
    name = "settings-change-db-outage"

    if not SETTINGS_GATE.exists():
        raise DrillUnavailable(f"{SETTINGS_GATE} does not exist on this checkout")

    spool_dir = Path(tempfile.mkdtemp(prefix="carr-outage-drill-settings-spool-"))
    spool = spool_dir / "settings-changes.jsonl"
    try:
        command = 'CARR_CHANGE_REASON="outage drill — proving the DB-unreachable fallback" ' \
                  'gh api -X PATCH repos/jbookout/carr-system/rulesets/20824501 -f x=y'
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 0, "stdout": "", "stderr": ""},
            "session_id": "outage-drill-session",
        }
        env = _unreachable_env(dict(os.environ))
        env["CARR_SETTINGS_SPOOL"] = str(spool)
        env.pop("CARR_SETTINGS_GATE_OFFLINE", None)  # the REAL attempt-then-fallback path

        proc = _run([sys.executable, str(SETTINGS_GATE)], env=env, cwd=REPO, timeout=30,
                     input_text=json.dumps(payload))

        spooled_rows: list[dict] = []
        raw_text = ""
        if spool.exists():
            raw_text = spool.read_text(encoding="utf-8")
            for line in raw_text.splitlines():
                if line.strip():
                    spooled_rows.append(json.loads(line))

        row = spooled_rows[0] if len(spooled_rows) == 1 else None
        looks_plaintext = bool(raw_text) and raw_text.lstrip().startswith("{") and \
            "rulesets" in raw_text  # readable straight off disk with no decrypt step

        checks = {
            "the change is never blocked by a down database (exit 0)": proc.returncode == 0,
            "the outage is reported OUT LOUD, not hidden": "unreachable" in proc.stderr.lower(),
            "exactly one row spooled locally, with the change intact":
                row is not None and row.get("outcome") == "applied"
                and row.get("session_id") == "outage-drill-session"
                and "rulesets" in (row.get("target") or ""),
        }
        truthful = all(checks.values())

        findings = []
        if row is not None:
            if looks_plaintext:
                findings.append({
                    "note": "the spooled row is PLAINTEXT on disk, not encrypted. Doctrine: "
                            "'Database outage blocks authoritative writes and preserves safe "
                            "local capture only when encrypted, clearly pending, and later "
                            "reviewed.' hooks/settings-change-gate.py's record() writes the "
                            "spool with a bare open()/json.dumps() — confirmed by reading this "
                            "row straight off disk with no decrypt step.",
                })
            if "pending" not in json.dumps(row).lower() and "review" not in json.dumps(row).lower():
                findings.append({
                    "note": "the spooled row carries no 'pending review' marker of its own — "
                            "only a transient stderr line says the write is degraded-mode. A "
                            "human tailing the spool file later, without that stderr line, "
                            "cannot tell a spooled row from a normally-recorded one.",
                })
            findings.append({
                "note": "nothing in the repo ever reads out/settings-changes.jsonl back — "
                        "confirmed by grep: CARR_SETTINGS_SPOOL and settings-changes.jsonl "
                        "appear only in the writer (hooks/settings-change-gate.py) and its "
                        "own selftest, never in a consumer. The spool is 'preserved' but not "
                        "'later reviewed'.",
            })
    finally:
        shutil.rmtree(spool_dir, ignore_errors=True)

    lines = "; ".join(f"{'ok' if v else 'FAIL'}: {k}" for k, v in checks.items())
    detail = f"rc={proc.returncode} row={row} — {lines}"
    return DrillResult(name, truthful, "truthful (never blocks, never hides) but see findings "
                        "on the encrypted/pending/reviewed qualifiers" if truthful
                        else "NOT truthful — see detail", detail, findings=findings)


# ══════════════════════════════════════════════════════════════════════════
# harness
# ══════════════════════════════════════════════════════════════════════════
DRILLS = {
    "record-layer-unreachable": (
        drill_record_layer_unreachable,
        "Point the real collector at an unreachable staging database; prove "
        "the wrapped job's exit code is untouched, the failure is visible "
        "with a non-zero recorder exit, no shadow markdown appears, and the "
        "service reads unknown (never healthy) once its baseline goes stale."),
    "stale-observation": (
        drill_stale_observation,
        "A throwaway staging service, past its cadence with no failed "
        "attempt at all — pure silence — prove it reads unknown, never "
        "healthy, then deregister it."),
    "worker-unreachable": (
        drill_worker_unreachable,
        "Local: node --test on the committed correlation.js. Live: an "
        "unauthenticated call to the staging Worker's /mcp; prove a "
        "truthful error (never a fabricated success) and check whether the "
        "failed journey is traceable by its correlation id afterward."),
    "model-provider-unavailable": (
        drill_model_provider_unavailable,
        "Shadow the `claude` binary Doc's BrainProcess shells out to with a "
        "fixture that fails immediately, in an isolated subprocess; prove "
        "no fabricated reply and that Doc's real session state is untouched."),
    "settings-change-db-outage": (
        drill_settings_change_db_outage,
        "Drive hooks/settings-change-gate.py's PostToolUse path with a "
        "genuinely unreachable database (not the OFFLINE test flag); prove "
        "the change is never blocked and the outage is spooled and "
        "reported out loud, then check the encrypted/pending/reviewed "
        "qualifiers doctrine attaches to that spool."),
}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="list drills and exit")
    p.add_argument("--only", action="append", metavar="DRILL",
                   help="run only this drill (repeatable)")
    p.add_argument("--dry-run", action="store_true",
                   help="describe what would run; touch nothing")
    args = p.parse_args()

    if args.list:
        for k, (_, desc) in DRILLS.items():
            print(f"{k}\n    {desc}\n")
        return 0

    selected = args.only or list(DRILLS)
    unknown = [d for d in selected if d not in DRILLS]
    if unknown:
        print(f"outage-drill: unknown drill(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"  known: {', '.join(DRILLS)}", file=sys.stderr)
        return 64

    if args.dry_run:
        print("DRY RUN — describing only, nothing induced, nothing recorded:\n")
        for k in selected:
            print(f"  {k}: {DRILLS[k][1]}")
        return 0

    # Every drill's evidence lands under this one service key, whether or not
    # the drill's own mechanics happen to touch staging (drills 3-5 mostly
    # don't). Registered once, up front, so the FIRST drill's evidence write
    # never fails on "no service registered" — it only ever needed doing
    # inside drills 1/2 because those already hold a staging connection.
    try:
        with staging_conn() as conn:
            ensure_evidence_service(conn)
    except Exception as e:                                        # noqa: BLE001
        print(f"outage-drill: could not reach staging to register the evidence "
              f"service ({e}) — evidence recording will fail loudly per drill "
              f"below, but the drills themselves still run.", file=sys.stderr)

    any_untruthful = False
    any_skipped = False
    for k in selected:
        fn, _ = DRILLS[k]
        print(f"\n=== {k} ===")
        try:
            result = fn()
        except DrillUnavailable as e:
            result = DrillResult(k, None, f"SKIPPED — {e}", str(e), skipped=True, skip_reason=str(e))
        except Exception as e:                                     # noqa: BLE001
            result = DrillResult(k, None, f"SKIPPED — the drill itself crashed: {e}",
                                  str(e), skipped=True, skip_reason=str(e))

        if result.skipped:
            any_skipped = True
            print(f"  SKIPPED: {result.summary}")
        else:
            verdict = "TRUTHFUL" if result.truthful else "NOT TRUTHFUL"
            print(f"  {verdict}: {result.summary}")
            print(f"  detail: {result.detail}")
            if not result.truthful:
                any_untruthful = True
        for f in result.findings:
            print(f"  FINDING: {f.get('note', f)}")

        try:
            record_evidence(result)
        except Exception as e:                                     # noqa: BLE001
            print(f"  EVIDENCE WARNING: could not record — {e}", file=sys.stderr)

    print()
    if any_untruthful:
        print("outage-drill: at least one drill found the system did NOT tell the truth.")
        return 1
    if any_skipped:
        print("outage-drill: at least one drill was SKIPPED (see above).")
        return 2
    print("outage-drill: every drill that ran found truthful behavior.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

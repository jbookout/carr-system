#!/usr/bin/env python3
"""built_unclosed.py — the missing second number: code on disk, not just closed.

WHY THIS EXISTS. capability-program reports completed=N (confirmed_closed
count). That is attestation truth, not build truth: a Work Request can have
artifacts already merged to main and still sit at state != confirmed_closed
because nobody ran prepare/attest/complete-capability-project. A session that
reads "0/51 completed" as "nothing is built" starts a rebuild of work that
already landed — which happened 24 times, 9 caught by a human.

This module is the local half of the latch. The Cloudflare Worker cannot stat
the repo; this can, because it runs in local hooks (SessionStart,
PreToolUse) that already see the tree and already read the exporter DB.

GENERALIZED 2026-08-21. The original hard-coded
program_key='carr-ai-engineering-suite-v1' and root='~/carr-system'. The
opslang state machine is:

  CONCEPTUAL:     captured → triaged → ready
                   (propose-ready-plan / accept-ready-plan)
  IMPLEMENTATION: claimed → in_progress → verification
  CONSTRUCTION:   verification → awaiting_release → released → confirmed_closed
                   (or verification → confirmed_closed)

A session must name its STAGE and its WORK REQUEST. If it cannot, it is a side
quest and must stop or file, not build. This module classifies every open work
request by stage so the session brief and the PreToolUse gate can say which
door is open.

DEFINITIONS:
  - built_unclosed: state != confirmed_closed AND evidence is a non-empty list
    AND every evidence path exists on disk in this checkout.
  - implementation_open: state in (claimed, in_progress, verification) — an
    active build session whose code may or may not be on disk yet.
  - conceptual_open: state in (captured, triaged) — a work request that has not
    reached ready and therefore has no accepted plan.  A ready row with no
    accepted plan would also be conceptual-open, but detecting that requires
    checking a separate plan field; skip it when that is not cheaply visible.
  - landed_in_repo: count of rows whose evidence paths all exist, regardless
    of state.  (confirmed_closed rows with evidence also count as landed.)

The evidence field lives in each Work Request's project_context JSON:
  project_context.evidence = ["path/to/artifact.py", ...]

Paths are repo-relative. A path that is not a real file (e.g. "MCP input
schemas", "workflow contracts") does not exist on disk and is NOT landed —
do not guess. Empty evidence is NOT landed.
"""
from __future__ import annotations

import json
import os
import os.path
from typing import Any


# ── stage classification ──────────────────────────────────────────────────

IMPLEMENTATION_STATES = frozenset(("claimed", "in_progress", "verification"))
CONCEPTUAL_STATES = frozenset(("captured", "triaged"))
# ready is the gate between conceptual and implementation.  A ready row
# normally has an accepted plan; if it does not, detecting that requires a
# field we do not assume here.  Skip rather than guess.

ALL_KNOWN_STATES = IMPLEMENTATION_STATES | CONCEPTUAL_STATES | frozenset(
    ("ready", "awaiting_release", "released", "confirmed_closed",
     "blocked", "failed", "needs_joe")
)


def _evidence_paths(row: dict[str, Any]) -> list[str]:
    """Extract evidence paths from a row's project_context, or []."""
    ctx = row.get("project_context") or row.get("context") or {}
    if not isinstance(ctx, dict):
        return []
    ev = ctx.get("evidence")
    if not isinstance(ev, list):
        return []
    return [p for p in ev if isinstance(p, str) and p.strip()]


def _all_exist(paths: list[str], root: str) -> bool:
    """True when paths is non-empty and every path exists under root."""
    if not paths:
        return False
    for p in paths:
        full = os.path.join(root, p)
        if not os.path.exists(full):
            return False
    return True


def detect_built_unclosed(rows: list[dict[str, Any]], root: str) -> list[dict[str, Any]]:
    """Return the subset of rows that are BUILT-UNCLOSED.

    A row is built-unclosed when:
      - state != confirmed_closed
      - evidence is non-empty
      - every evidence path exists on disk under root
    """
    out = []
    for row in rows:
        state = row.get("state", "")
        if state == "confirmed_closed":
            continue
        ev = row.get("evidence")
        if ev is None:
            ev = _evidence_paths(row)
        if not isinstance(ev, list) or not ev:
            continue
        if _all_exist(ev, root):
            out.append(row)
    return out


def detect_implementation_open(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the subset of rows whose state is in the implementation phase.

    IMPLEMENTATION-OPEN: claimed, in_progress, or verification.  These are
    work requests with an active build session — the code may not be on disk
    yet, but the session is running and a new side quest must not start.
    """
    out = []
    for row in rows:
        state = row.get("state", "")
        if state in IMPLEMENTATION_STATES:
            out.append(row)
    return out


def detect_conceptual_open(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the subset of rows whose state is in the conceptual phase.

    CONCEPTUAL-OPEN: captured or triaged.  A work request that has not reached
    ready has no accepted plan, so opening a new conceptual artifact (a design
    doc, a roadmap, a build plan) is a second front while the first is still
    open.  A ready row with no accepted plan would also qualify, but detecting
    that requires a field we do not assume here — skip it.
    """
    out = []
    for row in rows:
        state = row.get("state", "")
        if state in CONCEPTUAL_STATES:
            out.append(row)
    return out


def detect_all_open(rows: list[dict[str, Any]], root: str) -> dict[str, list[dict[str, Any]]]:
    """Classify every open work request by stage.

    Returns a dict with keys:
      - built_unclosed: code on disk, not confirmed_closed
      - implementation_open: in the implementation phase (claimed/in_progress/verification)
      - conceptual_open: in the conceptual phase (captured/triaged)

    A row can appear in both built_unclosed and implementation_open (code on
    disk AND state=verification), which is the normal case for a build that
    landed but was never attested.
    """
    return {
        "built_unclosed": detect_built_unclosed(rows, root),
        "implementation_open": detect_implementation_open(rows),
        "conceptual_open": detect_conceptual_open(rows),
    }


def landed_in_repo(rows: list[dict[str, Any]], root: str) -> int:
    """Count of rows whose evidence paths all exist on disk, regardless of state."""
    count = 0
    for row in rows:
        ev = row.get("evidence")
        if ev is None:
            ev = _evidence_paths(row)
        if not isinstance(ev, list):
            continue
        if _all_exist(ev, root):
            count += 1
    return count


def _exporter_url():
    """Resolve the exporter DB URL from the env var or ~/.config/carr/db.env.

    The env var is checked first; when it is absent or not a postgres URL, the
    conventional config file is read — the same pattern session-brief.py's
    dynamic_counts() uses. Returns the URL string or None.
    """
    url = os.environ.get("CARR_DB_EXPORTER_URL")
    if not url or not url.startswith(("postgres://", "postgresql://")):
        env_path = os.path.expanduser("~/.config/carr/db.env")
        if os.path.exists(env_path):
            with open(env_path) as fh:
                for line in fh:
                    if line.startswith("CARR_DB_EXPORTER_URL="):
                        url = line.split("=", 1)[1].strip().strip("\"'")
                        break
    if not url or not url.startswith(("postgres://", "postgresql://")):
        return None
    return url


def load_live_rows(program_key: str | None = None) -> list[dict[str, Any]] | None:
    """Load Work Request rows from the exporter DB when a URL is resolvable.

    Any program_key, or all rows when program_key is None.  Returns None when
    no URL is resolvable from the env var or db.env, or the DB is unreachable
    — the caller treats that as "fall back to the seed migration evidence
    paths."  Unit tests must never call this; pass fixtures to the detect_*
    functions instead.
    """
    url = _exporter_url()
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
            if program_key:
                cur.execute(
                    "select ref, state, program_key, project_context "
                    "from ops.work_request "
                    "where program_key = %s "
                    "order by program_ordinal",
                    (program_key,)
                )
            else:
                cur.execute(
                    "select ref, state, program_key, project_context "
                    "from ops.work_request "
                    "order by program_ordinal"
                )
            rows = []
            for ref, state, pk, ctx in cur.fetchall():
                row: dict[str, Any] = {"ref": ref, "state": state}
                if pk:
                    row["program_key"] = pk
                if isinstance(ctx, dict):
                    row["project_context"] = ctx
                    row["evidence"] = ctx.get("evidence", [])
                rows.append(row)
            return rows
    except Exception:
        return None

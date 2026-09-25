#!/usr/bin/env python3
# doctrine: doctorcre-v5-astra-integration-review
"""slice-done-marker.py — make the DoctorCRE v5 slice done-record fire.

WHY THIS EXISTS (2026-09-25, Joe: "fix the done record issue. If it's not
firing we need to know why and solve it"). #1245 shipped the Q153 doors
(register / mark progress / mark complete / read) and nothing ever called
them: production held 0 registrations, 0 criteria and 0 marks, so every one of
the 36 catalog slices read marked:false. Migration 0612 gives the automated
seat its own doors and two new server-resolved evidence kinds; this script is
the process that calls them. The release pipeline runs it after every
SHIPPED worker release (ops/release-pipeline.py, best-effort), and it runs by
hand for the backfill:

    ./.venv/bin/python ops/slice-done-marker.py            # every catalog slice
    ./.venv/bin/python ops/slice-done-marker.py --dry-run  # compute, write nothing
    ./.venv/bin/python ops/slice-done-marker.py --slices V5-F08,V5-J303

WHAT ONE RUN DOES, per catalog slice (the catalog is doctrine
`doctorcre-v5-astra-integration-review`, section
`v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09`):

  1. Membership. Every commit on origin/main since the catalog era whose
     subject names a catalog slice is attributed to it (Jev semantic_creation
     A1, 2026-09-25: an explicit `V5-<id>` always; a bare F/A/J/S id such as
     `F03` or `J303` as a whole word; never a bare R/D/M/W id, which collides
     with the older roadmap's R03 etc.). Each attributed commit is recorded
     against the EARLIEST complete production release that contains it
     (record-release-slice-members). The server accepts members only for a
     complete production release.
  2. Registration. An unregistered slice is registered with
     register-slice-criteria-from-catalog: the server reads the criteria from
     the catalog itself; this script never passes one.
  3. Binding. Each criterion still unbound (and never bound by a partner) is
     classified by Jev (semantic_creation): a source behaviour a shipped merge
     proves -> shipped_release; negatives whose production exercise would
     itself need a write -> refusal_proof/ci_gate (a shipped change whose CI
     gate proves the refusals); the restore exercise -> live_check on
     staging_restore_only_result; the DoctorCre-v5 portfolio's accepted,
     intact revision -> accepted_record; that acceptance creating no effects
     -> live_check portfolio_acceptance_effect_free; a runtime outcome with no
     server-recorded row -> left unbound. Only a confident answer binds (the
     seat binds a criterion once; a partner can rebind at any time and wins).
  4. Evidence, per criterion, from LIVE reads only. shipped_release and
     refusal_proof: Jev evidence_matching picks which shipped member (PR
     subject and body) implements the criterion, or none. Every other kind:
     the server's own newest candidate row and its `candidate_passes`, which
     read-slice-completion recomputes from the live rows on every read. The
     previous mark is never evidence of anything; the server re-resolves every
     ref on completion.
  5. Mark (Jev semantic_creation S1): every criterion has evidence ->
     auto-mark-slice-completion (the server recomputes and may refuse);
     any criterion still unbound, or a slice parked by Joe -> blocked with the
     reason; otherwise in_progress naming each missing fact. The previous mark
     is read for one thing only: not re-writing an identical mark. A
     partner-held slice is skipped.

It never writes a file outside out/slice-done-marker/, never force-anything,
and a failure is a nonzero exit the pipeline records without failing the
release it follows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
CATALOG_DOC = "doctorcre-v5-astra-integration-review"
CATALOG_SECTION = "v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09"
# Commits before this cannot belong to the 2026-09-09 catalog's slices.
HISTORY_SINCE = "2026-09-01"
# Joe's ruling: these are parked, not failing.
PARKED = {"V5-D03": "parked by Joe", "V5-D04": "parked by Joe"}
BARE_ID = re.compile(r"(?<![\w-])((?:F|A|S)\d{2}|J\d{3})(?![\w-])")
PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")
NAMESPACE = uuid.UUID("8f0b3a52-5f0e-4c55-9d7c-6b1a0f3e2d11")
BIND_MIN = 0.75
MATCH_MIN = 0.70
OUT_DIR = REPO / "out" / "slice-done-marker"

# The one accepted-record source the server resolves today.
PORTFOLIO_REF = "DoctorCre-v5"
REFUSAL_GATE = "ci:merge-gate"
REFUSAL_WRITE_REASON = ("Jev semantic_creation: the negatives are refusals of write verbs, so exercising them "
                        "in production would itself write; the shipped change's CI gate is the proof")

BIND_OPTIONS = {
    "source_behaviour": "The criterion states behaviour the slice's code, schema or checks implement; a merged, "
                        "shipped change that implements it is the proof (validations, stored shapes, "
                        "gates, projections, contracts) and it is NOT a list of negatives that refuse.",
    "refusal_needs_write": "The criterion is that negative / bypass cases REFUSE, and exercising those negatives "
                           "against production would itself require calling a write verb; a shipped change "
                           "whose CI gate runs the negatives is the proof.",
    "accepted_portfolio_record": "The criterion is proven by the DoctorCre-v5 portfolio's partner-accepted "
                                 "revision itself: its node/edge/child counts, acyclicity and digests, "
                                 "recomputed from the stored rows.",
    "acceptance_effect_free": "The criterion is that accepting the DoctorCre-v5 portfolio created zero jobs, "
                              "capability sessions or execution effects.",
    "restore_exercise": "The criterion is proven only by actually performing a database restore from an "
                        "independent copy and verifying it came back exact.",
    "runtime_outcome": "The criterion is proven only by a live runtime observation, activation, pilot, partner "
                       "decision or measured outcome that no shipped code change can show.",
}


class MarkerError(RuntimeError):
    pass


def ikey(*parts: Any) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(json.dumps(p, sort_keys=True, default=str) for p in parts)))


def attribute(subject: str, catalog_ids: set[str]) -> list[tuple[str, str]]:
    """(slice_id, attribution) for every catalog slice a commit subject names."""
    found: dict[str, str] = {}
    for sid in sorted(catalog_ids, key=len, reverse=True):
        if re.search(r"(?<![\w-])" + re.escape(sid) + r"(?![\w-])", subject):
            found[sid] = "explicit"
    for m in BARE_ID.finditer(subject):
        sid = f"V5-{m.group(1)}"
        if sid in catalog_ids and sid not in found:
            found[sid] = "bare_id"
    return sorted(found.items())


# ── adapters (replaced by fakes in ops/slice-done-marker-selftest.py) ─────────

def run_sh_call(verb: str, args: dict) -> dict:
    """The sanctioned Bash door, as ops/release-pipeline.py uses it."""
    proc = subprocess.run([str(REPO / "run.sh"), "call", verb, json.dumps(args)], cwd=str(REPO),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    text = (proc.stdout or "").strip()
    try:
        body = json.loads(text) if text else {}
    except ValueError:
        raise MarkerError(f"{verb}: unparseable reply (exit {proc.returncode}): {text[:300]}") from None
    if proc.returncode != 0 or (isinstance(body, dict) and body.get("ok") is False) \
            or (isinstance(body, dict) and body.get("error")):
        raise MarkerError(f"{verb}: {json.dumps(body)[:600] if body else (proc.stderr or '')[-300:]}")
    return body


def git(*args: str) -> str:
    proc = subprocess.run(["git", "-C", str(REPO), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise MarkerError(f"git {args[0]}: {(proc.stderr or '').strip()[:300]}")
    return proc.stdout


def jev_ask(state: dict, questions: dict, facets: list[str]) -> dict:
    sys.path.insert(0, str(REPO / "ops"))
    from typesafe_client import ask  # noqa: PLC0415 — only a live run needs the vendor client
    return ask(state, questions, facets=facets).get("answers", {})


def jev_choice(instructions: str, options: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


# ── the marker ────────────────────────────────────────────────────────────────

@dataclass
class SliceOutcome:
    slice_id: str
    status: str
    reason: str
    wrote: str
    refs: dict[str, str | None] = field(default_factory=dict)


class Marker:
    def __init__(self, *, call: Callable[[str, dict], dict], git_run: Callable[..., str],
                 ask: Callable[[dict, dict, list[str]], dict] | None, dry_run: bool = False,
                 out: Callable[[str], None] = print, cache_path: Path | None = None):
        self.call, self.git, self.ask, self.dry_run, self.out = call, git_run, ask, dry_run, out
        self.cache_path = cache_path
        self.cache: dict[str, Any] = {}
        if cache_path and cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text())
            except ValueError:
                self.cache = {}

    # -- catalog -----------------------------------------------------------
    def catalog(self) -> list[dict]:
        doc = self.call("read-doctrine", {"document": CATALOG_DOC})
        for section in doc.get("sections", []):
            if section.get("section_key") == CATALOG_SECTION:
                body = section.get("body") or {}
                text = body.get("text") if isinstance(body, dict) else body
                slices = json.loads(str(text)).get("slices") or []
                if not slices:
                    raise MarkerError("the catalog section names no slices")
                return slices
        raise MarkerError(f"catalog section {CATALOG_SECTION} not found in {CATALOG_DOC}")

    # -- membership ----------------------------------------------------------
    def attributed_commits(self, catalog_ids: set[str]) -> list[dict]:
        raw = self.git("log", "--first-parent", f"--since={HISTORY_SINCE}",
                       "--format=%H%x1f%s%x1f%b%x1e", "origin/main")
        commits = []
        for rec in raw.split("\x1e"):
            parts = rec.strip("\n").split("\x1f")
            if len(parts) < 2 or not re.fullmatch(r"[0-9a-f]{40}", parts[0].strip()):
                continue
            sha, subject = parts[0].strip(), parts[1].strip()
            body = parts[2].strip() if len(parts) > 2 else ""
            for sid, how in attribute(subject, catalog_ids):
                pr = PR_NUMBER.search(subject)
                commits.append({"slice_id": sid, "commit_sha": sha, "subject": subject[:400],
                                "pr_number": int(pr.group(1)) if pr else None, "attribution": how,
                                "body": body[:600]})
        return commits

    def sync_membership(self, catalog_ids: set[str], known: set[tuple[str, str, str]]) -> int:
        commits = self.attributed_commits(catalog_ids)
        if not commits:
            return 0
        releases = self.call("list-shipped-releases", {"since": HISTORY_SINCE}).get("releases", [])
        reach: list[tuple[str, set[str]]] = []
        for rel in releases:
            try:
                reach.append((rel["release_key"], set(self.git(
                    "rev-list", f"--since={HISTORY_SINCE}", rel["git_sha"]).split())))
            except MarkerError:
                continue          # a release SHA this checkout does not hold
        by_release: dict[str, list[dict]] = {}
        for c in commits:
            first = next((key for key, shas in reach if c["commit_sha"] in shas), None)
            if first is None or (first, c["slice_id"], c["commit_sha"]) in known:
                continue
            member = {k: c[k] for k in ("slice_id", "commit_sha", "pr_number", "subject", "attribution")}
            by_release.setdefault(first, []).append(member)
        written = 0
        for key, members in by_release.items():
            if self.dry_run:
                self.out(f"  [dry-run] record-release-slice-members {key}: {len(members)} member(s)")
                continue
            self.call("record-release-slice-members", {
                "idempotency_key": ikey("members", key, sorted((m["slice_id"], m["commit_sha"]) for m in members)),
                "release_key": key, "members": members})
            written += len(members)
        self._bodies = {c["commit_sha"]: c["body"] for c in commits}
        return written

    # -- Jev ---------------------------------------------------------------
    def _cached_ask(self, kind: str, state: dict, questions: dict, facets: list[str]) -> dict:
        if not questions:
            return {}
        key = hashlib.sha256(json.dumps([kind, state, questions], sort_keys=True).encode()).hexdigest()
        if key in self.cache:
            return self.cache[key]
        if self.ask is None:
            return {}
        answers = self.ask(state, questions, facets)
        self.cache[key] = answers
        return answers

    @staticmethod
    def _pick(answer: dict | None, floor: float) -> str | None:
        if not answer or answer.get("type") != "choice":
            return None
        choice = answer.get("choice")
        prob = (answer.get("probabilities") or {}).get(choice)
        if prob is None:
            prob = answer.get("confidence") or 0.0
        return choice if float(prob) >= floor else None

    def classify(self, item: dict, criteria: list[str]) -> dict[str, str | None]:
        state = {"slice": {"id": item.get("proposed_id"), "title": item.get("title"),
                           "kind": item.get("item_kind"), "goal": str(item.get("goal") or "")[:800]},
                 "criteria": criteria}
        questions = {f"semantic_creation_bind_{i}": jev_choice(
            f"How is criterion `criteria[{i}]` of this DoctorCRE v5 slice proven? Choose what would PROVE it, "
            "not what would help.", BIND_OPTIONS) for i in range(len(criteria))}
        answers = self._cached_ask("bind", state, questions, ["semantic_creation"])
        return {c: self._pick(answers.get(f"semantic_creation_bind_{i}"), BIND_MIN) for i, c in enumerate(criteria)}

    def match(self, item: dict, criteria: list[str], members: list[dict]) -> dict[str, str | None]:
        opts = {f"m{j}": f"PR #{m.get('pr_number') or '?'}: {m['subject']} -- "
                         f"{getattr(self, '_bodies', {}).get(m['commit_sha'], '')[:300]}"
                for j, m in enumerate(members)}
        opts["none"] = "No listed shipped change implements this criterion."
        state = {"slice": {"id": item.get("proposed_id"), "title": item.get("title")}, "criteria": criteria}
        questions = {f"evidence_matching_{i}": jev_choice(
            f"Which shipped change implements criterion `criteria[{i}]` of this slice? Choose `none` unless "
            "the change clearly implements that exact criterion.", opts) for i in range(len(criteria))}
        answers = self._cached_ask("match", state, questions, ["evidence_matching"])
        out: dict[str, str | None] = {}
        for i, c in enumerate(criteria):
            pick = self._pick(answers.get(f"evidence_matching_{i}"), MATCH_MIN)
            out[c] = members[int(pick[1:])]["id"] if pick and pick != "none" else None
        return out

    # -- one slice ---------------------------------------------------------
    def read(self, slice_id: str) -> dict:
        return self.call("read-slice-completion", {"slice_id": slice_id}).get("done_state") or {}

    def mark_slice(self, item: dict) -> SliceOutcome:
        sid = item["proposed_id"]
        state = self.read(sid)
        if not state.get("registered"):
            if self.dry_run:
                self.out(f"  [dry-run] register-slice-criteria-from-catalog {sid}")
                pending = [{"criterion": c, "evidence_kind": "unbound", "binding_source": "registration",
                             "automation_bound": False} for c in item.get("checkable_done") or []]
                state = {"registered": False, "criteria": pending, "release_members": state.get("release_members", [])}
            else:
                self.call("register-slice-criteria-from-catalog",
                          {"idempotency_key": ikey("register", sid), "slice_id": sid})
                state = self.read(sid)
        if state.get("held_by_partner"):
            latest = state.get("latest_mark") or {}
            return SliceOutcome(sid, latest.get("status") or "?", "held by partner", "skipped")
        criteria = [row["criterion"] for row in state.get("criteria", [])]
        kinds = {row["criterion"]: row for row in state.get("criteria", [])}

        if sid in PARKED:
            return self._write(sid, state, "blocked", PARKED[sid], {c: None for c in criteria})

        # Binding: only criteria still unbound, never bound by automation, and
        # not partner-decided (a partner binding reports binding_source
        # binding:authority -- including an explicit partner unbind).
        todo = [c for c in criteria if kinds[c]["evidence_kind"] == "unbound"
                and not kinds[c].get("automation_bound") and kinds[c].get("binding_source") == "registration"]
        if todo:
            decided = self.classify(item, todo)
            for c in todo:
                kind = decided.get(c)
                if kind == "source_behaviour":
                    self._bind(sid, c, "shipped_release", None, "Jev semantic_creation: source behaviour")
                elif kind == "refusal_needs_write":
                    self._bind(sid, c, "refusal_proof", "ci_gate", "Jev semantic_creation: write-requiring negatives",
                               key=REFUSAL_GATE, write_reason=REFUSAL_WRITE_REASON)
                elif kind == "restore_exercise":
                    self._bind(sid, c, "live_check", "staging_restore_only_result",
                               "Jev semantic_creation: restore exercise")
                elif kind == "accepted_portfolio_record":
                    self._bind(sid, c, "accepted_record", "portfolio_revision_acceptance",
                               "Jev semantic_creation: accepted portfolio record", key=PORTFOLIO_REF)
                elif kind == "acceptance_effect_free":
                    self._bind(sid, c, "live_check", "portfolio_acceptance_effect_free",
                               "Jev semantic_creation: acceptance created no effects", key=PORTFOLIO_REF)
            if not self.dry_run:
                state = self.read(sid)
                kinds = {row["criterion"]: row for row in state.get("criteria", [])}

        members = state.get("release_members") or []
        shipped = [c for c in criteria if kinds[c]["evidence_kind"] in ("shipped_release", "refusal_proof")]
        matched = self.match(item, shipped, members) if shipped and members else {}
        refs: dict[str, str | None] = {}
        missing: list[str] = []
        unbound: list[str] = []
        for c in criteria:
            k = kinds[c]
            kind = k["evidence_kind"]
            if kind in ("shipped_release", "refusal_proof"):
                refs[c] = matched.get(c)
                if refs[c] is None:
                    missing.append(f"{c} -> " + ("no shipped merge attributed to this slice is in a complete "
                                                 "production release" if not members else
                                                 "no shipped change was matched to this criterion"))
            elif kind in ("live_check", "accepted_record"):
                # The server's live recompute of its own newest candidate.
                cand = k.get("live_check_candidate")
                refs[c] = cand if cand and k.get("candidate_passes") is True else None
                if refs[c] is None:
                    what = f"{k.get('live_check_source')}" + (f" for {k['live_check_key']}" if k.get("live_check_key") else "")
                    missing.append(f"{c} -> " + (f"no {what} row" if not cand else
                                                 f"the newest {what} row ({cand}) does not pass on a live read"))
            elif kind == "unbound":
                refs[c] = None
                unbound.append(f"{c} -> no server-resolvable evidence binding (no server-recorded receipt "
                               "proves it today; a partner may bind it)")
            else:
                refs[c] = None
                missing.append(f"{c} -> {kind} evidence is partner-registered; the marker does not resolve it")

        if not missing and not unbound and criteria:
            latest = state.get("latest_mark") or {}
            prior = {el.get("criterion"): el.get("evidence_ref") for el in latest.get("criteria_receipt") or []}
            if latest.get("status") == "complete" and prior == refs:
                return SliceOutcome(sid, "complete", "every criterion proven", "unchanged", refs)
            if self.dry_run:
                return SliceOutcome(sid, "complete", "all criteria proven", "dry-run", refs)
            try:
                self.call("auto-mark-slice-completion", {
                    "idempotency_key": ikey("complete", sid, (state.get("latest_mark") or {}).get("id"), refs),
                    "slice_id": sid, "reason": "every criterion proven by server-resolved evidence",
                    "criteria_receipt": [{"criterion": c, "evidence_ref": refs[c]} for c in criteria]})
                return SliceOutcome(sid, "complete", "every criterion proven", "complete", refs)
            except MarkerError as exc:
                missing.append(f"server refused completion: {str(exc)[:300]}")
        if unbound:
            return self._write(sid, state, "blocked", "blocked: " + "; ".join(unbound + missing), refs)
        return self._write(sid, state, "in_progress", "missing: " + "; ".join(missing), refs)

    def _bind(self, sid: str, criterion: str, kind: str, source: str | None, reason: str, *,
              key: str | None = None, write_reason: str | None = None) -> None:
        if self.dry_run:
            self.out(f"  [dry-run] bind {sid} / {criterion[:60]} -> {kind}" + (f" {source}" if source else ""))
            return
        self.call("bind-slice-criterion-evidence", {
            "idempotency_key": ikey("bind", sid, criterion, kind, source, key), "slice_id": sid,
            "criterion": criterion, "evidence_kind": kind, "live_check_source": source,
            "live_check_key": key, "write_required_reason": write_reason, "reason": reason})

    def _write(self, sid: str, state: dict, status: str, reason: str, refs: dict[str, str | None]) -> SliceOutcome:
        latest = state.get("latest_mark") or {}
        prior_refs = {el.get("criterion"): el.get("evidence_ref") for el in latest.get("criteria_receipt") or []}
        if latest.get("status") == status and latest.get("reason") == reason and prior_refs == refs:
            return SliceOutcome(sid, status, reason, "unchanged", refs)
        if self.dry_run or not refs:
            return SliceOutcome(sid, status, reason, "dry-run" if self.dry_run else "no-criteria", refs)
        self.call("mark-slice-progress", {
            "idempotency_key": ikey("progress", sid, latest.get("id"), status, reason, refs),
            "slice_id": sid, "status": status, "reason": reason,
            "criteria_receipt": [{"criterion": c, "evidence_ref": r} for c, r in refs.items()]})
        return SliceOutcome(sid, status, reason, "marked", refs)

    # -- a run ---------------------------------------------------------------
    def run(self, only: set[str] | None = None) -> list[SliceOutcome]:
        catalog = self.catalog()
        ids = {s["proposed_id"] for s in catalog}
        wanted = [s for s in catalog if not only or s["proposed_id"] in only]
        known: set[tuple[str, str, str]] = set()
        for s in wanted:
            for m in self.read(s["proposed_id"]).get("release_members") or []:
                known.add((m["release_key"], s["proposed_id"], m["commit_sha"]))
        added = self.sync_membership(ids, known)
        self.out(f"slice-done-marker: {added} new release member(s)")
        outcomes = []
        for item in wanted:
            try:
                outcomes.append(self.mark_slice(item))
            except MarkerError as exc:
                outcomes.append(SliceOutcome(item["proposed_id"], "error", str(exc)[:400], "error"))
        if self.cache_path and not self.dry_run:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, sort_keys=True))
        return outcomes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true", help="compute and print; write nothing")
    ap.add_argument("--slices", default="", help="comma-separated slice ids (default: every catalog slice)")
    ap.add_argument("--release-key", default="", help="the release that just shipped (logged)")
    ap.add_argument("--no-jev", action="store_true", help="bind and match nothing new (cached answers only)")
    args = ap.parse_args(argv)
    marker = Marker(call=run_sh_call, git_run=git, ask=None if args.no_jev else jev_ask,
                    dry_run=args.dry_run, cache_path=OUT_DIR / "jev-cache.json")
    if args.release_key:
        print(f"slice-done-marker: after release {args.release_key}")
    try:
        outcomes = marker.run({s.strip() for s in args.slices.split(",") if s.strip()} or None)
    except MarkerError as exc:
        print(f"slice-done-marker: FAILED — {exc}", file=sys.stderr)
        return 2
    for o in outcomes:
        print(f"  {o.slice_id:9} {o.status:11} [{o.wrote}] {o.reason[:160]}")
    if not args.dry_run:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "last-run.json").write_text(json.dumps(
            [o.__dict__ for o in outcomes], indent=1, default=str))
    return 1 if any(o.status == "error" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())

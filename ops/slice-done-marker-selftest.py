#!/usr/bin/env python3
"""Behavioural selftest for ops/slice-done-marker.py.

The server is an in-memory fake of the 0612 doors: it keeps registrations,
bindings, release members and append-only marks, resolves evidence refs
itself (a shipped_release ref must be this slice's member; a live_check ref
must be the fake's successful receipt), and refuses completion unless every
criterion resolves -- so the marker's own claims are never what passes a
slice. git and Jev are fakes too; nothing here touches a network or a
database. The real SQL semantics are proven by
ops/slice-done-marker-local-pg-gate.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("slice_done_marker", HERE / "slice-done-marker.py")
assert spec and spec.loader
sdm = importlib.util.module_from_spec(spec)
sys.modules["slice_done_marker"] = sdm
spec.loader.exec_module(sdm)

SRC = "V5-F08 restore proof (#10)"
CATALOG = [
    {"proposed_id": "V5-F08", "title": "Backup and restore", "item_kind": "coding_slice",
     "checkable_done": ["scanner refuses PHI", "restore from independent copy succeeds"]},
    {"proposed_id": "V5-J303", "title": "Tour delivery", "item_kind": "coding_slice",
     "checkable_done": ["client-shared tours see only ruled fields"]},
    {"proposed_id": "V5-R01", "title": "Pilot", "item_kind": "coding_slice",
     "checkable_done": ["Joe pilot observed for two weeks"]},
    {"proposed_id": "V5-R03", "title": "Notifications", "item_kind": "coding_slice",
     "checkable_done": ["quiet hours respected"]},
    {"proposed_id": "V5-D03", "title": "Recording successor", "item_kind": "deferred_successor",
     "checkable_done": ["retention policy decided"]},
]
SHA_F08 = "a" * 40
SHA_J303 = "b" * 40
SHA_R03 = "c" * 40
REL1, REL2 = "1" * 40, "2" * 40
RESTORE_RECEIPT = "00000000-0000-4000-8000-00000000abcd"


class FakeServer:
    def __init__(self, catalog=CATALOG, releases=(("r-1", REL1), ("r-2", REL2)), restore_receipt=RESTORE_RECEIPT):
        self.catalog = catalog
        self.releases = list(releases)
        self.restore_receipt = restore_receipt
        self.registered: dict[str, list[str]] = {}
        self.bindings: dict[tuple[str, str], dict] = {}
        self.members: list[dict] = []
        self.marks: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.refuse_complete = False

    def __call__(self, verb, args):
        self.calls.append((verb, json.loads(json.dumps(args))))
        return getattr(self, "v_" + verb.replace("-", "_"))(args)

    def writes(self):
        return [v for v, _ in self.calls if v not in ("read-doctrine", "read-slice-completion", "list-shipped-releases")]

    def v_read_doctrine(self, _args):
        return {"sections": [{"section_key": sdm.CATALOG_SECTION,
                              "body": {"text": json.dumps({"slices": self.catalog})}}]}

    def v_list_shipped_releases(self, _args):
        return {"releases": [{"release_key": k, "git_sha": s} for k, s in self.releases]}

    def v_record_release_slice_members(self, args):
        assert args["release_key"] in dict(self.releases)
        for m in args["members"]:
            if not any(x["release_key"] == args["release_key"] and x["commit_sha"] == m["commit_sha"]
                       and x["slice_id"] == m["slice_id"] for x in self.members):
                self.members.append({**m, "release_key": args["release_key"], "id": f"mem-{len(self.members)}"})
        return {"ok": True}

    def v_register_slice_criteria_from_catalog(self, args):
        assert set(args) == {"idempotency_key", "slice_id"}, "the marker must never pass criteria"
        sid = args["slice_id"]
        assert sid not in self.registered
        self.registered[sid] = next(s["checkable_done"] for s in self.catalog if s["proposed_id"] == sid)
        return {"ok": True}

    def v_bind_slice_criterion_evidence(self, args):
        key = (args["slice_id"], args["criterion"])
        if key in self.bindings:
            raise sdm.MarkerError("criterion_already_bound")
        self.bindings[key] = {"kind": args["evidence_kind"], "source": args["live_check_source"], "via": "automation"}
        return {"ok": True}

    def partner_bind(self, sid, criterion, kind):
        self.bindings[(sid, criterion)] = {"kind": kind, "source": None, "via": "authority"}

    def resolve(self, sid, criterion, ref):
        b = self.bindings.get((sid, criterion))
        if not b or ref is None:
            return False
        if b["kind"] == "shipped_release":
            return any(m["id"] == ref and m["slice_id"] == sid for m in self.members)
        if b["kind"] == "live_check":
            return ref == self.restore_receipt
        return False

    def receipt(self, sid, items):
        return [{"criterion": i["criterion"], "evidence_ref": i["evidence_ref"],
                 "pass": self.resolve(sid, i["criterion"], i["evidence_ref"])} for i in items]

    def v_mark_slice_progress(self, args):
        sid = args["slice_id"]
        if self.held(sid):
            raise sdm.MarkerError("slice_mark_held_by_partner")
        self.marks.setdefault(sid, []).append({"id": f"mark-{sid}-{len(self.marks.get(sid, []))}",
            "status": args["status"], "reason": args.get("reason"), "marked_via": "automation",
            "criteria_receipt": self.receipt(sid, args["criteria_receipt"])})
        return {"ok": True}

    def v_auto_mark_slice_completion(self, args):
        sid = args["slice_id"]
        if self.refuse_complete or self.held(sid):
            raise sdm.MarkerError("slice_completion_mark_refused")
        rec = self.receipt(sid, args["criteria_receipt"])
        if not all(r["pass"] for r in rec) or {r["criterion"] for r in rec} != set(self.registered[sid]):
            raise sdm.MarkerError("slice_completion_complete_requires_every_criterion_proven")
        self.marks.setdefault(sid, []).append({"id": f"mark-{sid}-{len(self.marks.get(sid, []))}",
            "status": "complete", "reason": args.get("reason"), "marked_via": "automation", "criteria_receipt": rec})
        return {"ok": True}

    def hold(self, sid):
        self.marks.setdefault(sid, []).append({"id": f"hold-{sid}", "status": "blocked", "reason": "partner",
                                               "marked_via": "authority_hold", "criteria_receipt": []})

    def held(self, sid):
        m = self.marks.get(sid)
        return bool(m) and m[-1]["marked_via"] in ("authority", "authority_hold")

    def v_read_slice_completion(self, args):
        sid = args["slice_id"]
        members = [{"id": m["id"], "release_key": m["release_key"], "commit_sha": m["commit_sha"],
                    "pr_number": m.get("pr_number"), "subject": m["subject"]} for m in self.members
                   if m["slice_id"] == sid]
        criteria = []
        for c in self.registered.get(sid, []):
            b = self.bindings.get((sid, c))
            criteria.append({
                "criterion": c, "evidence_kind": b["kind"] if b else "unbound",
                "binding_source": f"binding:{b['via']}" if b else "registration",
                "automation_bound": bool(b and b["via"] == "automation"),
                "live_check_source": b["source"] if b else None, "live_check_key": None,
                "live_check_candidate": self.restore_receipt if b and b["kind"] == "live_check" else None})
        marks = self.marks.get(sid) or []
        return {"ok": True, "done_state": {
            "registered": sid in self.registered, "criteria": criteria, "release_members": members,
            "held_by_partner": self.held(sid), "latest_mark": marks[-1] if marks else None}}


def fake_git(commits, reach):
    """commits: [(sha, subject)] newest first; reach: {release_sha: set(commit shas)}."""
    def run(*args):
        if args[0] == "log":
            return "".join(f"{sha}\x1f{subject}\x1fbody of {subject}\x1e" for sha, subject in commits)
        if args[0] == "rev-list":
            return "\n".join(sorted(reach.get(args[-1], set())))
        raise AssertionError(args)
    return run


class FakeJev:
    """Classifies by keyword and matches the first member; records calls."""

    def __init__(self, bind_prob=0.95, match="m0", match_prob=0.9):
        self.bind_prob, self.match, self.match_prob = bind_prob, match, match_prob
        self.calls: list[list[str]] = []

    def __call__(self, state, questions, facets):
        self.calls.append(facets)
        answers = {}
        for qid in questions:
            if qid.startswith("semantic_creation_bind_"):
                c = state["criteria"][int(qid.rsplit("_", 1)[1])]
                pick = ("restore_exercise" if "restore" in c else
                        "runtime_outcome" if "observed" in c or "decided" in c else "source_behaviour")
                answers[qid] = {"type": "choice", "choice": pick, "probabilities": {pick: self.bind_prob}}
            else:
                answers[qid] = {"type": "choice", "choice": self.match, "probabilities": {self.match: self.match_prob}}
        return answers


COMMITS = [(SHA_R03, "R03 sweep runner keeps HEAD==pin (#850)"),
           (SHA_J303, "J303 client-shared tours allowlist (#12)"),
           (SHA_F08, SRC)]


def marker(server, jev=None, commits=COMMITS, reach=None):
    reach = reach if reach is not None else {REL1: {SHA_F08}, REL2: {SHA_F08, SHA_R03}}
    return sdm.Marker(call=server, git_run=fake_git(commits, reach), ask=jev or FakeJev(), out=lambda _s: None)


def by_id(outcomes):
    return {o.slice_id: o for o in outcomes}


class Attribution(unittest.TestCase):
    IDS = {c["proposed_id"] for c in CATALOG} | {"V5-F03", "V5-A00", "V5-RW02"}

    def test_explicit_and_bare_ids(self):
        self.assertEqual(sdm.attribute("Make a restore prove it (V5-F08) (#1240)", self.IDS), [("V5-F08", "explicit")])
        self.assertEqual(sdm.attribute("Deliver F03 validator migration (#1037)", self.IDS), [("V5-F03", "bare_id")])
        self.assertEqual(sdm.attribute("Add A00 benchmark draft storage", self.IDS), [("V5-A00", "bare_id")])
        self.assertEqual(sdm.attribute("Add the reconciliation kernel (V5-RW02)", self.IDS), [("V5-RW02", "explicit")])

    def test_roadmap_ids_and_ux_packages_are_not_attributed(self):
        self.assertEqual(sdm.attribute("R03 sweep runner: keep the HEAD==pin invariant", self.IDS), [])
        self.assertEqual(sdm.attribute("Add DoctorCRE V5-UX-C02 resource dashboard", self.IDS), [])
        self.assertEqual(sdm.attribute("D03 retention and M01 cadence", self.IDS), [])


class Run(unittest.TestCase):
    def test_backfill_marks_complete_in_progress_blocked_and_parked(self):
        server = FakeServer()
        out = by_id(marker(server).run())
        self.assertEqual(out["V5-F08"].status, "complete", out["V5-F08"])
        self.assertEqual(server.marks["V5-F08"][-1]["status"], "complete")
        self.assertTrue(all(r["pass"] for r in server.marks["V5-F08"][-1]["criteria_receipt"]))
        # J303's merge is in no complete release yet.
        self.assertEqual(out["V5-J303"].status, "in_progress")
        self.assertIn("no shipped merge", out["V5-J303"].reason)
        # A runtime outcome with no receipt source stays unbound: blocked, named.
        self.assertEqual(out["V5-R01"].status, "blocked")
        self.assertIn("Joe pilot observed for two weeks -> no server-resolvable evidence binding", out["V5-R01"].reason)
        self.assertEqual((out["V5-D03"].status, out["V5-D03"].reason), ("blocked", "parked by Joe"))
        # Every catalog slice ends marked.
        self.assertEqual(set(server.marks), {c["proposed_id"] for c in CATALOG})

    def test_membership_goes_to_the_earliest_containing_release_and_skips_roadmap_ids(self):
        server = FakeServer()
        marker(server).run()
        self.assertEqual([(m["release_key"], m["slice_id"]) for m in server.members], [("r-1", "V5-F08")])

    def test_a_second_run_writes_nothing(self):
        server = FakeServer()
        marker(server).run()
        before = len(server.writes())
        jev = FakeJev()
        marker(server, jev).run()
        self.assertEqual(server.writes()[before:], [])

    def test_a_partner_hold_is_skipped_untouched(self):
        server = FakeServer()
        marker(server).run()
        server.hold("V5-J303")
        before = len(server.writes())
        out = by_id(marker(server).run())
        self.assertEqual(out["V5-J303"].wrote, "skipped")
        self.assertEqual(server.writes()[before:], [])

    def test_low_confidence_binding_binds_nothing_and_blocks(self):
        server = FakeServer()
        out = by_id(marker(server, FakeJev(bind_prob=0.5)).run())
        self.assertEqual(server.bindings, {})
        self.assertEqual(out["V5-F08"].status, "blocked")

    def test_a_none_match_is_not_evidence(self):
        server = FakeServer()
        out = by_id(marker(server, FakeJev(match="none")).run())
        self.assertEqual(out["V5-F08"].status, "in_progress")
        self.assertIn("scanner refuses PHI -> no shipped change was matched", out["V5-F08"].reason)

    def test_a_low_confidence_match_is_not_evidence(self):
        server = FakeServer()
        out = by_id(marker(server, FakeJev(match_prob=0.4)).run())
        self.assertEqual(out["V5-F08"].status, "in_progress")

    def test_server_refusal_of_completion_is_recorded_as_in_progress(self):
        server = FakeServer()
        server.refuse_complete = True
        out = by_id(marker(server).run())
        self.assertEqual(out["V5-F08"].status, "in_progress")
        self.assertIn("server refused completion", out["V5-F08"].reason)

    def test_missing_live_receipt_names_the_source(self):
        server = FakeServer(restore_receipt=None)
        out = by_id(marker(server).run())
        self.assertEqual(out["V5-F08"].status, "in_progress")
        self.assertIn("no successful staging_restore_only_result receipt", out["V5-F08"].reason)

    def test_a_partner_decided_criterion_is_never_reclassified(self):
        # A partner explicitly UNBOUND a criterion Jev would call source
        # behaviour: the marker must leave it alone and report it blocked.
        server = FakeServer()
        server.registered["V5-R03"] = ["quiet hours respected"]
        server.partner_bind("V5-R03", "quiet hours respected", "unbound")
        out = by_id(marker(server, FakeJev()).run({"V5-R03"}))
        self.assertNotIn("bind-slice-criterion-evidence",
                         [v for v, a in server.calls if a.get("slice_id") == "V5-R03"])
        self.assertEqual(server.bindings[("V5-R03", "quiet hours respected")]["via"], "authority")
        self.assertEqual(out["V5-R03"].status, "blocked")

    def test_dry_run_writes_nothing(self):
        server = FakeServer()
        m = marker(server)
        m.dry_run = True
        out = by_id(m.run())
        self.assertEqual(server.writes(), [])
        self.assertEqual(out["V5-D03"].status, "blocked")

    def test_idempotency_keys_are_stable_and_distinct(self):
        self.assertEqual(sdm.ikey("register", "V5-F08"), sdm.ikey("register", "V5-F08"))
        self.assertNotEqual(sdm.ikey("register", "V5-F08"), sdm.ikey("register", "V5-F09"))


if __name__ == "__main__":
    unittest.main()

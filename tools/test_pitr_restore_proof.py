"""Lifecycle tests for tools/pitr-restore-proof.py (V5-F08, the RPO proof).

The provider API and the database are replaced by in-memory fakes; the proof's
own code runs unchanged. What is held:
  * the metering admission runs before any branch POST, and a refusal means no
    POST is ever made;
  * the branch name is in the local state file BEFORE the create call, and a
    create that fails ambiguously is found by that unique name and deleted;
  * a delete counts only when the provider answers 404 afterwards;
  * the sweep deletes only prefixed-and-stale or state-listed branches, never
    the default or a protected one;
  * prove always tears down the branches it created, on failure too, and asks
    for the branch with parent_id = production and parent_timestamp = T;
  * verify recomputes deletion, the default branch and both probe rows from
    the provider and production instead of copying them from the file.

  .venv/bin/python -m unittest tools/test_pitr_restore_proof.py
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pitr_restore_proof", REPO / "tools" / "pitr-restore-proof.py")
if spec is None or spec.loader is None:
    raise ImportError("cannot load tools/pitr-restore-proof.py")
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)

P = f"/projects/{proof.PROJECT_ID}"


def ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeProvider:
    def __init__(self):
        self.branches = {
            "br-prod": {"id": "br-prod", "name": "production", "default": True, "created_at": ago(days=90)},
        }
        self.calls: list[tuple[str, str, dict | None]] = []
        self.post_raises = False
        self.delete_sticks = False
        self.counter = 0

    def api(self, method, path, body=None, query=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == f"{P}/branches":
            return 200, {"branches": list(self.branches.values())}
        if method == "GET" and path == P:
            return 200, {"project": {"history_retention_seconds": 604800}}
        if method == "POST" and path == f"{P}/branches":
            self.counter += 1
            new = {"id": f"br-new-{self.counter}", "name": body["branch"]["name"], "default": False,
                   "created_at": ago(seconds=0), "parent_id": body["branch"]["parent_id"],
                   "parent_timestamp": body["branch"].get("parent_timestamp"), "parent_lsn": "0/1A2B3C4D",
                   "current_state": "ready"}
            self.branches[new["id"]] = new
            if self.post_raises:
                raise TimeoutError("response lost")
            return 201, {"branch": new}
        if path.startswith(f"{P}/branches/"):
            bid = path.rsplit("/", 1)[1]
            if method == "DELETE":
                if bid in self.branches and not self.delete_sticks:
                    del self.branches[bid]
                return 200, {}
            return (200, {"branch": self.branches[bid]}) if bid in self.branches else (404, {})
        raise AssertionError(f"unexpected {method} {path}")

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeProvider()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name) / "state.json"
        for target, value in (("api", self.fake.api), ("STATE", state), ("say", lambda _m: None)):
            patcher = mock.patch.object(proof, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleep = mock.patch.object(proof.time, "sleep", lambda _s: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def admission(self, returncode=0):
        result = mock.Mock(returncode=returncode, stderr="refused: too many branches")
        return mock.patch.object(proof.subprocess, "run", return_value=result)


class Lifecycle(Base):
    def test_admission_refusal_means_no_post(self):
        with self.admission(77):
            with self.assertRaisesRegex(proof.ProofError, "metering admission refused"):
                proof.create_branch("pitr-proof-x", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"})
        self.assertEqual(self.fake.posts(), [])

    def test_create_asks_for_production_at_T_and_records_the_name_first(self):
        seen_state = []
        real_api = self.fake.api

        def spying(method, path, body=None, query=None):
            if method == "POST":
                seen_state.append(json.loads(proof.STATE.read_text()))
            return real_api(method, path, body, query)

        with self.admission(), mock.patch.object(proof, "api", spying):
            branch = proof.create_branch("pitr-proof-x", "br-prod", "2026-09-24T12:00:00Z", {"br-prod"})
        self.assertEqual(seen_state[0][0]["name"], "pitr-proof-x")
        body = self.fake.posts()[0][2]
        self.assertEqual(body["branch"]["parent_id"], "br-prod")
        self.assertEqual(body["branch"]["parent_timestamp"], "2026-09-24T12:00:00Z")
        self.assertIn("expires_at", body["branch"])
        self.assertEqual(json.loads(proof.STATE.read_text())[0]["id"], branch["id"])

    def test_ambiguous_create_is_found_by_name_and_deleted(self):
        self.fake.post_raises = True
        with self.admission():
            with self.assertRaisesRegex(proof.ProofError, "branch create failed"):
                proof.create_branch("pitr-proof-lost", "br-prod", None, {"br-prod"})
        self.assertEqual([b["name"] for b in self.fake.branches.values()], ["production"])

    def test_delete_counts_only_on_a_404_readback(self):
        self.fake.branches["br-x"] = {"id": "br-x", "name": "pitr-proof-x", "default": False, "created_at": ago(seconds=5)}
        self.fake.delete_sticks = True
        self.assertFalse(proof.delete_and_confirm("br-x", {"br-prod"}))
        self.fake.delete_sticks = False
        self.assertTrue(proof.delete_and_confirm("br-x", {"br-prod"}))
        with self.assertRaises(proof.ProofError):
            proof.delete_and_confirm("br-prod", {"br-prod"})

    def test_sweep_deletes_only_stale_proof_branches_and_state_listed_ones(self):
        self.fake.branches.update({
            "br-old": {"id": "br-old", "name": "pitr-proof-20260101", "default": False, "created_at": ago(hours=2)},
            "br-young": {"id": "br-young", "name": "pitr-proof-20260924", "default": False, "created_at": ago(minutes=5)},
            "br-dev": {"id": "br-dev", "name": "dev-work", "default": False, "created_at": ago(hours=5)},
            "br-listed": {"id": "br-listed", "name": "odd-name", "default": False, "created_at": ago(minutes=1)},
            "br-guard": {"id": "br-guard", "name": "pitr-proof-guard", "default": False, "protected": True, "created_at": ago(hours=9)},
        })
        proof.save_state([{"id": "br-listed", "name": "odd-name"}])
        proof.sweep({"br-prod"})
        self.assertEqual(sorted(self.fake.branches), ["br-dev", "br-guard", "br-prod", "br-young"])


class ProveAndVerify(Base):
    def fake_db(self, *, branch_rows):
        """A connect() whose sessions answer the proof's queries; the probe writes return server-stamped rows."""
        clock = {"t": datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc).timestamp()}
        written = {}

        class Conn:
            def __init__(self, uri, read_only):
                self.uri, self.read_only = uri, read_only

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(inner, sql, params=None):
                res = mock.Mock()
                if "clock_timestamp()" in sql and "write_pitr_probe" not in sql:
                    clock["t"] += 7.3
                    res.fetchone.return_value = (clock["t"],)
                elif "write_pitr_probe" in sql:
                    assert not inner.read_only
                    role = params[0]
                    iso = datetime.fromtimestamp(clock["t"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    row = (f"00000000-0000-4000-8000-00000000000{len(written) + 1}", ("a" if role == "positive" else "b") * 32, iso)
                    written[role] = row
                    res.fetchone.return_value = row
                elif "from ops.pitr_probe" in sql:
                    assert inner.read_only
                    rows = branch_rows(written) if "branch" in inner.uri else list(written.values())
                    res.fetchall.return_value = rows
                elif "count(*)" in sql:
                    res.fetchone.return_value = (12,)
                return res

        return lambda uri, read_only, attempts=40: Conn(uri, read_only)

    def run_prove(self, branch_rows):
        uris = {"br-prod": "postgresql://u@prod-host/neondb"}
        self.db = self.fake_db(branch_rows=branch_rows)
        with self.admission(), \
             mock.patch.object(proof, "connection_uri", lambda bid: uris.get(bid, f"postgresql://u@branch-{bid}-host/neondb")), \
             mock.patch.object(proof, "connect", self.db):
            return proof.prove(False)

    def test_prove_records_T_probes_and_readback_and_tears_the_branch_down(self):
        obs = self.run_prove(lambda w: [w["positive"]])
        self.assertEqual(obs["branch_parent_id"], "br-prod")
        self.assertEqual(obs["branch_parent_timestamp"], obs["requested_parent_timestamp"])
        pos_epoch = proof.epoch_of(obs["positive_probe"]["written_at"])
        t_epoch = proof.epoch_of(obs["requested_parent_timestamp"])
        neg_epoch = proof.epoch_of(obs["negative_probe"]["written_at"])
        self.assertGreaterEqual(t_epoch - pos_epoch, 60)
        self.assertGreaterEqual(neg_epoch - t_epoch, 5)
        self.assertEqual(obs["positive_on_branch"], obs["positive_probe"])
        self.assertFalse(obs["negative_present_on_branch"])
        self.assertEqual(list(self.fake.branches), ["br-prod"])  # torn down
        self.assertEqual(json.loads(proof.STATE.read_text()), [])

    def test_prove_tears_down_even_when_a_check_raises(self):
        with mock.patch.object(proof, "utc_exact", side_effect=proof.ProofError("boom")):
            with self.assertRaises(proof.ProofError):
                self.run_prove(lambda w: [w["positive"]])
        self.assertEqual(list(self.fake.branches), ["br-prod"])

    def test_verify_recomputes_deletion_default_branch_and_probes(self):
        obs = self.run_prove(lambda w: list(w.values()))  # negative leaked onto the branch
        self.assertTrue(obs["negative_present_on_branch"])
        prod = mock.patch.object(proof, "connection_uri", lambda bid: "postgresql://u@prod-host/neondb")
        with prod, mock.patch.object(proof, "connect", self.db):
            ev = proof.verify(obs, False)
        self.assertEqual(ev["production_branch_id"], "br-prod")
        self.assertTrue(ev["branch_deleted_confirmed"])
        self.assertEqual(ev["history_retention_seconds"], 604800)
        # re-read from production, not copied from the observation
        self.assertEqual(ev["production_readback"]["positive"], obs["positive_probe"])
        self.assertEqual(ev["production_readback"]["negative"], obs["negative_probe"])
        self.assertTrue(ev["negative_present_on_branch"])
        edited = json.loads(json.dumps(obs))
        edited["positive_probe"]["nonce"] = "c" * 32
        with prod, mock.patch.object(proof, "connect", self.db):
            self.assertNotEqual(proof.verify(edited, False)["production_readback"]["positive"], edited["positive_probe"])
        self.fake.branches[obs["branch_id"]] = {"id": obs["branch_id"], "name": "back", "default": False, "created_at": ago(seconds=1)}
        with prod, mock.patch.object(proof, "connect", self.db):
            self.assertFalse(proof.verify(obs, False)["branch_deleted_confirmed"])

    def test_verify_refuses_an_observation_whose_parent_is_not_production(self):
        with self.assertRaisesRegex(proof.ProofError, "not the production branch"):
            proof.verify({"proof_parent_branch_id": "br-other"}, False)


class Formatting(unittest.TestCase):
    def test_utc_exact_keeps_fractions_and_refuses_empty(self):
        self.assertEqual(proof.utc_exact("2026-09-24T12:00:00Z"), "2026-09-24T12:00:00Z")
        self.assertEqual(proof.utc_exact("2026-09-24T07:00:00.5-05:00"), "2026-09-24T12:00:00.500000Z")
        with self.assertRaises(proof.ProofError):
            proof.utc_exact("")


if __name__ == "__main__":
    unittest.main()

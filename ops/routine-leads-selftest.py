#!/usr/bin/env python3
"""Fixture checks for the lead routine's predicates, parsers, and write boundary."""
import copy
import csv
import io
import json
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.routines import lead_signals as leads
from tools.routines import radar_inputs as radar

FIXTURE = json.loads((ROOT / "ops/fixtures/routines/leads.json").read_text())
NOW = datetime.fromisoformat(FIXTURE["as_of"])


def context(fixture=None, dry_run=True):
    calls = []
    def write(verb, args, key):
        calls.append((verb, args, key))
        return {"party_id": "test-party", "ref": "L-TEST", "ok": True}
    def read(*_):
        raise AssertionError("fixture run must not read records")
    def review(title, body, key):
        calls.append(("review", title, body, key))
        return {"ok": True}
    return SimpleNamespace(fixture=fixture, dry_run=dry_run, now=NOW, read=read, write=write, review_item=review, calls=calls)


class LeadTests(unittest.TestCase):
    def test_pecos_quarter_gate_catches_missed_quarters(self):
        self.assertFalse(radar.refresh_due(NOW, {"quarter": "2026Q4"}))
        self.assertTrue(radar.refresh_due(NOW, {"quarter": "2026Q2"}))
        self.assertTrue(radar.refresh_due(NOW.replace(month=11), {"quarter": "2026Q3"}))

    def test_pecos_catalog_and_enrollment_parsers(self):
        public = FIXTURE["pecos_fixture"]
        self.assertTrue(radar.dataset_api(public["catalog"])[0].endswith("/data"))
        parsed = radar.enrollment_rows(public["rows"])
        self.assertEqual(parsed["1000000005|I2026093000001"]["date"], "2026-09-30")
        with self.assertRaises(ValueError):
            radar.dataset_api({})
        with self.assertRaises(ValueError):
            radar.enrollment_rows([{"STATE_CD": "FL", "NPI": "bad"}])

    def test_pecos_baseline_bootstrap_and_new_enrollment_diff(self):
        public = FIXTURE["pecos_fixture"]
        fetch = lambda _: (_ for _ in ()).throw(AssertionError("fixture attempted network"))
        rows, state, health = radar.pull_pecos(fetch, NOW, {}, fixture=public)
        self.assertEqual(rows, [])
        self.assertTrue(health["bootstrap"])
        self.assertEqual(state["baseline_count"], 1)
        rows, _, health = radar.pull_pecos(fetch, NOW, {"baseline_initialized": True, "keys": []}, fixture=public)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["city"], "Milton")
        self.assertEqual(health["new_enrollments"], 1)
        rows, _, _ = radar.pull_pecos(fetch, NOW, state, fixture=public)
        self.assertEqual(rows, [])

    def test_consumed_candidates_exit_without_repeat_effects(self):
        fixture = copy.deepcopy(FIXTURE)
        first = leads.prepare(context(fixture))
        fixture["consumed_keys"] = [r["source_key"] for r in first["candidates"]]
        ctx = context(fixture)
        repeated = leads.prepare(ctx)
        self.assertFalse(repeated["work"])
        self.assertEqual(repeated["candidates"], [])
        self.assertEqual(ctx.calls, [])

    def test_quarterly_refresh_dry_run_performs_no_writes(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture["pecos"] = fixture["pecos_fixture"]
        fixture["pecos_state"] = {"baseline_initialized": True, "keys": [], "quarter": "2026Q3"}
        ctx = context(fixture)
        plan = leads.prepare(ctx)
        result = leads.execute(ctx, plan)
        self.assertEqual(result["candidate_count"], 5)
        self.assertEqual(ctx.calls, [])
        health = next(h for h in result["lane_health"] if h["pool"] == "pecos.json")
        self.assertEqual(health["territory_candidates"], 1)
    def test_pool_writer_paths_are_bound_to_repository(self):
        sys.path.insert(0, str(ROOT / "pipelines/radar"))
        try:
            import pool_paths
            self.assertEqual(pool_paths.UPSTREAM, ROOT / "out/routines/radar/upstream")
            self.assertTrue(pool_paths.DATA.is_relative_to(ROOT))
            self.assertNotIn("GoogleDrive", str(pool_paths.UPSTREAM))
        finally:
            sys.path.pop(0)

    def test_license_writer_preserves_rows_and_adds_source_provenance(self):
        sys.path.insert(0, str(ROOT / "pipelines/radar"))
        try:
            spec = importlib.util.spec_from_file_location("license_builder", ROOT / "pipelines/radar/build-license-pool.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source = folder / "transport.tsv"
                source.write_text("Last\tFirst\tProCode\tOriginalDate\tState\tCounty\tCity\nPerson\tExample\t701\t10/01/2026\tFL\tSanta Rosa\tMilton\n")
                module.UP = str(folder / "derived")
                argv = sys.argv
                sys.argv = ["build-license-pool.py", str(source)]
                try:
                    import contextlib
                    with contextlib.redirect_stdout(io.StringIO()):
                        module.main()
                finally:
                    sys.argv = argv
                rows = json.loads((folder / "derived/licenses-pool.json").read_text())
                self.assertEqual(rows[0]["name"], "Example Person")
                self.assertEqual(rows[0]["profession"], "Dental")
                self.assertEqual(rows[0]["date"], "10/01/2026")
                self.assertEqual(rows[0]["source_url"], "https://mqa-internet.doh.state.fl.us/MQASearchServices/")
                self.assertTrue(source.exists())
        finally:
            sys.path.pop(0)

    def test_territory_does_not_take_wrong_state_or_missing_geography(self):
        self.assertTrue(leads.in_territory({"city": "Pensacola", "state": "FL"}))
        self.assertTrue(leads.in_territory({"county": "Baldwin County", "state": "AL"}))
        self.assertFalse(leads.in_territory({"city": "Mobile", "state": "CA"}))
        self.assertFalse(leads.in_territory({"state": "FL"}))

    def test_nppes_includes_weak_and_unknown_taxonomy(self):
        rows = leads.parse_nppes(FIXTURE["nppes_rows"], as_of=NOW.date(), source=leads.INDEX_URL)
        self.assertEqual({r["npi"] for r in rows}, {"1000000001", "1000000004"})
        self.assertEqual(len(leads.candidates(rows)), 2)
        weak = next(r for r in leads.candidates(rows) if r["npi"] == "1000000004")
        self.assertEqual(weak["score"], 1)

    def test_enumeration_window_boundaries_and_future(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][0])
        for enumeration, expected in (("09/21/2026", 1), ("09/20/2026", 0), ("10/06/2026", 0)):
            raw["Provider Enumeration Date"] = enumeration
            self.assertEqual(len(leads.parse_nppes([raw], as_of=NOW.date(), source=leads.INDEX_URL)), expected)
        raw["Provider Enumeration Date"] = "not-a-date"
        with self.assertRaises(ValueError):
            leads.parse_nppes([raw], as_of=NOW.date(), source=leads.INDEX_URL)

    def test_weekly_publications_reject_stale_and_wrong_host(self):
        link = '<a href="NPPES_Data_Dissemination_092826_100426_Weekly_V2.zip">weekly</a>'
        self.assertEqual(len(leads.weekly_files(link, NOW.date())), 1)
        with self.assertRaises(ValueError):
            leads.weekly_files(link, NOW.date().replace(month=11))
        with self.assertRaises(ValueError):
            leads.weekly_files(link.replace('href="', 'href="https://wrong.example/'), NOW.date())

    def test_secondary_practice_location_archive_parser(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][2])
        location = {"NPI": raw["NPI"], "Provider Secondary Practice Location Address - City Name": "Pensacola", "Provider Secondary Practice Location Address - State Name": "FL", "Provider Secondary Practice Location Address - Postal Code": "32501"}
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for filename, row in (("npidata_pfile_20260928-20261004.csv", raw), ("pl_pfile_20260928-20261004.csv", location)):
                text = io.StringIO()
                writer = csv.DictWriter(text, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
                archive.writestr(filename, text.getvalue())
        rows = leads.parse_weekly_zip(output.getvalue(), as_of=NOW.date(), source=leads.INDEX_URL)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["city"], "Pensacola")
        self.assertFalse(rows[0]["mailing_only"])
        with self.assertRaises(zipfile.BadZipFile):
            leads.parse_weekly_zip(b"not-a-zip", as_of=NOW.date(), source=leads.INDEX_URL)

    def test_mailing_only_is_visible_with_lower_estimate(self):
        raw = copy.deepcopy(FIXTURE["nppes_rows"][2])
        raw.update({"Provider Business Mailing Address Postal Code": "32501", "Provider Business Mailing Address State Name": "FL"})
        rows = leads.candidates(leads.parse_nppes([raw], as_of=NOW.date(), source=leads.INDEX_URL))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["score"], 1)

    def test_pool_parser_and_no_name_only_join(self):
        with self.assertRaises(ValueError):
            leads.parse_pool({}, "new-license", "test")
        rows = leads.parse_pool([{"name": "Same Name", "city": "Mobile", "state": "AL", "date": None}, {"name": "Same Name", "city": "Pensacola", "state": "FL", "date": None}], "new-license", "test")
        self.assertEqual(len(leads.candidates(rows)), 2)
        rows[1]["npi"] = rows[0]["npi"] = "1000000001"
        self.assertEqual(len(leads.candidates(rows)), 1)

    def test_empty_predicate_and_dry_run_no_reads_or_writes(self):
        empty = context({"nppes_rows": [], "pools": {}, "claims": []})
        self.assertFalse(leads.prepare(empty)["work"])
        ctx = context(FIXTURE)
        plan = leads.prepare(ctx)
        result = leads.execute(ctx, plan)
        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(ctx.calls, [])

    def test_write_estimates_only_to_new_stage(self):
        ctx = context(FIXTURE, dry_run=False)
        plan = leads.prepare(ctx)
        leads.execute(ctx, plan)
        writes = [c for c in ctx.calls if c[0] == "new-lead"]
        self.assertEqual(len(writes), 4)
        self.assertTrue(all(c[1]["stage"] == "new" for c in writes))
        self.assertTrue(all(1 <= c[1]["score"] <= 10 for c in writes))
        keys = [c[2] for c in writes]
        ctx.calls.clear()
        leads.execute(ctx, plan)
        self.assertEqual(keys, [c[2] for c in ctx.calls if c[0] == "new-lead"])

    def test_ambiguous_identity_and_unverified_pool_go_to_app(self):
        ctx = context(FIXTURE, dry_run=False)
        ctx.write = lambda *_: {"needs_confirm": True, "candidates": [{"name": "Possible match"}]}
        result = leads.execute(ctx, leads.prepare(ctx))
        self.assertEqual(len(result["created"]), 0)
        self.assertEqual(len(result["reviews"]), 4)
        row = leads.parse_pool([{"name": "No source", "city": "Mobile", "state": "AL"}], "new-license", "repo:retained")
        result = leads.execute(ctx, {"candidates": leads.candidates(row), "lane_health": []})
        self.assertEqual(len(result["reviews"]), 1)


if __name__ == "__main__":
    unittest.main()

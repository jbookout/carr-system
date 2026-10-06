#!/usr/bin/env python3
"""Contact routine behavior, with no database, model, or record writes."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("contacts", ROOT / "tools/routines/contact_enrichment.py")
contacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contacts)


class Context:
    now = dt.datetime(2026, 10, 5, 12, tzinfo=dt.timezone.utc)
    dry_run = False

    def __init__(self):
        self.fixture = json.loads((ROOT / "ops/fixtures/routines/contacts.json").read_text())
        self.writes = []
        self.models = 0

    def query(self, sql, params=()):
        if "party where" in sql:
            return [{"version": 4, "contact_state": "active", "merged_into": None}]
        if "vendor where" in sql:
            return [{"version": 2}]
        raise AssertionError("fixture must avoid queue query")

    def model(self, prompt, inputs):
        self.models += 1
        return self.fixture["model_response"]

    def write(self, verb, args, key):
        self.writes.append((verb, args, key))
        return {"ok": True, "flag_id": "fixture-flag"}


class ContactTests(unittest.TestCase):
    def test_empty_starts_no_model(self):
        ctx = Context(); ctx.fixture["queue"] = []
        plan = contacts.prepare(ctx)
        self.assertFalse(plan["work"])
        self.assertEqual(contacts.execute(ctx, plan)["processed"], 0)
        self.assertEqual((ctx.models, ctx.writes), (0, []))

    def test_priority_cap_exclusions_and_party_dedup(self):
        rows = [{"ref": f"L-{i}", "party_id": str(i), "priority": 100-i,
                 "subject_type": "lead", "contact_state": "active"} for i in range(55)]
        rows += [dict(rows[0], priority=0), dict(rows[1], contact_state="do_not_contact", priority=-1)]
        selected = contacts.select_contacts(rows)
        self.assertEqual(len(selected), 40)
        self.assertEqual(selected[0]["priority"], 0)
        self.assertEqual(len({r["party_id"] for r in selected}), 40)
        self.assertNotIn(-1, [r["priority"] for r in selected])

    def test_valid_writes_stamp_and_review_identity(self):
        ctx = Context(); plan = contacts.prepare(ctx)
        outcome = contacts.execute(ctx, plan)
        self.assertEqual(ctx.models, 1)
        self.assertEqual(outcome["processed"], 1)
        flags = [args for verb,args,_ in ctx.writes if verb == "record-finding"]
        self.assertTrue(all(f["expires_on"] == "2027-04-03" for f in flags if f["kind"] != "contact_enrichment_attempt"))
        self.assertTrue(all(f["value"]["verified_at"] == ctx.now.isoformat() for f in flags))
        updates = [args for verb,args,_ in ctx.writes if verb == "update-party-contact"]
        self.assertEqual(updates[0]["fields"], {"email": "alex@example.com"})
        self.assertEqual(updates[0]["base_version"], 4)
        self.assertEqual(len([v for v,_,_ in ctx.writes if v == "report-problem"]), 1)

    def test_parser_rejects_untrusted_and_incomplete_output_before_writes(self):
        for change in ["ref", "citation", "identity", "duplicate", "missing", "placeholder", "match"]:
            with self.subTest(change=change):
                ctx = Context(); response = ctx.fixture["model_response"]
                row = response["records"][0]
                if change == "ref": row["ref"] = "L-unselected"
                if change == "citation": row["facts"][0]["citations"] = ["javascript:bad"]
                if change == "identity": row["facts"][0]["field"] = "name"
                if change == "duplicate": response["records"].append(copy.deepcopy(row))
                if change == "missing": response["records"] = []
                if change == "placeholder": row["facts"][0]["value"] = "dell@carr.us"
                if change == "match": row["identity_evidence"] = []
                with self.assertRaises(ValueError): contacts.execute(ctx, contacts.prepare(ctx))
                self.assertEqual(ctx.writes, [])

    def test_dry_run_uses_fixture_without_model_or_write(self):
        ctx = Context(); ctx.dry_run = True
        result = contacts.execute(ctx, contacts.prepare(ctx))
        self.assertEqual(result["processed"], 1)
        self.assertEqual((ctx.models, ctx.writes), (0, []))

    def test_ambiguous_result_never_promotes(self):
        ctx = Context(); row = ctx.fixture["model_response"]["records"][0]
        row["ambiguous"] = True; row["facts"] = []
        result = contacts.execute(ctx, contacts.prepare(ctx))
        self.assertEqual(result["reviews"], 1)
        self.assertNotIn("update-party-contact", [v for v,_,_ in ctx.writes])

    def test_categories_and_verticals_use_vendor_writer(self):
        ctx = Context(); row = ctx.fixture["model_response"]["records"][0]
        row["corrections"] = []
        row["facts"] += [{"field": "category_slug", "value": "cpa", "citations": ["https://example.com/team"]},
                         {"field": "verticals", "value": ["dental"], "citations": ["https://example.com/team"]}]
        contacts.execute(ctx, contacts.prepare(ctx))
        vendor = [a for v,a,_ in ctx.writes if v == "update-vendor"]
        self.assertEqual(vendor[0]["fields"], {"category_slug": "cpa", "verticals": ["dental"]})
        self.assertNotIn("report-problem", [v for v,_,_ in ctx.writes])

    def test_new_category_requires_homepage_review(self):
        ctx = Context(); row = ctx.fixture["model_response"]["records"][0]
        row["corrections"] = []
        row["facts"] = [{"field": "category_slug", "value": "rare-profession", "citations": ["https://example.com/team"]}]
        contacts.execute(ctx, contacts.prepare(ctx))
        self.assertNotIn("update-vendor", [v for v,_,_ in ctx.writes])
        self.assertIn("report-problem", [v for v,_,_ in ctx.writes])

    def test_empty_research_is_recorded_without_contact_update(self):
        ctx = Context(); row = ctx.fixture["model_response"]["records"][0]
        row["facts"] = []; row["corrections"] = []
        contacts.execute(ctx, contacts.prepare(ctx))
        self.assertEqual(len(ctx.writes), 2)
        self.assertFalse(ctx.writes[0][1]["found"])

    def test_fresh_attempt_cooldown_does_not_consume_another_model(self):
        ctx = Context(); ctx.fixture["queue"][0]["research_retry_after"] = "2026-11-04"
        plan = contacts.prepare(ctx)
        self.assertFalse(plan["work"])
        contacts.execute(ctx, plan)
        self.assertEqual((ctx.models, ctx.writes), (0, []))
        ctx.fixture["queue"][0]["research_retry_after"] = "2026-10-05"
        self.assertTrue(contacts.prepare(ctx)["work"])

    def test_restart_preserves_observation_and_compare_swap_effect(self):
        ctx = Context(); ctx.state = {"started_at": ctx.now.isoformat(), "effects": {}}
        plan = contacts.prepare(ctx)
        contacts.execute(ctx, plan)
        first = list(ctx.writes)
        for verb,args,key in first:
            ctx.state["effects"][key] = {"verb": verb, "args": dict(args, idempotency_key="fixture-id")}
        ctx.state.pop("completed_contact_refs")
        ctx.now += dt.timedelta(days=1)
        ctx.query = lambda sql,params=(): [{"version": 9, "contact_state": "active", "merged_into": None}]
        ctx.writes = []
        contacts.execute(ctx, plan)
        self.assertEqual(ctx.writes, first)

    def test_completed_contact_does_not_requery_or_rewrite_on_restart(self):
        ctx = Context(); ctx.state = {"started_at": ctx.now.isoformat(), "effects": {}}
        plan = contacts.prepare(ctx)
        contacts.execute(ctx, plan)
        ctx.writes = []
        ctx.query = lambda *args: self.fail("completed contact must not requery")
        contacts.execute(ctx, plan)
        self.assertEqual(ctx.writes, [])

    def test_failed_writer_prevents_success(self):
        ctx = Context(); ctx.write = lambda *args: {"ok": False}
        with self.assertRaises(RuntimeError): contacts.execute(ctx, contacts.prepare(ctx))

    def test_selection_hides_other_subjects_and_retired_rows(self):
        ctx = Context(); row = ctx.fixture["queue"][0]
        rows = [dict(row, subject_type="commit"), dict(row, merged_into="retired"), dict(row, deleted_at="2026-01-01")]
        self.assertEqual(contacts.select_contacts(rows), [])


if __name__ == "__main__":
    unittest.main()

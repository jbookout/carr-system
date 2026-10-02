#!/usr/bin/env python3
"""The calendar matcher reads the LIVE exports and refuses an empty book.

Regression for 2026-09-27: the matcher read the exporters' draft directory
(out/exports), which the live nightly chain never writes, so every run loaded
0 record contacts and reported every attendee as unknown. Fixture workbooks
only; example.test addresses.
"""
import importlib.util
import io
import os
import pathlib
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

import openpyxl

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calendar_touch_matcher", REPO / "tools" / "calendar-touch-matcher.py")
assert spec is not None and spec.loader is not None
matcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matcher)


def workbook(path, sheet, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(row)
    wb.save(path)


class LiveExports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def seed_live(self):
        workbook(self.root / matcher.ROSTER_REL, "Clients",
                 ["Client ID", "Name", "Practice / Entity", "Email"],
                 [["C-1", "Client One", "Practice A", "one@clinic-a.example.test"]])
        workbook(self.root / matcher.REGISTRY_REL, "Registry",
                 ["Lead ID", "Contact Name", "Practice", "Email"],
                 [["L-1", "Lead Two", "Practice B", "two@gmail.com"]])

    def test_relative_paths_match_the_exporters(self):
        text = (REPO / "exporters" / "targets.py").read_text(encoding="utf-8")
        self.assertIn(f'ROSTER_REL = "{matcher.ROSTER_REL}"', text)
        self.assertIn(f'REGISTRY_REL = "{matcher.REGISTRY_REL}"', text)

    def test_default_root_is_the_exporters_export_home(self):
        with mock.patch.dict(os.environ, {"CARR_EXPORT_HOME": str(self.root)}):
            sys.modules.pop("exporters.common", None)
            sys.modules.pop("exporters", None)
            self.assertEqual(matcher.export_home(), str(self.root))
        sys.modules.pop("exporters.common", None)

    def test_loads_contacts_from_the_live_layout(self):
        self.seed_live()
        by_email, by_domain = matcher.load_record_contacts(root=str(self.root))
        self.assertEqual(sorted(by_email), ["one@clinic-a.example.test", "two@gmail.com"])
        # Freemail never becomes a domain match.
        self.assertEqual(sorted(by_domain), ["clinic-a.example.test"])

    def test_draft_flat_layout_is_not_read(self):
        workbook(self.root / "client-roster.xlsx", "Clients",
                 ["Client ID", "Name", "Practice / Entity", "Email"],
                 [["C-1", "Client One", "Practice A", "one@clinic-a.example.test"]])
        with self.assertRaises(matcher.NoRecordContacts) as caught:
            matcher.load_record_contacts(root=str(self.root))
        self.assertIn("absent", str(caught.exception))

    def test_empty_book_refuses_loudly_without_addresses(self):
        dump = self.root / "dump.json"
        dump.write_text('{"Meeting|2026-09-25": ["someone@else.example.test"]}', encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(matcher, "export_home", return_value=str(self.root)), \
                mock.patch.object(sys, "argv", ["matcher", "7", "--json", "--from-dump", str(dump)]), \
                redirect_stderr(err):
            code = matcher.main()
        self.assertEqual(code, 5)
        self.assertIn("FATAL: record contacts: none loaded", err.getvalue())
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", err.getvalue()))

    def test_snapshot_path_is_unchanged(self):
        emails, domains = matcher.load_record_contacts(
            [{"email": "z@same.example.test", "ref": "C-1", "name": "Client", "org": "Org"}])
        self.assertEqual(list(emails), ["z@same.example.test"])
        self.assertEqual(list(domains), ["same.example.test"])


if __name__ == "__main__":
    unittest.main()

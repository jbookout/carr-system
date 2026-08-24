#!/usr/bin/env python3
"""Offline browser-verification host for the real Model Room bundle.

This is test-only plumbing: it serves the checked-in Model Room assets and a
single synthetic typed wire turn from the existing execution-fabric fixtures.
It neither writes a room turn nor stands in for the deployed Worker.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import execution_contract as contract  # noqa: E402
import bridge  # noqa: E402
import spatial_surface  # noqa: E402


class PreviewHandler(SimpleHTTPRequestHandler):
    assets = ROOT / "dealroom"
    fixtures = ROOT / "control-room" / "contracts" / "fixtures" / "execution-fabric"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.assets), **kwargs)

    def _turns(self) -> dict:
        envelope = json.loads((self.fixtures / "codex_desktop.execution-envelope.v1.json").read_text())
        receipt = json.loads((self.fixtures / "codex_desktop.attempt-receipt.v1.json").read_text())
        event = {"schema_version": "progress-event.v1", "attempt_id": "attempt-synthetic-codex", "sequence": 1,
                 "occurred_at": "2026-08-24T12:00:01Z", "event_type": "observed_tool", "declared_step_ref": "step:synthetic-read",
                 "observed_resource_ref": "resource:worktree-a", "observed_component_ref": "component:execution-fabric",
                 "tool_class": "tool:codex-event", "state": "active", "correlation_id": "corr:synthetic-1",
                 "causation_id": "cause:dispatch-1", "redaction_class": "metadata_only", "evidence_refs": ["evidence:synthetic-check"], "retention": "ephemeral"}
        posted = []
        portfolio = json.loads((self.fixtures / "carr-evaluation-kernel.synthetic.v1.json").read_text())
        telemetry = [json.loads((self.fixtures / name).read_text()) for name in (
            "codex_desktop.elapsed-time.telemetry-measurement.v1.json",
            "codex_desktop.billed-cost.telemetry-measurement.v1.json",
        )]
        projection = contract.project_observatory_attempt(envelope, receipt, [event], {"profile_id": "profile:doc", "display_label": "Doc"})
        rehearsal = bridge.rehearse_job_passport(envelope, receipt, [event], {"profile_id": "profile:doc", "display_label": "Doc"},
                                                  evaluation_kernel=portfolio, spatial_surface=spatial_surface.project_job_passport_surface(projection), telemetry_measurements=telemetry,
                                                  add_room_turn=lambda **row: posted.append(row) or {"preview": True})
        turns = [{"seq": str(index), "msg_id": f"preview-job-passport-{index}", "at": "2026-08-24T12:00:06Z",
                  "sponsor": "joe", "seat": row["seat"], "kind": row["kind"], "body": row["body"]}
                 for index, row in enumerate(posted, start=1)]
        return {"actor": {"slug": "joe"}, "csrf_token": "preview-only", "latest_seq": str(len(turns)), "turns": turns,
                "rehearsal": {"mode": rehearsal["mode"], "projection_digest": rehearsal["projection"]["projection_digest"]}}

    def do_GET(self):  # noqa: N802 - stdlib callback
        path = urlparse(self.path).path
        if path == "/api/room/turns":
            body = json.dumps(self._turns()).encode("utf-8")
            self.send_response(200); self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store"); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path == "/":
            self.path = "/room.html"
        return super().do_GET()

    def log_message(self, format, *args):  # noqa: A003 - keep browser verification quiet
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the real Model Room with an offline typed Job Passport wire fixture")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Local input-bearing receipts; private fields never reach the append sink.

CARR_JUDGE_CAPTURE=0 disables; 1 explicitly enables, including in CI. With no
setting, local calls capture and CI calls do not. A readable local client
roster is required. Output defaults to out/judge-corpus/traffic.redacted.jsonl
in this checkout. Capture failures append constant-only sidecar diagnostics and never change
judge behavior. These are unlabelled traffic, not eval gold or audit authority.
"""
import fcntl
import json
import os
import time
import uuid
from pathlib import Path

from ops import business_data_patterns as privacy
from tools.judge.corpus import redact
from tools.judge.paired_eval import digest

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "out/judge-corpus/traffic.redacted.jsonl"


def diagnostic(status):
    """Keep telemetry off hook stdout/stderr, which carry refusal decisions.

    Only an enumerated status and timestamp reach this best-effort sidecar;
    an unavailable sidecar cannot change the judge or its output streams.
    """
    try:
        sink = Path(os.environ.get("CARR_JUDGE_CAPTURE_PATH") or DEFAULT_PATH)
        path = sink.with_name(sink.name + ".diagnostics.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(json.dumps({"schema": "carr-judge-capture-diagnostic/v1",
                                     "status": status, "recorded_at_unix": time.time()}) + "\n")
    except Exception:
        pass


def enabled(work_class):
    if work_class != "system_work":
        return False
    switch = os.environ.get("CARR_JUDGE_CAPTURE")
    if switch is not None:
        return switch == "1"
    return not any(os.environ.get(key, "").lower() not in {"", "0", "false"}
                   for key in ("CI", "GITHUB_ACTIONS"))


def prepare(state, questions, *, model, caller, provider, work_class):
    if not enabled(work_class):
        return None
    try:
        if privacy.roster() is None:
            raise ValueError("roster unavailable")
        # Freeze before an adapter can mutate its arguments. No transport
        # options, key, endpoint, account or provider exception text is kept.
        request = redact({"state": state, "questions": questions, "model": model})
        return {"schema": "carr-judge-input-receipt/v1", "receipt_id": "capture-" + uuid.uuid4().hex,
                "recorded_at_unix": time.time(), "work_class": work_class,
                "request": request, "request_sha256": digest(request),
                "purpose": redact(caller or "unspecified"), "provider": provider,
                "redaction": "business-data-patterns/v1+local-roster", "origin": "live_seam_capture"}
    except Exception:
        diagnostic("privacy_preparation_failed")
        return None


def append(receipt, *, failed=False):
    if receipt is None:
        return
    try:
        receipt["status"] = "error" if failed else "ok"
        path = Path(os.environ.get("CARR_JUDGE_CAPTURE_PATH") or DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        # One locked append per record prevents concurrent large writes from
        # interleaving. Mode 600 applies to a new sink; no existing modes change.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(json.dumps(receipt, separators=(",", ":"), ensure_ascii=False) + "\n")
            stream.flush()
    except Exception:
        diagnostic("append_failed")

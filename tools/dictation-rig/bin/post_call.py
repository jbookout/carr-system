#!/usr/bin/env python3
"""Private, local-only Call Mode post-call processing.

This module deliberately has no network client and no send implementation.
Deal Room supplies an exact, short-lived context index; transcript, report and
email body remain in 0600 files in the recording session.  A local model is
called only through an injected command contract so tests never need a model.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
import urllib.request
import urllib.parse
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1
CONTEXT_FILE = "call-context.json"
REPORT_FILE = "post-call-report.json"
STATUS_FILE = "post-call.json"
DRAFTS_FILE = "outlook-drafts.json"
PUSH_FILE = "backend-push.json"
LOCK_FILE = ".post-call.lock"
LLAMA_SERVER = "/opt/homebrew/bin/llama-server"
LLAMA_MODEL = Path.home() / ".cache" / "llama.cpp" / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
# Quill owns 8596 (cleanup) and 8597 (final transcription); this child must
# never bind either resident service's port.
LLAMA_PORT = 8598
OUTLOOK_URL_LIMIT = 1900


def distiller_json_schema() -> dict[str, Any]:
    string_array = {"type": "array", "items": {"type": "string"}}

    def item(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {
            "type": "object", "additionalProperties": False,
            "properties": properties, "required": required,
        }

    common = {
        "deal_id": {"type": "string"},
        "participant_ids": string_array,
        "evidence": {"type": "string"},
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
            "report": item({
                "summary": {"type": "string"}, "decisions": string_array,
                "open_questions": string_array,
            }, ["summary", "decisions", "open_questions"]),
            "joe_tasks": {"type": "array", "items": item({
                **common, "title": {"type": "string"},
            }, ["title", "deal_id", "participant_ids", "evidence"])},
            "dell_tasks": {"type": "array", "items": item({
                **common, "title": {"type": "string"},
            }, ["title", "deal_id", "participant_ids", "evidence"])},
            "deal_updates": {"type": "array", "items": item({
                **common, "summary": {"type": "string"},
            }, ["deal_id", "summary", "participant_ids", "evidence"])},
            "draft_proposals": {"type": "array", "items": item({
                **common, "recipient_party_id": {"type": "string"},
                "subject": {"type": "string"}, "body": {"type": "string"},
            }, ["recipient_party_id", "deal_id", "subject", "body", "participant_ids", "evidence"])},
            "review_questions": string_array,
        },
        "required": ["schema_version", "report", "joe_tasks", "dell_tasks",
                     "deal_updates", "draft_proposals", "review_questions"],
    }


class ContractError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_private(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    os.replace(temp, path)
    path.chmod(0o600)


def _strict_keys(value: dict[str, Any], required: set[str], optional: set[str] = set()) -> None:
    keys = set(value)
    if not required <= keys or keys - required - optional:
        missing = sorted(required - keys)
        unexpected = sorted(keys - required - optional)
        raise ContractError(
            f"schema fields do not match the exact contract (missing={missing}; unexpected={unexpected})"
        )


def _string(value: Any, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ContractError(f"{field} must be a {'string' if allow_empty else 'non-empty string'}")
    return value


def validate_context(value: Any, session: str) -> dict[str, Any]:
    """Validate Deal Room's exact index; IDs are opaque and never fuzzy-matched."""
    if not isinstance(value, dict):
        raise ContractError("call context must be an object")
    _strict_keys(value, {"session", "workspace_kind", "generated_at", "deals"}, {"account_client_id"})
    if _string(value["session"], "session") != session:
        raise ContractError("context session must equal the local session")
    for field in ("workspace_kind", "generated_at"):
        _string(value[field], field)
    if "account_client_id" in value:
        _string(value["account_client_id"], "account_client_id")
    deals = value["deals"]
    if not isinstance(deals, list):
        raise ContractError("deals must be an array")
    deal_ids: set[str] = set()
    for deal in deals:
        if not isinstance(deal, dict):
            raise ContractError("deal must be an object")
        _strict_keys(deal, {"id", "name", "owner", "operating_state", "participants"})
        deal_id = _string(deal["id"], "deal.id")
        if deal_id in deal_ids:
            raise ContractError("duplicate deal.id is ambiguous")
        deal_ids.add(deal_id)
        for field in ("name", "owner", "operating_state"):
            _string(deal[field], f"deal.{field}", allow_empty=True)
        if not isinstance(deal["participants"], list):
            raise ContractError("deal.participants must be an array")
        party_ids: set[str] = set()
        for party in deal["participants"]:
            if not isinstance(party, dict):
                raise ContractError("participant must be an object")
            _strict_keys(party, {"party_id", "ref", "name", "email", "role"})
            party_id = _string(party["party_id"], "participant.party_id")
            if party_id in party_ids:
                raise ContractError("duplicate participant.party_id is ambiguous")
            party_ids.add(party_id)
            _string(party["ref"], "participant.ref")
            for field in ("name", "email", "role"):
                _string(party[field], f"participant.{field}", allow_empty=True)
    return value


def store_context(session_dir: Path, context: dict[str, Any]) -> dict[str, Any]:
    context = validate_context(context, session_dir.name)
    old = read_json(session_dir / CONTEXT_FILE) or {}
    # Retain physical-channel labels written at recording start; only Deal Room's
    # exact index becomes available to the model, and it is consumed on success.
    stored = {key: old[key] for key in ("schema", "mode", "started_at", "recorder", "speaker_labels", "speaker_method") if key in old}
    stored["call_context"] = context
    write_private(session_dir / CONTEXT_FILE, stored)
    return context


def context_from_session(session_dir: Path) -> dict[str, Any] | None:
    whole = read_json(session_dir / CONTEXT_FILE) or {}
    index = whole.get("call_context")
    try:
        return validate_context(index, session_dir.name)
    except ContractError:
        return None


def _word_count(value: str) -> int:
    return len(value.split())


def outlook_draft_url_length(to: str, subject: str, body: str) -> int:
    quote = urllib.parse.quote
    return len(f"mailto:{quote(to, safe='@,')}?subject={quote(subject)}&body={quote(body)}")


def _validate_distillation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("distiller output must be an object")
    _strict_keys(value, {"schema_version", "report", "joe_tasks", "dell_tasks", "deal_updates", "draft_proposals", "review_questions"})
    if value["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported distiller schema_version")
    report = value["report"]
    if not isinstance(report, dict):
        raise ContractError("report must be an object")
    _strict_keys(report, {"summary", "decisions", "open_questions"})
    _string(report["summary"], "report.summary", allow_empty=True)
    for key in ("decisions", "open_questions", "review_questions"):
        if not isinstance(value[key] if key == "review_questions" else report[key], list) or not all(isinstance(x, str) for x in (value[key] if key == "review_questions" else report[key])):
            raise ContractError(f"{key} must be an array of strings")
    for list_name, required in {
        "joe_tasks": {"title", "deal_id", "participant_ids", "evidence"},
        "dell_tasks": {"title", "deal_id", "participant_ids", "evidence"},
        "deal_updates": {"deal_id", "summary", "participant_ids", "evidence"},
        "draft_proposals": {"recipient_party_id", "deal_id", "subject", "body", "participant_ids", "evidence"},
    }.items():
        items = value[list_name]
        if not isinstance(items, list):
            raise ContractError(f"{list_name} must be an array")
        for item in items:
            if not isinstance(item, dict):
                raise ContractError(f"{list_name} item must be an object")
            _strict_keys(item, required)
            for key in required - {"deal_id", "participant_ids"}:
                _string(item[key], f"{list_name}.{key}")
            if not isinstance(item["deal_id"], str) or not item["deal_id"].strip():
                raise ContractError(f"{list_name}.deal_id must be an exact non-empty string")
            if not isinstance(item["participant_ids"], list) or not all(isinstance(x, str) for x in item["participant_ids"]):
                raise ContractError(f"{list_name}.participant_ids must be string IDs")
            if _word_count(item["evidence"]) > 15:
                # Evidence is an untrusted review hint, never authority. Keep
                # the model's exact leading words while enforcing the remote
                # 15-word ceiling deterministically instead of losing the
                # entire weekly report over one verbose quote.
                item["evidence"] = " ".join(item["evidence"].split()[:15])
    return value


def _review_id(session: str, kind: str, number: int) -> str:
    return hashlib.sha256(f"{session}:{kind}:{number}".encode()).hexdigest()[:16]


def normalize_distillation(value: Any, context: dict[str, Any], session: str) -> dict[str, Any]:
    raw = _validate_distillation(value)
    deal_ids = {deal["id"] for deal in context["deals"]}
    parties_by_deal = {deal["id"]: {party["party_id"]: party for party in deal["participants"]} for deal in context["deals"]}
    review = [{"id": _review_id(session, "model", n), "question": q, "resolved": False} for n, q in enumerate(raw["review_questions"], 1)]

    def clean(items: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
        good: list[dict[str, Any]] = []
        for number, item in enumerate(items, 1):
            bad: list[str] = []
            deal_id = item["deal_id"]
            if deal_id is not None and deal_id not in deal_ids:
                bad.append(f"unknown or ambiguous deal_id {deal_id!r}")
            attached = parties_by_deal.get(deal_id, {})
            unknown_parties = [party_id for party_id in item["participant_ids"] if party_id not in attached]
            if unknown_parties:
                bad.append("unknown or ambiguous participant_ids " + repr(unknown_parties))
            recipient = item.get("recipient_party_id")
            if recipient is not None and recipient not in attached:
                bad.append(f"unknown or ambiguous recipient_party_id {recipient!r}")
            if recipient is not None and recipient in attached and not attached[recipient].get("email"):
                bad.append(f"recipient_party_id {recipient!r} has no email")
            if recipient is not None and recipient in attached and attached[recipient].get("email"):
                if outlook_draft_url_length(attached[recipient]["email"], item.get("subject", ""), item.get("body", "")) > OUTLOOK_URL_LIMIT:
                    bad.append("email draft exceeds the local Outlook draft size limit; shorten it")
            if bad:
                review.append({"id": _review_id(session, kind, number), "question": f"{kind} #{number}: " + "; ".join(bad), "resolved": False})
            else:
                good.append(item)
        return good

    report = dict(raw["report"])
    result = {
        "schema_version": SCHEMA_VERSION, "session": session, "generated_at": now(), "report": report,
        "joe_tasks": clean(raw["joe_tasks"], "joe_task"), "dell_tasks": clean(raw["dell_tasks"], "dell_task"),
        "deal_updates": clean(raw["deal_updates"], "deal_update"), "draft_proposals": clean(raw["draft_proposals"], "draft_proposal"),
        "review_questions": review,
    }
    for index, draft in enumerate(result["draft_proposals"], 1):
        recipient = parties_by_deal[draft["deal_id"]].get(draft["recipient_party_id"])
        deal = next((deal for deal in context["deals"] if deal["id"] == draft["deal_id"]), None)
        # This private copy permits later approved draft creation after the
        # ephemeral index is consumed.  It is never in a backend candidate.
        draft["recipient_email"] = recipient["email"] if recipient else ""
        draft["recipient_name"] = recipient["name"] if recipient else ""
        draft["recipient_ref"] = recipient["ref"] if recipient else ""
        draft["deal_name"] = deal["name"] if deal else ""
        draft["draft_id"] = hashlib.sha256(f"{session}:{index}:{draft['recipient_party_id']}".encode()).hexdigest()[:16]
        draft["content_hash"] = hashlib.sha256(json.dumps({k: draft[k] for k in ("recipient_party_id", "subject", "body")}, sort_keys=True).encode()).hexdigest()
        draft["candidate_id"] = None
        draft["candidate_status"] = "unfiled"
    for list_name in ("joe_tasks", "dell_tasks", "deal_updates"):
        for item in result[list_name]:
            item["candidate_id"] = None
            item["candidate_status"] = "unfiled"
    return result


def command_distiller(request: dict[str, Any], command: str | None = None, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    command = command or os.environ.get("CARR_POST_CALL_DISTILLER_COMMAND")
    if not command:
        raise RuntimeError("no local post-call distiller configured")
    argv = shlex.split(command)
    if not argv:
        raise RuntimeError("local post-call distiller command is empty")
    result = runner(argv, input=json.dumps(request), text=True, capture_output=True, shell=False, timeout=120, check=False)
    if result.returncode != 0:
        raise RuntimeError("local post-call distiller failed")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("local post-call distiller returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ContractError("local post-call distiller returned non-object JSON")
    return parsed


def transcript_chunks(transcript: dict[str, Any], limit: int = 24000) -> list[dict[str, Any]]:
    """Split only at transcript segments, never in the middle of a speaker turn."""
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise ContractError("transcript segments must be an array")
    chunks: list[dict[str, Any]] = []
    current: list[Any] = []
    for segment in segments:
        candidate = {"segments": current + [segment]}
        if len(json.dumps(candidate, ensure_ascii=False)) > limit:
            if not current:
                raise RuntimeError("a transcript segment exceeds bounded local distiller context")
            chunks.append({"segments": current})
            current = [segment]
        else:
            current.append(segment)
    if current or not chunks:
        chunks.append({"segments": current})
    return chunks


def merge_chunk_outputs(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic merge: preserve first occurrence, no cross-chunk invention."""
    if not outputs:
        raise ContractError("local post-call model returned no chunk outputs")
    checked = [_validate_distillation(output) for output in outputs]
    result = {"schema_version": SCHEMA_VERSION, "report": {"summary": "\n\n".join(item["report"]["summary"] for item in checked if item["report"]["summary"]), "decisions": [], "open_questions": []}, "joe_tasks": [], "dell_tasks": [], "deal_updates": [], "draft_proposals": [], "review_questions": []}
    for key in ("decisions", "open_questions"):
        seen: set[str] = set()
        result["report"][key] = [entry for item in checked for entry in item["report"][key] if not (entry in seen or seen.add(entry))]
    for key in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals", "review_questions"):
        seen_items: set[str] = set()
        result[key] = [entry for item in checked for entry in item[key] if not (json.dumps(entry, sort_keys=True) in seen_items or seen_items.add(json.dumps(entry, sort_keys=True)))]
    return result


def parse_model_json(content: str) -> dict[str, Any]:
    """Accept one JSON object, tolerating only common code-fence/prose wrappers."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    if not text.startswith("{") or not text.endswith("}"):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ContractError("local post-call model returned no JSON object")
        text = text[start:end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError("local post-call model returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ContractError("local post-call model returned non-object JSON")
    return parsed


def llama_distiller(request: dict[str, Any], popen: Callable[..., Any] = subprocess.Popen, opener: Callable[..., Any] = urllib.request.urlopen) -> dict[str, Any]:
    """One bounded, private llama-server child; never touches Quill's port 8596."""
    if not Path(LLAMA_SERVER).is_file() or not LLAMA_MODEL.is_file():
        raise RuntimeError("no local post-call model runtime is available")
    context_json = json.dumps(request["context"], ensure_ascii=False)
    if len(context_json) > 28000:
        raise RuntimeError("call-context index exceeds bounded local distiller context")
    chunks = transcript_chunks(request["transcript"])
    child = popen([
        LLAMA_SERVER, "-m", str(LLAMA_MODEL), "--host", "127.0.0.1",
        "--port", str(LLAMA_PORT), "-c", "16384", "--threads", "4",
        "--reasoning", "off", "--reasoning-format", "none",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                with opener(f"http://127.0.0.1:{LLAMA_PORT}/health", timeout=1):
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("local post-call model did not become ready")
        outputs: list[dict[str, Any]] = []
        for chunk in chunks:
            instruction = (
                "You distill a Joe-and-Dell weekly commercial-real-estate call. "
                "Use only exact deal_id and party_id values present in CONTEXT; never copy display names into ID fields. "
                "When Joe says he/I/we will do something, create a joe_tasks item. "
                "When Dell says he/I/we will do something, create a dell_tasks item. "
                "A deal status statement creates a deal_updates item. "
                "A request to tell, update, email, or draft a message to an exact attached participant creates a draft_proposals item. "
                "Each item includes a transcript evidence phrase of at most 15 words and only attached participant_ids. "
                "Draft bodies are concise, professional, factual, and under 900 characters. "
                "Unknown or ambiguous references become review_questions; never invent an ID. "
                "Return only the required JSON object and no transcript field."
            )
            source = json.dumps(
                {"CONTEXT": request["context"], "TRANSCRIPT": chunk}, ensure_ascii=False,
            )
            body = json.dumps({
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": source},
                ],
                "temperature": 0, "max_tokens": 1200,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "carr_post_call", "strict": True,
                        "schema": distiller_json_schema(),
                    },
                },
            }).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"}, method="POST")
            with opener(req, timeout=120) as response:
                outer = json.loads(response.read().decode("utf-8"))
            choice = outer["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                fields = ",".join(sorted(str(key) for key in message))
                finish = str(choice.get("finish_reason") or "unknown")
                raise RuntimeError(
                    f"local post-call model returned empty content (fields={fields}; finish={finish})"
                )
            outputs.append(parse_model_json(content))
        return merge_chunk_outputs(outputs)
    except (RuntimeError, ContractError):
        raise
    except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("local post-call model failed closed") from exc
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except Exception:
            child.kill()


def default_distiller(request: dict[str, Any]) -> dict[str, Any]:
    command = os.environ.get("CARR_POST_CALL_DISTILLER_COMMAND")
    return command_distiller(request, command=command) if command else llama_distiller(request)


def process_session(session_dir: Path, distiller: Callable[[dict[str, Any]], dict[str, Any]] = default_distiller) -> dict[str, Any]:
    existing = read_json(session_dir / STATUS_FILE)
    if (session_dir / REPORT_FILE).exists() and existing is not None:
        return existing
    lock_path = session_dir / LOCK_FILE
    lock_path.touch(exist_ok=True)
    lock_path.chmod(0o600)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("post-call distillation already active for this session") from exc
        return _process_session_locked(session_dir, distiller)


def _process_session_locked(session_dir: Path, distiller: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    context = context_from_session(session_dir)
    if context is None:
        status = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "state": "awaiting_context", "updated_at": now()}
        write_private(session_dir / STATUS_FILE, status)
        return status
    transcript = read_json(session_dir / "transcript.json")
    if transcript is None:
        raise ContractError("transcript.json is required before post-call processing")
    request = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "context": context, "transcript": transcript}
    try:
        result = normalize_distillation(distiller(request), context, session_dir.name)
    except (RuntimeError, ContractError) as exc:
        status = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "state": "blocked", "updated_at": now(), "reason": str(exc)}
        write_private(session_dir / STATUS_FILE, status)
        return status
    write_private(session_dir / REPORT_FILE, result)
    # Context is single-use: no later caller gets a searchable customer index.
    stored = read_json(session_dir / CONTEXT_FILE) or {}
    stored.pop("call_context", None)
    write_private(session_dir / CONTEXT_FILE, stored)
    status = {"schema_version": SCHEMA_VERSION, "session": session_dir.name, "state": "ready_review", "updated_at": now(), "review_questions": len(result["review_questions"]), "candidate_counts": {k: len(result[k]) for k in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals")}}
    write_private(session_dir / STATUS_FILE, status)
    return status


def apply_candidate_statuses(session_dir: Path, statuses: dict[str, str]) -> dict[str, Any]:
    """Persist CARR's human candidate dispositions; no backend write occurs here."""
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    known: dict[str, dict[str, Any]] = {}
    for list_name in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals"):
        for item in report.get(list_name, []):
            candidate_id = item.get("candidate_id")
            if isinstance(candidate_id, str) and candidate_id:
                known[candidate_id] = item
    if not statuses or any(key not in known or value not in {"confirmed", "skipped", "rejected"} for key, value in statuses.items()):
        raise ContractError("candidate dispositions must name known candidate IDs")
    for candidate_id, status in statuses.items():
        known[candidate_id]["candidate_status"] = status
    write_private(session_dir / REPORT_FILE, report)
    return {"updated": len(statuses)}


def retention_ready(session_dir: Path, report_sha256: str) -> dict[str, Any] | None:
    """Return a purge-safe filing receipt only after every candidate is final."""
    report = read_json(session_dir / REPORT_FILE)
    if report is None or not isinstance(report_sha256, str) or not report_sha256.strip():
        return None
    items = [item for key in ("joe_tasks", "dell_tasks", "deal_updates", "draft_proposals") for item in report.get(key, [])]
    if any(item.get("candidate_status") not in {"confirmed", "skipped", "rejected"} for item in items):
        return None
    digest = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    report.update({"backend_report_sha256": report_sha256, "aggregate_report_hash": digest, "filed_at": now()})
    write_private(session_dir / REPORT_FILE, report)
    status = read_json(session_dir / STATUS_FILE) or {"schema_version": SCHEMA_VERSION, "session": session_dir.name}
    status.update({"state": "filed", "updated_at": now(), "pending_items": 0, "aggregate_report_hash": digest})
    write_private(session_dir / STATUS_FILE, status)
    return {"aggregate_report_hash": digest, "backend_report_sha256": report_sha256, "pending_items": 0}


def report_for_deal_room(session_dir: Path) -> dict[str, Any]:
    report = read_json(session_dir / REPORT_FILE)
    status = read_json(session_dir / STATUS_FILE) or {"state": "awaiting_context", "session": session_dir.name}
    if report is None:
        return {"status": status}
    # Do not mutate the private report while presenting it.  Outlook creation
    # is a local, idempotent side effect with its own receipt; fold only that
    # receipt into the loopback response so a reload cannot offer the same
    # draft as though it had never been made.
    visible = json.loads(json.dumps(report))
    receipts = (read_json(session_dir / DRAFTS_FILE) or {}).get("drafts", {})
    if isinstance(receipts, dict):
        for draft in visible.get("draft_proposals", []):
            receipt = receipts.get(draft.get("draft_id"))
            if isinstance(receipt, dict) and receipt.get("content_hash") == draft.get("content_hash"):
                draft["status"] = "created"
                draft["created_at"] = receipt.get("created_at")
    return {"status": status, "report": visible}


def resolve_question(session_dir: Path, question_id: str, resolution: str) -> dict[str, Any]:
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    _string(resolution, "resolution")
    for question in report.get("review_questions", []):
        if question.get("id") == question_id:
            question["resolved"] = True
            question["resolution"] = resolution
            write_private(session_dir / REPORT_FILE, report)
            return {"question_id": question_id, "resolved": True}
    raise ContractError("unknown question id")


def _candidate_binding(list_name: str, index: int, remote: str) -> dict[str, Any]:
    return {"list_name": list_name, "index": index, "remote": remote}


def sanitized_candidates(session_dir: Path) -> dict[str, Any]:
    """Return exact Worker payloads plus local-only report bindings.

    The returned post_call and legacy lists are intentionally separate because
    they have separate Worker validation routes.  Neither list contains a
    transcript, recipient email, or email body.  ``bindings`` never leaves the
    local capture bridge and lets the opaque Worker IDs be written back to the
    correct private report item.
    """
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    post_call_items: list[dict[str, Any]] = []
    legacy_items: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for owner in ("joe", "dell"):
        list_name = f"{owner}_tasks"
        for index, task in enumerate(report.get(list_name, [])):
            if task.get("candidate_id"):
                continue
            post_call_items.append({
                "kind": "assigned_action", "deal_id": task["deal_id"], "assignee": owner,
                "action": task["title"], "due_on": None, "evidence_quote": task["evidence"],
                "confidence": 0.5,
            })
            bindings.append(_candidate_binding(list_name, index, "post_call"))
    for index, update in enumerate(report.get("deal_updates", [])):
        if update.get("candidate_id"):
            continue
        # ``ref`` accepts an exact deal UUID.  This avoids a display-name match
        # and keeps the legacy activity candidate tied to the deal the model
        # was explicitly given.
        legacy_items.append({
            "kind": "activity",
            "payload": {"ref": update["deal_id"], "kind": "note", "summary": update["summary"]},
            "evidence_quote": update["evidence"], "confidence": 0.5,
        })
        bindings.append(_candidate_binding("deal_updates", index, "legacy"))
    for index, draft in enumerate(report.get("draft_proposals", [])):
        if draft.get("candidate_id"):
            continue
        recipient_ref = draft.get("recipient_ref")
        if not isinstance(recipient_ref, str) or not recipient_ref:
            raise ContractError("email draft proposal is missing its exact recipient ref")
        post_call_items.append({
            "kind": "email_draft", "deal_id": draft["deal_id"],
            "recipient_party_id": draft["recipient_party_id"], "recipient_ref": recipient_ref,
            "subject": draft["subject"], "body_sha256": draft["content_hash"],
            "evidence_quote": draft["evidence"], "confidence": 0.5,
        })
        bindings.append(_candidate_binding("draft_proposals", index, "post_call"))
    return {"schema_version": SCHEMA_VERSION, "session": session_dir.name,
            "post_call_items": post_call_items, "legacy_items": legacy_items,
            "bindings": bindings}


def apply_candidate_ids(session_dir: Path, bindings: list[dict[str, Any]], candidate_ids: list[str]) -> dict[str, Any]:
    """Atomically attach opaque Worker IDs to their private report items."""
    if len(bindings) != len(candidate_ids) or not all(isinstance(item, str) and item for item in candidate_ids):
        raise ContractError("candidate receipt does not match local proposal count")
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    for binding, candidate_id in zip(bindings, candidate_ids):
        if not isinstance(binding, dict):
            raise ContractError("candidate binding is invalid")
        list_name = binding.get("list_name")
        index = binding.get("index")
        if list_name not in {"joe_tasks", "dell_tasks", "deal_updates", "draft_proposals"} or not isinstance(index, int):
            raise ContractError("candidate binding is invalid")
        items = report.get(list_name)
        if not isinstance(items, list) or index < 0 or index >= len(items):
            raise ContractError("candidate binding points outside the report")
        if items[index].get("candidate_id") not in {None, candidate_id}:
            raise ContractError("candidate binding conflicts with an existing receipt")
        items[index]["candidate_id"] = candidate_id
        items[index]["candidate_status"] = "pending"
    write_private(session_dir / REPORT_FILE, report)
    return {"updated": len(candidate_ids)}


def report_sha256(session_dir: Path) -> str:
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def create_outlook_draft(session_dir: Path, draft_id: str, approved_content_hash: str, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    report = read_json(session_dir / REPORT_FILE)
    if report is None:
        raise ContractError("post-call report is not ready")
    draft = next((item for item in report["draft_proposals"] if item["draft_id"] == draft_id), None)
    if draft is None or approved_content_hash != draft["content_hash"]:
        raise ContractError("explicit approval must name the current draft content hash")
    if not draft.get("candidate_id") or draft.get("candidate_status") != "confirmed":
        raise ContractError("Outlook draft requires an already-confirmed CARR email candidate")
    drafts = read_json(session_dir / DRAFTS_FILE) or {"drafts": {}}
    existing = drafts["drafts"].get(draft_id)
    if isinstance(existing, dict) and existing.get("content_hash") == approved_content_hash:
        return {"draft_id": draft_id, "content_hash": approved_content_hash, "idempotent": True}
    # The address is retained only in the private report so consuming the index
    # never leaks recipient data into the candidate push payload.
    if not draft.get("recipient_email"):
        raise ContractError("recipient email is unavailable for this proposal")
    if outlook_draft_url_length(draft["recipient_email"], draft["subject"], draft["body"]) > OUTLOOK_URL_LIMIT:
        raise ContractError("email draft exceeds the local Outlook draft size limit")
    repo_root = Path(__file__).resolve().parents[3]
    command = [str(repo_root / "bin" / "outlook-draft.py")]
    payload = {"to": draft["recipient_email"], "subject": draft["subject"], "body": draft["body"]}
    result = runner(command, input=json.dumps(payload), text=True, capture_output=True, timeout=45, check=False)
    if result.returncode != 0:
        raise RuntimeError("draft-only creator failed")
    drafts["drafts"][draft_id] = {"content_hash": approved_content_hash, "created_at": now()}
    write_private(session_dir / DRAFTS_FILE, drafts)
    return {"draft_id": draft_id, "content_hash": approved_content_hash, "idempotent": False}

"""Typed, model-neutral input validation for device evidence submission.

The database remains the authority for principal, open-job, workflow, mode,
schedule, freshness, and append-only checks.  This module merely ensures a
device-local client cannot turn an arbitrary JSON document into SQL.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID


class SubmissionRefused(ValueError):
    pass


Kind = Literal["social_device_evidence", "npi_device_evidence", "claude_scheduler_observation",
               "launchd_scheduler_observation"]
SOCIAL_BUILDERS = frozenset({"linkedin.source-posts", "x.source-posts"})
NPI_RESULT_KEYS = {"source_ref", "npi", "enumeration_type", "last_updated", "addresses", "taxonomies"}


@dataclass(frozen=True)
class Submission:
    kind: Kind
    function: str
    params: tuple[str, ...]
    idempotency_key: str


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise SubmissionRefused(f"{label} has an unsupported shape")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubmissionRefused(f"{label} must be nonempty text")
    return value.strip()


def _timestamp(value: Any, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SubmissionRefused(f"{label} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SubmissionRefused(f"{label} must include an explicit offset")
    return parsed.isoformat()


def _uuid(value: Any) -> str:
    try:
        return str(UUID(_text(value, "job_id")))
    except ValueError as exc:
        raise SubmissionRefused("job_id must be a UUID") from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _idempotency(kind: str, body: dict[str, Any]) -> str:
    digest = hashlib.sha256(_canonical({"kind": kind, **body}).encode("utf-8")).hexdigest()
    return f"device-evidence:v1:{digest}"


def _social(payload: dict[str, Any]) -> Submission:
    source = _object(payload, {"schema_version", "kind", "job_id", "builder_key", "observed_at", "values"}, "submission")
    if source["schema_version"] != 1 or source["kind"] != "social_device_evidence":
        raise SubmissionRefused("social submission version or kind is unsupported")
    job_id, builder = _uuid(source["job_id"]), _text(source["builder_key"], "builder_key")
    if builder not in SOCIAL_BUILDERS:
        raise SubmissionRefused("builder_key is not a registered device builder")
    observed_at = _timestamp(source["observed_at"], "observed_at")
    values = _object(source["values"], {"platform", "collector_state", "voice_version", "source_posts"}, "values")
    platform = "linkedin" if builder == "linkedin.source-posts" else "x"
    if values["platform"] != platform or values["collector_state"] != "available":
        raise SubmissionRefused("platform or collector state does not match builder")
    if not isinstance(values["voice_version"], int) or isinstance(values["voice_version"], bool) or values["voice_version"] <= 0:
        raise SubmissionRefused("voice_version must be a positive integer")
    posts = values["source_posts"]
    if not isinstance(posts, list) or not posts:
        raise SubmissionRefused("source_posts must be a nonempty list")
    minimum, maximum = (3, 5) if platform == "linkedin" else (1, 20)
    if not minimum <= len(posts) <= maximum:
        raise SubmissionRefused("source_posts count is outside the registered range")
    post_fields = {"url", "network_priority"} if platform == "linkedin" else {"url", "read_at"}
    normalized: list[dict[str, Any]] = []
    for post in posts:
        item = _object(post, post_fields, "source post")
        normalized_item: dict[str, Any] = {"url": _text(item["url"], "source post url")}
        if platform == "linkedin":
            if type(item["network_priority"]) is not bool:
                raise SubmissionRefused("LinkedIn source post needs boolean network_priority")
            normalized_item["network_priority"] = item["network_priority"]
        else:
            normalized_item["read_at"] = _timestamp(item["read_at"], "X source post read_at")
        normalized.append(normalized_item)
    canonical_values = {"platform": platform, "collector_state": "available", "voice_version": values["voice_version"], "source_posts": normalized}
    body = {"job_id": job_id, "builder_key": builder, "observed_at": observed_at, "values": canonical_values}
    return Submission("social_device_evidence", "ops.record_device_evidence", (job_id, builder, observed_at, _canonical(canonical_values)), _idempotency("social_device_evidence", body))


def _npi(payload: dict[str, Any]) -> Submission:
    source = _object(payload, {"schema_version", "kind", "job_id", "observed_at", "source_release", "source_checksum", "results"}, "submission")
    if source["schema_version"] != 1 or source["kind"] != "npi_device_evidence":
        raise SubmissionRefused("NPI submission version or kind is unsupported")
    job_id, observed_at = _uuid(source["job_id"]), _timestamp(source["observed_at"], "observed_at")
    release, checksum = _text(source["source_release"], "source_release"), _text(source["source_checksum"], "source_checksum")
    if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
        raise SubmissionRefused("source_checksum must be lowercase SHA-256")
    rows = source["results"]
    if not isinstance(rows, list) or not rows:
        raise SubmissionRefused("results must be a nonempty list")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = _object(row, NPI_RESULT_KEYS, "NPI result")
        npi = _text(item["npi"], "npi")
        if re.fullmatch(r"[0-9]{10}", npi) is None:
            raise SubmissionRefused("npi must be exactly ten digits")
        addresses = item["addresses"]
        if not isinstance(addresses, list):
            raise SubmissionRefused("addresses must be a list")
        normalized_addresses: list[dict[str, str]] = []
        for address in addresses:
            entry = _object(address, {"postal_code"}, "NPI address")
            normalized_addresses.append({"postal_code": _text(entry["postal_code"], "postal_code")})
        taxonomies = item["taxonomies"]
        if not isinstance(taxonomies, list) or not taxonomies:
            raise SubmissionRefused("taxonomies must be a nonempty list")
        normalized_taxonomies = [_text(code, "taxonomy") for code in taxonomies]
        normalized.append({"source_ref": _text(item["source_ref"], "source_ref"), "npi": npi,
                           "enumeration_type": _text(item["enumeration_type"], "enumeration_type"),
                           "last_updated": _timestamp(item["last_updated"], "last_updated"),
                           "addresses": normalized_addresses, "taxonomies": normalized_taxonomies})
    body = {"job_id": job_id, "observed_at": observed_at, "source_release": release,
            "source_checksum": checksum, "results": normalized}
    return Submission("npi_device_evidence", "ops.record_npi_device_evidence", (job_id, observed_at, release, checksum, _canonical(normalized)), _idempotency("npi_device_evidence", body))


def _scheduler(payload: dict[str, Any]) -> Submission:
    source = _object(
        payload,
        {"schema_version", "kind", "surface_id", "provider_task_id", "cron_expression", "timezone",
         "enabled", "definition_sha256", "provider_revision", "source_fingerprint", "observed_at"},
        "submission",
    )
    if source["schema_version"] != 1 or source["kind"] != "claude_scheduler_observation":
        raise SubmissionRefused("scheduler observation version or kind is unsupported")
    surface_id = _text(source["surface_id"], "surface_id")
    provider_task_id = _text(source["provider_task_id"], "provider_task_id")
    cron_expression = _text(source["cron_expression"], "cron_expression")
    timezone_name = _text(source["timezone"], "timezone")
    if type(source["enabled"]) is not bool:
        raise SubmissionRefused("enabled must be boolean")
    definition_sha256 = _text(source["definition_sha256"], "definition_sha256")
    source_fingerprint = _text(source["source_fingerprint"], "source_fingerprint")
    if re.fullmatch(r"[0-9a-f]{64}", definition_sha256) is None:
        raise SubmissionRefused("definition_sha256 must be lowercase SHA-256")
    if re.fullmatch(r"[0-9a-f]{64}", source_fingerprint) is None:
        raise SubmissionRefused("source_fingerprint must be lowercase SHA-256")
    provider_revision = _text(source["provider_revision"], "provider_revision")
    observed_at = _timestamp(source["observed_at"], "observed_at")
    body = {
        "surface_id": surface_id, "provider_task_id": provider_task_id,
        "cron_expression": cron_expression, "timezone": timezone_name,
        "enabled": source["enabled"], "definition_sha256": definition_sha256,
        "provider_revision": provider_revision, "source_fingerprint": source_fingerprint,
        "observed_at": observed_at,
    }
    return Submission(
        "claude_scheduler_observation", "ops.record_claude_scheduler_observation",
        (surface_id, provider_task_id, cron_expression, timezone_name,
         "true" if source["enabled"] else "false", definition_sha256, provider_revision,
         source_fingerprint, observed_at),
        _idempotency("claude_scheduler_observation", body),
    )


def _launchd_scheduler(payload: dict[str, Any]) -> Submission:
    source = _object(
        payload,
        {"schema_version", "kind", "surface_id", "label", "timezone", "enabled",
         "plist_sha256", "schedule_sha256", "launchctl_revision", "source_fingerprint",
         "observed_at"},
        "submission",
    )
    if source["schema_version"] != 1 or source["kind"] != "launchd_scheduler_observation":
        raise SubmissionRefused("launchd observation version or kind is unsupported")
    surface_id = _text(source["surface_id"], "surface_id")
    label = _text(source["label"], "label")
    timezone_name = _text(source["timezone"], "timezone")
    if type(source["enabled"]) is not bool:
        raise SubmissionRefused("enabled must be boolean")
    hashes = {name: _text(source[name], name) for name in
              ("plist_sha256", "schedule_sha256", "launchctl_revision", "source_fingerprint")}
    if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in hashes.values()):
        raise SubmissionRefused("launchd provenance must use lowercase SHA-256")
    observed_at = _timestamp(source["observed_at"], "observed_at")
    body = {"surface_id": surface_id, "label": label, "timezone": timezone_name,
            "enabled": source["enabled"], **hashes, "observed_at": observed_at}
    return Submission(
        "launchd_scheduler_observation", "ops.record_launchd_scheduler_observation",
        (surface_id, label, timezone_name, "true" if source["enabled"] else "false",
         hashes["plist_sha256"], hashes["schedule_sha256"], hashes["launchctl_revision"],
         hashes["source_fingerprint"], observed_at),
        _idempotency("launchd_scheduler_observation", body),
    )


def validate_submission(payload: Any) -> Submission:
    if not isinstance(payload, dict):
        raise SubmissionRefused("submission must be an object")
    kind = payload.get("kind")
    if kind == "social_device_evidence":
        return _social(payload)
    if kind == "npi_device_evidence":
        return _npi(payload)
    if kind == "claude_scheduler_observation":
        return _scheduler(payload)
    if kind == "launchd_scheduler_observation":
        return _launchd_scheduler(payload)
    raise SubmissionRefused("submission kind is unregistered")

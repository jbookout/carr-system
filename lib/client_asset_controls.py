"""Deterministic refusal controls for client-facing and social-batch assets.

These are deliberately small predicates instead of model prompts: whether a
required block is present, whether an artifact has a declared readiness tier,
and whether a weekly batch contains a reply-only item are all facts.  Callers
must run these controls before creating or replacing an asset.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
import re
from typing import Any


class AssetControlRefusal(ValueError):
    """A deterministic precondition for an asset was not met."""


def _has_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Iterable):
        return any(_has_content(item) for item in value)
    return value is not None


def require_search_commentary(client: Mapping[str, Any]) -> None:
    """Every search packet needs both sourced findings and confirmations."""
    missing = [key for key in ("findings", "confirmations")
               if not _has_content(client.get(key))]
    if missing:
        raise AssetControlRefusal(
            "search packet refused: client." + ", client.".join(missing)
            + " must each contain at least one sourced market commentary item")


def require_declined_and_why(plan: Mapping[str, Any]) -> None:
    """A client-facing recommendation must name a declined alternative and why."""
    # The record-layer plan may expose the block directly or under its audience
    # metadata.  Accept either contract, but never infer a decline from silence.
    value = plan.get("declined_and_why")
    if not _has_content(value):
        value = (plan.get("recommendation") or {}).get("declined_and_why") \
            if isinstance(plan.get("recommendation"), Mapping) else None
    if not _has_content(value):
        raise AssetControlRefusal(
            "client-facing recommendation refused: declined_and_why must name "
            "at least one alternative and the reason it was declined")


def require_asset_tier(asset: Mapping[str, Any]) -> str:
    """Return the declared readiness tier or refuse an untiered new asset."""
    tier = asset.get("tier")
    if not isinstance(tier, str) or not tier.strip():
        raise AssetControlRefusal("asset creation refused: a non-empty tier is required")
    return tier.strip()


def require_supersession(old_artifact: str, tombstone_path: str | None,
                         loop_ref: str | None) -> None:
    """Refuse replacement unless the old artifact is tombstoned and queued.

    ``loop_ref`` is the canonical record-layer reference returned from the
    already-created add-loop call.  This gate intentionally does not create the
    loop itself: a renderer has no database-owner credential and cannot obtain
    one by asking to overwrite a client artifact.
    """
    if not isinstance(old_artifact, str) or not old_artifact.strip():
        raise AssetControlRefusal("supersession refused: old artifact path is required")
    if not isinstance(tombstone_path, str) or not tombstone_path.strip():
        raise AssetControlRefusal("supersession refused: _TO_DELETE tombstone path is required")
    normalized = tombstone_path.replace("\\", "/")
    if "/_TO_DELETE/" not in normalized and not normalized.startswith("_TO_DELETE/"):
        raise AssetControlRefusal("supersession refused: tombstone must be under _TO_DELETE/")
    if not isinstance(loop_ref, str) or not re.fullmatch(
            r"(?:loop:)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
            loop_ref.strip()):
        raise AssetControlRefusal(
            "supersession refused: canonical add-loop UUID receipt/reference is required")


def write_artifact_atomically(path: str, content: str, *,
                              tombstone_path: str | None = None,
                              loop_ref: str | None = None) -> None:
    """Install a complete artifact without creating a no-live-copy window."""
    existed = os.path.exists(path)
    if existed:
        require_supersession(path, tombstone_path, loop_ref)
        assert tombstone_path is not None
        os.makedirs(os.path.dirname(tombstone_path), exist_ok=True)
        if os.path.exists(tombstone_path):
            raise AssetControlRefusal(
                f"replacement refused: tombstone already exists at {tombstone_path}")
    temp_path = path + f".tmp-{os.getpid()}"
    try:
        with open(temp_path, "x", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        if not existed:
            os.replace(temp_path, path)
            return
        assert tombstone_path is not None
        os.replace(path, tombstone_path)
        try:
            os.replace(temp_path, path)
        except OSError as exc:
            if not os.path.exists(path) and os.path.exists(tombstone_path):
                os.replace(tombstone_path, path)
            raise AssetControlRefusal(
                f"replacement refused: atomic install failed and prior artifact was restored: {exc}") from exc
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def reject_weekly_quote_tweets(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate a weekly batch and return normalized candidates.

    Quote tweets belong exclusively to the daily reply route; accepting one in
    the weekly batch would create the wrong scheduled work even when it remains
    a draft.
    """
    accepted: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise AssetControlRefusal(f"weekly batch refused: candidate {index} is not an object")
        kind = str(candidate.get("content_type") or "").strip().lower().replace("_", "-")
        if kind == "quote-tweet":
            raise AssetControlRefusal(
                f"weekly batch refused: candidate {index} is quote-tweet; route it to daily X replies")
        accepted.append(dict(candidate))
    return accepted

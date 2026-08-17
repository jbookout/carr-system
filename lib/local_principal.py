"""The established local-machine principal, shared by local read paths.

This extracts the existing ``~/.config/carr/local-actor.json`` seam used by
``local-verb.mjs`` and ``db-tap.py``.  It accepts no caller-supplied slug and
fails closed for personal-scope reads unless the established identity is one
of the two known human principals.
"""
from __future__ import annotations

import json
from pathlib import Path

LOCAL_ACTOR_FILE = Path.home() / ".config" / "carr" / "local-actor.json"
PARTNER_PRINCIPALS = frozenset({"joe", "dell"})


class LocalPrincipalError(RuntimeError):
    pass


def local_actor_slug() -> str:
    try:
        data = json.loads(LOCAL_ACTOR_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LocalPrincipalError(
            "local principal is not established; run bin/set-local-actor.sh once"
        ) from exc
    slug = data.get("actor_slug") if isinstance(data, dict) else None
    if not isinstance(slug, str) or not slug.strip():
        raise LocalPrincipalError("local principal record has no actor_slug")
    return slug.strip()


def local_partner_principal() -> str:
    slug = local_actor_slug()
    if slug not in PARTNER_PRINCIPALS:
        raise LocalPrincipalError(
            f"local principal {slug!r} has no reviewed personal-scope mapping"
        )
    return slug

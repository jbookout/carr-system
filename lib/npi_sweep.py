"""Pure deterministic NPPES NPI-2 sweep filtering; no I/O or canonical writes."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any


class NpiInputError(ValueError):
    pass


def _instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise NpiInputError(f"{field} must be ISO-8601 text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NpiInputError(f"{field} must be ISO-8601 text") from exc
    if parsed.tzinfo is None:
        raise NpiInputError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def load_policy(raw: Mapping[str, Any]) -> dict[str, Any]:
    prefixes = raw.get("territory_zip_prefixes")
    if (raw.get("schema_version") != 1 or raw.get("policy_key") != "npi-sweep-weekly"
            or raw.get("npi_type") != "NPI-2" or not isinstance(prefixes, list)
            or set(prefixes) != {"323", "324", "325", "363", "364", "365", "366"}
            or raw.get("maximum_update_age_days") != 14):
        raise NpiInputError("unrecognized NPI sweep policy")
    taxonomy = raw.get("taxonomy")
    if not isinstance(taxonomy, Mapping) or taxonomy.get("required") is not True:
        raise NpiInputError("NPI policy must require taxonomy")
    return dict(raw)


def _codes(row: Mapping[str, Any]) -> set[str]:
    values = row.get("taxonomies")
    if not isinstance(values, list) or not values or not all(isinstance(x, str) and x for x in values):
        raise NpiInputError("NPPES result taxonomies must be non-empty code strings")
    return set(values)


def _territory_zip(row: Mapping[str, Any], prefixes: set[str]) -> str | None:
    addresses = row.get("addresses")
    if not isinstance(addresses, list) or not all(isinstance(x, Mapping) for x in addresses):
        raise NpiInputError("NPPES result addresses must be objects")
    zips = sorted({str(address.get("postal_code", ""))[:3] for address in addresses
                   if isinstance(address.get("postal_code"), str)
                   and str(address["postal_code"])[:3] in prefixes})
    return zips[0] if zips else None


def filter_candidates(results: Iterable[Mapping[str, Any]], *, policy: Mapping[str, Any],
                      approved_taxonomy_codes: Iterable[str], as_of: datetime) -> list[dict[str, Any]]:
    """Return stable, proposal-only NPI candidates or refuse malformed input.

    Taxonomy is intentionally supplied through a reviewed policy seam: with no
    exact repository doctrine allowlist, an empty/unknown taxonomy never passes.
    """
    validated = load_policy(policy)
    if as_of.tzinfo is None:
        raise NpiInputError("as_of must include timezone")
    allowed = {code for code in approved_taxonomy_codes if isinstance(code, str) and code}
    if not allowed:
        raise NpiInputError("reviewed healthcare taxonomy allowlist is required")
    cutoff = as_of.astimezone(timezone.utc) - timedelta(days=validated["maximum_update_age_days"])
    selected: dict[str, tuple[datetime, str, dict[str, Any]]] = {}
    prefixes = set(validated["territory_zip_prefixes"])
    for row in results:
        if not isinstance(row, Mapping):
            raise NpiInputError("NPPES result must be an object")
        source_ref, npi = row.get("source_ref"), row.get("npi")
        if not isinstance(source_ref, str) or not source_ref or not isinstance(npi, str) or not npi.isdigit() or len(npi) != 10:
            raise NpiInputError("NPPES result requires source_ref and 10-digit npi")
        if row.get("enumeration_type") != validated["npi_type"]:
            continue
        updated = _instant(row.get("last_updated"), "last_updated")
        if updated < cutoff:
            continue
        postal_prefix = _territory_zip(row, prefixes)
        if postal_prefix is None:
            continue
        codes = _codes(row)
        matched = sorted(codes & allowed)
        if not matched:
            continue
        candidate = {"npi": npi, "source_ref": source_ref, "last_updated": updated.isoformat(),
                     "postal_prefix": postal_prefix, "taxonomy_codes": matched,
                     "action": "propose"}
        prior = selected.get(npi)
        # Stable NPI dedup: freshest record wins; exact timestamp ties use the
        # lexical immutable source reference, independent of upstream order.
        if prior is None or (updated, source_ref) > (prior[0], prior[1]):
            selected[npi] = (updated, source_ref, candidate)
    return [selected[npi][2] for npi in sorted(selected)]

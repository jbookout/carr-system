"""Public quarterly PECOS refresh; retained pools never pretend to be fresh pulls."""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date
from pathlib import Path

CATALOG = "https://data.cms.gov/data.json"
TITLE = "Medicare Fee-For-Service Public Provider Enrollment"
PUBLICATION = "https://data.cms.gov/provider-characteristics/medicare-provider-supplier-enrollment/medicare-fee-for-service-public-provider-enrollment"
REGISTRY = "https://npiregistry.cms.hhs.gov/api/?version=2.1&number="
ROOT = Path(__file__).resolve().parents[2] / "out/routines/radar"
MAX_PAGES = 200
MAX_NEW_NPIS = 6000


def quarter(now):
    return f"{now.year}Q{(now.month - 1) // 3 + 1}"


def refresh_due(now, state):
    # A missed quarter is caught up on the next weekly firing, regardless of month.
    return state.get("quarter") != quarter(now)


def load_state():
    path = ROOT / "pecos-refresh.json"
    return json.loads(path.read_text()) if path.exists() else {}


def dataset_api(catalog):
    if not isinstance(catalog, dict):
        raise ValueError("CMS catalog is not an object")
    series = [s for s in catalog.get("datasetSeries", []) if s.get("title") == TITLE]
    if len(series) != 1:
        raise ValueError("CMS catalog must identify one PECOS dataset series")
    latest = series[0].get("last", {}).get("@id")
    datasets = [d for d in catalog.get("dataset", []) if d.get("@id") == latest]
    if len(datasets) != 1:
        raise ValueError("latest PECOS dataset is not in CMS catalog")
    for distribution in datasets[0].get("distribution", []):
        url = distribution.get("downloadURL") or distribution.get("accessURL") or ""
        if re.fullmatch(r"https://data\.cms\.gov/data-api/v1/dataset/[a-f0-9-]+/data", url):
            return url, latest
    raise ValueError("latest PECOS dataset lacks its public data API")


def enrollment_rows(rows):
    """Preserve public enrollment identities; dates come only from source IDs."""
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("PECOS API page must be an array of objects")
    result = {}
    for raw in rows:
        if raw.get("STATE_CD") not in {"FL", "AL"}:
            continue
        npi, enrollment = str(raw.get("NPI") or ""), str(raw.get("ENRLMT_ID") or "")
        if not re.fullmatch(r"\d{10}", npi) or not enrollment:
            raise ValueError("PECOS row lacks NPI or enrollment ID")
        key = npi + "|" + enrollment
        first, last = str(raw.get("FIRST_NAME") or "").strip(), str(raw.get("LAST_NAME") or "").strip()
        name = (first + " " + last).strip() if first and last else str(raw.get("ORG_NAME") or "").strip()
        if not name:
            continue
        filed = None
        match = re.match(r"^[IO](\d{4})(\d{2})(\d{2})", enrollment)
        if match:
            try:
                filed = date(*map(int, match.groups())).isoformat()
            except ValueError:
                pass
        result[key] = {"name": name, "npi": npi, "enrollment_id": enrollment,
                       "kind": "person" if first and last else "org", "date": filed,
                       "profession": str(raw.get("PROVIDER_TYPE_DESC") or ""),
                       "state": raw["STATE_CD"], "source_url": PUBLICATION}
    return result


def pull_pecos(fetch, now, state, *, fixture=None):
    """Bootstrap a baseline once, then inspect only newly appearing enrollments.

    Bootstrap preserves every public identifier but creates no historical lead
    flood. A later source revision is diffed by NPI+ENRLMT_ID, including rows
    without a decodable date. No score or profession threshold qualifies a row.
    """
    catalog = fixture["catalog"] if fixture is not None else json.loads(fetch(CATALOG))
    api, vintage = dataset_api(catalog)
    all_rows = []
    if fixture is not None:
        all_rows = fixture.get("rows", [])
    else:
        for region in ("AL", "FL"):
            for page in range(MAX_PAGES):
                query = urllib.parse.urlencode({"filter[STATE_CD]": region, "size": 5000, "offset": page * 5000})
                rows = json.loads(fetch(api + "?" + query))
                if not isinstance(rows, list):
                    raise ValueError("PECOS API did not return an array")
                all_rows.extend(rows)
                if len(rows) < 5000:
                    break
            else:
                raise ValueError("PECOS API exceeded its page bound; no truncated refresh accepted")
    parsed = enrollment_rows(all_rows)
    baseline = set(state.get("keys", []))
    fresh = sorted(set(parsed) - baseline) if state.get("baseline_initialized") is True else []
    new_npis = {parsed[key]["npi"] for key in fresh}
    if len(new_npis) > MAX_NEW_NPIS:
        raise ValueError("PECOS delta exceeds public geography lookup bound; no partial refresh accepted")
    geography = {}
    for npi in sorted(new_npis):
        response = fixture.get("registry", {}).get(npi, {}) if fixture is not None else json.loads(fetch(REGISTRY + npi))
        found = [r for r in response.get("results", []) if str(r.get("number")) == npi]
        if len(found) != 1:
            raise ValueError("PECOS geography lookup did not resolve the exact NPI")
        provider = found[0]
        addresses = [a for a in provider.get("addresses", []) if a.get("address_purpose") == "LOCATION"] + provider.get("practiceLocations", [])
        geography[npi] = addresses
    candidates = []
    for key in fresh:
        row = parsed[key]
        for address in geography[row["npi"]]:
            from .lead_signals import in_territory
            location = {"city": address.get("city"), "state": address.get("state"), "zip": address.get("postal_code")}
            if in_territory(location):
                candidates.append({**row, **location, "phone": address.get("telephone_number", ""),
                                   "registry_source_url": REGISTRY + row["npi"]})
                break
    new_state = {"quarter": quarter(now), "pulled_at": now.isoformat(), "vintage": vintage,
                 "baseline_initialized": True, "keys": sorted(parsed), "baseline_count": len(parsed)}
    health = {"pool": "pecos.json", "state": "refreshed", "source": PUBLICATION,
              "baseline_rows": len(parsed), "new_enrollments": len(fresh), "territory_candidates": len(candidates),
              "bootstrap": state.get("baseline_initialized") is not True}
    return candidates, new_state, health


def save_refresh(rows, state):
    ROOT.mkdir(parents=True, exist_ok=True)
    upstream = ROOT / "upstream"
    upstream.mkdir(exist_ok=True)
    for path, value in ((upstream / "pecos.json", rows), (ROOT / "pecos-refresh.json", state)):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True))
        temporary.replace(path)

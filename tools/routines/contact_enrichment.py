"""Select in code, research on the subscription, validate, then write via verbs."""
import datetime as dt
import hashlib
import json
import re
from urllib.parse import urlparse

PROMPT = "ops/routines/prompts/contact-enrichment.txt"
CONTACT_FIELDS = {"phone", "cell", "email", "title", "city", "county"}
IDENTITY_FIELDS = {"name", "company", "org", "org_id", "npi", "specialty"}
FACT_FIELDS = CONTACT_FIELDS | {"website", "social", "address", "license_status",
                              "entity_filing", "hours", "practitioners", "category_slug", "verticals"}
RESEARCH_RETRY_DAYS = 30

QUEUE_SQL = """
with hydrated as (
 select q.priority,q.subject_type,q.subject_id::text,
        coalesce(r.ref,p.ref) as ref,p.id::text as party_id,p.name,
        p.contact_state,p.merged_into::text,p.title,p.email,p.phone,p.cell,
        p.city,p.county,p.state,p.npi,p.specialty,p.version as party_version,
        org.name as company,v.category_slug,v.verticals,v.version as vendor_version,
        q.reverification_due,
        row_number() over (partition by p.id order by q.priority) as person_rank
 from v_control_plane_enrichment_queue q
 left join v_ref_index r on r.subject_type=q.subject_type and r.subject_id=q.subject_id
 join party p on p.id=case when q.subject_type='party' then q.subject_id else r.party_id end
 left join party org on org.id=p.org_id
 left join vendor v on q.subject_type='vendor' and v.id=q.subject_id
 where not coalesce(r.merged,false) and p.merged_into is null and p.deleted_at is null
   and p.contact_state <> 'do_not_contact'
   and not exists (
     select 1 from record_flag attempt
      where attempt.subject_type='party' and attempt.subject_id=p.id
        and attempt.kind='contact_enrichment_attempt'
        and attempt.expires_on > current_date
   )
)
select * from hydrated where person_rank=1 order by priority limit 40
"""


def select_contacts(rows, now=None):
    selected, seen = [], set()
    for row in sorted(rows, key=lambda r: (int(r["priority"]), str(r.get("ref", "")))):
        if (row.get("subject_type") not in {"party", "vendor", "lead", "client"}
                or row.get("contact_state") == "do_not_contact" or row.get("merged_into")
                or row.get("deleted_at") or not row.get("ref") or not row.get("party_id")):
            continue
        if row["party_id"] not in seen:
            if now is not None and row.get("research_retry_after"):
                if dt.date.fromisoformat(str(row["research_retry_after"])) > now.date():
                    continue
            seen.add(row["party_id"])
            selected.append(row)
        if len(selected) == 40:
            break
    return selected


def prepare(ctx):
    fixture = ctx.fixture
    rows = fixture.get("queue", []) if fixture is not None else ctx.query(QUEUE_SQL)
    selected = select_contacts(rows, ctx.now)
    categories = (fixture.get("categories", []) if fixture is not None else
                  ctx.query("select slug,label from vendor_category order by sort,slug")) if selected else []
    return {"work": bool(selected), "inputs": {"records": selected, "categories": categories},
            "selected": len(selected)}


def text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError(f"invalid {name}")
    return value.strip()


def citations(value):
    if not isinstance(value, list) or not value or len(value) > 12:
        raise ValueError("citations required")
    result = []
    for item in value:
        item = text(item, "citation")
        parsed = urlparse(item)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("citation must be a public source URL")
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("local citation is not a public source")
        result.append(item)
    return result


def validate_response(response, inputs):
    if not isinstance(response, dict) or set(response) != {"records"} or not isinstance(response["records"], list):
        raise ValueError("expected records object")
    selected = {r["ref"]: r for r in inputs["records"]}
    seen, result = set(), []
    for row in response["records"]:
        if not isinstance(row, dict) or set(row) != {"ref", "ambiguous", "identity_evidence", "facts", "corrections", "searched_sources"}:
            raise ValueError("unexpected contact response fields")
        ref = row["ref"]
        if ref not in selected or ref in seen:
            raise ValueError("unselected or duplicate contact")
        seen.add(ref)
        if type(row["ambiguous"]) is not bool:
            raise ValueError("ambiguous must be boolean")
        citations(row["searched_sources"])
        if not isinstance(row["identity_evidence"], list):
            raise ValueError("identity evidence required")
        evidence = row["identity_evidence"]
        fields = set()
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"field", "value", "citations"}:
                raise ValueError("invalid identity evidence")
            field = item["field"]
            if field not in {"name", "company", "city", "email", "phone", "cell", "npi", "address"}:
                raise ValueError("invalid matching field")
            current = selected[ref].get(field)
            if not current or str(current).strip().casefold() != text(item["value"], "identity value").casefold():
                raise ValueError("identity evidence must corroborate supplied record")
            citations(item["citations"]); fields.add(field)
        corroborated = "name" in fields and (bool(fields & {"email", "phone", "cell", "npi", "address"})
                                               or {"company", "city"} <= fields)
        if not row["ambiguous"] and not corroborated:
            raise ValueError("name plus a second identity field required")
        if not isinstance(row["facts"], list) or len(row["facts"]) > 40:
            raise ValueError("invalid facts")
        fact_names = set()
        for fact in row["facts"]:
            if not isinstance(fact, dict) or set(fact) != {"field", "value", "citations"}:
                raise ValueError("invalid fact shape")
            field = fact["field"]
            if field not in FACT_FIELDS or field in fact_names or row["ambiguous"]:
                raise ValueError("invalid, duplicate, or ambiguous contact fact")
            fact_names.add(field); citations(fact["citations"])
            if field in {"verticals", "practitioners"}:
                if not isinstance(fact["value"], list) or not fact["value"]:
                    raise ValueError("list fact required")
                for v in fact["value"]: text(v, field)
            else:
                value = text(fact["value"], field)
                if field == "category_slug" and value.casefold() in {"misc", "miscellaneous"}:
                    raise ValueError("catch-all vendor categories are forbidden")
                if field == "email" and (not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) or value.lower().endswith("@carr.us")):
                    raise ValueError("invalid or placeholder email")
                if field in {"phone", "cell"} and (len(re.sub(r"\D", "", value)) not in {10,11}
                                                       or re.sub(r"\D", "", value).endswith("2056436555")):
                    raise ValueError("invalid or placeholder phone")
                if field in {"website", "social"}: citations([value])
        if not isinstance(row["corrections"], list) or len(row["corrections"]) > 12:
            raise ValueError("invalid corrections")
        for correction in row["corrections"]:
            if not isinstance(correction, dict) or set(correction) != {"field", "proposed", "citations"}:
                raise ValueError("invalid correction shape")
            if correction["field"] not in IDENTITY_FIELDS:
                raise ValueError("invalid identity correction field")
            text(correction["proposed"], "correction"); citations(correction["citations"])
        if len({c["field"] for c in row["corrections"]}) != len(row["corrections"]):
            raise ValueError("duplicate identity corrections")
        result.append(row)
    if seen != set(selected):
        raise ValueError("research must account for every selected contact")
    return result


def effect_key(ref, kind, value):
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"contact:{ref}:{kind}:{digest}"


def execute(ctx, plan):
    if not plan["work"]:
        return {"processed": 0, "findings": 0, "reviews": 0}
    if ctx.dry_run:
        response = ctx.fixture.get("model_response") if ctx.fixture is not None else None
        if response is None:
            return {"processed": 0, "selected": plan["selected"], "model_calls": 0,
                    "writes": 0, "research_pending": True}
    else:
        response = ctx.model(PROMPT, plan["inputs"])
    rows = validate_response(response, plan["inputs"])
    records = {r["ref"]: r for r in plan["inputs"]["records"]}
    categories = {r["slug"] for r in plan["inputs"]["categories"]}
    state = getattr(ctx, "state", {})
    observed = dt.datetime.fromisoformat(state["started_at"]) if state.get("started_at") else ctx.now
    verified_at = observed.isoformat()
    expires_on = (observed.date() + dt.timedelta(days=180)).isoformat()
    findings, reviews, updates = 0, 0, 0

    def write(verb, args, key):
        if not ctx.dry_run:
            cached = state.get("effects", {}).get(key)
            if cached:
                if cached["verb"] != verb:
                    raise RuntimeError("contact effect key changed its verb")
                args = {k: v for k, v in cached["args"].items() if k != "idempotency_key"}
            reply = ctx.write(verb, args, key)
            if not isinstance(reply, dict) or reply.get("ok") is not True:
                raise RuntimeError(f"{verb} did not acknowledge the effect")

    for row in rows:
        if not ctx.dry_run and row["ref"] in state.get("completed_contact_refs", []):
            continue
        original = records[row["ref"]]
        if not ctx.dry_run:
            current = ctx.query("select version,contact_state,merged_into from party where id=%s", (original["party_id"],))
            if len(current) != 1 or current[0]["contact_state"] == "do_not_contact" or current[0]["merged_into"]:
                raise RuntimeError("party contact eligibility changed during research")
        review = []
        contact_fields, vendor_fields = {}, {}
        for fact in row["facts"]:
            field, value = fact["field"], fact["value"]
            kind = "office_phone" if field == "phone" else field
            payload = {"subject": row["ref"], "kind": kind,
                       "value": {"value": value, "verified_at": verified_at, "citations": fact["citations"]},
                       "source": " ".join(fact["citations"]), "observed_at": verified_at, "expires_on": expires_on}
            write("record-finding", payload, effect_key(row["ref"], kind, payload)); findings += 1
            if field in CONTACT_FIELDS: contact_fields[field] = value
            if field == "category_slug":
                if value in categories and original["subject_type"] == "vendor": vendor_fields[field] = value
                else: review.append(f"New vendor category proposal: {value}; sources: {payload['source']}")
            if field == "verticals" and original["subject_type"] == "vendor": vendor_fields[field] = value
        for correction in row["corrections"]:
            field = correction["field"]
            payload = {"subject": row["ref"], "kind": "discrepancy",
                       "value": {"verified_at": verified_at, "citations": correction["citations"]},
                       "proposes_correction": {"field": field, "current": original.get(field), "proposed": correction["proposed"]},
                       "source": " ".join(correction["citations"]), "observed_at": verified_at, "expires_on": expires_on}
            write("record-finding", payload, effect_key(row["ref"], "discrepancy", payload)); findings += 1
            review.append(f"{field}: recorded {original.get(field)!r}; proposed {correction['proposed']!r}; sources: {payload['source']}")
        if row["ambiguous"]:
            review.append("Multiple identities remain plausible. No contact fields were applied. Sources: " + " ".join(row["searched_sources"]))
        if not row["facts"] and not row["corrections"]:
            payload = {"subject": row["ref"], "kind": "verified", "found": False,
                       "value": {"verified_at": verified_at}, "source": " ".join(row["searched_sources"]),
                       "observed_at": verified_at, "expires_on": expires_on}
            write("record-finding", payload, effect_key(row["ref"], "nothing-found", payload)); findings += 1
        if contact_fields and not ctx.dry_run:
            current = ctx.query("select version,contact_state,merged_into from party where id=%s", (original["party_id"],))
            if len(current) != 1 or current[0]["contact_state"] == "do_not_contact" or current[0]["merged_into"]:
                raise RuntimeError("party contact eligibility changed during research")
            payload = {"party": row["ref"], "base_version": int(current[0]["version"]),
                       "fields": contact_fields,
                       "source": " ".join(dict.fromkeys(url for fact in row["facts"]
                                                         if fact["field"] in contact_fields
                                                         for url in fact["citations"]))}
            write("update-party-contact", payload, effect_key(row["ref"], "contact-update",
                  {"fields": contact_fields, "source": payload["source"], "observed_at": verified_at})); updates += 1
        if vendor_fields and not ctx.dry_run:
            current = ctx.query("select version from vendor where id=%s and merged_into is null", (original["subject_id"],))
            if len(current) != 1: raise RuntimeError("vendor eligibility changed during research")
            payload = {"vendor": row["ref"], "base_version": int(current[0]["version"]), "fields": vendor_fields}
            write("update-vendor", payload, effect_key(row["ref"], "vendor-update",
                  {"fields": vendor_fields, "observed_at": verified_at})); updates += 1
        if review:
            title = f"Review contact research for {original['name']} ({row['ref']})"
            body = "\n".join(review)
            payload = {"situation": "contact enrichment identity verification vendor category research",
                       "title": title[:200], "desired_outcome": body[:2000],
                       "acceptance_criteria": [{"id": "CONTACT-REVIEW", "text": "Confirm the sourced correction or new category, or reject it. Identity fields remain unchanged until review."}]}
            write("report-problem", payload, effect_key(row["ref"], "review", {"title": title, "body": body})); reviews += 1
        payload = {"subject": row["ref"], "subject_kind": "party",
                   "kind": "contact_enrichment_attempt", "internal": True,
                   "value": {"verified_at": verified_at, "identity_ambiguous": row["ambiguous"],
                             "review_pending": bool(review), "facts_recorded": len(row["facts"])},
                   "source": "contact-enrichment-weekly code research receipt",
                   "observed_at": verified_at,
                   "expires_on": (observed.date() + dt.timedelta(days=RESEARCH_RETRY_DAYS)).isoformat()}
        write("record-finding", payload, effect_key(row["ref"], "attempt", payload)); findings += 1
        if not ctx.dry_run:
            state.setdefault("completed_contact_refs", []).append(row["ref"])
            if hasattr(ctx, "save"):
                ctx.save()
    return {"processed": len(rows), "findings": findings, "reviews": reviews,
            "contact_updates": updates, "model_calls": 0 if ctx.dry_run else 1,
            "writes": 0 if ctx.dry_run else findings + reviews + updates}

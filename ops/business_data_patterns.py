"""business_data_patterns.py — shared regex patterns for "does this text carry
CARR business/client/deal content" checks.

WHY THIS EXISTS. jbookout/carr-system is a PUBLIC repository. The real-data
replay fixtures under ops/fixtures/real-replay/ are extracted from local
session transcripts that also hold client and deal work — dollar amounts,
addresses, lease terms, practice names, client reference ids. A single
snapshot scanning clean is not a guarantee the next refresh will: this module
is the ONE place the "is this business data" judgment lives, so
tools/extract-real-replay.py (drop-whole-record time) and
ops/gate-replay-coverage.py's fixture leak scan (CI gate time) never drift
against each other.

IT IS A LIBRARY: no shebang, no main guard, for the reason
ops/typesafe_client.py spells out — describe the construct, never write it
here, see that file's docstring for detail.

WHAT COUNTS AS A LEAK, deliberately broad (false positives here cost an
extractor row; false negatives cost a public leak):
  - dollar amounts, $/SF, square footage
  - street addresses
  - practice/clinic naming ("Dr.", DDS, DMD, clinic, dental, practice)
  - lease terms (NNN, CAM, TI allowance, cap rate, base rent, lease term)
  - phone numbers, email addresses, hostnames
  - secret-shaped strings (bearer tokens, api keys)
  - UUID-shaped tokens (a session id is the one legitimate UUID in this data;
    callers that already know a value IS the session id should exempt it
    explicitly rather than this module guessing)
  - CARR client/deal reference ids (L-/C-/V-/D- prefixed)
"""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

URL_HOSTNAME_RE = re.compile(r"\b(?:https?|ssh|git)://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
BARE_HOSTNAME_RE = re.compile(
    r"(?<![\w/.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:com|net|org|gov|edu)\b"
)

SECRET_MARKER_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|secret|password|bearer|authorization)\b"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-./]{12,}"
    # "Bearer <token>" / "token <token>" space-separated, the real HTTP
    # header shape -- no colon/equals between marker and value.
    r"|\bbearer\s+[A-Za-z0-9_\-./]{12,}"
)

DOLLAR_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?\b|\$\s?/\s?SF\b|\$/SF\b")
SQFT_RE = re.compile(r"(?i)\b\d[\d,]*\s?(?:sq\.?\s?ft\.?|square\s+feet|SF)\b")
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-zA-Z']*\s){1,4}"
    r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Ln|Lane|Way|"
    r"Ct|Court|Pkwy|Parkway|Hwy|Highway|Suite|Ste)\b\.?"
)
PRACTICE_NAME_RE = re.compile(
    r"(?:\bDr\.\s?[A-Z][a-zA-Z]+\b|\bDDS\b|\bDMD\b|(?i:\bclinic\b|\bdental\b|\bpractice\b))"
)
LEASE_TERM_RE = re.compile(
    r"(?i)\b(?:NNN|triple\s+net|CAM\s+charges?|TI\s+allowance|cap\s+rate|"
    r"base\s+rent|lease\s+term)\b"
)
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
CLIENT_REF_RE = re.compile(r"\b[LCVD]-\d{2,}\b")

# name -> compiled pattern. Order is the order findings are reported in.
PATTERNS = {
    "dollar_amount": DOLLAR_RE,
    "sqft": SQFT_RE,
    "street_address": ADDRESS_RE,
    "practice_name": PRACTICE_NAME_RE,
    "lease_term": LEASE_TERM_RE,
    "phone": PHONE_RE,
    "email": EMAIL_RE,
    "url_hostname": URL_HOSTNAME_RE,
    "bare_hostname": BARE_HOSTNAME_RE,
    "secret": SECRET_MARKER_RE,
    "uuid": UUID_RE,
    "client_ref": CLIENT_REF_RE,
}


def find_matches(text: str, *, exempt_uuids=frozenset()):
    """Returns a list of pattern names that matched `text`.

    `exempt_uuids` is the narrow, explicit exemption for a caller that knows
    a specific UUID string in this text IS the session id (the one
    legitimate UUID in this data) and wants it not to trigger `uuid` alone —
    every OTHER UUID-shaped token still counts.
    """
    if not isinstance(text, str) or not text:
        return []
    hits = []
    for name, pattern in PATTERNS.items():
        if name == "uuid" and exempt_uuids:
            found = [m for m in pattern.findall(text) if m not in exempt_uuids]
            if found:
                hits.append(name)
            continue
        if pattern.search(text):
            hits.append(name)
    return hits


def has_business_data(text: str, *, exempt_uuids=frozenset()) -> bool:
    return bool(find_matches(text, exempt_uuids=exempt_uuids))

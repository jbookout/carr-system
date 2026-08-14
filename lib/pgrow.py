"""pgrow — cursor helpers that refuse to hand back None.

`cur.fetchone()[0]` is the house idiom after an `insert … returning id` or a
scalar select, and it is correct right up until the statement returns nothing.
Then it fails with `TypeError: 'NoneType' object is not subscriptable`, several
frames from anything that names what was being asked — in a gate, at 2am, in a
log read the next morning.

It is also the single most repeated shape the type-check tripwire reports: nine
occurrences across two Program 3 gates on 2026-08-14, written by two different
sessions days apart, each re-deriving the same line. Hence one home for it.
"""
from __future__ import annotations

from typing import Any


def fetch_one(cur, what: str = "one row") -> tuple[Any, ...]:
    """cur.fetchone() that raises a named error instead of returning None.

    `what` names what was expected, so the failure says which statement came
    back empty rather than which line happened to subscript the None.
    """
    row = cur.fetchone()
    if row is None:
        raise AssertionError(f"expected {what} back and got none — the statement "
                             f"above returned no rows")
    return row


def fetch_scalar(cur, what: str = "one value") -> Any:
    """The first column of the one row that must exist."""
    return fetch_one(cur, what)[0]

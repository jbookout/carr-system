#!/usr/bin/env python3
"""bridges.py — which holding phrase Doc says while the brain works.

THE PROBLEM THIS FIXES (Joe, 2026-08-08: "anything we can do to engineer the
awkward silence out of interactions is good"): render-bridges.sh has screened a
seven-phrase bridge kit into the cache since it was written, but convo-server
hardcoded ONE of them — "One moment." — on every brain turn. The variety was
already paid for and never spent. A daily user hears the same two words before
every substantive answer, and under rule 6901cc3b that is an adoption failure,
not a rough edge.

THE TRUTHFULNESS LAW, inherited from the council and from render-bridges.sh's
own header: earcon ack + a TRUTHFUL bridge, then substance — never fake
progress. That is why this module does not reach for the situational phrases.
"Checking the record." is true only when a record is genuinely in play, and
pre-brain there is no way to know that (the demo on 2026-08-08 caught exactly
this: "Checking the record." answering "can you hear me?" read as canned).
So the rotation below is drawn ONLY from phrases that are true of every brain
turn without exception. Adding one means passing that test, not sounding nice.

TWO TIERS, because a short bridge makes the silence that follows it MORE
conspicuous, not less. An acknowledgement token ("Right.") followed by a
progress phrase ("One moment.") covers more of the wait and reads as a person
taking a breath rather than a machine emitting a beep. Four of each yields
sixteen openings from eight cached files, which is past the point a daily user
clocks the repetition.
"""

import random

# Tier 1 — pure acknowledgement. True on every turn: it claims only that Doc
# heard, which he did. Never claims work is underway.
ACKS: tuple[str, ...] = (
    "Right.",
    "Okay.",
    "Mm-hm.",
    "Got it.",
)

# Tier 2 — neutral progress. True on every BRAIN turn: he is in fact working on
# it and it will in fact be a moment. Nothing here names a record, a client, a
# lookup or a source, because pre-brain none of those are known.
BRIDGES: tuple[str, ...] = (
    "One moment.",
    "Working on it.",
    "Let me think.",
    "Give me a second.",
)

# Deliberately NOT in the rotation — each is true only in a situation the loop
# cannot detect before the brain answers. Kept here so the next reader knows
# they were considered and why they lost, rather than re-adding them.
_SITUATIONAL_DO_NOT_ROTATE = (
    "Checking the record.",          # only if a record is truly in play
    "Let me pull that up.",          # implies retrieval that may not happen
    "That write needs your confirm at the keyboard.",
    "I'd need the desk for that one.",
    "Say again? I lost part of that.",
)

# The one phrase that has been in the cache since the kit was first rendered.
# convo-server falls back to it if nothing in the rotation resolves, so a
# half-rendered kit degrades to the old behaviour instead of to silence.
FALLBACK = "One moment."

_last: dict[str, str | None] = {"ack": None, "bridge": None}


def _pick(pool: tuple[str, ...], slot: str) -> str:
    """Uniform choice that never repeats the previous pick for this slot.

    No-immediate-repeat matters more than uniformity here: hearing the same
    opening twice in a row is the thing a user notices, and it is exactly what
    plain random() does roughly a quarter of the time on a four-item pool.
    """
    choices = [p for p in pool if p != _last[slot]] or list(pool)
    pick = random.choice(choices)
    _last[slot] = pick
    return pick


def choose(with_ack: bool = True) -> list[str]:
    """The phrases to play, in order, while the brain works.

    Returns a list so the caller plays them back to back through the ordinary
    cache path. Every string returned is expected to be pre-rendered by
    render-bridges.sh; the caller uses speak.py --cache-only and silently plays
    nothing on a miss, because a 30-second live render of a holding phrase
    would be worse than the silence it is meant to cover.
    """
    out = []
    if with_ack:
        out.append(_pick(ACKS, "ack"))
    out.append(_pick(BRIDGES, "bridge"))
    return out


def all_phrases() -> list[str]:
    """Everything the rotation can emit — what render-bridges.sh must cover."""
    return list(ACKS) + list(BRIDGES)

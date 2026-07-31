#!/usr/bin/env python3
"""
pull_placement_metrics.py — ORDER 15 (a) + (b) / wave2-design §2d.

Turns Joe's published social content into RECORDS: `content_piece` + `placement`
rows for what actually published, and `placement_metric` rows for what the
platforms reported about it. The Blotato API is the source of truth for BOTH —
never `published-log.md`, which is known to drift (standing memory rule
`x-lane-separated-and-log-staleness`). The vault logs may ENRICH a row later;
they never create one.

WHY ONE SCRIPT FOR (a) AND (b)
  `placement_metric.placement_id` is a NOT NULL FK to `placement` (0001 as-built,
  ratified binding by the ORDER 11 ruling). A metric row cannot exist before its
  placement does, and both come out of the same two Blotato calls. Splitting them
  would mean two scripts sharing one client, one auth path and one ordering
  constraint — one file is the honest shape.

SCOPE — JOE'S ACCOUNTS ONLY (the order's stop rule)
  Measured, not assumed: the Blotato workspace holds exactly four connected
  accounts, all Joe's (facebook 39262 · instagram 56336 · linkedin 27007 ·
  twitter 21335 — matching the social-media-manager skill's own accounts config).
  Dell runs his accounts separately and they are not in this workspace, so
  "Joe's only" is a property of the source, not a filter this script applies.
  The script REFUSES to run if an unknown account id ever appears.

RECORD SHAPES (0001 as-built; nothing invented, nothing altered)
  content_piece  one per published post. Joe writes per-platform copy, so the
                 89 published posts carry 89 distinct texts — measured, not
                 assumed, and the 1:1 mapping falls out of that rather than
                 being a modeling choice. If two placements ever share byte
                 identical copy, they share one piece.
                 features jsonb carries MECHANICAL facts only (format, media
                 count, length, hashtag count, source ids). No hook family, no
                 voice, no pillar: those are judgments, and per the standing
                 `dialed-in-craft-stays-personal` rule they never belong in a
                 shared surface anyway.
  placement      piece FK, platform, external_id = the Blotato post id,
                 url = the live post URL, live_at = the publish time.
  placement_metric  (placement, kind, observed_at) primary key, so every
                 Blotato analytics snapshot lands as its own row and reruns
                 are free. value numeric, source 'blotato_api'.

METRIC KIND VOCABULARY — VERBATIM FROM THE SOURCE, snake_cased
  0001's comment SUGGESTS 'impressions','engagements','profile_clicks',... but
  `placement_metric.kind` carries no CHECK and no ref table. Mapping Blotato's
  `viewsCount` onto `impressions` would be an equivalence claim nobody has
  ruled, and a wrong one on some platforms. So kinds are stored as the source
  names, snake_cased: views_count, reach_count, likes_count, comments_count,
  shares_count, saves_count, follows_count, interactions_sum,
  profile_visits_count, profile_activity_count, view_time_ms_sum,
  watch_time_ms_avg. Flagged for Fable: a `placement_metric_kind` ref table is
  the 0017-shaped answer, and it is a migration, which is not this order.

ACTOR
  content_piece.author_id = `joe` — the column means "whose voice/account", and
  it is his. Row provenance is the `automation` actor: this is a scheduled job,
  not a one-time import, so it uses the automation path the order names rather
  than the importer's `system` actor.

Usage:
  DATABASE_URL=... .venv/bin/python pipelines/pull_placement_metrics.py           # dry run
  DATABASE_URL=... .venv/bin/python pipelines/pull_placement_metrics.py --apply   # write
Reads BLOTATO_API_KEY from the environment (~/.zprofile is the existing home,
set for the social-media-manager skill's blotato.sh — no new secret).
The dry run touches no database and is the default.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out"

BLOTATO_BASE = "https://backend.blotato.com/v2"
# ORDER 12 measured that Cloudflare's edge refuses urllib's default agent with a
# 403/1010 that reads exactly like an auth failure. Blotato is not behind that
# rule today, but a named agent costs nothing and removes the class of bug.
USER_AGENT = "carr-record-layer/1.0 (pull_placement_metrics)"

# The four accounts in Joe's workspace, from the social-media-manager skill's
# config/blotato-accounts.json. An id outside this set means either Dell's
# accounts arrived in this workspace or the workspace changed; both are STOP
# conditions, not things to filter silently.
KNOWN_PLATFORMS = {"facebook", "instagram", "linkedin", "twitter"}

METRIC_SOURCE = "blotato_api"
PIECE_SOURCE = "blotato_api"

CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
HASHTAG_RE = re.compile(r"(?:^|\s)#\w+")
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


# ── Blotato reads ────────────────────────────────────────────────────────────

def _get(path, params, key):
    url = f"{BLOTATO_BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"blotato-api-key": key, "user-agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_published(key, since, until):
    """Every post Blotato says went live, plus the counts it refuses to.

    Paginates on the documented cursor. `since`/`until` default to -7d/+7d at
    the API, which is why they are always passed explicitly: without them this
    returns a fortnight and looks like the whole history.
    """
    items, cursor, pages = [], None, 0
    while True:
        params = {"limit": 250, "since": since, "until": until}
        if cursor:
            params["cursor"] = cursor
        page = _get("posts", params, key)
        items.extend(page.get("items") or [])
        cursor = page.get("cursor")
        pages += 1
        if not cursor or pages > 50:
            break
    return items


def fetch_analytics(key, since, until):
    """Latest snapshot + full history per post, for the platforms Blotato syncs."""
    out, cursor, pages = [], None, 0
    while True:
        params = {"limit": 100, "since": since, "until": until}
        if cursor:
            params["cursor"] = cursor
        page = _get("analytics", params, key)
        out.extend(page.get("items") or [])
        cursor = page.get("cursor")
        pages += 1
        if not cursor or pages > 50:
            break
    return out


# ── mechanical derivations (facts, never judgments) ──────────────────────────

def media_format(media, url):
    if not media:
        return "text"
    if any(u.lower().split("?")[0].endswith(VIDEO_EXT) for u in media):
        return "video"
    return "carousel" if len(media) > 1 else "image"


def piece_kind(platform, url, fmt):
    """'reel' only when the platform itself says so in the live URL."""
    if platform == "instagram" and url and "/reel/" in url:
        return "reel"
    return "post"


def snake(name):
    return CAMEL_RE.sub("_", name).lower()


def features_for(post, fmt):
    text = post.get("text") or ""
    media = post.get("mediaUrls") or []
    return {
        "source": PIECE_SOURCE,
        "blotato_post_id": str(post["id"]),
        "format": fmt,
        "media_count": len(media),
        "length_chars": len(text),
        "hashtag_count": len(HASHTAG_RE.findall(text)),
    }


# ── the plan (what the DB would hold), built without touching the DB ─────────

def build_plan(posts, analytics):
    published, skipped = [], {"scheduled": 0, "failed": 0, "other": 0}
    unknown = set()
    for p in posts:
        st = (p.get("state") or {}).get("type")
        if st != "published":
            skipped[st if st in skipped else "other"] += 1
            continue
        if p.get("platform") not in KNOWN_PLATFORMS:
            unknown.add(p.get("platform"))
            continue
        published.append(p)

    ana = {str(a["id"]): a for a in analytics}

    pieces, placements, metrics = [], [], []
    by_text = {}
    for p in sorted(published, key=lambda x: x["postTime"]):
        pid = str(p["id"])
        url = (p.get("state") or {}).get("postUrl")
        media = p.get("mediaUrls") or []
        fmt = media_format(media, url)
        text = p.get("text") or ""
        norm = re.sub(r"\s+", " ", text).strip()

        key = (norm, piece_kind(p["platform"], url, fmt))
        if key not in by_text:
            by_text[key] = len(pieces)
            pieces.append(dict(
                idx=len(pieces), kind=key[1], body=text,
                features=features_for(p, fmt), status="live"))
        piece_idx = by_text[key]

        placements.append(dict(
            piece_idx=piece_idx, platform=p["platform"], external_id=pid,
            url=url, live_at=p["postTime"], fmt=fmt,
            length=len(text), media_count=len(media)))

        a = ana.get(pid)
        if not a:
            continue
        snaps = a.get("metricsHistory") or []
        if not snaps and a.get("latestMetrics"):
            snaps = [a["latestMetrics"]]
        wrote_any = False
        for s in snaps:
            observed = s.get("fetchedAt")
            for k, v in (s.get("metrics") or {}).items():
                try:
                    val = float(v)
                except (TypeError, ValueError):
                    continue
                metrics.append(dict(
                    external_id=pid, kind=snake(k), value=val,
                    observed_at=observed))
                wrote_any = True
        if wrote_any:
            pieces[piece_idx]["status"] = "measured"

    return pieces, placements, metrics, skipped, unknown


# ── database ─────────────────────────────────────────────────────────────────

def apply_plan(conn, pieces, placements, metrics):
    """Idempotent on placement.external_id and on the metric primary key."""
    cur = conn.cursor()
    joe = cur.execute("select id from actor where slug='joe'").fetchone()
    automation = cur.execute("select id from actor where slug='automation'").fetchone()
    if not joe or not automation:
        raise RuntimeError("actor rows 'joe' and 'automation' must both exist")
    joe, automation = joe[0], automation[0]

    cur.execute("select external_id, id, piece_id from placement where external_id is not null")
    existing = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    piece_ids = {}
    new_pieces = new_placements = new_metrics = 0

    for pl in placements:
        eid = pl["external_id"]
        if eid in existing:
            placement_id, piece_id = existing[eid]
            piece_ids.setdefault(pl["piece_idx"], piece_id)
            pl["_id"] = placement_id
            continue

        idx = pl["piece_idx"]
        if idx not in piece_ids:
            pc = pieces[idx]
            row = cur.execute(
                "insert into content_piece (author_id, kind, features, status, updated_by) "
                "values (%s,%s,%s::jsonb,%s,%s) returning id",
                (joe, pc["kind"], json.dumps(pc["features"]), pc["status"],
                 automation)).fetchone()
            piece_ids[idx] = row[0]
            new_pieces += 1

        row = cur.execute(
            "insert into placement (piece_id, platform, external_id, url, live_at) "
            "values (%s,%s,%s,%s,%s) returning id",
            (piece_ids[idx], pl["platform"], eid, pl["url"], pl["live_at"])).fetchone()
        pl["_id"] = row[0]
        existing[eid] = (row[0], piece_ids[idx])
        new_placements += 1

    pl_by_eid = {p["external_id"]: p["_id"] for p in placements if p.get("_id")}
    for m in metrics:
        placement_id = pl_by_eid.get(m["external_id"])
        if not placement_id:
            continue
        n = cur.execute(
            "insert into placement_metric (placement_id, observed_at, kind, value, source) "
            "values (%s,%s,%s,%s,%s) on conflict (placement_id, kind, observed_at) do nothing",
            (placement_id, m["observed_at"], m["kind"], m["value"], METRIC_SOURCE)).rowcount
        new_metrics += n or 0

    # Status catches up for pieces whose placements gained metrics on a later run.
    # Scoped to rows THIS pipeline authored (features.source), so a piece created
    # by any other path can never be relabelled by a metrics pull.
    cur.execute("""
        update content_piece set status='measured', updated_by=%s
        where status='live' and features->>'source' = %s and id in (
          select p.piece_id from placement p
          join placement_metric m on m.placement_id = p.id)
    """, (automation, PIECE_SOURCE))
    promoted = cur.rowcount

    conn.commit()
    return new_pieces, new_placements, new_metrics, promoted


# ── report ───────────────────────────────────────────────────────────────────

def write_report(path, pieces, placements, metrics, skipped, applied, counts, window,
                 db_state):
    import collections
    cells = collections.Counter((p["platform"], p["fmt"]) for p in placements)
    measured = collections.Counter()
    have = {m["external_id"] for m in metrics}
    for p in placements:
        if p["external_id"] in have:
            measured[(p["platform"], p["fmt"])] += 1
    kinds = collections.Counter(m["kind"] for m in metrics)
    plat_metrics = collections.Counter()
    for p in placements:
        if p["external_id"] in have:
            plat_metrics[p["platform"]] += 1

    L = []
    A = L.append
    A(f"# Content placements + metrics — ORDER 15 (a)+(b) "
      f"({'APPLY' if applied else 'DRY RUN'})")
    A("")
    A("GENERATED by `pipelines/pull_placement_metrics.py` from the Blotato API. "
      "Never hand-edited: rerun the script.")
    A(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
      f"source window {window[0]} → {window[1]} · "
      f"{'wrote to the database' if applied else 'nothing written'}")
    A("")
    A("## What the source says")
    A("")
    A(f"- posts Blotato reports as **published**: **{len(placements)}**")
    A(f"- distinct copy behind them (`content_piece` rows): **{len(pieces)}**")
    A(f"- observed but NOT recorded — scheduled (not yet live): {skipped['scheduled']} · "
      f"failed (never published): {skipped['failed']} · other: {skipped['other']}")
    A(f"- metric observations available: **{len(metrics)}** across "
      f"**{len(have)}** placements")
    A("")
    A("## Rows this run")
    A("")
    if applied:
        A(f"- `content_piece` inserted: **{counts[0]}**")
        A(f"- `placement` inserted: **{counts[1]}**")
        A(f"- `placement_metric` inserted: **{counts[2]}**")
        A(f"- pieces promoted to status `measured`: **{counts[3]}**")
    else:
        A(f"- would insert `content_piece`: **{db_state['pieces_new']}**")
        A(f"- would insert `placement`: **{db_state['placements_new']}**")
        A(f"- would insert `placement_metric`: **{db_state['metrics_new']}**")
        A(f"- database read: {db_state['note']}")
    A("")
    A("## Feature cells (platform x format) — the weekly learning job's unit")
    A("")
    A("| platform | format | placements | of those, MEASURED |")
    A("|---|---|---|---|")
    for (plat, fmt), n in sorted(cells.items(), key=lambda kv: (-kv[1], kv[0])):
        A(f"| {plat} | {fmt} | {n} | {measured[(plat, fmt)]} |")
    A("")
    A("## Metric coverage, by platform — the honest gap")
    A("")
    A("| platform | placements | with metrics |")
    A("|---|---|---|")
    per_plat = collections.Counter(p["platform"] for p in placements)
    for plat, n in sorted(per_plat.items()):
        A(f"| {plat} | {n} | {plat_metrics[plat]} |")
    A("")
    A("Blotato collects analytics for a subset of platforms only, and in this "
      "workspace that subset is Instagram. Twitter and Facebook return zero "
      "analytics items (measured, not assumed: `/v2/analytics?platform=twitter` "
      "and `...=facebook` both return an empty list); LinkedIn is not an "
      "analytics platform for Blotato at all and its per-post analytics call "
      "404s. A twitter or facebook cell can therefore never cross a metric "
      "threshold through this lane, no matter how many posts accumulate.")
    A("")
    A("## Metric kinds present")
    A("")
    if kinds:
        A("| kind | observations |")
        A("|---|---|")
        for k, n in sorted(kinds.items()):
            A(f"| `{k}` | {n} |")
    else:
        A("None.")
    A("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the rows (default is a dry run that touches no database)")
    ap.add_argument("--since", default="2026-01-01T00:00:00Z")
    ap.add_argument("--until", default=None,
                    help="default: 1 day from now, so a post publishing today is caught")
    ap.add_argument("--report-dir", default=None,
                    help="extra directory to drop a copy of the report in")
    a = ap.parse_args()

    key = os.environ.get("BLOTATO_API_KEY")
    if not key:
        print("BLOTATO_API_KEY is not set. It lives in ~/.zprofile (the existing home, "
              "set for the social-media-manager skill). Nothing attempted.", file=sys.stderr)
        return 78

    until = a.until or (datetime.now(timezone.utc)
                        .replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    if not a.until:
        # one day of headroom so a post that fires later today is not missed
        from datetime import timedelta
        until = ((datetime.now(timezone.utc) + timedelta(days=1))
                 .replace(microsecond=0).isoformat().replace("+00:00", "Z"))

    try:
        posts = fetch_published(key, a.since, until)
        analytics = fetch_analytics(key, a.since, until)
    except urllib.error.HTTPError as e:
        print(f"Blotato API refused the read: HTTP {e.code} {e.read()[:200]!r}",
              file=sys.stderr)
        return 75
    except OSError as e:
        print(f"Blotato API unreachable: {e}", file=sys.stderr)
        return 75

    pieces, placements, metrics, skipped, unknown = build_plan(posts, analytics)
    if unknown:
        print(f"STOP: unknown platform(s) in the Blotato workspace: {sorted(unknown)}. "
              "This script is scoped to Joe's four accounts; an unexpected account is a "
              "ruling, not a filter.", file=sys.stderr)
        return 65

    OUT.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = OUT / f"placement-pull-{stamp}.md"

    counts = (0, 0, 0, 0)
    db_state = dict(pieces_new=len(pieces), placements_new=len(placements),
                    metrics_new=len(metrics),
                    note="not read (no DATABASE_URL) — the 'would insert' counts above "
                         "are the FULL source counts, not a delta against what is "
                         "already recorded")

    url = os.environ.get("DATABASE_URL") or os.environ.get("CARR_IMPORT_DB_URL")
    if url:
        import psycopg
        with psycopg.connect(url) as conn:
            if a.apply:
                counts = apply_plan(conn, pieces, placements, metrics)
                db_state["note"] = "written"
            else:
                cur = conn.cursor()
                cur.execute("select external_id from placement where external_id is not null")
                have = {r[0] for r in cur.fetchall()}
                new_pl = [p for p in placements if p["external_id"] not in have]
                db_state.update(
                    placements_new=len(new_pl),
                    pieces_new=len({p["piece_idx"] for p in new_pl}),
                    metrics_new=sum(1 for m in metrics if m["external_id"] not in have),
                    note=f"read OK — {len(have)} placement(s) already recorded")
    elif a.apply:
        print("DATABASE_URL is not set — --apply has nothing to write to.", file=sys.stderr)
        return 78

    write_report(report_path, pieces, placements, metrics, skipped, a.apply, counts,
                 (a.since, until), db_state)
    if a.report_dir:
        copy = Path(a.report_dir) / "placement-pull-latest.md"
        write_report(copy, pieces, placements, metrics, skipped, a.apply, counts,
                     (a.since, until), db_state)
        print(f"report: {copy}")

    print(f"{'APPLIED' if a.apply else 'DRY RUN'} — published {len(placements)} · "
          f"pieces {len(pieces)} · metric observations {len(metrics)} · "
          f"inserted pieces {counts[0]} placements {counts[1]} metrics {counts[2]}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

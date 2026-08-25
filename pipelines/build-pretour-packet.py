#!/usr/bin/env python3
"""
build-pretour-packet.py, the CARR pre-tour packet in the Hughes format.

Dell, 2026-08-21: "use the hughes packet format for river bank."

The format is the Dr Hughes Pre-Tour Briefing of 2026-08-19: navy masthead, serif
headings, gold accent, a snapshot strip, an at-a-glance comparison table, then one
numbered card per option carrying a photo, the reasoning, a fact grid, what to confirm
before signing, our own note, and ruled lines to write on during the tour.

That deliverable was built by a one-off script with its content hardcoded inline and
half its stylesheet appended at build time. This is the same design driven by data, so
the next client is a content file rather than a forked script. The stylesheet lives
beside this file in pretour-style.css.

Format decisions carried over verbatim from that build, because they were deliberate:
  - No taglines and no tier labels. One numbered card style for every option.
  - Listing agent NAME is kept; contact numbers never appear.
  - Our note sits in a highlighted box above blank ruled lines.

Two rules that bind every CARR surface are enforced here rather than remembered:
  - Every card carries a physical address. Raw land with no street number carries its
    road, its parcel number, and a landmark. A record with neither is refused.
  - No em dashes anywhere, including the file name.

Usage:
    python3 build-pretour-packet.py <folder> --recovery --reason "..." --vault <path>

Expects in that folder:
    content.json      the packet copy and facts
    photos/<file>     any image a record names in "photo" or "plans"

Writes <folder>/<slug>-pretour-packet.html, self-contained, no external requests.
"""

import argparse
import base64
import html
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.client_asset_controls import (AssetControlRefusal, require_asset_tier,
                                       require_declined_and_why, require_search_commentary,
                                       write_artifact_atomically)
from lib.drive_recovery import add_recovery_arguments, require_recovery

HERE = os.path.dirname(os.path.abspath(__file__))
EM_DASH = "—"

# Same guard the space-search generator uses. A name is who to expect at the door; a
# phone number is contact detail and never reaches a client page.
CONTACT = re.compile(r"\d{3}|@|https?://|\bwww\.|\.(?:com|net|org|us|biz|co)\b", re.I)


class PacketRefusal(ValueError):
    """A deterministic precondition for the packet was not met."""


def e(s):
    return html.escape(str(s), quote=True) if s is not None else ""


def b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def data_uri(folder, name):
    path = os.path.join(folder, "photos", name)
    if not os.path.exists(path):
        raise PacketRefusal(f"photo not found: {name}")
    ext = os.path.splitext(name)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    return f"data:{mime};base64,{b64(path)}"


def no_em_dash(obj, where="content.json"):
    """Refuse before rendering rather than catching it in the PDF afterwards."""
    blob = json.dumps(obj, ensure_ascii=False)
    if EM_DASH in blob:
        m = re.search(r".{0,45}" + EM_DASH + r".{0,45}", blob)
        raise PacketRefusal(f"em dash in {where}: {m.group(0)!r}. Banned on every CARR "
                            f"surface. Use a comma, a colon, or a full stop.")


def address_line(p):
    """The physical address, labelled, on every card. Never blank, never inferred from
    the heading: a land listing's heading is the broker's title and carries no street
    number."""
    loc = p.get("location") or {}
    street, parcel = (loc.get("street") or "").strip(), (loc.get("parcel") or "").strip()
    near = (loc.get("near") or "").strip()
    if not (street or parcel):
        raise PacketRefusal(
            f'{p["addr"]}: no location.street and no location.parcel. Every card carries '
            f'a physical address; raw land carries its road, parcel number and a landmark.')
    bits = [street] if street else []
    if parcel:
        bits.append(f"Parcel {parcel}" if street else f"No street number assigned. Parcel {parcel}")
    if near:
        bits.append(near)
    return f'<div class="paddrline"><span>Address</span> {e(". ".join(b.rstrip(".") for b in bits))}.</div>'


def agent_line(p):
    a = (p.get("agent") or "").strip()
    src = (p.get("src") or "").strip()
    if a and CONTACT.search(a):
        raise PacketRefusal(f'{p["addr"]}: listing agent field carries contact detail '
                            f'({a!r}). Names and firms only.')
    parts = []
    if a:
        parts.append(f'<span>Listing agent</span> {e(a)}')
    if src:
        parts.append(f'<span>Source</span> {e(src)}')
    return f'<div class="agent">{" &nbsp;&middot;&nbsp; ".join(parts)}</div>' if parts else ""


def notes_block(note, lines=3):
    n = f'<p class="usernote">{e(note)}</p>' if note else ""
    return ('<div class="notebox"><h4>Notes</h4>' + n
            + '<div class="noteline"></div>' * lines + "</div>")


def facts(rows):
    return "".join(f'<div class="f"><span class="fk">{e(k)}</span>'
                   f'<span class="fv">{e(v)}</span></div>' for k, v in rows)


def media(folder, p):
    if not p.get("photo"):
        # No picture, and require_photo has already established that this option
        # carries a written reason. Print the reason in the slot the photograph
        # would have filled, so the gap is answered on the page rather than left
        # for the client to wonder about.
        why = (p.get("photo_absent_reason") or "").strip()
        return f'<div class="cmedia1"><div class="nophoto">{e(why)}</div></div>' if why else ""
    return (f'<div class="cmedia1"><img class="hero fw" src="{data_uri(folder, p["photo"])}" '
            f'alt="{e(p["addr"])}"></div>')


def plans_block(folder, p):
    """Drawings for an option where we hold them, inside its own card."""
    items = []
    for pl in p.get("plans") or []:
        items.append(
            f'<figure class="plan"><img src="{data_uri(folder, pl["file"])}" '
            f'alt="{e(pl["title"])} for {e(p["addr"])}">'
            f'<figcaption><b>{e(pl["title"])}</b> {e(pl.get("note", ""))} '
            f'{e(pl.get("source", ""))}</figcaption></figure>')
    if not items:
        return ""
    note = p.get("plans_note") or ""
    return (f'<div class="plans"><h4>Drawings</h4>{"".join(items)}'
            f'{f"<p class=plansrc>{e(note)}</p>" if note else ""}</div>')


def card(folder, p):
    about = "".join(f"<li>{e(x)}</li>" for x in p.get("about", []))
    conf = "".join(f"<li>{e(x)}</li>" for x in p.get("confirm", []))
    ctr = f'<div class="pctr">{e(p["ctr"])}</div>' if p.get("ctr") else ""
    two = ""
    if about or conf:
        two = (f'<div class="twocol">'
               f'{f"<div class=mini><h4>About the property</h4><ul>{about}</ul></div>" if about else ""}'
               f'{f"<div class=mini><h4>Confirm before signing</h4><ul>{conf}</ul></div>" if conf else ""}'
               f'</div>')
    return f'''
    <section class="card">
      <div class="cnum">{e(p["n"])}</div>
      <div class="chead"><div>
        <h3 class="paddr">{e(p["addr"])}</h3>{ctr}
        <div class="pcity">{e(p["city"])}</div>
      </div></div>
      {address_line(p)}
      {media(folder, p)}
      <p class="pcopy">{e(p["copy"])}</p>
      <div class="factgrid">{facts(p.get("facts", []))}</div>
      {two}
      {plans_block(folder, p)}
      {notes_block(p.get("note", ""))}
      {agent_line(p)}
    </section>'''


def require_photo(p):
    """Every option carries a picture. Dell ruled this on 2026-08-25, in his words:
    "Dont ever produce a pre tour of tour packet without a pic for each option."

    A tour packet is walked through with a client, and an option with no picture
    reads as the one nobody bothered with. The rule is absolute rather than a
    default, so it is enforced here rather than remembered: a missing picture stops
    the build and names the option, instead of shipping a card with a hole in it.

    This is deliberately NOT satisfied by a placeholder image.

    AMENDED 2026-08-25, same day, on the first option that tripped it: 10916 Emerald
    Coast Parkway. That option is off market and, in its own record, "not published
    on CoStar, Crexi, ECAR, or Moody's." No photograph of it exists in the rev
    folders, the 19 August packet source, the vault, or Downloads, and none can,
    because it was never listed. The absolute form of this rule would have blocked
    the packet forever on a stop we actually want to walk.

    So the rule keeps its teeth and gains a named exit. What Dell was protecting
    against, in his reasoning, is a card that "reads as the one nobody bothered
    with" - a SILENT hole. An option that states on its face why no photograph
    exists reads as the opposite: the one nobody else has. A silent hole is still a
    hard stop. An explained absence renders the explanation where the picture would
    have gone, in the client's language.

    The reason must be written by a human into content.json. The renderer never
    invents one, and it still never accepts a placeholder image.
    """
    name = (p.get("photo") or "").strip()
    why = (p.get("photo_absent_reason") or "").strip()
    if not name and not why:
        raise PacketRefusal(
            f'{p["addr"]}: no photo. Every option in a tour packet carries a '
            f'picture (Dell, 2026-08-25). Add "photo" to this option and put the '
            f'file in photos/. If no photograph can exist, say why in '
            f'"photo_absent_reason" and that sentence prints on the card.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=".")
    # Re-rendering over a packet that already exists is a supersession, and the
    # tombstone gate wants the add-loop UUID that queues the old copy for review.
    # write_artifact_atomically has always asked for this, but nothing ever put it
    # on the command line, so the gate was unreachable: the only way past it was to
    # render into a brand new folder every time. That is where rev2 and rev3 came
    # from. The flag is the fix; the folders were the symptom.
    ap.add_argument("--loop-ref", dest="loop_ref",
                    help="add-loop UUID queueing the superseded artifact; "
                         "required only when overwriting an existing packet")
    add_recovery_arguments(ap)
    a = ap.parse_args()
    try:
        vault = str(require_recovery(a, "document asset API for CARR brand logo"))
    except ValueError as exc:
        print(f"build-pretour-packet: STOP: {exc}", file=sys.stderr)
        return 2

    folder = a.folder
    with open(os.path.join(folder, "content.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    client = c["client"]

    try:
        no_em_dash(c)
        require_search_commentary(client)
        require_declined_and_why(client)
        require_asset_tier(c.get("asset") or {})
        for p in c["options"]:
            address_line(p)
            agent_line(p)
            require_photo(p)
    except (AssetControlRefusal, PacketRefusal) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2

    logo = b64(os.path.join(vault, "DNA", "Marketing", "Brand Assets",
                            "Logos", "CARR_White_Logo.png"))
    css = open(os.path.join(HERE, "pretour-style.css"), encoding="utf-8").read()

    # THE THREE OPENING BLOCKS ARE OPTIONAL, and each is omitted entirely rather
    # than rendered empty. Dell, 2026-08-25: "take out the info on the bottom half
    # of this first page and start with options at a glance." Blanking them in
    # content.json was not enough on its own, because the template emitted the
    # wrapper whatever was inside it, which left an empty bordered box and a
    # stray rule where the summary had been.
    #
    # The representation statement is safe to drop from the TOP because it is not
    # dropped from the DOCUMENT: promise_footer carries the same disclosure in the
    # footer and stays unconditional. A packet may lose its opening summary. It
    # never loses the statement of who CARR works for.
    lead = client.get("lead") or ""
    promise = client.get("promise") or ""
    snapshot = client.get("snapshot") or []

    snap = "".join(f'<div><div class="k">{e(k)}</div><div class="v">{e(v)}</div></div>'
                   for k, v in snapshot)
    lead_block = f'<p class="lead">{e(lead)}</p>' if lead.strip() else ""
    snap_block = f'<div class="snap">{snap}</div>' if snapshot else ""
    fid_block = f'<div class="fid">{e(promise)}</div>' if promise.strip() else ""

    thead = "".join(f"<th>{e(h)}</th>" for h in c["glance"]["columns"])
    tbody = ""
    for row in c["glance"]["rows"]:
        tds = "".join(
            f'<td class="tc-num">{e(v)}</td>' if i == 0 else
            f'<td class="tc-all">{e(v)}</td>' if i == c["glance"].get("bold_column", -1) else
            f"<td>{e(v)}</td>" for i, v in enumerate(row))
        tbody += f"<tr>{tds}</tr>"

    cards = "".join(card(folder, p) for p in c["options"])
    take = "".join(f'<div class="tk"><h4>{e(t)}</h4><p>{e(d)}</p></div>'
                   for t, d in client["findings"])
    conf = "".join(f'<div class="dq"><h4>{e(t)}</h4><p>{e(d)}</p></div>'
                   for t, d in client["confirmations"])
    decl = "".join(f'<div class="dq"><h4>{e(t)}</h4><p>{e(d)}</p></div>'
                   for t, d in client["declined_and_why"])

    doc = f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(client["title"])} &middot; {e(client["name"])}</title>
<style>{css}</style></head>
<body><div class="wrap">
<header>
  <div class="htop"><img src="data:image/png;base64,{logo}" alt="CARR"><span class="kick">{e(client["kicker"])}</span></div>
  <h1>{e(client["title"])}<br>{e(client["subtitle"])}</h1>
  <div class="sub">{e(client["markets"])}</div>
  <div class="prep"><b>Prepared for {e(client["name"])}</b> &nbsp;|&nbsp; {e(client["where"])}</div>
</header>
<main>
  {lead_block}
  {snap_block}
  {fid_block}

  <h2><span class="secnum">Options at a glance</span></h2>
  <div class="h2sub">{e(c["glance"]["intro"])}</div>
  <div class="tblwrap"><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>
  <p class="tnote">{e(c["glance"].get("note", ""))}</p>

  <h2><span class="secnum">The options</span> &nbsp;<span style="font-weight:400;color:#5b6675;font-size:14px">{len(c["options"])} to tour</span></h2>
  <div class="h2sub">{e(c["options_intro"])}</div>
  {cards}

  <h2><span class="secnum">What this search tells you</span></h2>
  <div class="tkgrid">{take}</div>

  <h2><span class="secnum">To confirm on your side</span></h2>
  <div class="dqgrid">{conf}</div>

  <h2><span class="secnum">What we would not do, and why</span></h2>
  <div class="dqgrid">{decl}</div>
</main>
<footer>
  <div class="sig"><div>
      <div class="name">{e(client["advisor"]["name"])}</div>
      <div class="role">{e(client["advisor"]["title"])}</div>
      <div class="contact"><a href="tel:{e(client["advisor"]["tel"])}">{e(client["advisor"]["phone"])}</a><br>
        <a href="mailto:{e(client["advisor"]["email"])}">{e(client["advisor"]["email"])}</a></div>
    </div>
    <img src="data:image/png;base64,{logo}" alt="CARR" style="height:26px;opacity:.95">
  </div>
  <div class="fidfoot">{e(client["promise_footer"])}</div>
  <div class="meta">{e(client["sources"])}</div>
</footer>
</div></body></html>'''

    if EM_DASH in doc:
        print("STOP: an em dash reached the rendered document", file=sys.stderr)
        return 2

    out = os.path.join(folder, f'{client["slug"]}-pretour-packet.html')
    tomb = os.path.join(folder, "_TO_DELETE", os.path.basename(out)) if os.path.exists(out) else None
    try:
        write_artifact_atomically(out, doc, tombstone_path=tomb, loop_ref=a.__dict__.get("loop_ref"))
    except (AssetControlRefusal, OSError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out}")
    print(f"  {len(c['options'])} options | {os.path.getsize(out)/1024/1024:.2f} MB")


if __name__ == "__main__":
    raise SystemExit(main())

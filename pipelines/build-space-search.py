#!/usr/bin/env python3
"""
build-space-search.py — CARR client-facing space-search report generator.

Reads a normalized properties.json (schema v1, see DNA/Deal Management/space-search-sop.md)
and emits ONE self-contained HTML file: CARR brand fonts, logo, and photos all inlined as
data URIs, no external requests, works offline and as an email attachment.

Source-agnostic by design. CoStar, Moody's/Catylist, Crexi, and ECAR all normalize into the
same record shape, so the report looks identical no matter which platform supplied a field.
That consistency is the whole point of generating our own detail sheets instead of stapling
four different MLS PDF formats together.

HARD RULE: no listing-agent or brokerage contact information EVER reaches a client-facing
report (Joe, 2026-07-22). Broker data lives in the internal xlsx only. This generator has no
code path that renders it — the normalized schema simply has no field for it.

Usage:
    python3 build-space-search.py <search-folder>

Expects inside that folder:
    properties.json          the normalized records
    photos/<file>.jpg        any photos referenced by a record's "photo" key (optional)

Writes:
    <client-slug>-space-search-<date>.html
"""

import argparse
import base64, json, os, re, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from lib.client_asset_controls import (AssetControlRefusal, require_asset_tier,
                                       require_declined_and_why, require_search_commentary,
                                       write_artifact_atomically)

# CARR_VAULT override (orchestrator-lane corrective, 2026-07-25): was a bare constant.
BRAND = os.path.join(os.environ.get("CARR_VAULT") or "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI",
    "DNA", "Marketing", "Brand Assets")  # moved to shared DNA 2026-07-29 (T54)

ASSETS = {
    "OSWALD":     f"{BRAND}/fonts/Oswald.ttf",
    "MONTSERRAT": f"{BRAND}/fonts/Montserrat.ttf",
    "LOGO_BLUE":  f"{BRAND}/Logos/CARR_Solo_Blue_Logo.png",
    "LOGO_WHITE": f"{BRAND}/Logos/CARR_White_Logo.png",
}


def b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def esc(s):
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def money(n):
    return "" if n is None else f"${n:,.0f}"


def sf(n):
    return "" if n is None else f"{n:,}"


NOT_PUBLISHED = '<span class="np">Not published</span>'


def val(v, fmt=str):
    """Render a value, or a visible 'Not published' when the source did not carry it.
    Gaps must stay visible — a blank cell reads as an oversight, a labelled gap reads
    as a known open item and becomes a question for the landlord."""
    return NOT_PUBLISHED if v in (None, "") else fmt(v)


def spec(label, value, em=False, note=None):
    cls = ' class="em"' if em else ""
    sub = f"<small>{esc(note)}</small>" if note else ""
    return f'<div><dt>{esc(label)}</dt><dd{cls}>{value}{sub}</dd></div>'


def size_line(s):
    # A listing that never published a suite size gets the visible gap, same as any
    # other unknown. " SF" with nothing in front of it reads as a broken template.
    if s["min_sf"] is None:
        return NOT_PUBLISHED
    if s["min_sf"] == s["max_sf"] or s["max_sf"] is None:
        return f'{sf(s["min_sf"])} SF'
    return f'{sf(s["min_sf"])}&ndash;{sf(s["max_sf"])} SF'


def rate_line(r):
    if r["min"] is None:
        return NOT_PUBLISHED
    if r["max"] is None or r["min"] == r["max"]:
        return f'${r["min"]:,.2f} /SF/yr'
    return f'${r["min"]:,.2f}&ndash;{r["max"]:,.2f} /SF/yr'


def monthly_note(s, r):
    """Qualify the monthly figure with the area it is based on, and with what it leaves
    out. A listing with no published service type gets no clause rather than the word
    None, which is how an unset field used to reach the page."""
    if not r["monthly_at_contig"]:
        return None
    base = f'at {sf(s["contig_sf"])} SF' if s.get("contig_sf") else None
    if r["services"] == "Triple Net":
        return f'{base}, before NNN' if base else "before NNN"
    if r["services"]:
        return f'{base}, {r["services"]}' if base else str(r["services"])
    return base




# ---------------------------------------------------------------------------
# PURCHASE / INVESTMENT SUPPORT (added 2026-07-31, Le C-063 30/70 search)
#
# The generator was lease-only: every headline figure was rent per square foot
# per year. An owner-occupier or investment BUY answers different questions --
# what does it cost, what does it yield, who is in it, and how much of it can
# the client actually occupy -- so a purchase record carries a "sale" block and
# these renderers run instead of the rate ones.
#
# ADDITIVE BY DESIGN. Nothing fires unless a record carries "sale", so every
# existing lease search still renders byte for byte. Verified against Hughes.
# ---------------------------------------------------------------------------

def price_line(x):
    if x.get("price") is None:
        return NOT_PUBLISHED
    psf = f' &middot; ${x["price_psf"]:,.0f} /SF' if x.get("price_psf") else ""
    return f'{money(x["price"])}{psf}'


def yield_line(x):
    """Cap rate and NOI travel together or the number means nothing on its own."""
    cap = f'{x["cap_rate"]:.2f}%' if x.get("cap_rate") else None
    noi = money(x["noi"]) if x.get("noi") else None
    if cap and noi:
        return f'{cap} on {noi} NOI'
    return cap or (f'{noi} NOI' if noi else NOT_PUBLISHED)


def occupancy_line(x):
    """What the buyer can actually take, stated as a share of the building, because
    that share is the whole deal on an owner-occupier purchase."""
    own, bldg = x.get("own_sf"), x.get("building_sf")
    if not own:
        return x.get("occupancy_note") or NOT_PUBLISHED
    pct = f' ({own / bldg:.0%} of the building)' if bldg else ""
    # square feet are whole numbers; a float reaching sf() prints "10,422.0 SF"
    return f'{sf(int(round(own)))} SF{pct}'


def sale_specs(p, s, b):
    x = p["sale"]
    return [
        spec("Price", price_line(x), em=True),
        spec("Yield", yield_line(x)),
        spec("Building", f'{sf(b["building_sf"])} SF' if b.get("building_sf") else NOT_PUBLISHED),
        spec("He could occupy", occupancy_line(x), em=True,
             note=x.get("occupancy_note")),
        spec("Tenancy", val(b.get("tenancy"))),
        spec("Status", val(p["timing"]["status"])),
    ]


def build_tour_card(p, photos, minis=None):
    """Tier 1 and 2: a full card with photo, reasoning, specs, and an expandable detail panel.

    NO loading="lazy" on these images (removed 2026-07-29, Joe's go). Every byte is already
    inlined as a data URI, so there is no network request to defer and lazy loading buys nothing.
    What it DID cost: 11 of 16 images stayed blank until a human scrolled them into view, so every
    screenshot, thumbnail and print-to-PDF captured empty photo frames and an empty map. Dell's
    side read the Hughes report as having no photos and no map. Do not add it back.
    """
    img = ""
    if p.get("photo") and p["photo"] in photos:
        img = (f'<div class="shot"><img src="data:image/jpeg;base64,{photos[p["photo"]]}" '
               f'alt="{esc(p["address"])}"></div>')

    # A locator cut centred on this property. The pin sits dead centre because the
    # image was cropped around the coordinate, so no positioning maths is needed.
    mini = ""
    m = (minis or {}).get(str(p["rank"]))
    if m:
        cap = ("Approximate position. The mapping data does not carry this address, "
               "so the pin sits on the road." if m.get("approx") else
               "Immediate surroundings.")
        mini = (f'<figure class="mini"><div class="miniwrap">'
                f'<img src="{m["image"]}" width="{m["w"]}" height="{m["h"]}" '
                f'alt="Close-up map showing the location of {esc(p["address"])}">'
                f'<span class="minipin" aria-hidden="true"></span></div>'
                f'<figcaption>{esc(cap)} Map data &copy; OpenStreetMap contributors</figcaption>'
                f'</figure>')
        img = img + mini

    s, r, t, b, site = p["size"], p["rate"], p["timing"], p["building_info"], p["site"]

    if p.get("sale"):
        specs = sale_specs(p, s, b)
    else:
      specs = [
        spec("Available", size_line(s)),
          spec("Contiguous", f'{sf(s["contig_sf"])} SF', em=True) if s.get("contig_sf") else "",
          spec("Asking", rate_line(r)),
          spec("Base monthly", money(r["monthly_at_contig"]), em=True,
               note=monthly_note(s, r))
               if r["monthly_at_contig"] else spec("Base monthly", NOT_PUBLISHED),
          spec("Structure", val(r["services"])),
          spec("Available from", val(t["occupancy"])),
          spec("Term", val(t["term"])),
        spec("Status", val(t["status"])),
      ]

    # The second panel is the detail sheet: everything we know, plus what we do not.
    detail = [
        spec("Building class", val(b["class"])),
        spec("Year built", val(b["year_built"])),
        spec("Building size", f'{sf(b["building_sf"])} SF' if b["building_sf"] else NOT_PUBLISHED),
        spec("Storeys", val(b.get("stories"))),
        spec("Tenancy", val(b["tenancy"])),
        spec("Property type", val(b["primary_type"])),
        spec("Spaces in building", val(s.get("space_count"))),
        spec("Floor", val(s.get("floor"))),
        spec("Divisible", "Yes" if s.get("divisible") else "No"),
        spec("Parking", val(site["parking_spaces"])),
        spec("Zoning", val(site["zoning"])),
        spec("Operating expenses", f'${r["opex_psf"]:,.2f} /SF' if r.get("opex_psf") else NOT_PUBLISHED),
        spec("Total annual rent",
             f'{money(r["total_year_min"])}&ndash;{money(r["total_year_max"])[1:]}'
             if r.get("total_year_min") and r.get("total_year_max") and r["total_year_min"] != r["total_year_max"]
             else (money(r["total_year_min"]) if r.get("total_year_min") else NOT_PUBLISHED)),
        spec("On market", val(t["on_market"])),
    ]

    hl = ""
    if p.get("highlights"):
        items = "".join(f"<li>{esc(h)}</li>" for h in p["highlights"])
        hl = f'<div class="sub"><h4>About the building</h4><ul>{items}</ul></div>'

    vf = ""
    if p.get("verify"):
        items = "".join(f"<li>{esc(v)}</li>" for v in p["verify"])
        vf = (f'<div class="sub"><h4>What we still need to confirm</h4><ul>{items}</ul>'
              f'<p class="vnote">These are open questions for the landlord, not problems with the space. '
              f'We get answers on all of them before you sign anything.</p></div>')

    src = ""
    if p.get("sources"):
        src = f'<p class="src">Listing data: {esc(", ".join(p["sources"]))}</p>'

    # UX doctrine law 8 (one card grammar) + law 3 (one action, the literal words).
    # This used to be a mailto per card, which fired a separate email on every click
    # (Joe, 2026-07-22). It is now a selection: pick as many as you like, and send one
    # message at the end. On paper the button prints as a tick box, which is what a
    # tour packet actually needs.
    act = (f'<div class="act">'
           f'<button type="button" class="pick" data-rank="{p["rank"]}" '
           f'data-addr="{esc(p["address"])}" data-city="{esc(p["city"])}" aria-pressed="false">'
           f'<span class="box" aria-hidden="true"></span>'
           f'<span class="lbl-off">Add to my tour list</span>'
           f'<span class="lbl-on">On your tour list</span>'
           f'</button>'
           f'<span class="hint">Pick as many as you want. Your list builds at the bottom of '
           f'this page and goes to me in one message.</span>'
           f'</div>')

    building = f' <span class="bname">{esc(p["building"])}</span>' if p.get("building") else ""

    return f"""
      <article class="prop" id="p{p["rank"]:02d}">
        <div class="idx">{p["rank"]:02d}</div>
        <div class="pbody">
          <div class="phead">
            <div><h3 class="paddr">{esc(p["address"])}{building}</h3>
                 <p class="pcity">{esc(p["city"])}</p></div>
            <span class="chip">{esc(p["chip"])}</span>
          </div>
          {img}
          <p class="why">{esc(p["why"])}</p>
          <dl class="specs">{"".join(x for x in specs if x)}</dl>
          <details class="more">
            <summary><span class="sopen">Full detail</span><span class="sclose">Hide detail</span></summary>
            <div class="mbody">
              <dl class="specs dspecs">{"".join(detail)}</dl>
              {hl}{vf}{src}
            </div>
          </details>
          {act}
        </div>
      </article>"""


def empty_state(title, body):
    """UX doctrine law 11b: a section that can be empty gets an authored state naming
    what happened and the next step, never a silent blank."""
    return f'<div class="empty"><h4>{esc(title)}</h4><p>{esc(body)}</p></div>'


def build_map(folder):
    """Render map.json, if the map step has been run. A search with no map is a
    complete report; the section simply does not appear."""
    path = os.path.join(folder, "map.json")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as fh:
        m = json.load(fh)

    pins = []
    for pin in m["pins"]:
        approx = " approx" if pin.get("approx") else ""
        qual = " (approximate location)" if pin.get("approx") else ""
        label = f'{pin["rank"]:02d} {pin["address"]}, {pin["city"]}{qual}'
        pins.append(
            f'<button class="pin t-{pin["tier"]}{approx}" style="left:{pin["x"]}%;top:{pin["y"]}%" '
            f'data-go="p{pin["rank"]:02d}" title="{esc(label)}" aria-label="{esc(label)}">'
            f'<i>{pin["rank"]}</i></button>')

    note = ""
    if m.get("missing"):
        names = ", ".join(f'{x["address"]}' for x in m["missing"])
        note = (f'<p class="mapnote">Not shown on the map: {esc(names)}. '
                f'These are new roads the mapping data does not carry yet, so rather than '
                f'guess at a position we have left them off. Each one is in the list below.</p>')

    approx_n = sum(1 for p in m["pins"] if p.get("approx"))
    if approx_n:
        note += (f'<p class="mapnote">{approx_n} pins sit on the road rather than the exact '
                 f'building, for the same reason. They are marked with a dashed ring.</p>')

    return f"""
  <section>
    <div class="sechead"><h2>Where these are</h2><span class="ct">{len(m["pins"])} of __N_TOTAL__ mapped</span></div>
    <p>Click any pin to jump to that space. The clustering tells you something on its own:
       how far apart your options really are, and how much of one afternoon a tour would take.</p>
    <div class="mapwrap">
      <img src="{m["image"]}" alt="Map of the search area with each available space marked"
           width="{m["width"]}" height="{m["height"]}">
      {"".join(pins)}
    </div>
    <div class="maplegend">
      <span><b class="k-tour"></b>Worth touring</span>
      <span><b class="k-look"></b>Worth a look</span>
      <span><b class="k-out"></b>Ruled out</span>
      <span style="margin-left:auto">{esc(m["attribution"])}</span>
    </div>
    {note}
  </section>"""


def build_out_row(p):
    return f"""
      <div class="out" id="p{p["rank"]:02d}"><div class="idx">{p["rank"]:02d}</div><div>
        <span class="oaddr">{esc(p["address"])}</span><span class="ocity">{esc(p["city"])}</span>
        <p class="oreason"><b>{esc(p["reason_lead"])}</b> {esc(p["reason"])}</p>
      </div></div>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=".")
    ap.add_argument("--supersede-existing", action="store_true",
                    help="tombstone a same-name prior render after its canonical add-loop receipt exists")
    ap.add_argument("--loop-ref", help="canonical add-loop receipt/reference for the supersession")
    a = ap.parse_args()
    folder = a.folder
    with open(os.path.join(folder, "properties.json"), encoding="utf-8") as fh:
        data = json.load(fh)

    c = data["client"]
    try:
        require_search_commentary(c)
        require_declined_and_why(c)
        require_asset_tier(data.get("asset") or {})
    except AssetControlRefusal as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    props = data["properties"]

    photos = {}
    pdir = os.path.join(folder, "photos")
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if fn.lower().endswith((".jpg", ".jpeg")):
                photos[fn] = b64(os.path.join(pdir, fn))

    minis = {}
    mpath = os.path.join(folder, "map.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as fh:
            minis = json.load(fh).get("minis", {})

    tour = [p for p in props if p.get("tier") == "tour"]
    look = [p for p in props if p.get("tier") == "look"]
    out  = [p for p in props if p.get("tier") == "out"]

    # Asking range across the TOUR set only. Spanning all 33 produced "$12 to $45",
    # which is not a market range, it is the distance between a warehouse bay and a
    # medical suite. Quoting it invites the client to anchor on a number attached to
    # a building we would never put them in. (Joe, 2026-07-22.)
    # The opening sentence names the deal type. It used to be hardcoded to "available
    # for lease", which told a BUYER his purchase search was a rent roll (caught on the
    # Le C-063 report, 2026-07-31, before it left the building).
    if any(p.get("sale") for p in tour):
        standfirst = (f'{len(props)} buildings are for sale across your search area right now. '
                      f'{len(tour)} of them are worth your time, and the reasons the others are not '
                      f'tell you a great deal about this market.')
    else:
        standfirst = (f'{len(props)} spaces are available for lease across your search area right now. '
                      f'{len(tour)} of them are worth your time, and the reasons the others are not '
                      f'tell you a great deal about this market.')

    if any(p.get("sale") for p in tour):
        # Asking PRICE range across the tour set only, same rule as the rent span:
        # quoting the full search range mixes buildings we would never put him in.
        prices = [p["sale"]["price"] for p in tour if p.get("sale") and p["sale"].get("price")]
        rate_span = (f'${min(prices):,.0f}&ndash;{max(prices):,.0f}' if prices else "&mdash;")
    else:
        rates = [p["rate"]["min"] for p in tour if p.get("rate") and p["rate"].get("min")]
        rate_span = f'${min(rates):,.0f}&ndash;{max(rates):,.0f}' if rates else "&mdash;"

    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "space-search-template.html")
    with open(tpl_path, encoding="utf-8") as fh:
        html = fh.read()

    pending = ""
    if c.get("sources_pending"):
        pending = (f'<p class="pending">Still to run: {esc(", ".join(c["sources_pending"]))}. '
                   f'Those sources publish parking counts, zoning, and operating expenses, which is '
                   f'where several of the open questions above get answered.</p>')
    elif c.get("coverage_note"):
        # Every source has been run. Say what each one did and did not cover, so the
        # reader can see the edges of the search rather than assuming there are none.
        pending = f'<p class="pending">{esc(c["coverage_note"])}</p>'

    def bullets(items):
        """Each item is [lead, body]. The lead is bolded; the body carries the point."""
        return "".join(f'<li><b>{esc(lead)}</b> {esc(body)}</li>' for lead, body in items)

    repl = {
        "__MAP__": build_map(folder),
        "__FINDINGS__": bullets(c.get("findings", [])),
        "__CONFIRMATIONS__": bullets(c.get("confirmations", [])),
        "__TITLE__": f'{c["area"].split(" through ")[0]} Space Search',
        "__CLIENT__": esc(c["name"]),
        "__PREPARED__": esc(c["prepared"]),
        "__PRACTICE__": esc(c["practice"]),
        "__TARGET__": f'{sf(c["target_min_sf"])} to {sf(c["target_max_sf"])} SF',
        "__SEARCHED__": f'{sf(c["searched_min_sf"])} to {sf(c["searched_max_sf"])} SF',
        "__AREA__": esc(c["area"]),
        "__STRUCTURE__": esc(c["structure"]),
        "__N_TOTAL__": str(len(props)),
        "__N_TOUR__": str(len(tour)),
        "__N_LOOK__": str(len(look)),
        "__N_OUT__": str(len(out)),
        "__RATE_SPAN__": rate_span,
        "__STANDFIRST__": standfirst,
        "__TOUR_CARDS__": ("".join(build_tour_card(p, photos, minis) for p in tour) or empty_state(
            "Nothing clears the bar yet",
            "Every available space in this size range has a disqualifying problem right now. That is a real "
            "finding rather than a gap in the search, and it usually means the right move is to wait for the "
            "next listing or approach an owner who is not marketing. Joe will explain which.")),
        "__LOOK_CARDS__": ("".join(build_tour_card(p, photos, minis) for p in look) or empty_state(
            "No borderline options",
            "Nothing in this search sits on the fence. Every space either fits or clearly does not.")),
        "__OUT_ROWS__": ("".join(build_out_row(p) for p in out) or empty_state(
            "Nothing was ruled out",
            "Every available space in your size range is still in play, which is unusual and worth talking about.")),
        "__SOURCES__": esc(", ".join(c["sources_run"])),
        "__PENDING__": pending,
        "__H1_A__": c["area"].split(" through ")[0],
    }
    for k, v in repl.items():
        html = html.replace(k, v)
    for k, path in ASSETS.items():
        html = html.replace(f"__{k}__", b64(path))

    leftover = re.findall(r"__[A-Z_]+__", html)
    assert not leftover, f"unreplaced tokens: {set(leftover)}"

    slug = re.sub(r"[^a-z0-9]+", "-", c["name"].split(",")[0].split()[-1].lower())
    date = re.sub(r"[^0-9a-zA-Z]+", "-", c["prepared"]).strip("-")
    outp = os.path.join(folder, f"{slug}-space-search-{date}.html")
    tombstone = None
    if os.path.exists(outp):
        tombstone = os.path.join(folder, "_TO_DELETE", os.path.basename(outp))
        if not a.supersede_existing:
            print("STOP: replacement refused: existing client artifact requires "
                  "--supersede-existing and --loop-ref", file=sys.stderr)
            return 2
    try:
        write_artifact_atomically(outp, html, tombstone_path=tombstone,
                                  loop_ref=a.loop_ref)
    except (AssetControlRefusal, OSError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2

    print(f"wrote {outp}")
    print(f"  {len(props)} properties: {len(tour)} tour, {len(look)} look, {len(out)} ruled out")
    print(f"  {len(photos)} photos inlined | {os.path.getsize(outp)/1024/1024:.2f} MB")


if __name__ == "__main__":
    raise SystemExit(main())

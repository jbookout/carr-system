# build-front-door.py — self-contained generator for The Front Door (DNA/Team/front-door.html)
# Regenerate: python3 build-front-door.py  (writes front-door.html beside it). Edit the GROUPS list to change tiles.
# Mirrors the lead board's build-lead-board.py pattern so the Front Door is regenerable, not hand-edited.

import urllib.parse, html, json, re, os, sys

# CARR_VAULT override (orchestrator-lane corrective, 2026-07-25): was a bare constant,
# which structurally blocked any non-Joe machine (Dell's clone) from running this.
FOLDER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")
REMINDER = "Reminder before we start: this session needs the CARR AI folder connected to read and write (this launcher link should connect it automatically; if it is not connected, connect it before sending)."
DOCTRINE = "Run this as a guided flow per DNA/Team/guided-entry-doctrine.md: tell me up front how many questions it will be, ask one at a time with multiple choice wherever possible and fill-in-the-blank otherwise, and if this creates a record, capture the core first, save it, then offer to fill in the rest later so a couple of minutes is enough (the daily heartbeat or Monday brief will chase the missing pieces)."

GROUPS = [
("Add to the system", [
 ("New Prospect","Lead or client, one flow",
  "New prospect intake for the CARR system. This is someone entering our pipeline, either a cold lead or an active client. Read DNA/Leads/lead-system.md (intake SOPs, the claim protocol, and the 'ownership is asked, never assumed' rule) and DNA/Clients/clients-active.md first. Then interview me ONE QUESTION AT A TIME: multiple-choice wherever you can (segment, deal type, owner, source type, stage) and fill-in-the-blank for names and details, never more than one thing at once. If whose relationship this is isn't obvious, ask me before stamping an owner. When you have enough, allocate the next L-ID (and a C-ID plus detail file if they are an active prospect, not just a cold name), cross-check the roster and registry for duplicates, cite the lead source, stamp every write with the DNA write-verify sandwich, then tell me exactly what you wrote and where."),
 ("New Vendor","Referral partner",
  "New vendor intake for the CARR system. Read DNA/Network/README.md (the five-layer vendor stack and the Tabled system) and DNA/Team/dna-protocol.md first. Interview me ONE QUESTION AT A TIME, multiple-choice wherever possible (category, territory or state, stage, owner) and fill-in-the-blank otherwise, never all at once. Dedup-check the name against vendors.xlsx and the client roster before creating anything. Allocate the next V-ID as max+1 on the live sheet, write the row with stamps and the write-verify sandwich, then tell me exactly what you wrote and where."),
 ("Log My Day","Debrief that routes everything",
  "Debrief me and log my day for the CARR system. Read the network-debrief flow and DNA/Leads/lead-system.md. Walk me through it one or two questions at a time in my usual debrief style: who I saw, new people, intros, deal moves, market observations, objections heard, events, commitments. Use multiple-choice prompts where you can. Route every item to the right file (registry, vendors, clients, content substance bank, team board, idea bank), applying the claim, ID, source-citation, and ownership rules, then tell me exactly what changed so I can catch any errors."),
 ("New Idea","Park it before it's lost",
  "I want to capture an idea for the CARR system. Ask me for the idea in my own words and any detail I want to add (what it is, why it's good, anything to remember), one question at a time. File it per my idea-inbox convention, confirm the filename back to me, and if it is really an action rather than an idea, tell me and offer to put it in open-loops instead."),
 ("Log an Event","Happy hour, panel, lunch-and-learn",
  "Log a joint event for the CARR system. Read DNA/Network/deals.md (the Joint Events log) and the intro-politics rules. Interview me one question at a time, multiple-choice where you can: what kind of event, co-hosts, date, cost, who we met, any leads it produced. Run the guest-list politics check if it is a planned event. Write it with ROI tracking, link any leads, then tell me what you logged."),
 ("Import a List","CSV of contacts",
  "I have a contact list to import into the CARR system. Tell me to attach the file, then ask me one line of context (whose list it is and what it is). Read tutorial 19 in DNA/Team/abilities.md and the import precedent. Dedup against the registry, roster, and vendors, enrich from email signals, route doctors to the registry with the newsletter drip and vendors to the network, keep every flag visible, and summarize what will land where before anything is final."),
 ("File a Comp","Real deal terms only",
  "File a comp for the CARR system. Read the DNA/Deal Management comps conventions (real terms only, no estimates). Interview me one question at a time, multiple-choice where possible (deal type, market, property type, lease or purchase) and fill-in-the-blank for the terms. Write the comp row, then tell me what you filed."),
 ("Teach a Rule","Intro politics, permanent",
  "I want to teach the system an intro-politics rule that is permanent and binds both brains. Read DNA/Network/introduction-rules.md. Ask me the rule in one or two questions (who cannot meet whom, and why), write it as law, and confirm it back to me."),
]),
("Deals & matchmaking", [
 ("Vendor to Vendor Match","Partners who should know each other",
  "Vendor-to-vendor matchmaker for the CARR network. Read DNA/Network/engine.md and DNA/Network/introduction-rules.md. Ask me one question at a time: are we finding good partners for one specific vendor, or scanning the whole network for ripe vendor-to-vendor intros. Propose compatible pairings (complementary categories, shared market, real mutual benefit), run the intro-politics hard-block check on every pair and never pair same-category competitors, rank by relationship stage and delivery tier, and show me each pair with the reason and a suggested warm-intro line."),
 ("Vendor to Client Match","Right vendor for a client",
  "Vendor-to-client matchmaker for CARR. Read DNA/Network/engine.md (delivery-weighted matching), DNA/Deal Management/playbooks/practice-os-playbooks.md, and the Scoreboard in DNA/Network/deals.md. Ask me one question at a time: am I matching one client to the right vendor, or one vendor to clients who could use them. For a client, read their file and vertical and pull the current best-fit vendor by Scoreboard tier and state fit; for a vendor, scan active clients for a genuine need. Run the intro-politics check, then give me the match with the reason and a warm-intro line."),
 ("Build a Tour Packet","Client-ready property tour",
  "Build a property tour packet for a CARR client. Read DNA/Research/costar-how-to.md (the pre-tour and tour-packet sections) and DNA/Deal Management/deal-room-kit.md. Ask me one question at a time: which client, the market and criteria (SF, budget, property type, lease or purchase), and which specific properties or listings to include. Assemble the packet the CARR way with client-facing rates shown as NA, produce it as a Word document, then tell me what to double-check before I send it."),
 ("Deal Comparison","Lease, purchase, or multi-option, side by side",
  "Build a client deal comparison for CARR using our company templates. First ask me ONE question: which comparison type, with these choices: 5-vs-10-year lease, single 10-year lease, multiple space options (LOIs) side by side, purchase vs lease, multiple purchase options, or tell me it is a different comparison and use the closest template. Then read DNA/Deal Management/fill-engine/fill-it-in-workflow.md, deal-analysis-toolkit.md, and purchase-vs-lease-fill-guide.md, and use the matching template (LeaseComparison-5vs10Year, LeaseComparison-10Year, LOI-Grid for multiple spaces, PurchaseVsLeaseComparison, or PurchaseComparison). Tell me to drop each LOI or set of terms into the chat now (paste or attach), and read any rent schedule from the RENDERED page image, not a text extraction. Ask me one question at a time for anything missing (client, addresses, the factor they weight most), apply CARR's conservative-direction assumptions, run the fill-engine, then reconcile every headline number against its source cell as a fresh-eyes pass before it is client-facing. Produce the finished Excel (and the branded one-pager for purchase vs lease), keep the estimates and consult-your-CPA disclaimer, and never include the internal commission calculator. Then tell me what you built and what to double-check."),
 ("Draft an LOI","Letter of intent, ready to send",
  "Draft a letter of intent for a CARR client. Read DNA/Deal Management/playbooks/negotiation.md and DNA/Deal Management/legal-considerations-lease-review.md, and the client's file in DNA/Clients/prospects/. Ask me one question at a time: which client and property, lease or purchase, and the key terms we are proposing (rate, term, TI, free rent, options, or purchase price and contingencies). Draft the LOI in CARR's voice per DNA/writing-rules.md, flag anything that should be negotiated or protected, and give it to me as a Word document to review. This is a draft for me to send, never sent on my behalf."),
 ("Renewal Workup","What a represented renewal saves",
  "Run a lease renewal workup for a CARR client. Read DNA/Deal Management/playbooks/renewals.md and DNA/Leads/lease-term-estimator.md, and use the LeaseComparison-5vs10Year template. Ask me one question at a time: which client, their current space and rent, when the lease expires, and whether they would consider relocating as leverage. Estimate the renewal position with CARR's conservative assumptions, quantify what a represented renewal saves versus renewing blind (free rent, escalation, TI), produce the comparison workbook plus a plain-English read, keep the estimates and consult-your-CPA disclaimer, and tell me the negotiation angle and what to double-check."),
 ("Deal One-Pager","Branded purchase-vs-lease summary",
  "Build the branded purchase-vs-lease one-pager for a CARR client. Read DNA/Deal Management/fill-engine/fill-it-in-workflow.md. If a filled purchase-vs-lease workbook already exists for this client, use it; if not, ask me one question at a time for the numbers and run the fill-engine first. Generate the CARR-branded one-pager (navy and orange, disclaimer intact), reconcile every headline number against its source cell as a fresh-eyes pass before it is client-facing, and give me the finished Word one-pager plus a note on what to verify."),
]),
("Get work done", [
 ("Write a Post","Your voice + a graphic",
  "Write me a social post in Joe's voice. Ask me one question at a time: what it is about, any platform preference, any angle I want. Use the write-content skill and the substance bank, and show me the draft and the graphic for approval before anything is published."),
 ("Audit Writing","Review only, no rewrite",
  "Audit a piece of writing for CARR, review only. Ask me to paste the text, then critique it for credibility, voice, and AI-tells against DNA/writing-rules.md. Do not rewrite it unless I ask you to."),
 ("Research a Market","Sourced pull",
  "Do a market research pull for the CARR system. Ask me one question at a time: which market or area, and the scope. Read the DNA/Research conventions, run the sourced pull and verify every claim to a source, save it to DNA/Research, feed the best data points to the content substance bank, and give me the prospect hooks."),
 ("Who Do We Know","Warmest path to anyone",
  "Find the warmest path through our network to someone I don't know yet. Read DNA/Network/engine.md (the intro-graph queries) and introduction-rules.md. Ask me who I am trying to reach (name, company, or role), then show me the warmest routes with the politics check applied."),
 ("Hand to Dell","Partner handoff",
  "I want to hand something off to Dell. Read DNA/Team/team-loops.md. Ask me what the ask is, one question at a time, write it as a stamped row on the team board aimed at Dell, and confirm it back to me."),
 ("Prep for a Meeting","Everything we know, one page",
  "Prep me for a meeting with a prospect or client. Ask me one question at a time: who I am meeting and when, and what the meeting is about. Then pull everything the system knows about them, their DNA/Clients/prospects/ file, their registry row and roster record, any network ties (DNA/Network/engine.md), and any market notes, into one tight brief: who they are, where the relationship stands, the open next step, the strongest angle for this conversation, and two or three smart questions to ask. Flag anything I should not raise. Keep it to one page I can read on my phone."),
 ("Draft a Follow-Up","Next touch, in your voice",
  "Draft the next follow-up touch for a lead. Read DNA/templates.md (the touch calendar and voice rules) and the lead's DNA/Clients/prospects/ file. Ask me one question at a time: which lead, and whether they have replied since the last touch. First check the registry and any reply-forwards for where they are in the sequence, then draft the correct next touch in Joe's voice, aware of every prior touch, following the handover-channel format, and hand it to me to send. Never send it on my behalf."),
]),
]

def link(prompt):
    q=REMINDER+" "+prompt+" "+DOCTRINE
    return "claude://cowork/new?q="+urllib.parse.quote(q,safe="")+"&folder="+urllib.parse.quote(FOLDER,safe="")
def fullprompt(prompt):
    return REMINDER+" "+prompt+" "+DOCTRINE
def tiles_html(btns):
    out=[]
    for lbl,sub,p in btns:
        href=link(p); pj=json.dumps(fullprompt(p))
        assert len(urllib.parse.quote(fullprompt(p),safe=""))<14000, lbl
        out.append(f'''<div class="tile"><a class="tile-main" target="_blank" rel="noopener" href="{html.escape(href)}"><span class="tile-label">{html.escape(lbl)}</span><span class="tile-sub">{html.escape(sub)}</span></a><button class="tile-copy" data-prompt={html.escape(pj,quote=True)} title="Copy the prompt">copy</button></div>''')
    return ''.join(out)
def section(name,btns,cls="group"):
    return f'''<section class="{cls}"><div class="group-head"><span class="bar"></span>{html.escape(name)}</div><div class="grid">{tiles_html(btns)}</div></section>'''

lookup={lbl:(sub,p) for _,bs in GROUPS for (lbl,sub,p) in bs}
EVERYDAY=[(lbl,lookup[lbl][0],lookup[lbl][1]) for lbl in ["Log My Day","New Prospect","Draft a Follow-Up","Prep for a Meeting","New Vendor"]]
BOARDS=[
 ("Open the Dashboard","Your whole system, live","Open the CARR dashboard and rebuild it from current data, then show it to me."),
 ("Open the Lead Board","This week, hot, segments","Open the lead board and show me this week and the hot leads."),
 ("Open the Deal Room","Signed, on deck, hot","Refresh The Deal Room and show me signed deals, on deck, and hot leads."),
]
everyday_html=section("Start here, your everyday few",EVERYDAY,"group everyday")
boards_html=section("See the boards",BOARDS)
more_html=''.join(section(g,b) for g,b in GROUPS)
nbtn=len(EVERYDAY)+len(BOARDS)+sum(len(b) for _,b in GROUPS)

HTML=f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CARR | The Front Door</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Montserrat:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  /* CARR design tokens (law 11: tokens before layout). Consume these; do not hardcode new colors/fonts. */
  :root{{
    --navy:#002F6C;--navy-deep:#00224D;--orange:#F57F29;
    --color-text:#ffffff;--color-muted:rgba(255,255,255,.62);--focus:#FF9D4D;
    --color-success:#3FB68B;--color-warning:#F5B841;--color-danger:#E5533D;
    --head:'Oswald','Archivo Narrow',sans-serif;--body:'Montserrat','Helvetica Neue',Arial,sans-serif;
    --space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-6:24px;--space-8:32px;
    --radius-sm:7px;--radius-md:10px;--radius-lg:14px;
    --shadow-sm:0 2px 8px rgba(0,15,40,.25);--shadow-md:0 12px 30px rgba(0,15,40,.35);--shadow-lg:0 16px 34px rgba(0,15,40,.4);
    --duration-fast:.12s;--duration-base:.2s;--ease-out:cubic-bezier(.2,.7,.3,1);
  }}
  *{{margin:0;padding:0;box-sizing:border-box;-webkit-font-smoothing:antialiased;}}
  a:focus-visible,button:focus-visible,summary:focus-visible{{outline:3px solid var(--focus);outline-offset:3px;border-radius:var(--radius-sm);}}
  :focus:not(:focus-visible){{outline:none;}}
  html,body{{min-height:100%;}}
  body{{padding:36px 20px 64px;background:radial-gradient(120% 90% at 82% 0%,#063a82 0%,var(--navy) 46%,var(--navy-deep) 100%);background-attachment:fixed;font-family:var(--body);color:#fff;}}
  .wrap{{max-width:900px;margin:0 auto;}}
  .head{{text-align:center;margin-bottom:8px;}}
  .wordmark{{font-family:var(--head);font-weight:700;letter-spacing:.16em;font-size:20px;text-transform:uppercase;}}
  .wordmark .b{{display:inline-block;width:30px;height:4px;background:var(--orange);border-radius:2px;vertical-align:middle;margin-left:9px;transform:translateY(-3px);}}
  h1{{font-family:var(--head);font-weight:600;font-size:46px;line-height:1.02;margin-top:14px;letter-spacing:-.01em;}}
  .lede{{margin:14px auto 4px;max-width:600px;font-size:15px;line-height:1.5;color:rgba(255,255,255,.72);}}
  .group{{margin-top:32px;}}
  .group.everyday{{margin-top:30px;padding:20px;border-radius:16px;background:linear-gradient(160deg,rgba(245,127,41,.10),rgba(245,127,41,.03));border:1px solid rgba(245,127,41,.28);}}
  .group-head{{font-family:var(--head);font-weight:600;text-transform:uppercase;letter-spacing:.14em;font-size:15px;color:var(--orange);display:flex;align-items:center;margin-bottom:16px;}}
  .group-head .bar{{display:inline-block;width:26px;height:3px;background:var(--orange);border-radius:2px;margin-right:12px;}}
  .grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;}}
  @media(max-width:640px){{.grid{{grid-template-columns:1fr;}}h1{{font-size:38px;}}}}
  .tile{{position:relative;border-radius:14px;background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(255,255,255,.025));border:1px solid rgba(255,255,255,.10);transition:transform .12s ease,box-shadow .12s ease,border-color .12s ease;}}
  .tile:hover{{transform:translateY(-2px);border-color:rgba(245,127,41,.55);box-shadow:0 16px 34px rgba(0,15,40,.4);}}
  .tile-main{{display:block;padding:20px 20px 18px;text-decoration:none;color:#fff;}}
  .tile-label{{display:block;font-family:var(--head);font-weight:600;font-size:21px;letter-spacing:.01em;}}
  .tile-sub{{display:block;margin-top:4px;font-size:13px;color:rgba(255,255,255,.6);}}
  .tile-copy{{position:absolute;top:12px;right:12px;background:transparent;border:1px solid rgba(255,255,255,.22);color:rgba(255,255,255,.66);font-family:var(--head);font-weight:500;text-transform:uppercase;letter-spacing:.08em;font-size:11px;padding:4px 9px;border-radius:7px;cursor:pointer;transition:all .12s ease;}}
  .tile-copy:hover{{border-color:var(--orange);color:var(--orange);}}
  .tile-copy.done{{border-color:var(--orange);color:var(--orange);background:rgba(245,127,41,.12);}}
  details.more{{margin-top:34px;}}
  details.more>summary{{list-style:none;cursor:pointer;font-family:var(--head);font-weight:600;text-transform:uppercase;letter-spacing:.12em;font-size:14px;color:var(--orange);display:inline-flex;align-items:center;gap:12px;padding:13px 22px;border:1px solid rgba(245,127,41,.4);border-radius:10px;transition:all .12s ease;}}
  details.more>summary:hover{{background:rgba(245,127,41,.10);}}
  details.more>summary::-webkit-details-marker{{display:none;}}
  details.more>summary::after{{content:"+";font-size:20px;line-height:1;}}
  details.more[open]>summary::after{{content:"\\2013";}}
  .foot{{margin-top:40px;font-size:13px;line-height:1.6;color:rgba(255,255,255,.55);text-align:center;max-width:640px;margin-left:auto;margin-right:auto;}}
  .foot b{{color:rgba(255,255,255,.8);font-weight:600;}}
  #toast{{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(20px);opacity:0;pointer-events:none;background:var(--orange);color:var(--navy-deep);font-family:var(--head);font-weight:600;letter-spacing:.02em;font-size:14px;padding:12px 22px;border-radius:10px;box-shadow:0 12px 30px rgba(245,127,41,.4);transition:all .2s ease;}}
  #toast.show{{opacity:1;transform:translateX(-50%) translateY(0);}}
  @media(prefers-reduced-motion:reduce){{
    *,*::before,*::after{{transition-duration:.001ms !important;animation-duration:.001ms !important;animation-iteration-count:1 !important;scroll-behavior:auto !important;}}
    .tile:hover{{transform:none;}}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="head">
      <div class="wordmark">CARR<span class="b"></span></div>
      <h1>The Front Door</h1>
      <p class="lede">Tap what you need. A new session opens pointed at your CARR folder with the task written in, you review, hit send, and answer one question at a time. Start with your everyday few. The full set is under All actions.</p>
    </div>
    {everyday_html}
    <details class="more"><summary>All actions</summary>{more_html}</details>
    <p class="foot">Every tile opens a fresh session with your CARR folder linked and the prompt loaded, and nothing sends on its own, so you always review first. Each session tells you in plain words what it changed, and you can say <b>undo</b> to reverse the last thing it did. If a tile does not open the app, tap <b>copy</b> and paste into a new session.</p>
  </div>
  <div id="toast">Prompt copied</div>
<script>
  var toast=document.getElementById('toast');
  function flash(){{toast.classList.add('show');setTimeout(function(){{toast.classList.remove('show');}},1400);}}
  document.querySelectorAll('.tile-copy').forEach(function(btn){{
    btn.addEventListener('click',function(e){{
      e.preventDefault();
      var text=btn.getAttribute('data-prompt');
      var ok=function(){{btn.classList.add('done');btn.textContent='copied';flash();setTimeout(function(){{btn.classList.remove('done');btn.textContent='copy';}},1600);}};
      if(navigator.clipboard&&navigator.clipboard.writeText){{navigator.clipboard.writeText(text).then(ok).catch(function(){{fallback(text,ok);}});}}
      else{{fallback(text,ok);}}
    }});
  }});
  document.querySelectorAll('.tile-main').forEach(function(a){{a.addEventListener('click',function(){{try{{var q=(a.getAttribute('href').split('q=')[1]||'').split('&folder=')[0];var text=decodeURIComponent(q);var ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();document.execCommand('copy');document.body.removeChild(ta);toast.textContent='Prompt copied. Paste into the new chat (Cmd+V)';flash();setTimeout(function(){{toast.textContent='Prompt copied';}},2800);}}catch(e){{}}}});}});
  function fallback(text,ok){{var ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();try{{document.execCommand('copy');ok();}}catch(err){{}}document.body.removeChild(ta);}}
</script>
</body>
</html>'''

import os as _os
open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),'front-door.html'),'w',encoding='utf-8').write(HTML)
visible=' '.join(re.findall(r'>([^<>]+)<',HTML))
assert '—' not in visible, "em-dash in visible copy"
low=visible.lower()
hits=[b for b in ['delve','seamless','unlock','elevate','leverage','robust','tapestry','realm'] if b in low]
hrefs=[html.unescape(x) for x in re.findall(r'href="(claude://[^"]+)"',HTML)]
allfolder=all('&folder=' in x for x in hrefs)
allrem=all(urllib.parse.parse_qs(urllib.parse.urlparse(x.replace('claude://','https://')).query)['q'][0].startswith("Reminder") for x in hrefs)
print("total tiles:",nbtn,"| links:",len(hrefs),"| AI-tell:",hits,"| folder all:",allfolder,"| reminder all:",allrem)
print("has everyday:","everyday" in HTML,"| has details:","<details" in HTML,"| boards:","See the boards" in HTML)

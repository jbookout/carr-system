# Introduction Rules — the taught compatibility layer

*Created July 6, 2026 from Joe's direct instruction. This file is how Joe & Dell TEACH Claude which vendors can meet — the engine (engine.md) must check every intro candidate AND every event guest list against these rules before suggesting anything. This is a LEARNING file: every intro Joe approves, rejects, or corrects becomes a dated rule here, same session (correction→rule). The vendor world is highly political — when a pairing isn't covered below, ask, don't guess.*

*Data source (July 7, 2026): vendor records now live in `DNA/Network/vendors.xlsx` (migrated from vendors.md). The fields these rules reference — Category, Vertical, Territory/State, Stage, Links, Rivalry Group — are columns there. The rules below are unchanged.*

> **Companion file — different axis:** `vendor-intro-timeline.md` covers vendor → **CLIENT** intros (which vendor category to introduce to a client at each deal stage). THIS file is vendor ↔ vendor (which of Dell's vendors can meet each other). Both pull from `vendors.xlsx`.

## Hard blocks (never introduce, never co-invite to the same event)

1. **Same category = competitors by default.** Never intro lender↔lender, CPA↔CPA, supply rep↔supply rep, GC↔GC, architect↔architect, etc. This holds even across different VERTICALS of the same category — two lenders are two lenders (a veterinary-only lender and a general healthcare lender still compete at the relationship level). Whitelisting a same-category pair requires Joe/Dell explicitly.
2. **Named rivalries (extra caution beyond the category rule):**
   - **Henry Schein ↔ Patterson** — direct competitors, highly political. Never intro, never same guest list, and be deliberate about not name-dropping one's activity to the other. Both are referral sources (see deals.md: HS Broker, Patterson, Patterson Vet referrals) — this rivalry is live revenue on both sides, handle accordingly.
   - *(Add rivalries here as they surface — banks with competing healthcare divisions, dueling practice brokers, etc.)*
3. **Avoid-stage vendors:** anyone at Stage "Avoid" gets no intros in either direction.

## Confirmed good pairs (Joe-taught)

- **Contractor ↔ Architect** — *sometimes* (Joe's word). Conditions that make it good: a live or likely shared project, complementary territory, and neither already has an aligned partner of the other's type they're loyal to. Check the Links field for existing loyalties before suggesting.

## Default-plausible pairs — UNCONFIRMED (suggest only with the caveat "confirm this pairing logic," and move to Confirmed once Joe/Dell rules on it)

- Lender ↔ CPA (financing + financials on the same startup)
- CPA ↔ Attorney (entity formation / transaction work)
- Practice broker ↔ Lender (acquisitions need financing)
- Supply/equipment rep ↔ GC or Architect (equipment specs drive buildout design)
- Demographics/Marketing ↔ Developer (site selection data)
- Banker ↔ SBDC consultant (startup pipeline both directions)

## Vertical awareness (added with the `Vertical` field, July 6, 2026)

Vendors carry a `Vertical` within their category — e.g., Bank of America has a **veterinary-specific lender** and a separate lender for **all other healthcare**. Verticals matter two ways:
- **Matching:** a veterinary startup gets the vet-vertical lender, not the general one — vertical fit outranks relationship stage when both are decent.
- **NOT an exception to competition:** different verticals within a category do not make two vendors introducible (see hard block #1).
- **State splits exist too (Joe, Jul 6, 2026):** some vendors carve territory by STATE within the same vertical — BofA vet lending: Michael Diehl = Alabama, Carlos Nieto = Florida. Matching rule: an Alabama vet client gets Diehl, a Florida vet client gets Nieto — sending the wrong-state banker wastes the intro and can step on the right banker's turf. Check Terr/Vertical together when matching lenders.

## Cross-category service overlaps (logged Jul 7, 2026 from CARR "Vendor Info by Industry" — see `vendor-by-industry-reference.md`)

Some vendors quietly compete ACROSS category lines because their services overlap. Softer than the same-category hard block — these are defaults, not blocks. Two uses: (a) don't intro or co-invite two vendors who actually compete on a service even though their categories differ, and (b) engagement awareness — a few overlap with CARR itself. Confirm the specific pairing before acting.

- **Competes with CARR (handle with care):** some **attorneys**, some **consultants**, and some **practice brokers** try to handle the real-estate brokerage side themselves. Don't assume they'll welcome a broker — lead with "we take the RE work off your plate; you keep the client and your fee."
- **Consultants** overlap with Architects, CPAs, Attorneys, and Financial Planners.
- **Practice brokers** overlap with CPAs, Attorneys, Consultants, and distributors who broker practices.
- **CPAs** overlap with Financial Planners, Consultants, and (occasionally) IT.
- **Financial planners** overlap with Insurance reps and some CPAs.
- **Architects** overlap with design/build GCs and consultants/equipment reps that provide design services.
- **IT companies** overlap with distributors selling computer equipment and CPAs/vendors providing IT.
- **General contractors** overlap with developers/landlords who self-perform construction.
- **Equipment reps'** in-house financing overlaps mildly with **Lenders** (most reps don't care how it's financed).
- **Insurance reps** compete with other insurance reps and some financial planners.

## Delivery-weighted matching (added Jul 13, 2026 — the Practice OS steering rule)

When picking WHICH vendor to plug into a client's need, after the hard blocks and vertical/state fit, prefer the higher **delivery tier** (deals.md Vendor Delivery Scoreboard):
- Delivery tier is a tiebreaker that sits BELOW hard blocks and vertical/state fit, but ABOVE raw relationship stage — a vendor you're close to who burns clients is worse than a Solid one you're still building.
- **Never route a client through a ⛔ Benched vendor** without Joe's explicit override, and log the reason when he overrides.
- A ⚠ Watch vendor is fine to use — that's how they earn a tier — just don't stack a fragile, high-stakes client on an unproven one when a Trusted option fits.
- The point: protect the client experience AND CARR's name. A bad intro costs more than a missed one.

## Event guest-list rule

Before any joint event (happy hour, speaker panel, lunch-and-learn), run the invite list through the hard blocks: no same-category competitors on one list unless Joe explicitly decides otherwise for that event (big mixed events can be exceptions — his call, never the default). Log every event in deals.md's Joint Events Log.

## How the teaching loop works

1. Engine proposes intros (engine.md Section 1) already filtered through this file.
2. Joe/Dell approve, reject, or correct. A rejection gets ONE follow-up question: "what's the rule I should learn?"
3. The answer lands here, dated, under Confirmed/Blocked. Unconfirmed defaults migrate up or get deleted as they're ruled on.
4. Dell's onboarding (DNA/Team/twin-system-playbook.md Phase 3) includes a pairing-rules walk — his politics knowledge is deeper in some categories; capture it the same way.

## Learned rules log (dated — newest first)

- 2026-07-07 — Logged from CARR corporate "Vendor Info by Industry" (Joe-provided PDF): cross-category service overlaps added (consultant / practice-broker / CPA / FP / architect / IT / GC / insurance overlaps; some attorneys, consultants, and practice brokers self-broker RE = compete with CARR). Full per-category approach reference: `vendor-by-industry-reference.md`; source PDF `CARR_VendorInfoByIndustry_source.pdf`.
- 2026-07-06 (later) — Joe taught: BofA vet lending splits by state (Diehl=AL, Nieto=FL) → state-aware lender matching added to Vertical awareness.
- 2026-07-06 — Seeded: same-category hard block; Schein↔Patterson rivalry; contractor↔architect conditional; vertical awareness (BofA vet vs. general healthcare lender example). Source: Joe, verbally, this date.

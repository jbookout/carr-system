---
name: surface-review
description: >-
  Reviews a CARR surface against CARR's own UX doctrine and returns a
  severity-ranked verdict. Use it before any board, Deal Room, command center,
  client packet, artifact, or web page is called done, and when reviewing a
  change to one. Trigger on "review this surface", "is this done", "check this
  against doctrine", "does this meet the design rules", "audit this page",
  "surface review". It checks the doctrine's own laws and constraints, never
  generic web-minimalism best practice, and it measures rendered output rather
  than reading CSS. Review only: it reports and ranks, it does not rewrite.
  Not for prose quality (that is writing-audit) and not for correctness, tests
  or security (that is the project's code review).
disable-model-invocation: true
---

# Surface review

CARR's design rules each end in an audit signal saying a non-compliant surface
must be named out loud in that session's report. Until this skill existed,
nothing executed that: it relied on a session remembering four rules scattered
across a store it may not have read. This is the mechanism. It runs when a
human asks for it and never starts on its own.

## The ruleset is CARR's doctrine, not the open web

**Read the doctrine first, every run.** Call `read-doctrine` with document
`ux-doctrine` and `standing-context` for the active rules. That document is the
ruleset. Nothing in this file restates it, because a copy would drift the day
the doctrine changed.

**The trap this skill exists to avoid.** Generic interface-review instincts are
tuned to a light, minimal, low-motion web baseline. CARR is deliberately the
opposite: near-black cinematic grounds, glass, heavy ambient motion, mandatory
pulse. An audit run on generic instincts will flag CARR's own decisions as
faults and quietly walk the system back to the bare grey surface the doctrine
explicitly forbids. So:

- Richness, depth, motion and visual density are NEVER findings on their own.
- "Too much motion", "distracting", "could be simpler", "cleaner without it"
  are not findings. Under-building is CARR's actual failure mode.
- A surface that is calm-because-bare is a HIGH finding, not a pass. Calm is
  defined as a place a partner enjoys being in.

If a check would produce a finding that contradicts the doctrine, the check is
wrong. Say so and drop it.

## Scope: a surface, or a change to one

**Whole surface.** Review the page as it stands.

**A change.** Follow the doctrine's change-review section. In short: the changed
file is evidence, not the subject, so review the surfaces it renders in and
expand two hops for shared tokens; read the DELETED side of the diff for a
focus ring, keyboard path, reduced-motion guard, empty state or status cue that
vanished; and give every finding one status of introduced, regression, or
pre-existing. Never check anything out. If there is no change to review, say so
and ask rather than reviewing whatever landed last.

## Verify by measuring, never by reading

A claim about how a surface looks or behaves is worth nothing unless the
surface was rendered and measured. Serve it, open it, and get numbers.

- **Declared font size is not rendered size.** An SVG label declared at 11px
  inside a shrunk diagram can render at 4px. Measure real glyphs with
  `getBoundingClientRect()`.
- **Reduced motion cannot be read off the CSS.** Force the fallback by
  injecting the media block's declarations unconditionally, then measure that
  nothing is stuck invisible: entrance elements at opacity 1, SVG draw paths at
  dashoffset 0, bars at full height, pulses still lit and colour-distinct.
- **A suspended tab hides animation, it does not disprove it.** Scrub CSS
  animations through `document.getAnimations()` and `currentTime` to prove they
  produce different values at different points. Scrub to `endTime`, not
  `duration`, when the animation carries a delay.
- **Overflow is found by walking, not by looking.** Report every element whose
  right edge exceeds the viewport, excluding intentional scroll containers.
- Anything not actually measured is labelled **not verified**. That label is
  cheap and honest; a confident report that never looked is neither.

## What to check

Derive the checks from the doctrine each run. These are the recurring ones.

**Automatic HIGH, no judgement needed:**

- Pure white or a near-white as paper or card on a light on-screen surface.
  Print and ink-save modes are exempt; a document's on-screen default is not.
- Orange carrying data or status meaning rather than chrome, focus and CTA.
- A missing or removed visible focus state, or focus rendered unreachable.
- Motion that ignores `prefers-reduced-motion`, or a fallback that leaves
  content invisible.
- Status riding on colour alone, with no shape, pattern or text carrying it.
- An interactive control below the 44px tap target.
- Content clipped rather than scrollable at narrow width.
- A structured surface with no visual of its structure.
- A workstation surface with no ambient life.
- Pulse rates that do not follow the doctrine's scale, or that are equal to
  each other and therefore encode nothing.
- Display type not led by the brand display face, body not led by the brand
  body face.

**Check the numbers.** The doctrine's craft-values section carries checkable
figures; these are the ones that catch the most, and each is a grep or a
measurement rather than an opinion:

- Reduced-motion kill switch uses `0.01ms`, not `none` or `0`. Zero stops
  `animationend` and `transitionend` from firing and hangs anything waiting on
  them, for reduced-motion users only.
- `transition: all` anywhere. Name the properties.
- `will-change` on anything but transform, opacity or filter, or applied
  before real stutter was observed.
- Press feedback at `scale(0.96)`; below 0.95 reads as exaggerated.
- A toggle animated with keyframes rather than a transition: it snaps or
  restarts when clicked mid-flight.
- Text below 18px at a weight under 400, or weights 100 to 300 used below 28px.
- Text wrapping to three or more lines with line-height under 1.4.
- Long-form measure beyond 75 characters.
- An input rendering below 16px, which makes Safari zoom the page on focus.
- A changing number without `tabular-nums`, which makes the layout jump.
- Gap between groups less than twice the gap within a group.
- Nested panels breaking `outer radius = inner radius + padding`.
- `tabindex` with a positive value.
- A live region inserted along with its text rather than already present.
- `.sr-only` implemented at zero size instead of a 1x1px box.
- Breakpoints in px where the surface has scalable text.

**Judgement, ranked MEDIUM or LOW:**

- Complete interactive states: default, hover, active, focus-visible, disabled,
  loading, error, plus an authored empty state wherever a list can be empty. A
  new variant styled for some states and not others is the classic incomplete
  change and only shows up when the change is held to what it claimed to do.
- One card grammar reused rather than a new layout invented per surface.
- Spacing on the scale; container width one of the sanctioned widths.
- Elevation on the ramp, navy-tinted, luminance-stepped on dark.
- Copy authored, outcome-framed, no filler, no internal identifiers on screen.
- Diagram text legible at phone width, and reflowing rather than shrinking.
- Non-ASCII characters in a page authored for a publishing wrapper. Run
  `LC_ALL=C grep -c '[^\x00-\x7F]' <file>`; anything above zero risks mojibake.

## Review foundations before polish

Work the domains in this order, so a real failure is never buried under a taste
note: accessibility, then layout, then copy, then typography, then colour, then
visual detail. A contrast failure and a radius nit are not peers, and a report
that lists them in authoring order reads as though they were.

## Output

One report, findings first, most severe first. Every finding carries: severity,
what is wrong in plain words, where (file and line, or the element), the
evidence that it is real (a measured number, not an impression), and the fix.
Consolidate one finding per root cause rather than listing every instance.

**Name what you deliberately did not flag.** Every report lists one to five
things it inspected and passed on purpose, with the reason: the ambient pulse,
the long aurora cycle, the dense diagram, whatever a generic reviewer would
have called excessive. This is what separates a short review from a lazy one,
and it is the standing defence against the register being sanded down one
well-meant finding at a time.

Close with one verdict:

- **Block** — an automatic-HIGH finding is present.
- **Needs changes** — MEDIUM findings only.
- **Approve** — nothing above LOW, and say plainly what was measured and what
  was left unverified.

Then the line the doctrine's audit signals actually ask for: if the surface
fails the register, **name that out loud**, with what should have been there. A
review that returns nothing while a bare, static or text-only surface exists is
itself the visible failure, and reporting a clean pass in that situation is the
one outcome this skill exists to prevent.

## Boundaries

Report and rank. Do not rewrite the surface: the maker does not check their own
work, and a review that edits has stopped being a review. Prose quality belongs
to `writing-audit`. Correctness, tests and security belong to the project's own
code review. Name a concern outside this scope once, point at its owner, and
drop it.

import { LOOP_KINDS, UUID_RE } from "./verb-support.js";
import { ToolError } from "./tool-error.js";
import { versionGuard, withEnvelope, writeEvent } from "./versioned-write.js";

// ---------- loop helpers (one-writer Phase A, ORDER 31) ----------

// A LOOP NUMBER IS NOT AN IDENTIFIER, and pretending otherwise is how a verb
// writes to the wrong row. Measured in the source files 2026-07-31: '111' names
// two different items inside open-loops.md, '103'/'95'/'88'/'108' each name one
// hot item and a different backlog item, 'T34' names one row in team-loops' Open
// table and another in its Done table. So `number` narrows and `loop_id`
// identifies, and an ambiguous number REFUSES with the candidates listed —
// ORDER 1's needs_disambiguation behaviour, applied to a surface that is
// genuinely ambiguous rather than occasionally so.
// opts.anyStatus (default false, unchanged behaviour for every existing
// caller) lets amend-closed-loop resolve a CLOSED row by number too: the
// default number-lookup is scoped to status='open' because 0112 only
// guarantees uniqueness there, and every caller before amend-closed-loop only
// ever needed an open row anyway. amend-closed-loop needs the opposite row —
// closed — so it opts in rather than the default widening for everyone.
async function resolveLoop(client, args, opts = {}) {
  const anyStatus = opts.anyStatus === true;
  if (args.loop_id) {
    const r = await client.query(
      `select li.id, li.kind, li.number, li.status, li.marker, li.due_on,
              li.close_outcome, lb.block_key as section
         from loop_item li join loop_block lb on lb.id = li.block_id
        where li.id = $1`, [args.loop_id]);
    if (!r.rows.length) throw new ToolError({ error: "not_found", loop_id: args.loop_id });
    return r.rows[0];
  }
  if (!args.number)
    throw new ToolError({ error: "missing_loop_ref",
      hint: "pass loop_id, or number (plus kind when the number is shared across kinds)" });
  const r = await client.query(
    `select li.id, li.kind, li.number, li.status, li.marker, li.due_on,
            li.close_outcome, lb.block_key as section, lb.rel_path
       from loop_item li join loop_block lb on lb.id = li.block_id
      where li.number = $1 and (${anyStatus ? "true" : "li.status = 'open'"})
        and ($2::text is null or li.kind = $2)`, [args.number, args.kind || null]);
  if (!r.rows.length)
    throw new ToolError({ error: "loop_not_found", number: args.number, kind: args.kind || null,
      hint: anyStatus ? "no row, open or closed, carries this number — pass loop_id"
                       : "only OPEN loops resolve by number; a closed one needs its loop_id" });
  if (r.rows.length > 1)
    throw new ToolError({ error: "needs_disambiguation", number: args.number,
      candidates: r.rows.map(x => ({ loop_id: x.id, kind: x.kind, section: x.section,
                                     renders_into: x.rel_path })),
      hint: "this number names more than one live row — pass loop_id to act on one now, and " +
            "fix the collision itself with update-loop's `number` (plus renumber_reason). " +
            "Migration 0112 makes a new one impossible; anything left is pre-0112 history." });
  return r.rows[0];
}

// Next visible ref for a kind. Numeric part only, because that is the part the
// files increment; the prefix is the kind's own. Reads the MAX across every row
// including closed ones, so a number is never reused after a close.
async function nextLoopNumber(client, kind) {
  const prefix = kind === "team_loop" ? "T" : kind === "action_required" ? "A" : "";
  const r = await client.query(
    `select coalesce(max(nullif(regexp_replace(number, '\\D', '', 'g'), '')::int), 0) as m
       from loop_item where kind = $1`, [kind]);
  return `${prefix}${r.rows[0].m + 1}`;
}

async function nextRenderSeq(client, blockId) {
  const r = await client.query(
    "select coalesce(max(render_seq), 0) + 1 as n from loop_item where block_id = $1", [blockId]);
  return r.rows[0].n;
}

const BLOCKER_CLASSES = Object.freeze([
  "human_only",     // needs Joe or Dell in person: a call, a signature, a site visit, a login only he holds
  "counterparty",   // waiting on someone outside: landlord, broker, client, vendor — named
  "ruling",         // needs Joe's decision, and the question is stated
  "external_event", // a dated event must arrive first, and the date is named
  "other_lane",     // depends on another lane's in-flight deliverable, named
  "capability",     // a credential, gate or verb this session cannot hold, named (rule 1b8e7f43)
]);

// loop_item.marker's own contract (migration 0024): 'check (marker in (...))'.
// THE BUG THIS LIST FIXES (found 2026-08-13, decision 7026246b): add-loop's
// inputSchema had always documented this enum, but inputSchema is advisory
// only — the MCP transport never validates a call's arguments against it
// (mcp.js's callTool passes `rpc.params?.arguments` straight to the handler),
// so an illegal value like 'wrench' sailed past the JS layer entirely and hit
// the DB's CHECK constraint raw, which the generic top-level catch then
// flattened into a bare {"error":"internal error"} naming neither the field
// nor the allowed values. Same pattern BLOCKER_CLASSES fixed for `blocker`
// above; marker gets the identical up-front-validation treatment in add-loop.
export const LOOP_MARKERS = Object.freeze(["bell", "dated", "decision", "none"]);

// The detail field is where a determined session would smuggle the deferral back
// in, so the phrases that mean "not now, no reason" are refused by name. This is
// not a quality bar on writing; it is a check that the sentence names a WHO or a
// WHAT rather than a mood about time. Anchored loosely because the failure mode
// is a whole detail that reads "revisit later", not a passing mention of a word.
const VAGUE_BLOCKER_RE =
  /\b(?:later|someday|some day|eventually|when (?:there(?:'s| is) )?(?:more )?time|when time (?:permits|allows)|time permitting|revisit|circle back|down the (?:road|line)|at some point|in (?:the )?future|future session|next session|tbd|to be determined|n\/?a|low priority|nice to have|opportunistically|as time allows|no rush|whenever)\b/i;

// ── THE OWNERSHIP GATE (Joe 2026-08-10) ────────────────────────────────────
// Joe asked why the backlog never falls. The measured answer: intake is
// autonomous and the drain is not. Audits, IT sweeps, council reviews and
// research waves all OPEN loops on their own initiative — 34 of the 108 August
// loops still open came straight out of one — while nothing CLOSES one unless a
// human orders it. Stripping the purge Joe ordered on 2026-08-09 (68 closures
// in a day, 48 inside one hour), the baseline was about 4 closures a day
// against 21 opened.
//
// THE FIELD THAT ENFORCES THAT ASYMMETRY IS THIS ONE. 110 of the 150 open work
// loops were owned jointly — "Joe/Claude", "Joe + Dell", "Joe→Dell" — and only
// FOUR were owned by the system outright. Joint ownership reads as
// collaboration and functions as ambiguity: a row owned by everyone is picked
// up by no one, and the system is never licensed to close it alone. So the
// backlog can only fall on a day Joe says so.
//
// A single owner is not bureaucracy, it is the precondition for an autonomous
// drain. Of those 110 rows, 15 say in their own text that a human must act and
// 25 name Dell — the other 75 carry no human signal at all and were only ever
// waiting because nobody was unambiguously holding them.
const LOOP_OWNERS = Object.freeze(["joe", "dell", "claude"]);

// Any separator between two names is the ambiguity: slash, plus, arrow,
// ampersand, comma, or the word "and". Matched on the raw string because that
// is exactly how these rows were written by hand.
const JOINT_OWNER_RE = /[\/+&,]|→|->|\band\b/i;

function assertSingleOwner(owner) {
  const raw = (owner || "").trim();
  if (!raw) return null;                 // absent is allowed; ambiguous is not
  if (JOINT_OWNER_RE.test(raw))
    throw new ToolError({
      error: "joint_ownership_refused",
      got: raw,
      owners: LOOP_OWNERS,
      hint: "a loop owned by two people is owned by neither, and a jointly-owned loop can never be closed by the system on its own — which is why this backlog only falls when Joe orders a purge. Pick ONE: 'claude' if the system can finish it without a human, otherwise 'joe' or 'dell' — and then the row must name a blocker saying what it waits on.",
    });
  if (!LOOP_OWNERS.includes(raw.toLowerCase()))
    throw new ToolError({
      error: "unknown_owner",
      got: raw,
      owners: LOOP_OWNERS,
      hint: "owner is a single actor, lowercase — the field decides who may act, so a free-text value means nobody can be selected for by a query",
    });
  return raw.toLowerCase();
}

export function loopTools() {
  return {
  // ---------- the loop accumulators (one-writer Phase A, ORDER 31) ----------
    // open-loops.md, open-loops-backlog.md, action-required.md and team-loops.md
    // are generated renders of loop_item after this lands. Sessions stop editing
    // those four files and use these three verbs instead — which is the whole
    // point of Joe's ruling: "if i do something in my session, i want dell to be
    // able to instantly recall it in his session."

    "add-loop": {
      discoveryOrder: 72,
      write: true,
      description: "Open a new loop — a Joe/Dell task (kind open_loop), a partner handoff (team_loop), a cross-brain interrupt (action_required), or a parked idea (kind idea, which renders into 00_Context/idea-bank.md and is personal, never shared). Do NOT hand-edit open-loops.md, open-loops-backlog.md, action-required.md or team-loops.md; they are rendered from this. Markers carry meaning the heartbeat obeys: `bell` = actionable THIS WEEK (hard cap 3 PER DOMAIN — more than 3 means re-tier, not stack; read v_loop_bell_cap for breaches. The old cap was 5 across the whole hot list, written before domains existed: with six lanes that was under one bell each, so everything drifted to 'none' until the hot list held 21 items against a cap of 5), `dated` + due_on = silent until its day, `decision` = a ❓ the Monday brief surfaces, `none` = backlog. An open_loop with bell, or a dated one already due, lands hot; everything else lands in the backlog, which is the file's own rule. The action_required bar is deliberately high: only a new shared mechanism, a build the other side must replicate, or a protocol change — if everything is urgent, nothing is. THE DEFERRAL GATE: an open_loop is REFUSED unless it names `blocker` (a closed list of states of the world outside this session) and `blocker_detail` (the specific person, ruling, date or credential). There is no value meaning 'later'. Before filing one, ask whether this session could just do the work — if it could, do it and file nothing, because a session with the context already loaded is the cheapest builder this item will ever get.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        kind: { type: "string", enum: LOOP_KINDS },
        domain: { type: "string", enum: ["deals","prospecting","networking","marketing","business","system"],
          description: "deals | prospecting | networking | marketing | business | system. Classify by WHAT THE WORK IS, not who appears in it: a vendor introducing a PROSPECT normally means real intent and is DEALS (prospecting only while no deal has formed); a vendor introducing a VENDOR is networking; connecting a prospect to a vendor is networking; connecting a client to a vendor on a LIVE deal is deals. Omit only when genuinely unclear — an unclassified loop renders in its own unsorted section, which is honest, but a loop nobody can find is a loop nobody does." },
        title: { type: "string", description: "team_loop 'Ask' / action_required 'Action needed'. Not used by open_loop, whose text is `body`." },
        body: { type: "string", description: "open_loop 'Item' / team_loop 'Notes / links'" },
        owner: { type: "string", description: "the label the file uses: 'Joe', 'Joe/Claude', 'Dell', 'Joe→Dell'" },
        unblocks: { type: "string", description: "what it unblocks / why it matters" },
        source_note: { type: "string", description: "source / detail / links" },
        marker: { type: "string", enum: LOOP_MARKERS },
        due_on: { type: "string", description: "YYYY-MM-DD; required when marker is 'dated'" },
        drift_critical: { type: "boolean", description: "the ⚡ — leaving it undone causes system drift; BOTH brains' heartbeats surface it daily" },
        number: { type: "string", description: "override the auto-assigned ref. Only pass this to reproduce a number that already exists somewhere; the files already contain collisions." },
        since: { type: "string", description: "YYYY-MM-DD; defaults to today" },
        blocker: { type: "string", enum: BLOCKER_CLASSES,
          description: "REQUIRED on kind open_loop: why THIS session cannot do the work now. Every value is a state of the world outside the session, and the list is closed on purpose — there is no value meaning 'later'. human_only = needs Joe or Dell in person (a call, a signature, a site visit, a login only he holds) · counterparty = waiting on someone outside (name the landlord, broker, client or vendor) · ruling = needs Joe's decision (state the question) · external_event = a dated event must arrive first (name the date) · other_lane = depends on another lane's in-flight deliverable (name it) · capability = a credential, gate or verb this session cannot hold (name it). IF NONE OF THESE FIT, DO NOT FILE THE LOOP — do the work. Not asked for team_loop or action_required (the blocker is the other partner by construction) or for idea (parked by design)." },
        blocker_detail: { type: "string",
          description: "REQUIRED whenever `blocker` is set: the SPECIFIC thing, named. 'the landlord' is not a counterparty; 'Sanders, the listing broker on C-112' is. 'a ruling' is not a ruling; 'whether the 3% escalation cap applies to renewal years' is." } },
        required: ["idempotency_key", "kind", "owner"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "add-loop", args, async () => {
        // kind decides EVERYTHING downstream — the deferral gate, the tier, and
        // which block the row renders into — so it is checked before anything
        // else. See LOOP_KINDS for the live defect this closes (2026-08-14: an
        // omitted kind surfaced as a no_block error blaming the loop importer).
        if (args.kind === undefined || args.kind === null)
          throw new ToolError({ error: "missing_kind", allowed: LOOP_KINDS,
            hint: "kind is required and decides where the loop lives: open_loop (a Joe/Dell task), team_loop (partner handoff), action_required (cross-brain interrupt), or idea (parked, personal)" });
        if (!LOOP_KINDS.includes(args.kind))
          throw new ToolError({ error: "unknown_kind", got: args.kind, allowed: LOOP_KINDS,
            hint: "kind must be one of open_loop/team_loop/action_required/idea — see this verb's description for what each means" });
        if (!args.title && !args.body)
          throw new ToolError({ error: "empty_loop",
            hint: "a loop needs text: `body` for an open_loop, `title` for a team_loop or action_required" });
        // THE OTHER HALF OF DEFECT 2 (found 2026-08-13, decision 7026246b): marker
        // is documented in inputSchema as an enum but was never checked before
        // hitting loop_item's CHECK constraint. An illegal value ('wrench' — not
        // in bell/dated/decision/none) reached the DB raw and came back as a bare
        // {"error":"internal error"}, reproduced twice live. Validate up front,
        // exactly like BLOCKER_CLASSES does for `blocker` a few lines below.
        if (args.marker !== undefined && !LOOP_MARKERS.includes(args.marker))
          throw new ToolError({ error: "unknown_marker", got: args.marker, allowed: LOOP_MARKERS,
            hint: "marker must be one of bell/dated/decision/none — the file's own convention " +
                  "(see this verb's description for what each means)" });
        if (args.marker === "dated" && !args.due_on)
          throw new ToolError({ error: "dated_marker_needs_date",
            hint: "a 🗓 row is silent until its day — without a date it would be silent forever" });
        // Same class of bug, same fix: domain is a reference-table taxonomy
        // (loop_domain, 'open taxonomy, insert a row not a migration' per this
        // system's own convention — rule 0001), enforced by a FOREIGN KEY rather
        // than a CHECK, but an unrecognized slug fails exactly the same way: a
        // raw constraint violation with no field named. Checked against the live
        // table rather than a hardcoded list, because the taxonomy is deliberately
        // open to a new row without a code change.
        if (args.domain !== undefined && args.domain !== null) {
          const dom = await c.query("select slug from loop_domain where slug=$1", [args.domain]);
          if (!dom.rows.length) {
            const all = await c.query("select slug from loop_domain order by sort asc");
            throw new ToolError({ error: "unknown_domain", got: args.domain,
              allowed: all.rows.map(r => r.slug),
              hint: "classify by what the WORK is, not who appears in it — see this verb's description" });
          }
        }

        // ── THE DEFERRAL GATE (migration 0081, Joe 2026-08-09) ──────────────────
        // Joe taught rule 179be4b8 on 2026-08-08 — "why would you put them off?
        // thats the exact reason we have a giant growing list of loops" — and one
        // day later the list stood at 189 open rows. That rule binds at BUILD
        // WRAP-UP; nothing ever bound at the moment a session decides to file
        // instead of finish. This is that moment, and it is the only place the
        // question can be asked while the session still has the context to answer
        // it. A session that cannot name a blocker has just demonstrated it could
        // have done the work.
        const blockerGated = args.kind === "open_loop";
        // A BLOCKER ON A KIND THAT DOES NOT CARRY ONE USED TO BE DROPPED IN
        // SILENCE. Only open_loop rows are blocker-gated, and the insert below
        // writes `blockerGated ? args.blocker : null` — so a blocker passed on a
        // team_loop, action_required or idea row was accepted, reported back as a
        // successful create, and stored as NULL. Same defect class as e34d2b88
        // (a write verb accepted a field and silently discarded it), found by the
        // sweep loop #476 asked for. Refuse instead: the caller either meant a
        // different kind or did not need the field.
        if (!blockerGated && (args.blocker !== undefined || args.blocker_detail !== undefined))
          throw new ToolError({ error: "blocker_not_carried_by_kind",
            kind: args.kind, blocker: args.blocker || null,
            hint: "only an open_loop carries a blocker — on every other kind the field would be " +
                  "stored as null, so this refuses rather than reporting a write that did not " +
                  "happen. Drop the blocker, or file this as kind:'open_loop'." });
        if (blockerGated) {
          if (!args.blocker)
            throw new ToolError({ error: "blocker_required",
              classes: BLOCKER_CLASSES,
              hint: "an open_loop must name why THIS session cannot do the work now, from the closed list in `blocker`. There is no value meaning 'later' on purpose. If none of them fits, the answer is not a better loop — it is to do the work now and file nothing." });
          if (!BLOCKER_CLASSES.includes(args.blocker))
            throw new ToolError({ error: "unknown_blocker_class",
              got: args.blocker, classes: BLOCKER_CLASSES,
              hint: "the list is closed — every entry is a state of the world outside this session" });
          const detail = (args.blocker_detail || "").trim();
          if (detail.length < 12)
            throw new ToolError({ error: "blocker_detail_required", got: detail || null,
              hint: "name the SPECIFIC thing: which person, which ruling, which date, which credential. A class with no specific thing is the vague deferral wearing a label." });
          const vague = VAGUE_BLOCKER_RE.exec(detail);
          if (vague)
            throw new ToolError({ error: "blocker_detail_vague", matched: vague[0],
              hint: `"${vague[0]}" names a feeling about time, not a blocker. Say who or what has to happen first — and if nothing has to, do the work now instead of filing this.` });
        }
        // capability_no_decider and internal_decision_parked (bypass audit
        // C33/C34, ported from hooks/blocker-decider-gate.py and
        // hooks/escalation-gate.py) are enforced in this module's
        // executeRegisteredTool(), before this handler ever runs — see the
        // comment there for why that placement (the CANONICAL one, since
        // break-glass bypasses mcp.js's callTool() entirely) is what makes
        // every door, including break-glass, hit the same check. callTool()
        // also runs the same imported check earlier, purely as a fail-fast
        // ahead of its writer-pool connect — see its comment.

        // ── THE OWNERSHIP GATE ──────────────────────────────────────────────
        // Refuses a jointly-owned row at the moment it is filed. See LOOP_OWNERS
        // above for why: joint ownership is what stops the system ever draining
        // the backlog on its own initiative.
        args.owner = assertSingleOwner(args.owner);

        // THE CREATION-SIDE MIRROR of update-loop's due_date_needs_dated_marker.
        // Omitting the marker and passing a date INFERS 'dated', which is the
        // convenient path and stays. Passing an explicit non-dated marker
        // alongside a date was the bad one: the date was stored on a row whose
        // marker sends it elsewhere, and nothing reads due_on except on a dated
        // row — so the value sat in the column, honoured by nobody. Stored-and-
        // inert is the same lie as dropped, told in a way that is harder to spot.
        if (args.due_on && args.marker !== undefined && args.marker !== "dated")
          throw new ToolError({ error: "due_date_needs_dated_marker",
            marker: args.marker, due_on: args.due_on,
            hint: "a due date is only acted on when the marker is 'dated' — on any other marker " +
                  "it would sit in the column unread. Pass marker:'dated', or omit the marker " +
                  "entirely and it is inferred, or drop the date." });
        const marker = args.marker || (args.due_on ? "dated" : "none");
        const literal = marker === "bell" ? "🔔"
          : marker === "decision" ? "❓"
          : marker === "dated" ? `🗓${args.due_on}` : null;

        // Placement follows the files' own rule. It is STORED, not derived, so a
        // later promotion is a recorded act (see v_loop_promotion_due).
        const nowDue = marker === "dated" && args.due_on <= new Date().toISOString().slice(0, 10);
        // 'idea' has no "open" block — its live section is 'parked' (44 rows) and
        // 'retired' is its closed state. Asking for "open" would throw no_block.
        const wantKey = args.kind === "idea" ? "parked"
          : args.kind !== "open_loop" ? "open"
          : (marker === "bell" || nowDue) ? "hot" : "backlog";
        const b = await c.query(
          "select id, rel_path, col_order from loop_block where kind=$1 and block_key=$2",
          [args.kind, wantKey]);
        if (!b.rows.length)
          throw new ToolError({ error: "no_block", kind: args.kind, section: wantKey,
            hint: "the loop importer has not run for this kind — nothing to render into" });
        const block = b.rows[0];

        const num = args.number || await nextLoopNumber(c, args.kind);
        const seq = await nextRenderSeq(c, block.id);
        const tier = (args.kind === "open_loop" || args.kind === "idea") ? "personal" : "shared";
        const personal = tier === "personal" ? actor.id : null;

        const r = await c.query(
          `insert into loop_item (kind, number, block_id, render_seq, title, body, owner,
           since_text, unblocks, source_note, marker, marker_literal, due_on,
           drift_critical, status, tier, personal_to, created_by, updated_by, domain,
           blocker_class, blocker_detail)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'open',$15,$16,$17,$17,$18,$19,$20)
         returning id`,
          [args.kind, num, block.id, seq, args.title || null, args.body || null, args.owner,
           args.since || new Date().toISOString().slice(0, 10), args.unblocks || null,
           args.source_note || null, marker, literal, args.due_on || null,
           args.drift_critical === true, tier, personal, actor.id, args.domain || null,
           blockerGated ? args.blocker : null,
           blockerGated ? args.blocker_detail.trim() : null]);

        // The blocker goes on the EVENT as well as the row: an open_loop that was
        // filed under a blocker which later turns out to be false is a thing Joe
        // should be able to find, and events are the only surface that keeps the
        // claim as it was made on the day it was made.
        await writeEvent(c, actor, "add-loop", "loop", r.rows[0].id,
          { new: { number: num, kind: args.kind, section: wantKey, marker,
                   due_on: args.due_on || null, owner: args.owner, domain: args.domain || null,
                   blocker_class: blockerGated ? args.blocker : null,
                   blocker_detail: blockerGated ? args.blocker_detail.trim() : null },
            idempotency_key: args.idempotency_key });
        return { ok: true, loop_id: r.rows[0].id, number: num, kind: args.kind,
                 section: wantKey, renders_into: block.rel_path,
                 blocker: blockerGated ? args.blocker : null };
      }),
    },

    "update-loop": {
      discoveryOrder: 73,
      write: true,
      description: "Change an open loop — its text, its owner, its marker, or which section it sits in. This is also how a due backlog row gets PROMOTED to the hot list: pass section 'hot'. Promotion is a recorded act by an actor, never something a view does to Joe's file behind his back — read v_loop_promotion_due for what has come due. Pass only the fields you are changing; anything omitted is left alone. Closing is a different act: use close-loop, which requires an outcome.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        loop_id: { type: "string" },
        number: { type: "string", description: "alternative to loop_id; refuses when the number is ambiguous, and several are. To RENUMBER the row, pass a different number and renumber_reason: two open rows of the same kind can carry the same number, and every verb that resolves by number then refuses. Refused if the target number is already taken by another OPEN row of this kind." },
        kind: { type: "string", enum: LOOP_KINDS, description: "narrows an ambiguous number" },
        base_version: { type: "integer" },
        title: { type: "string" }, body: { type: "string" }, owner: { type: "string" },
        unblocks: { type: "string" }, source_note: { type: "string" },
        domain: { type: "string", enum: ["deals","prospecting","networking","marketing","business","system"],
          description: "reclassify the loop. Same rule as add-loop: classify by what the WORK is, not who appears in it." },
        marker: { type: "string", enum: LOOP_MARKERS },
        due_on: { type: "string", description: "YYYY-MM-DD. ONLY STORED ON A 'dated' ROW. On any " +
          "other marker this is refused rather than silently dropped — pass marker:'dated' in the " +
          "same call to promote the row, or omit the date. A bare due_on on a row that is already " +
          "dated is a reschedule and is fine. add-loop documents this dependency the other way " +
          "round (the date is required WHEN the marker is dated), which reads as a constraint on " +
          "the marker and is why a careful caller still walked into it — defect e34d2b88, 46 " +
          "writes lost on 2026-08-20." },
        drift_critical: { type: "boolean" },
        blocker: { type: "string", enum: ["human_only","counterparty","ruling","external_event","other_lane","capability"],
          description: "REVISE what the loop is waiting on. add-loop refuses a loop without a blocker, but until 2026-08-09 nothing could change one, so a blocker named on day one was permanent even after the real obstacle turned out to be different — found on loop #295, whose blocker read human_only until building it revealed the actual block was a missing corpus (other_lane). Changing this requires blocker_detail too: a reclassification with the old specifics attached is worse than the original, because it reads as current and is not." },
        blocker_detail: { type: "string", description: "the SPECIFIC thing, restated for the new class: which person, which ruling, which date, which prerequisite. Required whenever blocker changes; may also be passed alone to sharpen the detail without reclassifying." },
        renumber_reason: { type: "string", description: "REQUIRED whenever number changes: why, and where the old number still appears. Rule 7105955b binds here — a renumbered row is not an abandoned one, and the note recording the change has to say so in its first words, because the old number lives on in other rows' prose and in every generated render." },
        last_surfaced: { type: "string", description: "IDEA ROWS ONLY: stamp the idea bank's 'Last surfaced' column, YYYY-MM-DD. This is what the monthly resurface writes when a parked idea is presented and KEPT — the column its own rotation reads to pick the oldest ideas next month. Blank or '—' means never surfaced, so leaving it unwritten is not neutral: it keeps re-presenting the same rows." },
        section: { type: "string", enum: ["hot", "backlog", "open"], description: "move the row to this section of its file" } },
        required: ["idempotency_key"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "update-loop", args, async () => {
        const cur = await resolveLoop(c, args);
        await versionGuard(c, "loop_item", cur.id, args.base_version);
        if (cur.status !== "open")
          throw new ToolError({ error: "loop_not_open", loop_id: cur.id, status: cur.status,
            hint: "a closed loop is history; open a new one rather than editing the record of what happened" });

        // Ownership gate on the edit path too — otherwise the rule holds only for
        // rows filed after it shipped, and a session that wants a joint owner
        // just files clean and edits it back. See LOOP_OWNERS above.
        if (args.owner !== undefined) args.owner = assertSingleOwner(args.owner);

        const sets = [], vals = [];
        const set = (col, v) => { vals.push(v); sets.push(`${col}=$${vals.length}`); };
        for (const f of ["title", "body", "owner", "unblocks", "source_note", "domain"])
          if (args[f] !== undefined) set(f, args[f]);
        if (args.drift_critical !== undefined) set("drift_critical", args.drift_critical === true);

        // A blocker may be REVISED, because what a loop is waiting on is a finding
        // and findings change. Reclassifying without restating the specifics is
        // refused: a row reading `other_lane` above a detail that explains a
        // `human_only` block is worse than the stale original, because it reads as
        // current. Detail alone is allowed — sharpening the specifics under an
        // unchanged class is exactly what a working loop should do.
        if (args.blocker !== undefined) {
          if (args.blocker_detail === undefined)
            throw new ToolError({ error: "blocker_detail_required",
              hint: "changing the blocker class means restating the specific thing it is now waiting on. Pass blocker_detail in the same call." });
          set("blocker_class", args.blocker);
        }
        if (args.blocker_detail !== undefined) set("blocker_detail", args.blocker_detail);

        // RENUMBER (loop #306). Two open rows of one kind sharing a number is a
        // data-integrity defect, not a cosmetic one: update-loop, close-loop and
        // read-loop all resolve by number and all refuse on an ambiguous one — which
        // is the right behaviour and still leaves the human unable to act, because
        // the failure reads like a broken verb rather than like broken data. Until
        // now nothing could set this column, so the only workaround was to pass
        // loop_id, which silently picks whichever row the caller happened to look up.
        if (args.number !== undefined) {
          const next = String(args.number).replace(/^#/, "").trim();
          if (!/^\d+$/.test(next))
            throw new ToolError({ error: "bad_number", got: args.number,
              hint: "digits only — the renders sort on this and a free-form ref sorts wrong forever" });
          // THE REASON IS REQUIRED FOR A RENUMBER, NOT FOR SAYING WHICH ROW YOU
          // MEAN. This check used to sit above the comparison, so it fired on the
          // mere PRESENCE of `number` — and `number` is also this verb's
          // documented way to identify a row ("alternative to loop_id" in its own
          // input schema). The result: editing a loop by the number a human
          // actually says was refused with an error about renumbering, and the
          // only way through was to fetch the row with read-loop and pass
          // loop_id, one extra round trip to work around a guard that was not
          // guarding anything. Hit twice on 2026-08-14 clearing stale blockers.
          // The guard itself is right (rule 7105955b); it was reading the wrong
          // condition. Now it asks only when the number is genuinely CHANGING.
          if (next !== cur.number) {
            if (args.renumber_reason === undefined)
              throw new ToolError({ error: "renumber_reason_required",
                from: cur.number, to: next,
                hint: "rule 7105955b: a renumbered row is not an abandoned one, and the old number " +
                      "survives in other rows' prose and in every render. Say why, in the same call." });
            // The uniqueness index added alongside this enforces it in the database;
            // this check exists so the caller gets the two colliding ids back instead
            // of a constraint name.
            const clash = await c.query(
              "select id from loop_item where kind=$1 and number=$2 and status='open' and id <> $3",
              [cur.kind, next, cur.id]);
            if (clash.rows.length)
              throw new ToolError({ error: "number_taken", kind: cur.kind, number: next,
                held_by: clash.rows.map((x) => x.id),
                hint: "another OPEN row of this kind already carries that number — pick one nothing holds" });
            set("number", next);
          }
        }

        // 'Last surfaced' is an extra_cells key, not a column — the idea bank's two
        // bank-specific columns (Status, Last surfaced) ride in that jsonb because
        // loop_item is generic over four kinds. Until 2026-08-09 no verb could write
        // it, so the monthly resurface stamped source_note instead and the column it
        // rotates on stayed "—" forever (loop #273, the half that outlived the
        // close-loop fix above). MERGE rather than replace: #42 and #44-#47 carry a
        // `domain` and a `status` key in the same object, and a bare assignment would
        // silently drop both.
        if (args.last_surfaced !== undefined) {
          if (cur.kind !== "idea")
            throw new ToolError({ error: "last_surfaced_is_idea_only", kind: cur.kind,
              hint: "only the idea bank renders a 'Last surfaced' column; other kinds have no such cell" });
          if (!/^\d{4}-\d{2}-\d{2}$/.test(args.last_surfaced))
            throw new ToolError({ error: "bad_last_surfaced", got: args.last_surfaced,
              hint: "YYYY-MM-DD. The rotation sorts on this text, so a free-form date " +
                    "silently sorts wrong and the same ideas keep coming back." });
          vals.push(args.last_surfaced);
          sets.push("extra_cells=coalesce(extra_cells,'{}'::jsonb) || " +
                    `jsonb_build_object('last_surfaced', $${vals.length}::text)`);
        }

        if (args.marker !== undefined || args.due_on !== undefined) {
          const marker = args.marker !== undefined ? args.marker : cur.marker;
          const due = args.due_on !== undefined ? args.due_on : cur.due_on;
          if (marker === "dated" && !due)
            throw new ToolError({ error: "dated_marker_needs_date",
              hint: "a 🗓 row without a date is silent forever" });
          // A DATE ON A ROW THAT IS NOT DATED USED TO BE ACCEPTED AND THROWN
          // AWAY. The line below this block writes `marker === "dated" ? due
          // : null`, so a due_on arriving on a row whose marker is none/bell/
          // decision was written straight to NULL — while the call returned
          // success, bumped the version and moved updated_at. On 2026-08-20
          // that swallowed 46 consecutive writes during the idea-bank
          // conversion to due-date selection, caught only because the whole
          // board happened to be read back before reporting (defect e34d2b88,
          // the first of its class: a write verb accepted a field and silently
          // discarded it).
          //
          // REFUSING IS THE FIX, not implicitly promoting the row to "dated".
          // The marker decides which render a row lands in — a dated row that
          // has come due goes hot, everything else goes to the backlog — so
          // inferring the marker from the date would silently move rows on a
          // file Joe reads. Making the caller say both is one extra argument
          // and no surprises.
          //
          // Note what is deliberately still allowed: a bare due_on on a row
          // ALREADY marked dated, which is how a row is rescheduled and has
          // never been ambiguous.
          if (args.due_on !== undefined && args.due_on !== null && marker !== "dated")
            throw new ToolError({ error: "due_date_needs_dated_marker",
              marker, due_on: args.due_on,
              hint: "a due date is only stored on a 'dated' row — on any other marker it " +
                    "would be dropped, so this refuses rather than reporting a write that " +
                    "did not happen. Pass marker:'dated' in the same call, or drop the date." });
          set("marker", marker);
          set("due_on", marker === "dated" ? due : null);
          set("marker_literal", marker === "bell" ? "🔔"
            : marker === "decision" ? "❓"
            : marker === "dated" ? `🗓${due}` : null);
        }

        let moved = null;
        if (args.section && args.section !== cur.section) {
          const b = await c.query(
            "select id, rel_path from loop_block where kind=$1 and block_key=$2",
            [cur.kind, args.section]);
          if (!b.rows.length)
            throw new ToolError({ error: "no_such_section", kind: cur.kind, section: args.section,
              hint: `open_loop has hot and backlog; team_loop and action_required have open` });
          set("block_id", b.rows[0].id);
          set("render_seq", await nextRenderSeq(c, b.rows[0].id));
          moved = { from: cur.section, to: args.section };
        }

        if (!sets.length)
          throw new ToolError({ error: "nothing_to_update",
            hint: "pass at least one field; base_version alone changes nothing" });

        vals.push(actor.id); sets.push(`updated_by=$${vals.length}`);
        vals.push(cur.id);
        await c.query(`update loop_item set ${sets.join(", ")} where id=$${vals.length}`, vals);
        const renumbered = sets.some(s => s.startsWith("number="))
          ? { from: cur.number, to: String(args.number).replace(/^#/, "").trim(),
              reason: args.renumber_reason }
          : null;
        await writeEvent(c, actor, "update-loop", "loop", cur.id,
          { old: renumbered ? { number: cur.number } : undefined,
            new: { changed: sets.map(s => s.split("=")[0]), moved, renumbered },
            idempotency_key: args.idempotency_key });
        return { ok: true, loop_id: cur.id, number: renumbered ? renumbered.to : cur.number,
          moved, renumbered };
      }),
    },

    "loop-headers": {
      discoveryOrder: 74,
      write: false,
      description: "Read the standing paragraph that sits at the top of each loops section — the header prose of open-loops.md, its backlog file, team-loops.md, action-required.md and the idea bank. THE GAP THIS CLOSES: that prose is DATA (loop_block.prose_md), held there deliberately so a partner's own words stay his to change instead of being a code edit, but nothing could read it back except a raw table query, so nobody could see that a header had gone stale. Returns each block's file, section, version and prose, so a caller has the base_version edit-loop-header needs without probing a write verb for it.",
      inputSchema: { type: "object", properties: {
        file: { type: "string", description: "narrow to one render, e.g. '00_Context/open-loops.md'; substring match" },
        section: { type: "string", description: "narrow to one section key: hot, backlog, open, done, parked, retired" },
      } },
      handler: async (c, _a, args) => {
        const where = [], params = [];
        if (args.file) { params.push(`%${args.file}%`); where.push(`rel_path ilike $${params.length}`); }
        if (args.section) { params.push(args.section); where.push(`block_key = $${params.length}`); }
        const r = await c.query(
          `select id as block_id, rel_path as file, kind, block_key as section, seq,
                version, coalesce(prose_md,'') as prose_md
           from loop_block ${where.length ? "where " + where.join(" and ") : ""}
          order by rel_path, seq`, params);
        return { count: r.rows.length, blocks: r.rows };
      },
    },

    "edit-loop-header": {
      discoveryOrder: 75,
      write: true,
      description: "Rewrite the standing paragraph at the top of one loops section. THE GAP THIS CLOSES (loop #294): this prose is stored data rather than code — migration 0024 put it in loop_block.prose_md on purpose, so Joe's doctrine paragraph stays his to change — but no verb wrote that column, so a header that went stale could only be corrected by a raw table write, which is exactly the kind of write the record layer exists to prevent. It cost something real: open-loops.md's header pointed closed rows at a file that had been a frozen archive since 2026-07-31, a session read the stale pointer instead of the archive's own header, and reported to Joe that the closed-loop history was broken when 152 outcomes were sitting exactly where they belonged. Read the current text with loop-headers first and pass its version back as base_version. Pass the WHOLE replacement paragraph, not a patch — this verb sets the column, it does not merge.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        block_id: { type: "string", description: "exact uuid from loop-headers; wins over file+section" },
        file: { type: "string", description: "the render, e.g. '00_Context/open-loops.md'; use with section" },
        section: { type: "string", description: "the section key within that file: hot, backlog, open, done, parked, retired" },
        base_version: { type: "integer", description: "the version loop-headers returned for this block" },
        prose_md: { type: "string", description: "REQUIRED: the complete replacement paragraph, markdown, exactly as it should render" },
      }, required: ["idempotency_key", "prose_md"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "edit-loop-header", args, async () => {
        const cols = "id, rel_path, kind, block_key, version, coalesce(prose_md,'') as prose_md";
        let r;
        if (args.block_id) {
          r = await c.query(`select ${cols} from loop_block where id=$1`, [args.block_id]);
          if (!r.rows.length) throw new ToolError({ error: "not_found", block_id: args.block_id,
            hint: "no loops section carries that id — read loop-headers for the list" });
        } else {
          if (!args.file || !args.section)
            throw new ToolError({ error: "need_block_id_or_file_and_section",
              hint: "pass block_id, or both file and section — a file alone is ambiguous because most render two sections" });
          r = await c.query(
            `select ${cols} from loop_block where rel_path ilike $1 and block_key = $2`,
            [`%${args.file}%`, args.section]);
          if (!r.rows.length) throw new ToolError({ error: "no_such_section", file: args.file, section: args.section,
            hint: "read loop-headers for the real file/section pairs" });
          if (r.rows.length > 1) throw new ToolError({ error: "ambiguous_file",
            candidates: r.rows.map((x) => ({ block_id: x.id, file: x.rel_path, section: x.block_key })),
            hint: "the file substring matched more than one render — pass block_id" });
        }
        const cur = r.rows[0];
        await versionGuard(c, "loop_block", cur.id, args.base_version);

        // An EMPTY header is not an edit, it is a deletion of the only explanation a
        // reader of that render ever gets. Two of these blocks are Done tables whose
        // prose is a single short line; blanking one silently is indistinguishable
        // from the column never having been populated.
        if (!String(args.prose_md).trim())
          throw new ToolError({ error: "empty_prose",
            hint: "pass the replacement paragraph; to say nothing, say it in words rather than by blanking the header" });
        if (args.prose_md === cur.prose_md)
          throw new ToolError({ error: "nothing_to_update", block_id: cur.id,
            hint: "the text passed is byte-identical to what is stored" });

        await c.query("update loop_block set prose_md=$1, updated_by=$2 where id=$3",
          [args.prose_md, actor.id, cur.id]);
        // The OLD text is kept in the event, in full. This paragraph is doctrine a
        // partner wrote; an edit that leaves no way back is not a correction.
        await writeEvent(c, actor, "edit-loop-header", "loop_block", cur.id,
          { old: { prose_md: cur.prose_md }, new: { prose_md: args.prose_md },
            idempotency_key: args.idempotency_key });
        return { ok: true, block_id: cur.id, file: cur.rel_path, section: cur.block_key,
          was_length: cur.prose_md.length, now_length: args.prose_md.length };
      }),
    },

    "close-loop": {
      discoveryOrder: 76,
      write: true,
      description: "Close a loop — done, or deliberately dropped. AN OUTCOME IS REQUIRED and the verb refuses without one: team-loops states the reason in its own words, 'outcomes are how the asker finds out without asking twice.' Say what actually came of it, not that it is closed. Where the row goes depends on whether its file keeps closed rows visible: a team_loop or action_required row moves to its Done table carrying the outcome, and an idea moves to the idea bank's Retired table the same way (its rule 4 is 'move, don't delete — the reasoning stays visible so we don't re-litigate it later'); an open_loop simply leaves the hot/backlog render, the same thing closing a row has always done. Use resolution 'dropped' when it is being abandoned rather than finished — recording an abandonment as done inflates every completion measure built on this.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        loop_id: { type: "string" },
        number: { type: "string", description: "alternative to loop_id; refuses when ambiguous" },
        kind: { type: "string", enum: LOOP_KINDS },
        base_version: { type: "integer" },
        outcome: { type: "string", description: "REQUIRED: what came of it, in your words" },
        resolution: { type: "string", enum: ["done", "dropped"] },
        successor_loop: { type: "string", description: "Required when the row is renumbered, superseded, merged, or split: the open row that carries the work forward. Say it the way you would out loud — the number, with or without the leading hash ('#213' or '213') — or pass its loop_id if you have one." } },
        required: ["idempotency_key", "outcome"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "close-loop", args, async () => {
        // The refusal is first and unconditional. A whitespace-only outcome is no
        // outcome: the record-level CHECK would accept ' ' and the asker would still
        // never find out, which is the failure this rule exists to prevent.
        const outcome = (args.outcome || "").trim();
        if (!outcome)
          throw new ToolError({ error: "outcome_required",
            hint: "close-loop will not close a loop silently — say what came of it. " +
                  "If nothing came of it and you are abandoning it, say that and pass resolution 'dropped'." });

        const cur = await resolveLoop(c, args);
        await versionGuard(c, "loop_item", cur.id, args.base_version);
        if (cur.status !== "open")
          throw new ToolError({ error: "loop_not_open", loop_id: cur.id, status: cur.status,
            closed_outcome: cur.close_outcome,
            hint: "already closed — this is what came of it" });

        const resolution = args.resolution || "done";
        // A BOOKKEEPING CLOSE DECLARES ITSELF; it is not sniffed out of free text.
        // This used to be /\b(renumbered|superseded|merged|split)\b/i tested
        // ANYWHERE in the outcome, which conflated two unrelated senses of one
        // word: a LOOP merged into another loop (bookkeeping) versus a PULL
        // REQUEST landing on main (completion). Since main is PR-only with
        // automerge, every honest close of a code loop names a landed pull
        // request — so the guard fired hardest against the most common true
        // completion it would ever see. It refused loop #500 on 2026-08-22, whose
        // work was finished, landed and verified twice (defect 3fe38a2f). "split"
        // and "superseded" carry the same double meaning: a split file, a
        // superseded API endpoint.
        //
        // The anywhere-match bought no real protection either. It filters WORDING
        // rather than substance, and is evaded by writing "landed" instead, which
        // is how that close eventually went through — a guard a caller escapes by
        // reaching for a synonym is not enforcing anything. What actually signals
        // continuing work is the outcome OPENING with a bookkeeping declaration
        // (which the prefix check just below already demands) or a successor loop
        // being named. Both are deliberate acts; neither is an accident of prose.
        //
        // A real bookkeeping close is identifiable three ways, and all three are
        // things this verb already demands of one: the outcome OPENS with the
        // declaration (the prefix check immediately below requires exactly that),
        // or it NAMES the loop the work moved to, or it passes successor_loop.
        // Prose about a branch reaching main does none of those.
        //
        // The narrowing is real and worth naming: an outcome saying "merged with
        // another effort", naming no loop and passing no successor, is no longer
        // flagged. That phrasing already failed the two checks below, so it could
        // never have completed a bookkeeping close — it would only have been
        // refused later and less clearly.
        const bookkeeping =
          /^\s*(renumbered|superseded)\b/i.test(outcome) ||
          /\b(?:superseded\s+by|renumbered\s+(?:to|as)|merged\s+(?:into|with)|split\s+into)\s+(?:open\s+)?(?:loop\s*)?#?\d+/i.test(outcome) ||
          Boolean(args.successor_loop);
        let successor = null;
        if (bookkeeping) {
          if (resolution !== "dropped")
            throw new ToolError({ error: "bookkeeping_close_is_dropped", hint: "continuing work is not done; close it as dropped with the successor named" });
          if (!/^(renumbered|superseded)/i.test(outcome))
            throw new ToolError({ error: "bookkeeping_outcome_prefix", hint: "open a bookkeeping close with RENNUMBERED or SUPERSEDED, not an abandonment claim" });
          if (!args.successor_loop)
            throw new ToolError({ error: "successor_loop_required", hint: "name the open loop that now carries this work; a bookkeeping close cannot read as abandonment" });
          // Accept the form a partner actually says. This field is described as
          // "the open row that carries the work forward" and its own missing-field
          // hint says "name the open loop", but it used to be handed straight to
          // resolveLoop as loop_id — the uuid-only branch. Passing "#213", which is
          // exactly what the hint asks for, sent a non-uuid string into a where
          // clause on a uuid column: Postgres raised invalid input syntax and the
          // caller got a bare `internal error` naming nothing. Four loop closes
          // failed that way during the 2026-08-21 markdown-endgame consolidation
          // before the uuid was substituted by hand (defect 3eb1ad6d, rule
          // 3a9dbafd — never make a partner decode an id).
          const successorRef = String(args.successor_loop).trim();
          successor = UUID_RE.test(successorRef)
            ? await resolveLoop(c, { loop_id: successorRef })
            : await resolveLoop(c, { number: successorRef.replace(/^#/, "") });
          if (successor.id === cur.id || successor.status !== "open")
            throw new ToolError({ error: "successor_loop_not_open", successor_loop: args.successor_loop,
              hint: "the successor must be a different open loop" });
        }

        // A file with a Done table keeps its closed rows visible in the render; that
        // is the file's own convention, not a new one. open_loop has no Done table
        // in either of its two files, so a closed one simply leaves the list.
        //
        // FOUND BY block_key='done' UNTIL 2026-08-09, WHICH SILENTLY BROKE THE IDEA
        // BANK (loop #273). Ideas call their Done table "Retired", so the lookup
        // matched nothing, the row kept its `parked` block_id, and because `parked`
        // has renders_closed=false the join in v_export_loops dropped it from the
        // file entirely — neither Parked nor Retired. The outcome was never lost
        // (close_outcome is required) but it was UNRENDERED, which broke the bank's
        // founding rule 4, "move, don't delete — the reasoning stays visible so we
        // don't re-litigate it later", and blinded the monthly resurface gate that
        // reads the file to decide whether the round already ran.
        //
        // Match on renders_closed instead of on a hardcoded name. That column IS the
        // property being asked about — "the block that keeps closed rows visible" —
        // so the lookup can no longer be defeated by a file calling its Done table
        // something else, and a future kind gets the behaviour by setting one flag.
        // Exactly one block per kind carries it today (team_loop/done,
        // action_required/done, idea/retired; open_loop has none, so those rows keep
        // leaving the render as they always have). `order by seq` makes the pick
        // deterministic rather than dependent on row order if that ever stops being true.
        const done = await c.query(
          "select id, rel_path, block_key from loop_block " +
          " where kind=$1 and renders_closed order by seq limit 1", [cur.kind]);
        const sets = ["status=$1", "close_outcome=$2", "closed_by=$3", "closed_at=now()",
                      "outcome=$2", "closed_text=to_char(now(),'YYYY-MM-DD')", "updated_by=$3"];
        const vals = [resolution, outcome, actor.id];
        let movedTo = null, movedToBlock = null;
        if (done.rows.length) {
          vals.push(done.rows[0].id); sets.push(`block_id=$${vals.length}`);
          vals.push(await nextRenderSeq(c, done.rows[0].id)); sets.push(`render_seq=$${vals.length}`);
          movedTo = done.rows[0].rel_path;
          movedToBlock = done.rows[0].block_key;
        }
        vals.push(cur.id);
        await c.query(`update loop_item set ${sets.join(", ")} where id=$${vals.length}`, vals);

        await writeEvent(c, actor, "close-loop", "loop", cur.id,
          { field: "status", old: { status: "open" },
            new: { status: resolution, outcome,
                   ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}) }, human_quote: outcome,
            idempotency_key: args.idempotency_key });
        // Name the destination BLOCK, not just the file. `ok:true` proves the call
        // parsed, never that the row landed where a reader will find it (rule
        // c53beeaa) — and a null here is now the caller's signal that this kind
        // keeps no closed table, rather than something having gone wrong.
        return { ok: true, loop_id: cur.id, number: cur.number, status: resolution,
                 ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}),
                 moved_to_done_table_in: movedTo, closed_rows_render_in: movedToBlock };
      }),
    },

    "amend-closed-loop": {
      discoveryOrder: 77,
      write: true,
      description: "Correct a CLOSED loop's outcome, append-only — never rewrite history. THE GAP THIS CLOSES: close-loop refuses with loop_not_open on anything already closed, by design (a closed loop is history; open a new one rather than editing the record of what happened) — but that rule has no answer for the outcome text itself being WRONG. Loop c7265238-effe-4166-bc9a-eccc5f389763 was closed with outcome \"x\" by mistake and nothing could fix it (defect a2c04ffa-92d0-4428-b175-32fa3cfb0802): the only paths were a raw table UPDATE, exactly what the record layer exists to prevent, or living with a nonsense outcome forever. This verb is the third path. It NEVER reopens the loop and never rewrites the prior outcome in place — it appends a loop_amendment row (prior outcome, new outcome, reason, server-derived actor) and only then updates loop_item's current projection to match, so the loop's current outcome reads the latest amendment while every prior one stays on the record. Refused on a still-OPEN loop (use update-loop to change it, or close-loop to close it) and on a stale base_version (version_conflict — re-read and re-decide, never auto-retried).",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        loop_id: { type: "string" },
        number: { type: "string", description: "alternative to loop_id; refuses when ambiguous. Unlike every other loop verb's `number`, this resolves CLOSED rows too, because that is the only kind this verb ever acts on." },
        kind: { type: "string", enum: LOOP_KINDS, description: "narrows an ambiguous number" },
        base_version: { type: "integer" },
        outcome: { type: "string", description: "REQUIRED: the CORRECTED outcome, in your words. Refused under ~10 characters — a placeholder correction ('x', 'n/a', 'fixed') is not a correction, and is exactly the failure mode this verb exists to repair." },
        reason: { type: "string", description: "REQUIRED: why the recorded outcome is being corrected. Never inferred, never defaulted — the reason is as much a part of the record as the new text." },
        resolution: { type: "string", enum: ["done", "dropped"], description: "Corrects the loop's resolution alongside its outcome. Omit to leave the resolution exactly as it was closed. Passing 'dropped' with a bookkeeping outcome (opens with RENUMBERED/SUPERSEDED, or names a successor) is held to the same successor rules close-loop enforces: successor_loop is required and must name a different, currently OPEN loop." },
        successor_loop: { type: "string", description: "Same field, same rule as close-loop's: the open row that now carries the work forward, required whenever the corrected outcome reads as a bookkeeping close. Pass the number as a human would say it ('#213' or '213') or a loop_id." } },
        required: ["idempotency_key", "outcome", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "amend-closed-loop", args, async () => {
        // Refusals are unconditional and first, exactly like close-loop's own
        // outcome_required check: a caller who cannot yet say the corrected text
        // and why should not be able to half-amend the record.
        const newOutcome = (args.outcome || "").trim();
        if (!newOutcome)
          throw new ToolError({ error: "outcome_required",
            hint: "say what actually came of it — the corrected text, not that it needs correcting" });
        // "x" is the literal outcome that caused defect a2c04ffa-92d0-4428-b175-32fa3cfb0802.
        // A length floor cannot judge PROSE, but it can catch a placeholder, and a
        // placeholder is exactly what got this verb built.
        if (newOutcome.length < 10)
          throw new ToolError({ error: "outcome_too_short", got: newOutcome.length, min: 10,
            hint: "a meaningful correction reads as one — say what actually came of it, at least 10 characters" });
        const reason = (args.reason || "").trim();
        if (!reason)
          throw new ToolError({ error: "reason_required",
            hint: "say why the recorded outcome is being corrected — required, never inferred" });

        // anyStatus:true is the whole reason this verb needed to extend the seam
        // rather than call resolveLoop as every other loop verb does: it is the
        // one verb whose entire purpose is acting on a row every other number
        // lookup would refuse to find.
        const cur = await resolveLoop(c, args, { anyStatus: true });
        await versionGuard(c, "loop_item", cur.id, args.base_version);
        if (cur.status === "open")
          throw new ToolError({ error: "loop_open", loop_id: cur.id, status: cur.status,
            hint: "amend-closed-loop only corrects a CLOSED loop's outcome — this one is still open. " +
                  "Use update-loop to change it, or close-loop to close it." });

        const resolution = args.resolution !== undefined ? args.resolution : cur.status;

        // Identical bookkeeping-close detection to close-loop's own, applied to
        // the CORRECTED outcome: a correction that turns an outcome into a
        // renumber/supersede/merge/split declaration is making the same kind of
        // claim close-loop guards, and deserves the same guard. See close-loop's
        // own comment for the full history of why this is exact-prefix-or-named-
        // successor rather than an anywhere-in-prose match.
        const bookkeeping =
          /^\s*(renumbered|superseded)\b/i.test(newOutcome) ||
          /\b(?:superseded\s+by|renumbered\s+(?:to|as)|merged\s+(?:into|with)|split\s+into)\s+(?:open\s+)?(?:loop\s*)?#?\d+/i.test(newOutcome) ||
          Boolean(args.successor_loop);
        let successor = null;
        if (bookkeeping) {
          if (resolution !== "dropped")
            throw new ToolError({ error: "bookkeeping_close_is_dropped",
              hint: "continuing work is not done; correct the resolution to 'dropped' with the successor named" });
          if (!/^(renumbered|superseded)/i.test(newOutcome))
            throw new ToolError({ error: "bookkeeping_outcome_prefix",
              hint: "open a bookkeeping close with RENUMBERED or SUPERSEDED, not an abandonment claim" });
          if (!args.successor_loop)
            throw new ToolError({ error: "successor_loop_required",
              hint: "name the open loop that now carries this work; a bookkeeping close cannot read as abandonment" });
          const successorRef = String(args.successor_loop).trim();
          successor = UUID_RE.test(successorRef)
            ? await resolveLoop(c, { loop_id: successorRef })
            : await resolveLoop(c, { number: successorRef.replace(/^#/, "") });
          if (successor.id === cur.id || successor.status !== "open")
            throw new ToolError({ error: "successor_loop_not_open", successor_loop: args.successor_loop,
              hint: "the successor must be a different open loop" });
        }

        // The prior outcome, read from resolveLoop's row — BEFORE versionGuard's
        // `for update` lock, not from it. That read is still safe: it is the
        // version check right above, not the lock itself, that makes it so — a
        // concurrent amend that changed the row between this read and the lock
        // moves the version, versionGuard throws version_conflict on the stale
        // base_version, and this priorOutcome is never written. Not from the
        // args, not reconstructed either way — the actual current text this
        // amendment is correcting.
        const priorOutcome = cur.close_outcome || "";
        const priorResolution = cur.status;

        // APPEND FIRST. The amendment row is the history; the loop_item update
        // right after it is only the current projection catching up to what the
        // history now says. Reversing this order would let a crash between the
        // two leave a projection change with no corresponding record of why.
        await c.query(
          `insert into loop_amendment (loop_id, prior_outcome, new_outcome, prior_resolution,
           new_resolution, reason, actor_id, idempotency_key)
         values ($1,$2,$3,$4,$5,$6,$7,$8)`,
          [cur.id, priorOutcome, newOutcome, priorResolution, resolution, reason, actor.id,
           args.idempotency_key]);

        // The loop's current outcome reads the latest amendment: close_outcome
        // and outcome (the pair close-loop itself always keeps in sync) are set
        // to the corrected text, and the row's own version increments via
        // trg_touch_row exactly as any other loop_item update does. closed_at
        // and closed_by are deliberately UNTOUCHED — this is a correction to
        // what was said, not a re-closing of the loop, and the original closer
        // and closing time stay accurate.
        await c.query(
          `update loop_item set close_outcome=$1, outcome=$1, status=$2, updated_by=$3 where id=$4`,
          [newOutcome, resolution, actor.id, cur.id]);

        await writeEvent(c, actor, "amend-closed-loop", "loop", cur.id,
          { field: "close_outcome",
            old: { outcome: priorOutcome, resolution: priorResolution },
            new: { outcome: newOutcome, resolution, reason,
                   ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}) },
            idempotency_key: args.idempotency_key });

        return { ok: true, loop_id: cur.id, number: cur.number, status: resolution,
                 prior_outcome: priorOutcome, outcome: newOutcome,
                 ...(successor ? { successor_loop: { id: successor.id, number: successor.number } } : {}) };
      }),
    },
  };
}

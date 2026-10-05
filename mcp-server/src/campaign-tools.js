import { ToolError } from "./tool-error.js";
import { versionGuard, withEnvelope, writeEvent } from "./versioned-write.js";
import { config, require0066, resolveCampaign } from "./verb-support.js";

// A placement by uuid, live URL, or Blotato external_id. The URL and the id are
// what a caller actually holds: the marketing seat's own campaign-proposal block
// names content "by placement URL or Blotato id" because those are the only
// handles that appear in the published log. Measured 2026-08-02: all 89
// placements carry a non-null external_id and url, and all 89 of each are
// distinct, so both are usable as keys and neither can silently collide.
// LinkedIn hands a human a DIFFERENT id than the one we store, and there is no
// way to convert between them. Blotato reports `postUrl` in the share/ugcPost
// form (urn:li:share:… / urn:li:ugcPost:…) and that is what `placement.url`
// holds. Every LinkedIn surface a person can reach — the recent-activity feed,
// the per-post analytics link, the whole rendered DOM — shows only the ACTIVITY
// urn. The two ids are minted for the same post milliseconds apart and are not
// derivable from each other:
//     2026-08-05  stored ugcPost 7490800344260841472  activity 7490800345598779392
//     2026-08-03  stored share   7490064185725280256  activity 7490064188413870080
// Measured 2026-08-19: this stranded four readings in one week, including the
// best-performing post on any platform, and it would have stranded four or five
// more every week for as long as it stood.
//
// Both ids are Snowflake-shaped, so the high bits ARE the publish time
// (ms = id >> 22). That gives an exact, checkable bridge rather than a guess.
// Measured against all four stranded posts on 2026-08-19: each activity urn
// decoded to within 0.1 SECONDS of a real linkedin placement's live_at, while
// the next-nearest linkedin placement sat ~170,000 seconds (about two days)
// away. A ±90s window therefore separates the true match from its nearest rival
// by more than three orders of magnitude. If that ever stops holding — two
// LinkedIn posts inside 90 seconds — this REFUSES as ambiguous rather than
// guessing, which is the same posture as every other handle here.
const LINKEDIN_ACTIVITY_URN = /urn:li:activity:(\d{6,25})\b/i;

const LINKEDIN_SNOWFLAKE_EPOCH_SHIFT = 22n;

const LINKEDIN_MATCH_WINDOW_SECONDS = 90;

export function linkedInActivityPublishedAt(ref) {
  const m = LINKEDIN_ACTIVITY_URN.exec(ref);
  if (!m) return null;
  const ms = BigInt(m[1]) >> LINKEDIN_SNOWFLAKE_EPOCH_SHIFT;
  const at = new Date(Number(ms));
  // A decode that lands outside plausible range means the id is not what we
  // think it is; say nothing rather than match on nonsense.
  if (!Number.isFinite(at.getTime())) return null;
  if (at.getUTCFullYear() < 2010 || at.getUTCFullYear() > 2100) return null;
  return at;
}

async function resolvePlacement(c, ref) {
  const raw = String(ref || "").trim();
  if (!raw) throw new ToolError({ error: "placement_required" });
  const by = async (sql, v) => (await c.query(sql, [v])).rows;
  let rows = [];
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw))
    rows = await by("select * from placement where id=$1", raw);
  if (!rows.length && /^https?:\/\//i.test(raw))
    rows = await by("select * from placement where url = $1", raw);
  if (!rows.length) rows = await by("select * from placement where external_id = $1", raw);
  if (!rows.length) {
    const publishedAt = linkedInActivityPublishedAt(raw);
    if (publishedAt) {
      rows = (await c.query(
        "select * from placement where platform = 'linkedin' and live_at is not null " +
        "and live_at between $1::timestamptz - make_interval(secs => $2) " +
        "                and $1::timestamptz + make_interval(secs => $2)",
        [publishedAt.toISOString(), LINKEDIN_MATCH_WINDOW_SECONDS])).rows;
      if (rows.length === 1) {
        // Say so out loud. A handle that matched on a derived timestamp rather
        // than on a stored key is a different kind of certainty, and the caller
        // recording a number against it should see which one they got.
        return Object.assign({}, rows[0], {
          _resolved_by: "linkedin_activity_urn_publish_time",
          _resolved_note:
            `matched the linkedin activity urn to placement.live_at within ` +
            `${LINKEDIN_MATCH_WINDOW_SECONDS}s (LinkedIn never exposes the stored ` +
            `share/ugcPost urn, so the publish time encoded in the activity id is ` +
            `the only bridge)`,
        });
      }
      if (rows.length > 1) throw new ToolError({ error: "ambiguous_placement", ref: raw,
        candidates: rows.map(r => ({ placement_id: r.id, platform: r.platform, url: r.url,
                                     live_at: r.live_at })),
        hint: "two or more linkedin placements published within " +
              `${LINKEDIN_MATCH_WINDOW_SECONDS}s of this activity urn, so the publish ` +
              "time cannot identify one. Pass the stored post URL or the Blotato id." });
    }
  }
  if (rows.length === 1) return rows[0];
  if (rows.length > 1) throw new ToolError({ error: "ambiguous_placement", ref: raw,
    candidates: rows.map(r => ({ placement_id: r.id, platform: r.platform, url: r.url })),
    hint: "this handle resolves to more than one placement — a data fault; surface it" });
  throw new ToolError({ error: "placement_not_found", ref: raw,
    hint: "pass the live post URL, the Blotato post id, or the placement uuid — and for " +
          "LinkedIn, the activity urn or any URL containing it works too, matched on the " +
          "publish time encoded in the id. Placements are created by " +
          "pipelines/pull_placement_metrics.py when a post publishes — if the post is live " +
          "and this fails, the pull has not run since it published. Do NOT invent a " +
          "placement to hang a number on." });
}

async function livePlatformSlugs(c) {
  const r = await c.query(
    "select slug from marketing_subject where subject_type='platform' and retired_at is null order by slug");
  return r.rows.map(x => x.slug);
}

export function campaignTools() {
  return {
  // ═══════════════════════════════════════════════════════════════════════════
    // MARKETING (0066) — the four verbs that give the lane an intent and an answer
    //
    // WHAT WAS BROKEN, measured on 2026-08-02 and not inferred. `campaign` held 0
    // rows. `content_piece` held 89 and every single campaign_id was null. 259
    // placement_metric rows existed and could not answer whether anything worked,
    // because nothing in the database ever said what any of it was FOR. The only
    // writer of any of these tables was pipelines/pull_placement_metrics.py, a
    // scheduled ingest that creates pieces and placements from Blotato and sets
    // campaign_id to nothing. The marketing COO seat could SPECIFY a campaign in
    // prose and could not RECORD one.
    //
    // WHY FOUR VERBS AND NOT THREE — the close/score question, answered.
    // open-campaign and score-campaign are separate on this system's own
    // precedent: activate-rule and retire-rule are separate, and update-loop and
    // close-loop are separate, because a state transition that carries a JUDGMENT
    // needs arguments the opening act must never accept. Folding them together
    // would mean a verb whose required fields depend on a mode flag, and a mode
    // flag is how a campaign gets closed by accident. More concretely: scoring
    // requires a verdict, evidence, and a measurement-coverage check that
    // open-campaign has no business running, and it must REFUSE a "worked" verdict
    // formed over unmeasured placements — a refusal that only makes sense at the
    // closing end. The cost is one more verb; the benefit is that "we decided this
    // worked" can never be a side effect of editing a start date.
    //
    // WHY NO VERB CREATES A content_piece. Checked before deciding, which is the
    // only reason the answer is trustworthy: all 89 existing pieces were born in
    // pull_placement_metrics.py, keyed on placement.external_id, at publish time.
    // A second birth path would mint duplicates the moment the ingest next runs,
    // because the ingest matches on external_id and a hand-made piece has none.
    // So pieces arrive by publishing, and attach-to-campaign BINDS them. The real
    // consequence, stated rather than hidden: content that is PLANNED but not yet
    // published has no record-layer home at all, and that is a genuine gap for
    // Joe to rule on, not something to paper over by minting orphan rows here.
    //
    // NOTHING HERE IS OUTBOUND AND NOTHING HERE SPENDS. These four verbs write
    // records about content that already exists. No verb publishes, schedules,
    // boosts, funds or touches a platform credential — the one human gate is
    // unchanged.

    "open-campaign": {
      discoveryOrder: 78,
      write: true,
      description: "Open a campaign: the object that says what a run of content is FOR, so its results can later be judged instead of admired. THIS IS THE MISSING MIDDLE OF THE WHOLE MARKETING LANE — as of 2026-08-02 the campaign table held 0 rows while 89 content pieces and 259 metrics existed, so nothing published in the system's entire recorded history had a stated objective. Requires the objective (goal), the WINDOW (starts_on, optionally ends_on), the CHANNELS, and a success_criterion written so it can be CHECKED: 'X drives three practice-owner replies by Sept 30' is a criterion; 'grow awareness on X' is a restated goal and this verb refuses it. The criterion is required at OPEN because a criterion invented after the numbers arrive is not a criterion, and score-campaign quotes this one back before it accepts any verdict. ONE CAMPAIGN PER NAME, enforced by a unique index — reopening the same name is refused with the existing campaign's id, because the way this system produced 415 organisation rows for 306 real organisations was writers that never looked first. Backdating over content that already published is legitimate and normal (all 89 existing pieces are historical), so a start far in the past asks for confirm rather than refusing. NOT a publishing verb: it schedules nothing, spends nothing, and posts nothing. NOT the place for a piece of content — attach-to-campaign binds those, and it can only bind pieces that already published.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        name: { type: "string", description: "short, human, no ids. Unique — one campaign per name." },
        goal: { type: "string", description: "the objective in one sentence: what this run of content is FOR" },
        success_criterion: { type: "string",
          description: "REQUIRED. What would have to be observably TRUE for this to have worked, stated so it can be checked against the record. Name the observable and, where you can, the number and the date." },
        starts_on: { type: "string", description: "YYYY-MM-DD. Required — a campaign is a window." },
        ends_on: { type: "string", description: "YYYY-MM-DD. Omit for an open-ended run; scoring works either way." },
        channels: { type: "array", items: { type: "string" },
          description: "platform slugs this runs on: facebook, instagram, linkedin, twitter. Validated against the registered platforms; never empty." },
        note: { type: "string", description: "anything a later reader needs in order to judge the verdict" },
        human_quote: { type: "string", description: "the partner's literal words when he set this campaign" },
        confirm: { type: "boolean", description: "acknowledge an implausible window (deep backdate, or over a year long)" } },
        required: ["idempotency_key","name","goal","success_criterion","starts_on","channels"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "open-campaign", args, async () => {
        await require0066(c);

        const name = String(args.name || "").trim();
        const goal = String(args.goal || "").trim();
        const crit = String(args.success_criterion || "").trim();
        if (!name) throw new ToolError({ error: "name_required" });
        if (!goal) throw new ToolError({ error: "goal_required",
          hint: "one sentence: what is this run of content FOR?" });
        if (!crit) throw new ToolError({ error: "success_criterion_required",
          hint: "what would have to be observably true for this to have worked?" });
        // A criterion that merely restates the goal is the shape that lets a
        // campaign be declared a success on vibes. The check is deliberately crude
        // — it catches the copy-paste, not the merely vague — because a verb
        // cannot judge prose and pretending otherwise would be worse.
        if (crit.toLowerCase() === goal.toLowerCase())
          throw new ToolError({ error: "criterion_restates_goal",
            hint: "the success criterion must be CHECKABLE, and different from the objective: " +
                  "name the observable, and where you can the number and the date" });

        const existing = await c.query(
          "select id, status, starts_on, ends_on from campaign where lower(btrim(name))=lower($1)",
          [name]);
        if (existing.rows.length)
          throw new ToolError({ error: "campaign_exists", campaign: existing.rows[0],
            hint: "one campaign per name. Attach to the existing one, or pick a name that says " +
                  "what makes this run different. Nothing was written." });

        const channels = Array.isArray(args.channels)
          ? [...new Set(args.channels.map(x => String(x || "").trim().toLowerCase()).filter(Boolean))]
          : [];
        if (!channels.length) throw new ToolError({ error: "channels_required",
          hint: "a campaign with no channel cannot be measured; name at least one platform" });
        const live = await livePlatformSlugs(c);
        const unknown = channels.filter(ch => !live.includes(ch));
        if (unknown.length) throw new ToolError({ error: "unknown_channel", unknown,
          known_platforms: live,
          hint: "register a platform in marketing_subject before running a campaign on it — a " +
                "channel nobody registered is a channel no view will ever roll up" });

        const start = String(args.starts_on || "").trim();
        const end = args.ends_on ? String(args.ends_on).trim() : null;
        if (!/^\d{4}-\d{2}-\d{2}$/.test(start))
          throw new ToolError({ error: "bad_date", field: "starts_on", got: args.starts_on });
        if (end && !/^\d{4}-\d{2}-\d{2}$/.test(end))
          throw new ToolError({ error: "bad_date", field: "ends_on", got: args.ends_on });
        if (end && end < start) throw new ToolError({ error: "window_inverted", starts_on: start, ends_on: end,
          hint: "an end before its start would exclude every piece from every date filter" });

        // PLAUSIBILITY, NOT PROHIBITION. Backdating a campaign over content that
        // already published is exactly what this lane needs first — all 89 pieces
        // are historical — so a deep backdate ASKS rather than refuses.
        const today = new Date().toISOString().slice(0, 10);
        const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
        if (!args.confirm) {
          if (days(start, today) > 90)
            throw new ToolError({ error: "needs_confirm",
              reason: `starts_on is ${days(start, today)} days in the past`,
              hint: "backdating over already-published content is legitimate — resubmit with " +
                    "confirm:true if that is what you mean, and say so in note" });
          if (end && days(start, end) > 365)
            throw new ToolError({ error: "needs_confirm",
              reason: `the window is ${days(start, end)} days long`,
              hint: "a campaign longer than a year is usually a pillar wearing a campaign's " +
                    "clothes, and it will never be scorable. Resubmit with confirm:true if intended" });
        }

        const r = await c.query(
          `insert into campaign (name, goal, success_criterion, starts_on, ends_on, channels,
                               status, created_by, updated_by)
         values ($1,$2,$3,$4::date,$5::date,$6,'active',$7,$7)
         returning id, version, starts_on, ends_on`,
          [name, goal, crit, start, end, channels, actor.id]);
        const row = r.rows[0];

        // WHAT EVIDENCE THIS CAMPAIGN CAN EVEN HOPE FOR, returned at open time
        // rather than discovered at scoring time. On these channels, in this
        // window: how many placements exist and how many of them are measured. If
        // the answer is "42 placements, 0 measured", the caller learns NOW that
        // this campaign is unscorable, instead of six weeks from now.
        const ev = await c.query(
          `select count(*)::int as placements,
                count(*) filter (where measured)::int as measured,
                count(*) filter (where campaign_id is null)::int as unattached
           from v_placement_measurement
          where platform = any($1)
            and (live_at is null or live_at::date >= $2::date)
            and ($3::date is null or live_at is null or live_at::date <= $3::date)`,
          [channels, start, end]);

        await writeEvent(c, actor, "open-campaign", "campaign", row.id,
          { new: { name, goal, success_criterion: crit, starts_on: start, ends_on: end,
                   channels, status: "active" },
            human_quote: args.human_quote || null,
            agent_rationale: args.note || null,
            idempotency_key: args.idempotency_key });

        return { ok: true, campaign_id: row.id, name, version: row.version,
                 starts_on: row.starts_on, ends_on: row.ends_on, channels, status: "active",
                 success_criterion: crit,
                 evidence_available_in_window: {
                   placements: ev.rows[0].placements,
                   measured: ev.rows[0].measured,
                   unmeasured: ev.rows[0].placements - ev.rows[0].measured,
                   unattached_to_any_campaign: ev.rows[0].unattached },
                 note: ev.rows[0].measured === 0
                   ? "NOTHING in this window on these channels is measured today. This campaign " +
                     "is not scorable until measure-placement or the Blotato pull lands metrics — " +
                     "say that to the human rather than letting the campaign imply evidence it has not got."
                   : null };
      }),
    },

    "score-campaign": {
      discoveryOrder: 79,
      write: true,
      description: "Close a campaign with a VERDICT against the criterion it was opened with — the act that turns a pile of metrics into an answer. Separate from open-campaign on purpose (the same reason retire-rule is separate from activate-rule): a verdict is a judgment, it needs arguments opening must never accept, and it must be impossible to reach by accident while editing a date. THE REFUSAL THAT MATTERS: it will not accept 'worked' or 'did_not_work' over ZERO measured placements, and it asks for confirm below the coverage floor in system_config (marketing.scoring_min_coverage_pct). 73 of 89 placements in this system have never been measured, including all 42 on X, so a verdict formed over them would be a guess wearing a number. 'inconclusive' is always available and is the HONEST answer when the measurement never happened — use it rather than reaching for confirm. Snapshots the coverage into coverage_at_scoring so nobody can later re-read a thin verdict as a thick one. Requires base_version from a fresh read. Refuses a campaign that is already scored: changing a recorded verdict is a new fact, not an edit, and Joe rules on it.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        campaign: { type: "string", description: "campaign name (exact) or uuid" },
        base_version: { type: "integer", description: "from a fresh read; a conflict is a question for the human, never a retry" },
        verdict: { type: "string", enum: ["worked","did_not_work","inconclusive"],
          description: "measured against the success_criterion this campaign was OPENED with — the verb quotes it back to you in the response" },
        evidence: { type: "string",
          description: "REQUIRED. What in the record supports this verdict, in one or two lines. Name the numbers you read." },
        close: { type: "boolean", default: true,
          description: "false scores it but leaves status active — for a mid-flight read-out. The verdict is still recorded and still requires evidence." },
        human_quote: { type: "string", description: "the partner's literal words when he called it" },
        confirm: { type: "boolean", description: "acknowledge scoring below the coverage floor" } },
        required: ["idempotency_key","campaign","base_version","verdict","evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "score-campaign", args, async () => {
        await require0066(c);

        const evidence = String(args.evidence || "").trim();
        if (!evidence) throw new ToolError({ error: "evidence_required",
          hint: "a verdict with no evidence is an opinion the record will later quote as a fact" });

        const cam = await resolveCampaign(c, args.campaign);
        await versionGuard(c, "campaign", cam.id, args.base_version);
        if (cam.scored_at) throw new ToolError({ error: "already_scored",
          campaign_id: cam.id, verdict: cam.outcome_verdict, scored_at: cam.scored_at,
          outcome_note: cam.outcome_note,
          hint: "this campaign already carries a verdict. Changing it is a new fact, not an " +
                "edit — surface the existing verdict to the human and let him rule. Nothing was written." });

        const sc = (await c.query(
          `select placements, pieces, measured_placements, unmeasured_placements,
                coverage_pct, views_total, interactions_total
           from v_campaign_scorecard where campaign_id=$1`, [cam.id])).rows[0]
          || { placements: 0, pieces: 0, measured_placements: 0, unmeasured_placements: 0,
               coverage_pct: null, views_total: null, interactions_total: null };

        const measured = Number(sc.measured_placements || 0);
        const coverage = sc.coverage_pct === null ? null : Number(sc.coverage_pct);

        // THE HARD FLOOR, and confirm cannot cross it. A campaign over which
        // nothing at all was measured has no evidence of any kind, so 'worked' and
        // 'did_not_work' are both unsupportable — not merely thin. 'inconclusive'
        // is the true answer and is always allowed, which is why this refuses
        // instead of asking: an unmeasured campaign is exactly the case where a
        // confirm prompt would be clicked through.
        if (measured === 0 && args.verdict !== "inconclusive")
          throw new ToolError({ error: "no_measured_evidence",
            campaign_id: cam.id, placements: Number(sc.placements || 0),
            measured_placements: 0, unmeasured_placements: Number(sc.unmeasured_placements || 0),
            hint: "not one placement on this campaign carries a metric, so '" + args.verdict +
                  "' cannot be supported by anything. Record 'inconclusive' with the reason, or " +
                  "measure the placements first. An unmeasured placement is NOT a zero result." });

        // THE SOFT FLOOR: thin but real evidence asks rather than refuses.
        const floor = Number(await config(c, "marketing.scoring_min_coverage_pct", 50));
        if (!args.confirm && coverage !== null && coverage < floor && args.verdict !== "inconclusive")
          throw new ToolError({ error: "needs_confirm",
            reason: `only ${coverage}% of this campaign's placements are measured ` +
                    `(${measured} of ${sc.placements}); the floor is ${floor}%`,
            measured_placements: measured, unmeasured_placements: Number(sc.unmeasured_placements || 0),
            hint: "the unmeasured placements are not zeros, they are unknowns. Either measure " +
                  "more, record 'inconclusive', or resubmit with confirm:true and say in evidence " +
                  "why the measured subset is representative." });

        const snapshot = { placements: Number(sc.placements || 0), pieces: Number(sc.pieces || 0),
                           measured_placements: measured,
                           unmeasured_placements: Number(sc.unmeasured_placements || 0),
                           coverage_pct: coverage,
                           views_total: sc.views_total === null ? null : Number(sc.views_total),
                           interactions_total: sc.interactions_total === null ? null : Number(sc.interactions_total),
                           floor_pct: floor, confirmed_below_floor: !!args.confirm,
                           snapshot_at: new Date().toISOString() };

        const close = args.close !== false;
        await c.query(
          `update campaign set outcome_verdict=$1, outcome_note=$2, coverage_at_scoring=$3,
                             scored_at=now(), scored_by=$4, updated_by=$4,
                             status = case when $5 then 'closed' else status end
          where id=$6`,
          [args.verdict, evidence, JSON.stringify(snapshot), actor.id, close, cam.id]);

        await writeEvent(c, actor, "score-campaign", "campaign", cam.id, {
          field: "outcome_verdict",
          old: { status: cam.status, outcome_verdict: null },
          new: { status: close ? "closed" : cam.status, outcome_verdict: args.verdict,
                 coverage: snapshot },
          agent_rationale: evidence,
          human_quote: args.human_quote || null,
          idempotency_key: args.idempotency_key });

        return { ok: true, campaign_id: cam.id, name: cam.name,
                 verdict: args.verdict, status: close ? "closed" : cam.status,
                 scored_against_criterion: cam.success_criterion,
                 coverage: snapshot,
                 note: coverage !== null && coverage < 100
                   ? `${snapshot.unmeasured_placements} of ${snapshot.placements} placements on this ` +
                     "campaign were never measured. Say that beside the verdict — the totals above " +
                     "cover the measured subset only and are not the campaign's whole result."
                   : null };
      }),
    },

    "attach-to-campaign": {
      discoveryOrder: 80,
      write: true,
      description: "Bind published content to the campaign it belongs to — the link that makes 259 metrics answerable. Takes content by the handles a caller actually holds: the live post URL, the Blotato post id, or a placement/piece uuid. IT DOES NOT CREATE CONTENT, and that is a decision from evidence rather than a limitation: all 89 existing pieces were created by pipelines/pull_placement_metrics.py at publish time, keyed on placement.external_id, so a second birth path here would mint a duplicate the moment the ingest next ran. Content that is PLANNED but unpublished therefore has no record-layer home yet — say so plainly rather than inventing a row. ATOMIC: if any item cannot be resolved the WHOLE call refuses and nothing is written, because a partial attach that silently skips two items is a campaign that quietly under-reports its own content. A piece already attached to a DIFFERENT campaign is refused; moving one is `reattach` with base_version and a reason, one piece at a time, because re-pointing content rewrites what a past verdict was based on. Attaching content that published outside the campaign's window asks for confirm. The response always reports how many of the attached placements are actually MEASURED — usually the answer is few, and the caller needs to know that before quoting any total.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        campaign: { type: "string", description: "campaign name (exact) or uuid" },
        items: { type: "array", items: { type: "string" },
          description: "post URLs, Blotato post ids, placement uuids or content_piece uuids. Max 100." },
        reattach: { type: "boolean",
          description: "move a piece off another campaign. Requires exactly ONE item, a reason, and piece_base_version." },
        piece_base_version: { type: "integer", description: "content_piece.version, from a fresh read. reattach only." },
        reason: { type: "string", description: "REQUIRED for reattach: why this content belongs to a different campaign than the one it was filed under" },
        confirm: { type: "boolean", description: "acknowledge attaching content published outside the campaign window" } },
        required: ["idempotency_key","campaign","items"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "attach-to-campaign", args, async () => {
        await require0066(c);

        const items = Array.isArray(args.items) ? args.items.filter(x => String(x || "").trim()) : [];
        if (!items.length) throw new ToolError({ error: "items_required" });
        if (items.length > 100) throw new ToolError({ error: "too_many_items", count: items.length,
          hint: "max 100 per call — split the batch" });

        const cam = await resolveCampaign(c, args.campaign);
        if (cam.scored_at && !args.confirm)
          throw new ToolError({ error: "needs_confirm",
            reason: "this campaign is already scored; adding content changes what the recorded verdict covers",
            verdict: cam.outcome_verdict, scored_at: cam.scored_at,
            hint: "resubmit with confirm:true only if the verdict is still honest with this " +
                  "content in it, and expect to re-state the coverage" });

        // RESOLVE EVERYTHING FIRST, WRITE NOTHING UNTIL IT ALL RESOLVES. A partial
        // attach is the false-completeness failure in this domain: the campaign
        // would look complete while quietly missing whatever did not resolve.
        const resolved = [];
        const failed = [];
        for (const raw of items) {
          const ref = String(raw).trim();
          try {
            let piece = null, placement = null;
            if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(ref)) {
              const p = await c.query("select id, campaign_id, version, status from content_piece where id=$1", [ref]);
              if (p.rows.length) piece = p.rows[0];
            }
            if (!piece) {
              placement = await resolvePlacement(c, ref);
              const p = await c.query(
                "select id, campaign_id, version, status from content_piece where id=$1", [placement.piece_id]);
              piece = p.rows[0];
            }
            resolved.push({ ref, piece, placement });
          } catch (e) {
            failed.push({ ref, error: e.payload ? e.payload.error : "unresolved",
                          detail: e.payload ? e.payload.hint : String(e) });
          }
        }
        if (failed.length)
          throw new ToolError({ error: "unresolved_items", unresolved: failed,
            resolved_count: resolved.length,
            hint: "NOTHING was written. Every item must resolve, because a campaign that " +
                  "silently dropped two of its twelve posts under-reports its own content and " +
                  "nothing downstream can tell." });

        // Reattach is a different act with a different blast radius, so it has
        // different rules: one piece, a version guard, and a stated reason. The
        // plain attach path needs no version guard because it only ever writes
        // null -> value and the update is conditional on the null, so it cannot
        // clobber a concurrent writer — it loses the race visibly instead.
        const reattach = args.reattach === true;
        if (reattach) {
          if (resolved.length !== 1) throw new ToolError({ error: "reattach_is_one_at_a_time",
            count: resolved.length,
            hint: "moving content between campaigns rewrites what a past verdict was based on; " +
                  "do it deliberately, one piece at a time" });
          if (!String(args.reason || "").trim()) throw new ToolError({ error: "reason_required",
            hint: "say why this content belongs to a different campaign than the one it was filed under" });
          await versionGuard(c, "content_piece", resolved[0].piece.id, args.piece_base_version);
        }

        // Window plausibility, across the batch, once.
        if (!args.confirm) {
          const ids = resolved.map(r => r.piece.id);
          const out = await c.query(
            `select count(*)::int as n from placement p
            where p.piece_id = any($1) and p.live_at is not null
              and (p.live_at::date < $2::date
                   or ($3::date is not null and p.live_at::date > $3::date))`,
            [ids, cam.starts_on, cam.ends_on]);
          if (out.rows[0].n > 0)
            throw new ToolError({ error: "needs_confirm",
              reason: `${out.rows[0].n} of these placements published outside the campaign window ` +
                      `(${cam.starts_on} to ${cam.ends_on || "open"})`,
              hint: "either the window is wrong or this content is not part of this campaign. " +
                    "Resubmit with confirm:true if you mean it." });
        }

        const attached = [], already = [], conflicts = [];
        for (const r of resolved) {
          if (r.piece.campaign_id === cam.id) { already.push(r.ref); continue; }
          if (r.piece.campaign_id && !reattach) {
            const other = await c.query("select name from campaign where id=$1", [r.piece.campaign_id]);
            conflicts.push({ ref: r.ref, piece_id: r.piece.id,
                             currently_on: other.rows[0] ? other.rows[0].name : r.piece.campaign_id });
            continue;
          }
          const upd = reattach
            ? await c.query(
                "update content_piece set campaign_id=$1, updated_by=$2 where id=$3 returning id",
                [cam.id, actor.id, r.piece.id])
            : await c.query(
                "update content_piece set campaign_id=$1, updated_by=$2 where id=$3 and campaign_id is null returning id",
                [cam.id, actor.id, r.piece.id]);
          if (!upd.rows.length) { // lost a race with a concurrent attach
            const now = await c.query("select campaign_id from content_piece where id=$1", [r.piece.id]);
            conflicts.push({ ref: r.ref, piece_id: r.piece.id,
                             currently_on: now.rows[0] ? now.rows[0].campaign_id : null,
                             note: "another writer attached this piece first — nothing was overwritten" });
            continue;
          }
          attached.push({ ref: r.ref, piece_id: r.piece.id,
                          moved_from: reattach ? r.piece.campaign_id : null });
          await writeEvent(c, actor, reattach ? "attach-to-campaign:reattach" : "attach-to-campaign",
            "content_piece", r.piece.id,
            { field: "campaign_id",
              old: { campaign_id: r.piece.campaign_id },
              new: { campaign_id: cam.id, campaign: cam.name },
              agent_rationale: args.reason || null,
              idempotency_key: args.idempotency_key });
        }

        if (conflicts.length)
          throw new ToolError({ error: "already_on_another_campaign", conflicts,
            hint: "a piece belongs to one campaign. Use reattach (one item, a reason and " +
                  "piece_base_version) if it really moved. This call is rolled back whole." });

        // THE COVERAGE LINE. Attaching content does not measure it, and a caller
        // who reads only "12 attached" will quote totals that cover almost none of
        // them. As of 2026-08-02 that is 73 placements out of 89.
        const cov = (await c.query(
          `select count(*)::int as placements,
                count(*) filter (where measured)::int as measured
           from v_placement_measurement where campaign_id=$1`, [cam.id])).rows[0];

        return { ok: true, campaign_id: cam.id, campaign: cam.name,
                 attached: attached.length, attached_items: attached,
                 already_attached: already,
                 campaign_now_covers: {
                   placements: cov.placements, measured: cov.measured,
                   unmeasured: cov.placements - cov.measured },
                 note: cov.measured < cov.placements
                   ? `${cov.placements - cov.measured} of this campaign's ${cov.placements} ` +
                     "placements carry NO metrics. They are unmeasured, not zero — do not " +
                     "average or total over them as if they scored nothing."
                   : null };
      }),
    },

    "measure-placement": {
      discoveryOrder: 81,
      write: true,
      description: "Record what one placement actually did — including, and especially, that it could not be measured. THE RULE THIS VERB EXISTS FOR: an unmeasured placement must stay VISIBLY unmeasured and must never read as a zero. 73 of 89 placements have never been measured, among them all 42 on X, and a reader who totals metrics by platform is currently handed 0 for X, which is a lie about performance rather than a fact about it. So `unavailable:true` with a reason is a first-class outcome here, exactly the way record-finding's found:false is: it lands a real placement_measurement row saying we looked and there was nothing, which is a different fact from nobody having looked. USE THIS FOR MEASUREMENTS THE SCHEDULED PULL CANNOT SEE — a figure read off a platform's own dashboard, an off-platform outcome (a DM, a reply, a consult that traced back to a post), or a confirmed 'this platform returns no analytics for us'. It REFUSES source 'blotato_api': that provenance belongs to pipelines/pull_placement_metrics.py, and a hand-written row claiming it would make the pull's output untrustworthy — use 'blotato_ui_manual', 'platform_native', 'joe_observed' or similar. A genuine measured zero IS allowed and is common (173 of 259 existing metric values are 0), but a payload where EVERY value is zero asks for confirm, because that is the shape an empty API response takes and it is precisely how an unmeasured post becomes a measured zero. Metrics are snapshots keyed (placement, kind, observed_at), so re-recording the same instant is a no-op rather than a duplicate.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        placement: { type: "string", description: "the live post URL, the Blotato post id, or the placement uuid" },
        source: { type: "string",
          description: "REQUIRED. Where the number came from: 'blotato_ui_manual', 'platform_native', 'joe_observed', a URL. 'blotato_api' is REFUSED — that source string belongs to the scheduled pull." },
        metrics: { type: "object",
          description: "{kind: number}. Kinds are stored verbatim as the source names them, snake_cased — views_count, reach_count, likes_count, comments_count, shares_count, saves_count, follows_count, interactions_sum, profile_visits_count, profile_activity_count. Do NOT map a platform's word onto a different platform's word; an equivalence nobody ruled is a wrong number later." },
        unavailable: { type: "boolean",
          description: "true records that measurement was ATTEMPTED and returned nothing. Requires reason. This is the honest alternative to silence, and to a zero." },
        reason: { type: "string", description: "REQUIRED with unavailable: why there is no number — 'the platform exposes no analytics for this account', 'post deleted', 'API returns 404'." },
        observed_at: { type: "string", description: "when the number was read (ISO); defaults to now. The snapshot key — pass the real read time, not the time you typed it." },
        note: { type: "string", description: "anything a later reader needs" },
        confirm: { type: "boolean", description: "acknowledge an all-zero payload or an out-of-band value" } },
        required: ["idempotency_key","placement","source"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "measure-placement", args, async () => {
        await require0066(c);

        const source = String(args.source || "").trim();
        if (!source) throw new ToolError({ error: "source_required",
          hint: "a number with no provenance is a rumour; say where you read it" });
        if (source.toLowerCase() === "blotato_api")
          throw new ToolError({ error: "reserved_source", source,
            hint: "'blotato_api' is the scheduled pull's provenance " +
                  "(pipelines/pull_placement_metrics.py). A hand-written row wearing it would " +
                  "make every API row unverifiable. Use 'blotato_ui_manual' for a figure read " +
                  "off Blotato's own screen, 'platform_native' for the platform's dashboard, or " +
                  "'joe_observed' for something Joe saw happen." });

        const unavailable = args.unavailable === true;
        const metrics = (args.metrics && typeof args.metrics === "object") ? args.metrics : null;
        const kinds = metrics ? Object.keys(metrics).filter(k => metrics[k] !== undefined && metrics[k] !== null) : [];

        // THE TWO REFUSALS THAT KEEP SILENCE OUT OF THE RECORD.
        if (unavailable && kinds.length)
          throw new ToolError({ error: "ambiguous_measurement",
            hint: "unavailable:true says there was nothing to record. Send the metrics, or send " +
                  "the unavailability — never both in one act." });
        if (!unavailable && !kinds.length)
          throw new ToolError({ error: "nothing_to_record",
            hint: "pass metrics{}, or pass unavailable:true with a reason. An empty call would " +
                  "leave this placement looking exactly like the 73 nobody has ever measured, " +
                  "which is the one outcome this verb exists to prevent." });
        if (unavailable && !String(args.reason || "").trim())
          throw new ToolError({ error: "reason_required",
            hint: "'no data' with no reason cannot be acted on. Say whether the platform gives " +
                  "us nothing, the post is gone, or the pull has simply never run — those lead " +
                  "to three different next moves." });

        const pl = await resolvePlacement(c, args.placement);
        const observedAt = args.observed_at || null;

        if (unavailable) {
          const att = await c.query(
            `insert into placement_measurement (placement_id, attempted_at, source, outcome, reason,
                                              metric_kinds, note, recorded_by)
           values ($1, coalesce($2::timestamptz, now()), $3, 'unavailable', $4, '{}', $5, $6)
           on conflict (placement_id, source, attempted_at) do nothing
           returning id, attempted_at`,
            [pl.id, observedAt, source, String(args.reason).trim(), args.note || null, actor.id]);
          await writeEvent(c, actor, "measure-placement", "placement", pl.id, {
            occurred_at: observedAt,
            field: "measurement",
            new: { outcome: "unavailable", source, reason: String(args.reason).trim() },
            agent_rationale: "attempted and returned nothing — recorded so it is not mistaken for zero",
            idempotency_key: args.idempotency_key });
          return { ok: true, placement_id: pl.id, platform: pl.platform,
                   outcome: "unavailable", source, reason: String(args.reason).trim(),
                   recorded: !!att.rows.length,
                   measured: false,
                   note: "This placement is now recorded as ATTEMPTED AND UNMEASURED. It still " +
                         "reports measured:false in v_placement_measurement and it still has no " +
                         "number — that is the point. Do not read it as a zero." };
        }

        // ── values: validated one at a time, and the refusals are specific ──────
        const band = await config(c, "marketing.metric_value_band", { max: 1000000 });
        const clean = {};
        let allZero = true;
        for (const k of kinds) {
          const key = String(k).trim();
          if (!/^[a-z][a-z0-9_]*$/.test(key))
            throw new ToolError({ error: "bad_metric_kind", kind: k,
              hint: "kinds are the source's own names, snake_cased: views_count, reach_count, " +
                    "interactions_sum. Never invent an equivalence between two platforms' words." });
          const v = Number(metrics[k]);
          if (!Number.isFinite(v))
            throw new ToolError({ error: "bad_metric_value", kind: key, got: metrics[k],
              hint: "values are numbers. A missing number is not 0 — omit the kind entirely, or " +
                    "record the whole placement as unavailable." });
          if (v < 0)
            throw new ToolError({ error: "negative_metric", kind: key, got: v,
              hint: "no engagement count is negative; this is a sign error or a delta pasted as a total" });
          if (!args.confirm && Number(band.max) && v > Number(band.max))
            throw new ToolError({ error: "needs_confirm",
              reason: `${key} = ${v} exceeds the plausibility band (${band.max})`,
              hint: "the largest real value in placement_metric on 2026-08-02 was 845,877 " +
                    "(view_time_ms_sum). Check for a units error, then resubmit with confirm:true if real." });
          if (v !== 0) allZero = false;
          clean[key] = v;
        }

        // THE ALL-ZERO GATE, and the number behind it. Real zeros are ordinary: 173
        // of 259 existing metric values are 0. But across 26 real analytics
        // snapshots, ZERO of them were all-zero — an entirely zero payload is not
        // what real data looks like, it is what an empty API response looks like.
        // Writing one turns an unmeasured placement into a measured zero, which is
        // exactly the false completeness this whole verb guards against.
        if (allZero && !args.confirm)
          throw new ToolError({ error: "needs_confirm",
            reason: `every one of the ${Object.keys(clean).length} values is 0`,
            hint: "0 of 26 real snapshots in this system were all-zero, so this is far more " +
                  "likely an empty response than a measured nothing. If the platform genuinely " +
                  "returned no data, use unavailable:true with a reason — that keeps the " +
                  "placement UNMEASURED instead of recording it as a zero result. Resubmit with " +
                  "confirm:true only if the post truly earned zero of everything." });

        let landed = 0, unchanged = 0;
        for (const [kind, value] of Object.entries(clean)) {
          const r = await c.query(
            `insert into placement_metric (placement_id, observed_at, kind, value, source)
           values ($1, coalesce($2::timestamptz, now()), $3, $4, $5)
           on conflict (placement_id, kind, observed_at) do nothing returning kind`,
            [pl.id, observedAt, kind, value, source]);
          if (r.rows.length) landed++; else unchanged++;
        }

        await c.query(
          `insert into placement_measurement (placement_id, attempted_at, source, outcome, reason,
                                            metric_kinds, note, recorded_by)
         values ($1, coalesce($2::timestamptz, now()), $3, 'recorded', null, $4, $5, $6)
         on conflict (placement_id, source, attempted_at) do nothing`,
          [pl.id, observedAt, source, Object.keys(clean), args.note || null, actor.id]);

        // The same status catch-up the scheduled pull performs, for the same
        // reason: a piece whose placements gained metrics is 'measured'. Guarded on
        // the current status so it can never walk a retired or rejected piece back
        // into the live funnel.
        const promoted = await c.query(
          `update content_piece set status='measured', updated_by=$1
          where id=$2 and status in ('live','scheduled') returning id`,
          [actor.id, pl.piece_id]);

        await writeEvent(c, actor, "measure-placement", "placement", pl.id, {
          occurred_at: observedAt,
          field: "metrics",
          new: { outcome: "recorded", source, kinds: Object.keys(clean), values: clean },
          agent_rationale: args.note || null,
          idempotency_key: args.idempotency_key });

        return { ok: true, placement_id: pl.id, platform: pl.platform, piece_id: pl.piece_id,
                 outcome: "recorded", source, measured: true,
                 metrics_written: landed, metrics_already_present: unchanged,
                 piece_marked_measured: !!promoted.rows.length,
                 // Present ONLY when the handle did not match a stored key. The
                 // caller is recording a number against a row identified by a
                 // derived publish time, and that is worth seeing.
                 ...(pl._resolved_by ? { resolved_by: pl._resolved_by,
                                         resolved_note: pl._resolved_note,
                                         resolved_url: pl.url } : {}),
                 note: unchanged && !landed
                   ? "every kind already had a row at this exact observed_at — nothing changed. " +
                     "Pass the real read time if this was a new pull."
                   : null };
      }),
    },
  };
}

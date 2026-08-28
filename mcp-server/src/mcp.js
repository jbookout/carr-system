// CARR MCP server — the MCP transport (stateless streamable HTTP:
// initialize / tools/list / tools/call). Verb surface unchanged.
//
// AUTH IS NOT DONE HERE. This handler is mounted as the OAuthProvider's
// `apiHandler` for `/mcp`, so it only ever runs on a request whose token the
// provider already validated. The actor comes from `ctx.props` — which the
// provider decrypts from the grant — and NEVER from a header this file parses
// or from the request payload. One way, and only one, to arrive at those props:
// an OAuth grant issued after a verified Google identity passed the allow-list.
// The migration-only PARTNER_TOKENS bearer was retired 2026-08-03.
//
// NO SEND CAPABILITY EXISTS OR WILL EXIST IN THIS WORKER.

import { neon, Pool } from "@neondatabase/serverless";
import { TOOLS, ToolError, executeRegisteredTool, auditIdentity, assertNoCallerAuthorityFields } from "./tools.js";
import { partnerAuthoritySlugForActor } from "./partner-authority.js";
import { actorFromProps, authorizationClassForActor, organizationTenantForActor, personalScopeForActor } from "./identity.js";
import { scheduleFailureRecord, rpcInternalErrorFailureClass, actorUnresolvedFailureClass, RPC_INTERNAL_ERROR_CODE } from "./trace.js";

const JSON_HEADERS = { "content-type": "application/json" };
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

// Transaction-local actor context for SECURITY DEFINER functions that must
// derive authorship from the authenticated server principal rather than accept
// it in a caller payload.  The verified-human setting is deliberately empty
// for sponsored and machine actors; Tour entrance verification checks it as a
// second, narrower boundary.
export async function setWriterActorContext(client, actor) {
  const authorizationClass = actor?.authorization_class || authorizationClassForActor(actor);
  const verifiedHumanSlug = actor?.human === true &&
    authorizationClass === "verified_partner" ? actor.slug : "";
  await client.query(
    "select set_config('carr.acting_actor_slug',$1::text,true), " +
    "set_config('carr.verified_human_actor_slug',$2::text,true) /* writer-actor-context */",
    [actor.slug, verifiedHumanSlug],
  );
}

const PROTOCOL = "2025-06-18";
const RULE_DELIVERY_RAIL = ` RULE DELIVERY: use only exact canonical pack names from standing-context rule_delivery.pack_index. A name in packs_not_found is unknown and is NOT loaded. If observed work enters a pack absent from rule_delivery.declared_packs, call standing-context again with that canonical pack and read the result before acting. Shadow mode records drift without blocking, but does not waive this recall protocol.`;

// ---------- capability profiles (2026-08-02) ----------
//
// WHAT THIS IS, AND WHAT IT IS NOT. This is a BLAST-RADIUS REDUCER for surfaces
// a human configured, not an authorization boundary. Authorization is, and stays,
// the OAuth grant plus `humanOnly` — a caller who wants the full surface simply
// omits the parameter, and that is fine, because the threat this addresses is not
// a hostile caller. It is an UNATTENDED OR DELEGATED session doing something
// destructive by mistake: a 2am scheduled run whose model tier cannot be pinned,
// or a Haiku/Sonnet subagent told to stay mechanical while technically holding
// `reassign-deal` and `confirm-merge`. The profile is set in that surface's MCP
// config by a human, once, and the model inside the session cannot widen it.
//
// SCOPED BY RISK OF THE SESSION, NEVER BY WHICH PARTNER HOLDS THE GRANT.
// Joe and Dell both get `full` on interactive sessions. Dell teaches rules and
// trains workflows exactly as Joe does, and a permission hierarchy between the
// two partners would contradict the two-writer, one-book design (taught rule
// b42e217e, 2026-08-02). A newer partner needs better defaults and clearer verb
// descriptions, never fewer capabilities.
//
// Selected per-request: POST /mcp?profile=capture
export const PROFILES = {
  // Everything. The default, and what both partners' interactive sessions use.
  full: null,

  // Reads plus the capture verbs that are safe to run unattended: each one is
  // additive, carries an idempotency key, and cannot destroy or re-point an
  // existing record. This is the profile for scheduled runs and delegated seats
  // — it is deliberately ABLE TO WRITE, because the capture gap (0 of 290 vendors
  // and 0 of 207 leads with a last touch, found 2026-08-02) is not fixed by a
  // profile that can only read.
  capture: new Set([
    "log-activity", "stamp-touch", "add-loop", "update-loop",
    "set-next-action", "complete-action", "add-critical-date", "record-finding",
    "record-signal", "record-branch-evidence",
    "report-problem",
    "propose-outcome-feedback",
    // record-defect (0103) belongs in the NARROWEST writing profile, not the widest.
    // An unattended run is exactly where an error is least likely to be caught by a
    // human, so it is the seat that most needs to be able to file its own — and the
    // verb is purely additive, keyed, and cannot touch any other record.
    "record-defect",
    // open-incident (2026-08-23) is here for record-defect's reason exactly, and
    // it is the verb that reason was really written for: a 2am scheduled run is
    // the seat most likely to be the FIRST thing to see a service fail, and the
    // least likely to have a human beside it. It is additive and keyed, it
    // de-dupes onto an open fingerprint rather than growing the pile, and it
    // structurally cannot touch the columns that close an incident — closing is
    // partner authority (close-incident is humanOnly and refuses anything that
    // is not Joe or Dell), so the widest thing this profile can do with the
    // operational ledger is say truthfully that something broke.
    "open-incident",
  ]),

  // AWAY MODE, added 2026-08-03 on Joe's ruling. The scheduled CEO session that
  // runs the book while he is gone, plus the system-administrator seat it
  // convenes. Wider than `capture` because away mode's whole purpose is that
  // DEALS KEEP MOVING rather than merely not breaking — a profile that cannot
  // advance a deal or produce a document delivers a healthy system and a dead
  // week. Narrower than `full` because rule b42e217e still binds: unattended
  // runs get a narrow profile, since nobody is watching and a scheduled run's
  // model cannot reliably be pinned.
  //
  // THE LINE: it may RECORD, ADVANCE and DRAFT. It may not DESTROY, RE-POINT
  // OWNERSHIP, EDIT IDENTITY, or CREATE A PARTY.
  //
  // Each exclusion earns its place rather than being cautious by default.
  // confirm-merge destroys a record irreversibly and is the one write no
  // unattended seat should ever hold. reassign-deal and set-lead move ownership,
  // which is a matter between two humans and never a machine's call while one of
  // them is unreachable. add-party, new-lead, new-client and new-vendor create
  // records, and a duplicate created here can only be cleaned up by
  // confirm-merge, which this profile deliberately lacks — so it could make a
  // mess it cannot clean. update-vendor edits identity fields, which rule
  // 5d44d3f3 says are never changed on research alone. promote-pool and
  // register-template are judgment and config. The rule verbs are human-gated by
  // design and appear here only to say they were considered.
  //
  // link-parties is excluded on purpose and it is the closest call: it is purely
  // additive, but an intro-graph edge is a CLAIM ABOUT A RELATIONSHIP BETWEEN
  // TWO PEOPLE, and asserting one while the only humans who could vouch for it
  // are away is exactly the wrong direction of error.
  away: new Set([
    // everything capture holds
    "log-activity", "stamp-touch", "add-loop", "update-loop",
    "set-next-action", "complete-action", "add-critical-date", "record-finding",
    "record-signal", "record-branch-evidence", "record-defect", "open-incident",
    // plus: close what it opened, advance the book, draft, and keep the record honest
    "close-loop", "update-deal", "add-premises", "record-counter",
    "prepare-document", "update-document-status",
    "log-decision", "update-decision",
    "open-investigation", "open-investigation-branch",
    "adjudicate-investigation-branch", "close-investigation",
    // plus: the marketing lane, since social is carved out of Dell's review
    "open-campaign", "score-campaign", "attach-to-campaign", "measure-placement",
  ]),

  // Observation only. No writes at all.
  read: new Set(),

  // PROBE (loop #192, 2026-08-06). The smoke suite's identity, re-credited
  // after the legacy PARTNER_TOKENS bearer it used to run under was retired
  // 2026-08-03 (#111b). This profile is NOT selected by ?profile= like the
  // others above — it is the ONLY profile a caller cannot ask for. It is
  // forced in dispatch() below whenever the actor authenticated via a
  // PROBE_TOKENS bearer (see index.js), and ?profile= is ignored entirely for
  // that actor. That is the server-side lock: the token cannot be asked for
  // more than it was provisioned for, no matter what the request says.
  //
  // THE WRITE SET IS EXACTLY THE VERBS THE SUITE REPLAYS UNDER A FROZEN
  // IDEMPOTENCY KEY, never a verb it would have to mint a fresh key for:
  //   - log-activity: three fixtures — the ORDER 18 addendum write probe
  //     (smoke-write-probe-permanent), the ORDER 34 auto-edge probe
  //     (smoke-links-probe-permanent), the ORDER 36 analysis probe
  //     (smoke-analysis-probe-permanent).
  //   - set-next-action + complete-action: the ORDER 19 completion-path pair
  //     on the AMA Law Office fixture (smoke-ball-probe-permanent /
  //     smoke-complete-probe-permanent).
  // Every one of those keys already exists in `tool_call` from years of runs
  // under a human actor, and withEnvelope() in tools.js keys its replay
  // lookup on idempotency_key + request hash alone, never on the calling
  // actor — so a probe call against any of them can only ever replay the
  // stored response, never insert a second row. No other write verb is safe
  // to hand this token: the 0066 marketing negative-answer probes vary their
  // idempotency key on purpose (a refusal stores no row, so editing them
  // never causes key_reuse), which means a probe call against them would be a
  // LIVE write attempt, not a replay — exactly what this profile exists to
  // rule out. Those checks self-skip instead: they are gated behind a
  // tools/list capability check, and tools/list is itself profile-filtered,
  // so a probe-authenticated caller never even sees those verbs are there.
  probe: new Set(["log-activity", "set-next-action", "complete-action"]),

  // REVIEWER (Automatic Review Council, Codex lane, 2026-08-06). The write set
  // is EXACTLY `record-finding` — nothing else, ever. Like `probe`, this
  // profile is NOT selected by ?profile= — it is forced in dispatch() below
  // whenever the actor authenticated via a REVIEW_TOKENS bearer (see
  // reviewActorFor in index.js), and ?profile= is ignored entirely for that
  // actor. Reads need no entry here at all: allowedIn() already grants every
  // read verb in every profile (`if (!tool.write) return true;`), so "ALL
  // reads + exactly record-finding" falls straight out of that existing rule
  // plus this one-verb write set — no separate read allowlist to keep in sync.
  //
  // WHY record-finding AND NOTHING ELSE. A reviewer's whole job is to land a
  // structured, sourced opinion beside a record or a commit — which is
  // precisely what record-finding is for (subject_kind defaults to resolving
  // a C-127/L-204/deal ref, or a work-order/record ref passed as the subject;
  // source is required, so every reviewer finding carries its provenance: the
  // model, the commit sha, the contract version — exactly like a human
  // researcher's URL). It is NOT humanOnly, so a machine actor can call it.
  // No other write verb belongs here: a reviewer reads code and a record, and
  // reports what it found. It never advances a deal, never drafts a document,
  // never touches a party or a rule, and never gets a wider grant by asking —
  // the profile is the whole point, not a suggestion the model could widen by
  // passing a different verb name (callTool's allowedIn() check enforces this
  // at call time, same as every other profile).
  reviewer: new Set(["record-finding"]),

  // HERMES (R0 runtime evaluation, 2026-08-16). The write set is EMPTY, which
  // is the whole design: the 2026-08-12 frontier council cleared Hermes for a
  // read-only, synthetic evaluation, and live CARR data behind a second Joe
  // decision. Reads need no entry, exactly as with `reviewer` — allowedIn()
  // already grants every read verb in every profile.
  //
  // It is a SEPARATE ENTRY rather than a reuse of `read` so the profile a
  // Hermes call runs under is legible in tool_call rows and in the session
  // notice, rather than being indistinguishable from a human who happened to
  // pass ?profile=read. Same reason `probe` and `reviewer` are their own
  // entries when both are "reads plus a tiny write set".
  //
  // Like those two, it is forced in dispatch() on a HERMES_TOKENS bearer match
  // and ?profile= is ignored for that actor.
  //
  // R0 SHIPPED THIS EMPTY, on 2026-08-16, and it stayed empty for a few hours.
  // Joe's grant the same day: give it the additive write set so it can file the
  // work he tells it to file. The engineering path he asked about first (create
  // a durable work request, dispatch it to Claude Code or Codex) stays shut —
  // the work_request store exists as of migration 0114 but its transition
  // guards do not, and the council made a canonical action-risk registry
  // blocking before any of it.
  //
  // THE SET IS A STRICT SUBSET OF `capture`, ASSERTED BY TEST rather than by
  // this comment. Written out rather than spread from capture on purpose: a
  // spread means a verb added to capture for a scheduled run's benefit lands
  // silently in a persistent daemon's hands, and "silently" is the whole
  // problem with this class of actor. Widening here is an edit somebody makes
  // and reviews.
  //
  // WHAT IT DELIBERATELY EXCLUDES, beyond everything outside capture:
  // record-signal and record-branch-evidence, which are investigation
  // machinery. An investigation is a chain of reasoning a human is steering,
  // and a runtime dropping evidence into one mid-flight changes what the
  // adjudicator sees without being asked.
  //
  // Nothing here can destroy, re-point, or create a party, advance a deal,
  // touch a rule, draft a client document, or send anything. Every verb is
  // additive and idempotency-keyed.
  hermes: new Set([
    "log-activity", "stamp-touch", "add-loop", "update-loop",
    "set-next-action", "complete-action", "add-critical-date", "record-finding",
    "record-defect",
    // A runtime-only door: exact queue receipts, fixed provenance, no raw room prose.
    "project-room-queue",
  ]),

  // HERMES CoS (loop #459). This is a separate server-locked capability door,
  // never a request-side profile override. It inherits the ordinary Hermes
  // business-write set and adds exactly the two bounded brief/premises verbs.
  // Keep this explicit rather than spreading the set so a future ordinary
  // Hermes write cannot silently land in the CoS credential.
  "hermes-cos": new Set([
    "log-activity", "stamp-touch", "add-loop", "update-loop",
    "set-next-action", "complete-action", "add-critical-date", "record-finding",
    "record-defect", "project-room-queue", "update-deal", "add-premises",
  ]),
};

const PROFILE_NOTICE = {
  capture:
    "\n\n<notice>This session runs on the CAPTURE profile: reads, plus additive capture verbs " +
    "only. Verbs that re-point, merge, retire or delete a record are not available here. This is " +
    "intentional — the session is unattended or delegated. Do not try to work around it; report " +
    "what you would have done and let a partner's interactive session do it.</notice>",
  away:
    "\n\n<notice>This session runs on the AWAY profile: Joe is not reachable. You may record, " +
    "advance and draft. You may NOT merge, re-point ownership, edit an identity field, create a " +
    "party, or assert a relationship — those verbs are absent by design, not by oversight, and " +
    "the absence is the control. Anything client-facing or binding goes to DELL for review before " +
    "it leaves the system (rule 1e62c007); anything involving money waits for Joe and does not " +
    "proceed. When unsure, convene the red team with distinct lenses and a seat that re-runs the " +
    "evidence rather than arguing the conclusion (rule 81709f57). A queue Joe returns to is " +
    "recoverable; a wrong write made while nobody was watching is not.\n\n" +
    "BLOCKED WORK IS FILED, NEVER DROPPED. When this profile lacks the verb you need — a party " +
    "to create, a merge to confirm, an owner to re-point — you MUST open a loop describing what " +
    "needs doing, for whom, and the evidence you already gathered. `add-loop` is in this profile " +
    "for exactly that reason. The missing verb means 'not by you, not now'; it never means 'this " +
    "did not need to happen.' A blocked action that leaves no loop is lost work, and lost work is " +
    "worse than the write you were stopped from making.</notice>",
  read:
    "\n\n<notice>This session runs on the READ profile: no write verb is available. This is " +
    "intentional. Do not try to work around it; report what you would have written.</notice>",
  probe:
    "\n\n<notice>This session runs on the PROBE profile: reads, plus exactly the three write " +
    "verbs the smoke suite replays under a frozen idempotency key (log-activity, set-next-action, " +
    "complete-action). Every other write verb refuses with not_in_profile. This profile is locked " +
    "server-side by a PROBE_TOKENS bearer, not by ?profile=, and cannot be widened by this token " +
    "under any request. This is the smoke-probe machine actor, never a human seat.</notice>",
  reviewer:
    "\n\n<notice>This session runs on the REVIEWER profile: reads, plus exactly one write verb, " +
    "record-finding. Every other write verb refuses with not_in_profile — no advancing a deal, no " +
    "drafting a document, no touching a party or a rule. This profile is locked server-side by a " +
    "REVIEW_TOKENS bearer, not by ?profile=, and cannot be widened by this token under any request. " +
    "This is the Automatic Review Council's Codex-reviewer machine actor, never a human seat. Land " +
    "your findings as one or more record-finding calls: source is required on every one (name the " +
    "model, the commit sha, and the contract version), and a clean run with nothing to flag is still " +
    "a finding worth recording (found:false), not silence.</notice>",
  hermes:
    "\n\n<notice>This session runs on the HERMES profile: every read verb, plus exactly nine " +
    "additive write verbs — log-activity, stamp-touch, add-loop, update-loop, set-next-action, " +
    "complete-action, add-critical-date, record-finding, record-defect, and one runtime-only " +
    "shape-checked queue projection door. Every other write verb " +
    "refuses with not_in_profile: no advancing a deal, no creating a party, no merging, no touching " +
    "a rule, no drafting a client document, and there is no send verb in this system at all. This " +
    "profile is locked server-side by a HERMES_TOKENS bearer, not by ?profile=, and cannot be " +
    "widened by this token under any request. You carry Joe's personal brain and never Dell's. " +
    "File what he tells you to file; for anything outside those nine verbs, say what you would have " +
    "written and hand it back for a human.</notice>",
  "hermes-cos":
    "\n\n<notice>This session runs on the HERMES CoS profile: the ordinary Hermes business-write set " +
    "plus exactly update-deal (deal_type, segment, city and lane only) and add-premises against " +
    "existing parties only. Its server-issued door cannot be widened by ?profile= or caller fields. " +
    "It may hand a next action to Joe, its verified sponsor, but never Dell; created_by remains the " +
    "Hermes runtime. Phase, outcome, close/value, identity, merge, teach, authority-only and send " +
    "operations remain refused.</notice>",
};

/** Resolve ?profile= to a name, defaulting to full. An unknown value fails CLOSED to read. */
function profileFor(request) {
  const raw = new URL(request.url).searchParams.get("profile");
  if (!raw) return "full";
  return Object.prototype.hasOwnProperty.call(PROFILES, raw) ? raw : "read";
}

/**
 * The profile an authenticated actor runs under, decided server-side.
 *
 * Extracted from dispatch() on 2026-08-16 so the three locks are provable by
 * `node --test` rather than only by deploying: dispatch() itself needs a
 * Worker, and a lock nothing can exercise before a deploy is a lock nobody
 * should trust. The behaviour is unchanged — this is the same expression that
 * lived inline, with the Hermes arm added.
 *
 * A locked actor's flag (probe / review / hermes) is set in exactly one place
 * each, in index.js, on a bearer match against a Worker secret. Nothing a
 * caller sends on the wire can set one, which is what makes ?profile= a
 * voluntary limiter for everyone else and a no-op for these three.
 */
export function profileForActor(actor, request) {
  if (actor?.probe) return "probe";
  if (actor?.review) return "reviewer";
  if (actor?.hermesCos === true && actor?.via === "hermes-cos-token") return "hermes-cos";
  if (actor?.hermes) return "hermes";
  return profileFor(request);
}

export function allowedIn(profile, name, tool) {
  if (profile === "full") return true;
  if (tool.fullOnly) return false;            // sensitive operational reads stay off probe/reviewer/read
  if (!tool.write) return true;              // reads are allowed in every profile
  return PROFILES[profile].has(name);
}

// update-deal is globally allowed for interactive sessions, including its
// closed deal_type vocabulary. The CoS door is field-locked separately: one
// extra field refuses the whole call rather than applying a silent subset.
export const HERMES_COS_DEAL_FIELDS = Object.freeze(["deal_type", "segment", "city", "lane"]);

export function hermesCosDealFieldRefusal(profile, name, args) {
  if (profile !== "hermes-cos" || name !== "update-deal") return null;
  const fields = args?.fields;
  if (!fields || typeof fields !== "object" || Array.isArray(fields)) return null;
  const refused = Object.keys(fields).filter((key) => !HERMES_COS_DEAL_FIELDS.includes(key));
  return refused.length ? refused : null;
}

export function hermesCosPremisesRefusal(profile, name, args) {
  return profile === "hermes-cos" && name === "add-premises" &&
    Array.isArray(args?.ownership) && args.ownership.some((row) => row && row.new_party);
}

function toolList(profile = "full") {
  return Object.entries(TOOLS)
    .filter(([name, t]) => allowedIn(profile, name, t))
    .map(([name, t]) => ({
      name,
      description: t.description + (profile === "full" ? "" : (PROFILE_NOTICE[profile] || "")),
      inputSchema: t.inputSchema,
      // Machine-readable danger signal, so a client's permission layer and the
      // model can tell `find` from `reassign-deal` without parsing prose.
      annotations: {
        readOnlyHint: !t.write,
        destructiveHint: Boolean(t.write),
        idempotentHint: true,               // every write runs the idempotency envelope
        openWorldHint: false,
      },
    }));
}

// ---------- read-call recording (Phase 1, 0108) ----------
//
// withEnvelope() in tools.js is the ONLY writer of tool_call, and it only ever
// runs inside a WRITE handler body. Every read verb — including the one every
// session calls at boot, standing-context — reached executeRegisteredTool
// DIRECTLY below with no record left anywhere. That made "did a session boot
// from the store, and whose?" unanswerable except by a human running a manual
// test, which is exactly the question the Dell 2026-08-21 cutover needs
// answered passively. See migrations/0108_tool_read_call.sql for why this is
// a SIBLING table rather than a reuse of tool_call (idempotency_key is a
// primary key with no read equivalent; response is NOT NULL and reads must
// never carry a response body).
//
// readCallInsertSQL is pure — no DB, no env, no ctx — so it is unit-testable
// on its own (mcp-server/test/tool-read-call.test.mjs). It builds the exact
// statement recordReadCall sends, and by construction it can only ever see
// (actor, verb, ok, errorKind): there is no parameter through which an
// argument value or a response body could reach it.
export function readCallInsertSQL(actor, verb, ok, errorKind) {
  const identity = auditIdentity(actor);
  return {
    text: `insert into tool_read_call (verb, actor_slug, actor_id, ok, error_kind, via, client_id,
             organization_tenant_id, sponsoring_human_slug, personal_scope, authorization_class)
           values ($1, $2, (select id from actor where slug=$2), $3, $4, $5, $6, $7, $8, $9, $10)`,
    params: [verb, actor.slug || null, ok, errorKind || null, actor.via || null, actor.client_id || null,
             identity.organization_tenant_id, identity.sponsoring_human_slug, identity.personal_scope,
             identity.authorization_class],
  };
}

// insertFn is (text, params) => Promise<rows>. Production passes a thin neon()
// wrapper against DATABASE_URL_WRITER (carr_reader is views-only and cannot
// INSERT — see 0108's grants); tests pass a fake that just records the call.
// NEVER THROWS: a logging failure must not become a read failure, so any
// rejection from insertFn is swallowed here, not propagated to the caller
// that scheduled this via ctx.waitUntil.
export async function recordReadCall(insertFn, actor, verb, ok, errorKind) {
  const { text, params } = readCallInsertSQL(actor, verb, ok, errorKind);
  try {
    await insertFn(text, params);
  } catch {
    // fire-and-forget: the read already succeeded (or failed) and its response
    // is already on the wire by the time this runs under ctx.waitUntil.
  }
}

// Authority operations have a separate, human-principal-bound database
// connection. The scoped variables permit Joe and Dell to have distinct DB
// login identities. The unscoped value is deliberately Joe-only: letting a
// Dell-attributed call fall back to a Joe login would make the application actor
// and database principal disagree, bypassing DB-enforced Joe-only operations.
export function authorityDsnForActor(env, runtimeActor) {
  const partner = partnerAuthoritySlugForActor(runtimeActor);
  if (!partner) return null;
  // Preserve the provisioning contract's actor-shaped binding while selecting
  // the server-derived sponsor, never the runtime agent or caller input.
  const actor = { slug: partner };
  const scoped = env?.[`CARR_DB_AUTHORITY_${actor.slug.toUpperCase()}_URL`];
  if (scoped) return scoped;
  return partner === "joe" ? env?.CARR_DB_AUTHORITY_URL || null : null;
}

// Exported for deterministic no-network identity-gate tests. It remains the
// single normal dispatcher path; callers receive no additional route or grant.
export async function callTool(env, actor, name, args, profile = "full") {
  const personalScope = personalScopeForActor(actor);
  if (personalScope.status === "error") {
    throw new ToolError({ error: personalScope.error,
      hint: "this OAuth agent grant lacks its required server-derived sponsor. Reconnect through the registered OAuth flow; no tool argument can select a personal brain." });
  }
  // Refuse caller-claimed authority before call-verb recursion or a writer pool.
  // executeRegisteredTool repeats this same pure gate for composite dispatches
  // that bypass callTool, so no registered handler gets a different boundary.
  assertNoCallerAuthorityFields(args);
  // call-verb: the deploy-gap passthrough (Joe, 2026-08-08: "theres got to be
  // a way to fix the need for having to reconnect the connector to ship
  // things"). Connectors cache tools/list at connect time, so a freshly
  // deployed verb is invisible until a reconnect — but the REGISTRY is live
  // the moment the Worker deploys. This one stable tool dispatches by name to
  // the live registry, so new verbs are callable immediately; their first-class
  // schemas surface at the next natural session start. Enforcement is BY
  // RECURSION: the inner name re-enters callTool and hits every profile,
  // payload, and humanOnly check exactly as a direct call would — the
  // passthrough grants reach, never permission.
  if (name === "call-verb") {
    const inner = args && args.verb;
    if (!inner || typeof inner !== "string")
      throw new ToolError({ error: "missing_verb",
        hint: "call-verb takes {verb, args}; list live verbs with list-verbs" });
    if (inner === "call-verb" || inner === "list-verbs")
      throw new ToolError({ error: "no_recursion" });
    // Some clients serialize the nested `args` object as a JSON STRING. The old
    // line was `(args && args.args) || {}`, and a string is truthy, so the inner
    // verb received a STRING, read every field off it as undefined, and answered
    // with its own "missing_<field>" error. That is how a passthrough bug wears
    // the mask of a caller mistake: on 2026-08-13 a session trying to file a
    // defect burned four attempts rearranging idempotency_key before testing
    // read-loop with a known-good id, getting need_number_or_id, and realising
    // EVERY verb reached this way was receiving empty arguments. The passthrough
    // was granting reach and delivering nothing. Parse a string, refuse anything
    // that is neither string nor object, and never silently forward {} — a
    // dropped payload must fail as itself, not as the inner verb's problem.
    let innerArgs = args && args.args;
    if (typeof innerArgs === "string") {
      try { innerArgs = JSON.parse(innerArgs); }
      catch {
        throw new ToolError({ error: "unparseable_args",
          hint: "args arrived as a string that is not valid JSON; pass the inner verb's own argument object" });
      }
    }
    if (innerArgs === undefined || innerArgs === null) innerArgs = {};
    if (typeof innerArgs !== "object" || Array.isArray(innerArgs))
      throw new ToolError({ error: "args_not_an_object", got: typeof innerArgs,
        hint: "call-verb takes {verb, args} where args is the inner verb's own argument object" });
    return callTool(env, actor, inner, innerArgs, profile);
  }
  const tool = TOOLS[name];
  if (!tool) throw new ToolError({ error: "unknown_tool", name });
  // ENFORCED AT CALL TIME, not just filtered out of tools/list. Removing a verb
  // from the list is a hint; a model that has seen the full list in an earlier
  // turn, or guesses a name, would otherwise still reach it.
  if (!allowedIn(profile, name, tool))
    throw new ToolError({ error: "not_in_profile", verb: name, profile,
      hint: "this session is scoped; report what you would have done and let an interactive partner session do it" });
  if (tool.authorityOnly && !authorityDsnForActor(env, actor))
    throw new ToolError({ error: "authority_connection_unavailable",
      hint: "this partner-authority operation requires a verified Joe/Dell principal or sponsored Codex/Claude identity plus the sponsor-scoped authority database binding" });
  // Payload-aware profile guard (2026-08-05). Name-level gating cannot see that
  // add-premises' ownership[].new_party path CREATES a party row — the exact act
  // the away profile's own charter excludes (asserting a new identity while the
  // only humans who could vouch for it are away). The verb stays in the profile
  // because premises capture against existing parties is squarely away-mode work;
  // only the create path is refused, filed-not-dropped like any other block.
  if (profile === "away" && name === "add-premises" &&
      Array.isArray(args?.ownership) && args.ownership.some(o => o && o.new_party))
    throw new ToolError({ error: "not_in_profile", verb: "add-premises (new_party)", profile,
      hint: "away mode may not create a party — file the ownership facts with add-loop and let an interactive partner session create the party, then re-run add-premises by ref" });
  if (hermesCosPremisesRefusal(profile, name, args))
    throw new ToolError({ error: "not_in_profile", verb: "add-premises (new_party)", profile,
      hint: "the Hermes CoS door may capture premises against existing party refs only; a human session must create a new party first" });
  const refusedDealFields = hermesCosDealFieldRefusal(profile, name, args);
  if (refusedDealFields)
    throw new ToolError({ error: "not_in_profile", verb: "update-deal", profile,
      refused_fields: refusedDealFields, allowed_fields: HERMES_COS_DEAL_FIELDS,
      hint: "the Hermes CoS door may correct deal_type and search criteria only; phase, outcome, close/value, identity and narrative fields remain human-owned" });
  // [#214 RED-4, 2026-08-06] The same payload-aware pattern, eleven lines down
  // from its model: log-activity's links[] array runs link-parties' exact INSERT
  // (writeLinks, tools.js), so a profile that refuses link-parties BY NAME could
  // still assert relationships between real people through this field —
  // relationships who-do-we-know then renders to a partner as referral chains he
  // acts on. The charter's reasoning is about who can VOUCH for a relationship,
  // not which verb carries it, so every narrow profile loses the field: away,
  // probe, AND capture (the auditor's recommended scope; an interactive partner
  // session — profile full — keeps it). Filed-not-dropped, like every block.
  if (profile !== "full" && name === "log-activity" &&
      Array.isArray(args?.links) && args.links.length)
    throw new ToolError({ error: "not_in_profile", verb: "log-activity (links[])", profile,
      hint: "a narrow profile may log the activity but not assert relationships — drop links[] from this call and file the introduction facts with add-loop for an interactive partner session to link-parties" });
  if (!tool.write && !tool.writerConnection) {
    const sql = neon(env.DATABASE_URL_READER);
    // sideWrite is the ONLY way a read verb may write, and it is deliberately
    // awkward: a separate credential, never awaited, failure isolated. A read
    // that writes on the read connection is what took doctrine search down —
    // search_doctrine_situations carried an insert inside its own statement, so
    // a refused write killed the answer (migration 0223). Anything using this
    // must treat the write as optional: losing it costs a record, never a reply.
    const client = {
      query: async (text, params = []) => ({ rows: await sql.query(text, params) }),
      sideWrite: env?.DATABASE_URL_WRITER
        ? (text, params = []) => {
            const run = neon(env.DATABASE_URL_WRITER).query(text, params)
              .catch(() => {});           // a lost log row must never surface as a failed read
            env.ctx?.waitUntil?.(run);
            return run;
          }
        : null,
    };
    // Record AFTER the response is ready, via ctx.waitUntil, so recording never
    // adds latency to the read the caller is waiting on. ok/errorKind are
    // metadata only — never the result itself, never args.
    let ok = true, errorKind = null;
    try {
      return await executeRegisteredTool(client, actor, name, args || {});
    } catch (e) {
      ok = false;
      errorKind = e instanceof ToolError ? String(e.payload?.error || "tool_error").slice(0, 64) : "internal_error";
      throw e;
    } finally {
      if (env?.DATABASE_URL_WRITER) {
        const insertFn = (text, params) => neon(env.DATABASE_URL_WRITER).query(text, params);
        env.ctx?.waitUntil?.(recordReadCall(insertFn, actor, name, ok, errorKind));
      }
    }
  }

  // Writes use the routine writer pool except the two authority operations,
  // which receive a separate DB identity and cannot fall back to writer.
  const connectionString = tool.authorityOnly ? authorityDsnForActor(env, actor) : env.DATABASE_URL_WRITER;
  const pool = new Pool({ connectionString });
  const client = await pool.connect();
  try {
    await client.query(tool.writerConnection && !tool.write ? "begin read only" : "begin");
    const a = await client.query("select id from actor where slug=$1", [actor.slug]);
    // Guarded 2026-08-03. Unguarded, a missing actor row made this a raw
    // TypeError on `undefined.id` — a 500 with a stack trace where the real
    // answer is "this token names an actor nobody provisioned". The token has
    // already passed the identity allow-list by here, so the two can disagree:
    // adding a partner to ALLOW_LIST without inserting their actor row lands
    // exactly here, and that is a plausible shape of mistake during onboarding.
    if (!a.rows.length) throw new ToolError({ error: "actor_not_provisioned", slug: actor.slug,
      hint: "the token authenticates as this actor but no row exists in the actor table — " +
            "provision the actor before any write verb will run" });
    const fullActor = { ...actor, id: a.rows[0].id };
    await setWriterActorContext(client, fullActor);
    const result = await executeRegisteredTool(client, fullActor, name, args || {});
    await client.query("commit");
    return result;
  } catch (e) {
    await client.query("rollback").catch(() => {});
    throw e;
  } finally {
    client.release();
    env.ctx?.waitUntil?.(pool.end());
  }
}

export async function dispatch(request, env, ctx, actor) {
  // PROBE LOCK (loop #192, 2026-08-06): a probe-authenticated actor's profile
  // is decided here, server-side, and NEVER by ?profile= — actor.probe is set
  // in exactly one place (index.js's probeActorFor, on a PROBE_TOKENS bearer
  // match) and cannot be set by anything a caller sends on the wire.
  // REVIEWER LOCK (Automatic Review Council, 2026-08-06): same mechanism, same
  // reasoning, for actor.review, set in exactly one place (index.js's
  // reviewActorFor, on a REVIEW_TOKENS bearer match).
  // HERMES LOCK (R0 runtime evaluation, 2026-08-16): same mechanism again, for
  // actor.hermes, set in exactly one place (index.js's hermesActorFor, on a
  // HERMES_TOKENS bearer match). It forces `read`, whose write set is empty, so
  // a Hermes runtime gets every read verb and no write verb at all.
  //
  // WHY A LOCK RATHER THAN TELLING THE RUNTIME TO PASS ?profile=read. The
  // council's R0 disposition is read-only and synthetic, and `?profile=` is a
  // VOLUNTARY limiter — identity.js says so in as many words. A Hermes token
  // asking for ?profile=capture would get capture's write verbs, which makes
  // "read-only" a promise the runtime keeps rather than a boundary the server
  // holds. Hermes is a persistent daemon with its own memory, scheduler and
  // messaging channels, so its own restraint is the wrong thing to rely on:
  // within minutes of first install on 2026-08-16 it seeded itself a Copilot
  // credential off the gh CLI unasked. Held credentials and autonomous
  // authority are the boundary here, never confidentiality — Joe closed the
  // exposure question on 2026-08-12 and every frontier council seat already
  // reads CARR doctrine (rule d7f74c93).
  const profile = profileForActor(actor, request);
  // The authority class is server-derived from the authenticated actor. The
  // legacy ?profile= remains only a voluntary operational limiter: it can
  // reduce the listed/callable verbs, never select a sponsor or widen humanOnly.
  const scopedActor = { ...actor,
    authorization_class: authorizationClassForActor(actor),
    organization_tenant_id: organizationTenantForActor(actor),
    operational_profile: profile,
    // Program 4 Gap A2 (2026-08-14, defect cae5be2e): env.CORRELATION_ID is set
    // per-request by correlation.js's wrapWithCorrelation, the same per-request
    // env-mutation pattern this file already uses for env.ctx (see tools/call
    // below: `env.ctx = ctx;`). Decorating it onto the actor here — rather than
    // threading a new parameter through callTool -> executeRegisteredTool ->
    // every verb handler — means every write verb's existing withEnvelope()/
    // writeEvent() calls pick it up for free through auditIdentity(actor)
    // (tools.js), with zero change to any individual verb.
    correlation_id: env.CORRELATION_ID || null };
  if (request.method !== "POST")
    return json({ error: "method_not_allowed", hint: "MCP streamable HTTP: POST JSON-RPC" }, 405);

  let rpc;
  try {
    rpc = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const reply = (result) => json({ jsonrpc: "2.0", id: rpc.id, result });
  const rpcError = (code, message, data) =>
    json({ jsonrpc: "2.0", id: rpc.id, error: { code, message, data } });

  try {
    switch (rpc.method) {
      case "initialize":
        return reply({
          protocolVersion: PROTOCOL,
          capabilities: { tools: {} },
          serverInfo: { name: "carr-record-layer", version: "0.1.0" },
          instructions:
            "CARR's record layer AND the doctrine store — the ONE source of truth for Joe Bookout's " +
            "healthcare-CRE practice (partner Dell McCraney; business only, personal life is Life AI). " +
            "OPENING ACT, every session: call standing-context FIRST — it returns the taught rules " +
            "with the counts to recite in your first response, open action-required items, and the " +
            "doctrine pointer. There are NO doctrine files: read via doctrine-index / search-doctrine / " +
            "read-doctrine; state via catch-me-up / today-triage. WRITE LAW (rule 14181e60): database " +
            "first — content goes through verbs, NEVER into a .md file. Writes need a fresh " +
            "idempotency_key (UUID) per intended action; mutations need base_version from a fresh read. " +
            "version_conflict and needs_confirm are questions for the human, never auto-retried. There " +
            "is no send tool: drafts are produced, Joe sends. If this server is unreachable mid-task, " +
            "STOP AND SAY SO — never improvise files. DELEGATION LATCH: when a partner says delegate, " +
            "subagent, or cheapest qualified model for an active task, that authority survives new logins " +
            "or data sources, phase changes, retries, continuation and compaction until the task ends " +
            "or the partner revokes it. Choose the cheapest model still qualified to do the task " +
            "correctly; this may be a peer-tier agent, never a forced downgrade. The main seat " +
            "orchestrates, verifies and performs authorized " +
            "writes; it does not reclaim the mechanical sweep. State the executor before each phase; " +
            "a second inline mechanical tool call is the tripwire." + RULE_DELIVERY_RAIL +
            (profile === "full" ? "" : ` ACTIVE PROFILE: ${profile}.` + (PROFILE_NOTICE[profile] || "")),
        });
      case "notifications/initialized":
        return new Response(null, { status: 202 });
      case "ping":
        return reply({});
      case "tools/list":
        return reply({ tools: toolList(profile) });
      case "tools/call": {
        env.ctx = ctx;
        try {
          const result = await callTool(env, scopedActor, rpc.params?.name, rpc.params?.arguments, profile);
          return reply({ content: [{ type: "text", text: JSON.stringify(result) }] });
        } catch (e) {
          if (e instanceof ToolError)
            return reply({ isError: true, content: [{ type: "text", text: JSON.stringify(e.payload) }] });
          // UNMASK IT. Until 2026-08-21 an untyped throw fell through to the
          // outer handler, which answers -32603 with the literal message
          // "internal error" and puts the real cause in the JSON-RPC `data`
          // field — a field MCP clients routinely drop, so the caller saw a
          // four-word string naming nothing. The detail was already being
          // recorded to the incident store and already being returned on the
          // wire; the only thing missing was putting it where a caller reads.
          //
          // What that masking cost in one day: the capability queue's close
          // verb had never once succeeded (a row lock on a table the serving
          // role may only read) and read as "0 complete of 51" while six
          // projects were finished; close-loop and update-decision each died
          // on a short id reaching a uuid column. Three defects, all diagnosed
          // by reading handler SQL, because the error itself said nothing.
          //
          // Still recorded to the incident store, same as before, and the
          // string is redacted for connection-string shapes on the way out. A
          // failure a caller can act on is not a leak; a failure nobody can
          // read is just a longer outage.
          const cause = String((e && e.stack) || e).slice(0, 600)
            .replace(/\b\w+:\/\/[^\s'"]+/gi, "[redacted]");
          scheduleFailureRecord(env, ctx, {
            routeKey: `mcp:tools/call:${rpc?.params?.name || "unknown"}`,
            failureClass: rpcInternalErrorFailureClass(RPC_INTERNAL_ERROR_CODE, {
              verb: rpc?.params?.name || null,
              error: e,
            }),
            detail: cause.slice(0, 300),
          });
          return reply({ isError: true, content: [{ type: "text", text: JSON.stringify({
            error: "unhandled_verb_failure",
            verb: rpc?.params?.name || null,
            cause,
            hint: "this is the server's own exception, not a refusal of your arguments — the verb " +
                  "reached code that threw. Read it before retrying: an unhandled failure repeated " +
                  "with the same arguments fails the same way. If it names a database fault, the " +
                  "handler's SQL is the place to look.",
          }) }] });
        }
      }
      default:
        return rpcError(-32601, `method not found: ${rpc.method}`);
    }
  } catch (e) {
    // Program 4 Gap A2 (2026-08-14, defect cae5be2e): -32603 is the ONLY
    // JSON-RPC code an uncaught exception can produce on this route (this is
    // its sole call site), and /mcp always answers HTTP 200 for a JSON-RPC-
    // level error — see trace.js's file header for why that makes this the
    // one error code worth recording here. The detail string is the SAME
    // truncated text already returned to the caller two lines below; nothing
    // new is exposed by also recording it.
    const detail = String(e).slice(0, 300);
    scheduleFailureRecord(env, ctx, {
      routeKey: `mcp:${rpc?.method || "unknown"}${rpc?.params?.name ? ":" + rpc.params.name : ""}`,
      failureClass: rpcInternalErrorFailureClass(RPC_INTERNAL_ERROR_CODE, {
        verb: rpc?.params?.name || null,
        error: e,
      }),
      detail,
    });
    return rpcError(-32603, "internal error", detail);
  }
}

/** Mounted as OAuthProvider `apiHandler` for /mcp. ctx.props is already authenticated. */
export const mcpApiHandler = {
  async fetch(request, env, ctx) {
    const actor = actorFromProps(ctx.props);
    // Fails closed: a token whose grant does not name one of the two actors is
    // no better than no token at all.
    if (!actor) {
      // Program 4 Gap A2 (2026-08-14, defect cae5be2e): NOT the routine
      // unauthenticated-caller 401 (that never reaches this line — the
      // OAuthProvider library refuses it before mcpApiHandler.fetch ever
      // runs). This is a grant the PROVIDER ALREADY VALIDATED whose props
      // still fail to name a known actor — a real identity/config problem.
      // See trace.js's file header, class 3.
      scheduleFailureRecord(env, ctx, {
        routeKey: "mcp:unauthorized",
        failureClass: actorUnresolvedFailureClass(),
        detail: null,
      });
      return json({ error: "unauthorized" }, 401);
    }
    return dispatch(request, env, ctx, actor);
  },
};

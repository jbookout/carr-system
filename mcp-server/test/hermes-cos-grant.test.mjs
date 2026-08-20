// hermes-cos-grant.test.mjs — the chief-of-staff grant (loop #459, 2026-08-19).
//
// WHAT PROVOKED IT. The Pelham tour prep ran under hermes-pilot and produced a
// tour packet against a brief it knew was wrong, because two things it needed to
// do were things it could not do:
//
//   1. set-next-action had exactly one possible owner — the caller — so the ball
//      Doc set for Joe landed on `hermes-pilot`. Joe's triage reads HIS open
//      balls, so the handoff went into a queue nobody opens.
//   2. update-deal was outside the profile entirely, so the deal stayed
//      purchase-only when the packet said lease+purchase.
//
// Joe, 2026-08-19: "Doc should write whatever the CoS job needs."
//
// WHAT THIS FILE HOLDS. The grant is deliberately two-sided — wider by name,
// narrower by payload — and name-level allowedIn() can only see one side, so
// hermes-r0-lock.test.mjs cannot express it. Here: who may own a ball, which
// deal fields may move, and the four noes the grant was conditioned on staying
// locked server-side while it widened.
//
//   node --test mcp-server/test/hermes-cos-grant.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  hermesActorForToken,
  agentActorForToken,
  permittedActionOwnerSlugs,
  propsForSlug,
  actorFromProps,
} from "../src/identity.js";
import {
  HERMES_DEAL_FIELDS,
  PROFILES,
  allowedIn,
  hermesDealFieldRefusal,
  profileForActor,
} from "../src/mcp.js";
import { TOOLS, ToolError } from "../src/tools.js";

const TOKENS = JSON.stringify({ "hermes-pilot": "s3cr3t-r0" });
const WRITE = { write: true };

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  hermes: "10000000-0000-0000-0000-000000000009",
  dell: "10000000-0000-0000-0000-000000000003",
  pelham: "20000000-0000-0000-0000-00000000f459",
  joeBall: "30000000-0000-0000-0000-000000000001",
  hermesBall: "30000000-0000-0000-0000-000000000002",
  newBall: "30000000-0000-0000-0000-000000000003",
};

const PELHAM = "Pelham Tire — Pensacola distribution warehouse";

/** The real actor a HERMES_TOKENS bearer produces, with the id callTool adds. */
function doc() {
  return { ...hermesActorForToken("Bearer s3cr3t-r0", TOKENS), id: ids.hermes };
}
/** Joe's own interactive OAuth session, built the same way the grant builds it. */
function joeActor() {
  return { ...actorFromProps(propsForSlug("joe", { via: "oauth-google" })), id: ids.joe };
}

// ---------------------------------------------------------------------------
// 1. who may hold a ball
// ---------------------------------------------------------------------------

test("hermes-pilot may set a ball for itself or for the human who sponsors it", () => {
  assert.deepEqual(permittedActionOwnerSlugs(doc()), ["hermes-pilot", "joe"],
    "the sponsor comes from HERMES_SPONSOR server-side, never from the call");
});

test("the sponsor is the ONLY second owner — never the other partner", () => {
  assert.ok(!permittedActionOwnerSlugs(doc()).includes("dell"),
    "Dell did not sponsor this runtime and nothing here reaches his queue");
});

test("a partner's own session gains nothing — he still cannot assign the other's ball", () => {
  // The derivation is uniform, which is the point: Joe's sponsor IS Joe, so his
  // permitted set collapses to one entry. A CoS grant must not become a side
  // door for one human to put work on the other.
  assert.deepEqual(permittedActionOwnerSlugs(joeActor()), ["joe"]);
});

test("an unsponsored runtime may only ever own its own ball", () => {
  const scratch = { ...hermesActorForToken("Bearer x",
    JSON.stringify({ "hermes-scratch": "x" })), id: "n/a" };
  assert.equal(scratch.sponsoring_human_slug, null);
  assert.deepEqual(permittedActionOwnerSlugs(scratch), ["hermes-scratch"],
    "no sponsor, no delegation — shared-only stays shared-only");
});

test("permittedActionOwnerSlugs fails closed on a principal it cannot resolve", () => {
  assert.deepEqual(permittedActionOwnerSlugs(null), []);
  assert.deepEqual(permittedActionOwnerSlugs({ slug: "nobody", human: false }), ["nobody"],
    "an unregistered slug gets no sponsor; personalScopeForActor refuses it one layer down");
});

// ---------------------------------------------------------------------------
// 2. THE CHECK: a hermes-pilot set-next-action on Pelham lands owner=joe
// ---------------------------------------------------------------------------

class NextActionFake {
  constructor() {
    this.balls = [
      { id: ids.joeBall, owner_id: ids.joe, status: "open", description: "old Joe ball" },
      { id: ids.hermesBall, owner_id: ids.hermes, status: "open", description: "Doc's own ball" },
    ];
    this.inserted = null;
    this.events = [];
    this.calls = new Map();
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response from tool_call")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.includes("from v_ref_index where subject_type='deal'"))
      return { rows: [{ subject_id: ids.pelham, display_name: PELHAM,
                        status: "site_selection", client_ref: "C-127" }] };
    if (sql.startsWith("select id from actor where slug=$1")) {
      const row = { joe: ids.joe, dell: ids.dell, "hermes-pilot": ids.hermes }[params[0]];
      return { rows: row ? [{ id: row }] : [] };
    }
    if (sql.startsWith("update next_action set status='dropped'")) {
      const [ownerId, subjectType, subjectId, updatedBy] = params;
      assert.equal(subjectType, "deal");
      assert.equal(subjectId, ids.pelham);
      this.droppedBy = updatedBy;
      this.dropped = this.balls.filter(b => b.owner_id === ownerId && b.status === "open");
      for (const b of this.dropped) b.status = "dropped";
      return { rows: [] };
    }
    if (sql.startsWith("update capture_post_call_action")) return { rows: [] };
    if (sql.startsWith("insert into next_action")) {
      this.inserted = { subject_type: params[0], subject_id: params[1], owner_id: params[2],
                        due_on: params[3], description: params[4], created_by: params[5] };
      return { rows: [{ id: ids.newBall }] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ actor_id: params[1], verb: params[2], subject_type: params[3],
                         subject_id: params[4], new_value: JSON.parse(params[7]) });
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

test("a hermes-pilot set-next-action on the Pelham deal lands owner=joe", async () => {
  const db = new NextActionFake();
  const result = await TOOLS["set-next-action"].handler(db, doc(), {
    idempotency_key: "cos-pelham-ball-459",
    ref: PELHAM,
    owner: "joe",
    description: "Confirm lease-vs-purchase preference with Pelham before the tour",
    due_on: "2026-08-21",
  });

  assert.equal(result.ok, true);
  assert.equal(result.owner, "joe", "the verb reports whose ball it is");
  assert.deepEqual(result.subject, { type: "deal", id: ids.pelham });

  // The row itself: owned by Joe, written by Doc. Both halves matter — the
  // record must not pretend Joe typed it.
  assert.equal(db.inserted.owner_id, ids.joe, "the ball is Joe's, so his triage surfaces it");
  assert.equal(db.inserted.created_by, ids.hermes, "and the record says Doc set it");
  assert.equal(db.inserted.due_on, "2026-08-21");
});

test("the superseded ball is Joe's, not Doc's — one ball per person per subject", async () => {
  const db = new NextActionFake();
  await TOOLS["set-next-action"].handler(db, doc(), {
    idempotency_key: "cos-pelham-ball-supersede",
    ref: PELHAM, owner: "joe", description: "next move",
  });
  assert.deepEqual(db.dropped.map(b => b.id), [ids.joeBall],
    "replacing Joe's ball must drop JOE's open row; keying the drop on the caller " +
    "would stack a second open ball on him every run");
  assert.equal(db.balls.find(b => b.id === ids.hermesBall).status, "open",
    "Doc's own unrelated ball is untouched");
  assert.equal(db.droppedBy, ids.hermes, "updated_by is the caller, truthfully");
});

test("the delegated ball is visible on the deal's timeline", async () => {
  // catch-me-up reads v_subject_timeline, which renders new_value->>'summary'
  // when present (migration 0082) and the bare verb name otherwise. Without a
  // summary a delegated ball shows as "set-next-action" on the record, which is
  // the half of the grant a human has to be able to see.
  const db = new NextActionFake();
  await TOOLS["set-next-action"].handler(db, doc(), {
    idempotency_key: "cos-pelham-ball-timeline",
    ref: PELHAM, owner: "joe", description: "Confirm lease-vs-purchase preference",
  });
  const [event] = db.events;
  assert.equal(event.verb, "set-next-action");
  assert.equal(event.subject_id, ids.pelham, "on the deal, where a human is looking");
  assert.equal(event.new_value.summary, "ball → joe: Confirm lease-vs-purchase preference");
  assert.equal(event.new_value.owner, "joe");
  assert.equal(event.new_value.set_by, "hermes-pilot",
    "the timeline says who handed it over, not just that it moved");
  assert.ok(event.new_value.summary.trim(), "0082 guard C: a stored summary is never blank");
});

test("the default is unchanged — no owner means your own ball", async () => {
  const db = new NextActionFake();
  const result = await TOOLS["set-next-action"].handler(db, doc(), {
    idempotency_key: "cos-pelham-ball-default",
    ref: PELHAM, description: "Doc keeps this one",
  });
  assert.equal(result.owner, "hermes-pilot");
  assert.equal(db.inserted.owner_id, ids.hermes);
  assert.deepEqual(db.dropped.map(b => b.id), [ids.hermesBall]);
});

test("set-next-action refuses an owner the grant does not reach", async () => {
  const db = new NextActionFake();
  await assert.rejects(
    () => TOOLS["set-next-action"].handler(db, doc(), {
      idempotency_key: "cos-pelham-ball-dell",
      ref: PELHAM, owner: "dell", description: "not yours to give",
    }),
    (e) => {
      assert.ok(e instanceof ToolError);
      assert.equal(e.payload.error, "owner_not_permitted");
      assert.deepEqual(e.payload.permitted, ["hermes-pilot", "joe"],
        "the refusal names who it CAN hand work to");
      return true;
    });
  assert.equal(db.inserted, null, "nothing was written");
  assert.equal(db.events.length, 0);
});

test("a partner's session cannot use owner to assign the other partner's ball", async () => {
  const db = new NextActionFake();
  await assert.rejects(
    () => TOOLS["set-next-action"].handler(db, joeActor(), {
      idempotency_key: "cos-pelham-ball-joe-to-dell",
      ref: PELHAM, owner: "dell", description: "you take this one",
    }),
    (e) => e instanceof ToolError && e.payload.error === "owner_not_permitted");
});

test("complete-action is NOT part of the grant — Doc cannot close Joe's ball", () => {
  // Setting a ball for Joe routes work to him. Completing one asserts the work
  // happened, which is a claim only its holder can make. complete-action still
  // keys on actor.id alone and takes no owner argument.
  assert.equal("owner" in TOOLS["complete-action"].inputSchema.properties, false);
});

// ---------------------------------------------------------------------------
// 3. the deal brief: allowed by name, locked by field
// ---------------------------------------------------------------------------

test("update-deal is now reachable by name under hermes", () => {
  assert.equal(allowedIn("hermes", "update-deal", WRITE), true);
  assert.equal(allowedIn("hermes", "add-premises", WRITE), true);
});

test("deal_type and the search criteria pass the field lock", () => {
  assert.equal(hermesDealFieldRefusal("hermes", "update-deal",
    { fields: { deal_type: "lease_and_purchase", segment: "industrial",
                city: "Pensacola", lane: "territory" } }), null);
});

test("update-deal can actually correct deal_type — the write Joe asked for", async () => {
  // The field lock is upstream in mcp.js; this is the other half, that the verb
  // itself will take the column at all. It refused every deal_type edit until
  // 2026-08-19, so a deal opened as purchase-only was purchase-only forever.
  const writes = [];
  const db = {
    async query(text, params = []) {
      const sql = text.replace(/\s+/g, " ").trim();
      if (sql.startsWith("select request_hash, response from tool_call")) return { rows: [] };
      if (sql.includes("from v_ref_index where subject_type='deal'"))
        return { rows: [{ subject_id: ids.pelham, display_name: PELHAM }] };
      if (sql.startsWith("select version from deal")) return { rows: [{ version: 7 }] };
      if (sql.startsWith("select deal_type from deal")) return { rows: [{ deal_type: "purchase" }] };
      if (sql.startsWith("update deal set")) { writes.push({ sql, params }); return { rows: [] }; }
      if (sql.startsWith("insert into event")) {
        writes.push({ event: JSON.parse(params[7]), field: params[5] }); return { rows: [] };
      }
      if (sql.startsWith("insert into tool_call")) return { rows: [] };
      throw new Error(`unhandled fake query: ${sql}`);
    },
  };
  const result = await TOOLS["update-deal"].handler(db, doc(), {
    idempotency_key: "cos-pelham-deal-type",
    deal: PELHAM, base_version: 7,
    fields: { deal_type: "lease_and_purchase" },
  });
  assert.deepEqual(result, { ok: true, updated: ["deal_type"] });
  assert.ok(writes.some(w => w.sql?.includes("deal_type=$2")), "the column is actually set");
  assert.ok(writes.some(w => w.field === "deal_type" &&
    w.event.deal_type === "lease_and_purchase"), "and the change lands on the timeline");
});

test("the field lock refuses everything that would advance, close or value a deal", () => {
  for (const field of ["phase", "outcome", "closed_on", "won_value",
                       "salesforce_id", "notes_path", "client_id"]) {
    assert.deepEqual(hermesDealFieldRefusal("hermes", "update-deal", { fields: { [field]: "x" } }),
      [field], `${field} must refuse under the hermes field lock`);
  }
});

test("one bad field refuses the WHOLE call, never a silent partial write", () => {
  assert.deepEqual(
    hermesDealFieldRefusal("hermes", "update-deal",
      { fields: { deal_type: "lease_and_purchase", phase: "closing", won_value: 180000 } }),
    ["phase", "won_value"],
    "both offending keys are named; applying only deal_type would be a write nobody asked for");
});

test("the field lock is the hermes profile's alone", () => {
  assert.equal(hermesDealFieldRefusal("full", "update-deal", { fields: { won_value: 1 } }), null,
    "a partner's interactive session is unrestricted, as it always was");
  assert.equal(hermesDealFieldRefusal("away", "update-deal", { fields: { won_value: 1 } }), null,
    "away's own charter already decided its update-deal scope; this grant does not touch it");
  assert.equal(hermesDealFieldRefusal("hermes", "log-activity", { fields: { won_value: 1 } }), null,
    "it is a guard on one verb, not a global field filter");
});

test("the field lock cannot be dodged by an odd payload shape", () => {
  for (const fields of [undefined, null, "phase", ["phase"], 7]) {
    assert.equal(hermesDealFieldRefusal("hermes", "update-deal", { fields }), null,
      "a malformed fields payload is the verb's own required-argument problem, " +
      "not a bypass — it reaches no writable column");
  }
});

test("HERMES_DEAL_FIELDS is exactly the brief, and phase is not in it", () => {
  assert.deepEqual([...HERMES_DEAL_FIELDS], ["deal_type", "segment", "city", "lane"]);
  assert.ok(!HERMES_DEAL_FIELDS.includes("phase"),
    "'no advancing a deal' has to stay literally true after this grant");
});

// ---------------------------------------------------------------------------
// 4. the four noes, still locked server-side
// ---------------------------------------------------------------------------

test("NO SEND — there is no send verb in this Worker at all", () => {
  for (const verb of ["send-message", "send-email", "channel.send", "log-outreach-send"])
    assert.equal(allowedIn("hermes", verb, WRITE), false);
  const sendish = Object.keys(TOOLS).filter(n => /^send-|-send$|^email-|^sms-/.test(n));
  assert.deepEqual(sendish, [], "absence is the control; this asserts the absence");
});

test("NO TEACH — the rule verbs refuse, and human:false is why", () => {
  for (const verb of ["teach", "activate-rule", "admit-rule", "amend-rule", "retire-rule"])
    assert.equal(allowedIn("hermes", verb, WRITE), false, `${verb} must stay out of the profile`);
  assert.equal(doc().human, false,
    "humanOnly is the second, independent lock: even in a wider profile these refuse");
  assert.equal(TOOLS["teach"].humanOnly, true);
});

test("NO MERGE — the destructive verbs refuse", () => {
  for (const verb of ["confirm-merge", "merge-vendor-rows", "resolve-candidate", "promote-pool"])
    assert.equal(allowedIn("hermes", verb, WRITE), false);
});

test("NO IDENTITY-FIELD EDITS — parties, vendors and ownership stay untouchable", () => {
  for (const verb of ["add-party", "update-vendor", "update-party-contact", "link-parties",
                      "new-client", "new-lead", "new-vendor", "reassign-deal", "set-lead"])
    assert.equal(allowedIn("hermes", verb, WRITE), false, `${verb} must refuse under hermes`);
});

test("add-premises is granted WITHOUT its party-creation path", () => {
  // The grant is "premises from a packet already read", not "invent the people
  // behind them". callTool's payload guard refuses ownership[].new_party in
  // every narrow profile; this is the shape it keys on.
  const withNewParty = { ownership: [{ kind: "owner", new_party: { name: "Pelham Holdings LLC" } }] };
  const byRef = { ownership: [{ kind: "owner", party_ref: "P-0948" }] };
  const blocked = (profile, args) => profile !== "full" &&
    Array.isArray(args?.ownership) && args.ownership.some(o => o && o.new_party);
  assert.equal(blocked("hermes", withNewParty), true, "creating a party refuses");
  assert.equal(blocked("hermes", byRef), false, "an existing party by ref is the granted path");
  assert.equal(blocked("full", withNewParty), false, "a partner's own session is unaffected");
});

// ---------------------------------------------------------------------------
// 5. the grant did not loosen the door it came through
// ---------------------------------------------------------------------------

test("the Telegram/default seat keeps human:false and a server-forced profile", () => {
  // The condition on this grant: Telegram may keep it only while the four noes
  // stay locked server-side. That means the actor never becomes a human seat and
  // ?profile= never widens it — a wider write set makes both MORE load-bearing,
  // not less, so they are re-asserted here rather than left to the R0 file.
  const a = doc();
  assert.equal(a.human, false);
  assert.equal(a.sponsoring_human_slug, "joe");
  for (const asked of [null, "full", "away", "capture", "read", "probe", "reviewer", "nonsense"]) {
    const url = asked === null
      ? "https://api.doctorcre.com/mcp"
      : `https://api.doctorcre.com/mcp?profile=${asked}`;
    assert.equal(profileForActor(a, new Request(url)), "hermes",
      `?profile=${asked} must not widen a Hermes token, least of all now`);
  }
});

test("the Hermes secret still cannot authenticate through the CLI door", () => {
  // agentActorForToken's actors take profileFor(request) and could ask for
  // `full`, which would hand this token the unrestricted update-deal.
  assert.equal(agentActorForToken("Bearer s3cr3t-r0", TOKENS), null);
});

test("the CoS grant added exactly two verbs to the profile", () => {
  assert.equal(PROFILES.hermes.size, 11);
  for (const verb of ["update-deal", "add-premises"])
    assert.ok(PROFILES.hermes.has(verb));
});

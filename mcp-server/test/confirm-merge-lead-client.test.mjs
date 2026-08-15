// confirm-merge-lead-client.test.mjs — the gate for Joe's ruling that a lead
// record and a client record for the same person are NOT a duplicate.
//
// HIS WORDS, and they are the whole rule: "Tyrer is a client now duh. everyone
// starts as a lead." Every party enters as a lead and converts to a client;
// both refs coexist by design. An L- and a C- ref for one person is the system
// working, not a mess to tidy up.
//
// WHY A GATE AND NOT A NOTE. `find` already returns lead_client_links and
// explains link_basis, but explaining a taxonomy is not the same as refusing an
// action — and the action here is destructive. confirm-merge sets merged_into on
// the loser, which retires its ref permanently: a lost ref is never reissued, so
// every piece of doctrine that quotes it goes dead and has to be repointed by
// hand. Undoing a wrong merge is not a delete.
//
// WHAT IT MUST NOT BREAK. The legitimate case is real and this verb's own
// comments record it: Petersen was TWO party rows for one human, one carrying
// the lead and one carrying the client, and merging them was right — the point
// of that merge was one person holding BOTH roles. So the gate cannot be a flat
// refusal. It refuses the merge whose only stated basis is that the names match,
// and takes `same_person_because` as the human's evidence that it is the
// Petersen shape.
//
// THE SHAPE IT WATCHES FOR is narrow on purpose: one side holding ONLY a lead
// row, the other holding ONLY a client row. Two leads, two clients, anything
// touching a vendor row, or either side holding both roles already — none of
// those is the pattern Joe ruled on, and none is gated here.
//
// Run with: node --test mcp-server/test/confirm-merge-lead-client.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  leadSide: "20000000-0000-0000-0000-00000000000a",
  clientSide: "20000000-0000-0000-0000-00000000000b",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "mcp", client_id: "claude" };

// roles: { [partyId]: ["lead"] | ["client"] | ["lead","client"] | ["vendor"] … }
class Fake {
  constructor(roles) {
    this.roles = roles;
    this.updates = [];
    this.events = [];
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();

    if (sql.startsWith("select request_hash, response from tool_call")) return { rows: [] };

    // ref resolution — both inputs arrive as uuids in these tests
    if (sql.includes("v_ref_index")) return { rows: [] };

    // THE PRE-CHECK the gate depends on: which role rows does each party hold?
    // Matches the real query by the marker comment it carries, so the fake
    // cannot drift into answering a query the handler no longer sends.
    if (sql.includes("role_kinds_for_party")) {
      const held = this.roles[params[0]] || [];
      return { rows: held.map(k => ({ kind: k })) };
    }

    if (sql.startsWith("update lead set party_id") ||
        sql.startsWith("update client set party_id") ||
        sql.startsWith("update vendor set party_id")) {
      this.updates.push(sql.split(" ")[1]);
      return { rows: [{ id: "row" }] };
    }
    if (sql.startsWith("update party set merged_into")) {
      this.updates.push("party");
      return { rows: [] };
    }
    if (sql.includes("having count(*)>1")) return { rows: [] };

    if (sql.startsWith("insert into event")) { this.events.push(params); return { rows: [] }; }
    return { rows: [] };
  }
}

// ToolError carries its detail on `.payload`. An earlier draft of this file read
// `.body ?? .message` and the first test still went green — off the bare error
// string, having never seen the explanation it claimed to be checking. Read the
// field the error actually uses.
const detail = (e) => JSON.stringify(e.payload ?? e.body ?? e.message ?? e);

const verb = TOOLS["confirm-merge"];

async function merge(fake, extra = {}) {
  return verb.handler(fake, joe, {
    idempotency_key: "k-" + Math.random().toString(36).slice(2),
    survivor_party: ids.clientSide,
    merged_party: ids.leadSide,
    ...extra,
  });
}

test("a lead row and a client row for one person are refused as a merge", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await assert.rejects(() => merge(fake), (e) => {
    const body = detail(e);
    assert.match(body, /lead_client_pair/,
      "the refusal names the shape it caught");
    return true;
  });
  assert.deepEqual(fake.updates, [],
    "NOTHING is written when the gate refuses — a merge that got half-applied is worse than one refused");
});

test("the refusal explains the rule in Joe's terms, not as a code", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await assert.rejects(() => merge(fake), (e) => {
    const body = detail(e).toLowerCase();
    assert.match(body, /everyone starts as a lead|starts as a lead/,
      "it quotes the ruling rather than making the reader look up an id");
    assert.match(body, /same_person_because/,
      "and it names the way through, so the refusal is a closed route and not a closed problem");
    return true;
  });
});

test("the Petersen case still merges when the human states the basis", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  const out = await merge(fake, {
    same_person_because: "Two party rows for one human — same NPI, same practice address, "
      + "confirmed against the intake record. Not a name match.",
  });
  assert.equal(out.ok, true, "the legitimate merge is not blocked");
  assert.ok(fake.updates.includes("party"), "and it actually runs");
});

test("the stated basis is recorded on the event, so the reason survives the merge", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await merge(fake, { same_person_because: "same NPI and address, verified in the record" });
  const written = JSON.stringify(fake.events);
  assert.match(written, /same NPI and address/,
    "a merge is permanent; the reason it was allowed has to outlive the session that gave it");
});

test("an empty or throwaway basis does not open the gate", async () => {
  for (const excuse of ["", "   ", "ok", "yes", "same person"]) {
    const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
    await assert.rejects(() => merge(fake, { same_person_because: excuse }),
      `"${excuse}" should not be enough to retire a ref permanently`);
  }
});

test("two leads are an ordinary duplicate and are NOT gated", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["lead"] });
  const out = await merge(fake);
  assert.equal(out.ok, true, "this rule is about a lead/client PAIR, not about duplicates generally");
});

test("two clients are an ordinary duplicate and are NOT gated", async () => {
  const fake = new Fake({ [ids.leadSide]: ["client"], [ids.clientSide]: ["client"] });
  const out = await merge(fake);
  assert.equal(out.ok, true);
});

test("a party that already holds BOTH roles is not the shape Joe ruled on", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead", "client"], [ids.clientSide]: ["client"] });
  const out = await merge(fake);
  assert.equal(out.ok, true,
    "the conversion has already happened on that row; this is a different question");
});

test("a vendor row on either side takes it out of scope", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client", "vendor"] });
  const out = await merge(fake);
  assert.equal(out.ok, true,
    "vendor duplicates have their own verb and their own history; do not widen this gate onto them");
});

test("the gate is symmetric — it does not depend on which side is the survivor", async () => {
  const fake = new Fake({ [ids.leadSide]: ["lead"], [ids.clientSide]: ["client"] });
  await assert.rejects(() => verb.handler(fake, joe, {
    idempotency_key: "k-sym",
    survivor_party: ids.leadSide,   // reversed
    merged_party: ids.clientSide,
  }), /lead_client_pair/,
    "a caller who swaps the arguments must not slip past the gate");
});

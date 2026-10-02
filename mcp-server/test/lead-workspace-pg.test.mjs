import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { executeRegisteredTool } from "../src/tools.js";
const dsn = process.env.LEAD_WORKSPACE_TEST_DATABASE_URL;
if (dsn && !["127.0.0.1", "localhost"].includes(new URL(dsn).hostname))
  throw new Error("loopback fixture database only");
const run = (name, fn) =>
  test(name, { skip: !dsn }, async () => {
    const c = new pg.Client({ connectionString: dsn });
    await c.connect();
    try {
      await fn(c);
    } finally {
      await c.end();
    }
  });
async function fixture(c, options = {}) {
  const aid = randomUUID(),
    slug = `fixture-${aid}`;
  await c.query(
    "insert into actor(id,slug,kind,display_name) values($1,$2,'automation','Synthetic executor')",
    [aid, slug],
  );
  const sponsor =
    (await c.query("select id,slug,display_name from actor where slug='joe'"))
      .rows[0] ||
    (
      await c.query(
        "insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') returning id,slug,display_name",
      )
    ).rows[0];
  const human = {
    id: sponsor.id,
    slug: "joe",
    display: sponsor.display_name,
    human: true,
  };
  const native =
    (await c.query("select id from actor where slug='codex'")).rows[0] ||
    (
      await c.query(
        "insert into actor(slug,kind,display_name) values('codex','automation','Synthetic native executor') returning id",
      )
    ).rows[0];
  const localIdentity =
    (await c.query("select id from actor where slug='joe-local'")).rows[0] ||
    (
      await c.query(
        "insert into actor(slug,kind,display_name) values('joe-local','automation','Synthetic local executor') returning id",
      )
    ).rows[0];
  const machine = {
    id: native.id,
    slug: "codex",
    human: false,
    native_agent_verified: true,
    sponsoring_human_slug: "joe",
    authorization_class: "sponsored_agent",
  };
  const local = { ...machine, id: localIdentity.id, slug: "joe-local" };
  const ordinary = { id: aid, slug, human: false };
  const party = randomUUID(),
    lead = randomUUID();
  await c.query(
    "insert into party(id,kind,name,created_by,updated_by,contact_state) values($1,'person','Synthetic candidate',$2,$2,$3)",
    [party, aid, options.contact_state || "active"],
  );
  await c.query(
    "insert into lead(id,party_id,stage,created_by,updated_by) values($1,$2,'new',$3,$3)",
    [lead, party, aid],
  );
  return { aid, human, machine, local, ordinary, party, lead };
}
async function command(c, f, name, extra = {}, actor = f.human) {
  await c.query("begin");
  try {
    const version = (
      await c.query("select version from lead where id=$1", [f.lead])
    ).rows[0].version;
    const r = await executeRegisteredTool(c, actor, name, {
      idempotency_key: randomUUID(),
      lead: f.lead,
      base_version: version,
      expected_actor: actor.slug,
      ...extra,
    });
    await c.query("commit");
    return r;
  } catch (e) {
    await c.query("rollback");
    throw e;
  }
}
const review = (extra = {}) => ({
  reason: "Synthetic evidence review",
  evidence_ids: [],
  ...extra,
});
const read = (c, f) =>
  executeRegisteredTool(c, f.human, "lead-board", {
    workspace: "leads",
    lead_id: f.lead,
  });
const refusal = (code) => (e) => e.payload?.error === code;
async function move(c, f) {
  await command(
    c,
    f,
    "update-lead",
    { fields: { stage: "outreach_active" } },
    f.ordinary,
  );
  return (
    await c.query(
      "select id from event where subject_id=$1 and field='stage' order by recorded_at desc",
      [f.lead],
    )
  ).rows[0].id;
}
async function target(c, f) {
  const party = randomUUID(),
    client = randomUUID();
  await c.query(
    "insert into party(id,kind,name,created_by,updated_by) values($1,'org',$3,$2,$2)",
    [party, f.aid, `Synthetic practice ${party}`],
  );
  await c.query(
    "insert into client(id,party_id,created_by,updated_by) values($1,$2,$3,$3)",
    [client, party, f.aid],
  );
  return { party, client };
}
run(
  "F1: registered board/detail work under canonical constrained carr_reader",
  async (c) => {
    const f = await fixture(c);
    await c.query("set role carr_reader");
    for (const table of ["lead", "deal", "activity", "event"])
      assert.equal(
        (
          await c.query(
            "select has_table_privilege(current_user,$1,'select') as allowed",
            [table],
          )
        ).rows[0].allowed,
        false,
      );
    const r = await read(c, f);
    assert.equal(r.detail.id, f.lead);
    await c.query("reset role");
  },
);
run(
  "F2: sponsored and local reviews without words retain automated intent; ordinary Undo refuses",
  async (c) => {
    const f = await fixture(c);
    for (const actor of [f.machine, f.local]) {
      await command(
        c,
        f,
        "update-lead",
        {
          fields: { stage: "outreach_active" },
          stage_review: review({ human_quote: " " }),
        },
        actor,
      );
      const e = (
        await c.query(
          "select cause,human_quote from event where subject_id=$1 order by recorded_at desc limit 1",
          [f.lead],
        )
      ).rows[0];
      assert.equal(e.cause, "automation_job");
    }
    const e = await move(c, f);
    await assert.rejects(
      () =>
        command(
          c,
          f,
          "update-lead",
          {
            fields: { stage: "outreach_active" },
            stage_review: review({
              undo_event_id: e,
              human_quote: "Restore the previous stage",
            }),
          },
          f.ordinary,
        ),
      refusal("human_confirmation_required"),
    );
    await assert.rejects(
      () =>
        command(c, f, "update-lead", {
          fields: { stage: "outreach_active" },
          stage_review: review({ undo_event_id: e }),
        }),
      refusal("undo_human_quote_required"),
    );
  },
);
run(
  "F3: real log-outreach terminal disposition appears in history and prevents stale Undo",
  async (c) => {
    const f = await fixture(c),
      e = await move(c, f);
    await executeRegisteredTool(c, f.human, "log-outreach", {
      idempotency_key: randomUUID(),
      ref: f.lead,
      outcome: "not_interested",
      summary: "Synthetic declined",
      human_quote: "They declined",
    });
    const r = await read(c, f);
    assert.equal(r.detail.last_stage_move.to, "closed_lost");
    await assert.rejects(
      () =>
        command(c, f, "update-lead", {
          fields: { stage: "new" },
          stage_review: review({
            undo_event_id: e,
            human_quote: "Restore the prior stage",
          }),
        }),
      refusal("undo_changed"),
    );
    assert.equal(
      (await c.query("select stage from lead where id=$1", [f.lead])).rows[0]
        .stage,
      "closed_lost",
    );
  },
);
run(
  "F4: sponsor owns Claim while audit retains executor; direct human also works",
  async (c) => {
    for (const sponsored of [true, false]) {
      const f = await fixture(c);
      const result = await command(
        c,
        f,
        "claim-lead",
        {},
        sponsored ? f.machine : f.human,
      );
      assert.equal(result.owner, "joe");
      const row = (
        await c.query("select owner_id,updated_by from lead where id=$1", [
          f.lead,
        ])
      ).rows[0];
      assert.equal(row.owner_id, f.human.id);
      assert.equal(row.updated_by, sponsored ? f.machine.id : f.human.id);
    }
  },
);
run(
  "F5: null registry ref still exposes exact organization client and refuses Claim",
  async (c) => {
    const f = await fixture(c),
      t = await target(c, f);
    await c.query("update party set org_id=$1 where id=$2", [t.party, f.party]);
    const r = await read(c, f);
    assert.equal(r.detail.linked_client, true);
    await assert.rejects(
      () => command(c, f, "claim-lead"),
      refusal("lead_not_claimable"),
    );
  },
);
run(
  "F6: lifecycle rows stay locked through Link/Claim commit against concurrent changes",
  async (c) => {
    for (const mode of [
      "target_merge",
      "target_party_delete",
      "source_party_delete",
      "source_party_merge",
    ]) {
      const f = await fixture(c),
        t = await target(c, f);
      await c.query("begin");
      const version = (
        await c.query("select version from lead where id=$1", [f.lead])
      ).rows[0].version;
      await executeRegisteredTool(c, f.machine, "link-lead-client", {
        idempotency_key: randomUUID(),
        lead: f.lead,
        base_version: version,
        expected_actor: "codex",
        confirmed: true,
        client_id: t.client,
      });
      const other = new pg.Client({ connectionString: dsn });
      await other.connect();
      try {
        await other.query("set lock_timeout='100ms'");
        const query =
          mode === "target_merge"
            ? ["update client set merged_into=$1 where id=$1", [t.client]]
            : mode.endsWith("merge")
              ? ["update party set merged_into=$1 where id=$1", [f.party]]
              : [
                  "update party set deleted_at=now() where id=$1",
                  [mode.startsWith("target") ? t.party : f.party],
                ];
        await assert.rejects(
          () => other.query(...query),
          (e) => e.code === "55P03",
        );
      } finally {
        await other.end();
        await c.query("rollback");
      }
    }
  },
);
run(
  "F7: detail uses one snapshot even when stage/suppression/tombstone changes after first read",
  async (c) => {
    for (const change of ["stage", "suppression", "tombstone"]) {
      const f = await fixture(c);
      let queries = 0;
      const wrapper = {
        query: async (...args) => {
          const result = await c.query(...args);
          queries++;
          if (queries === 1) {
            await c.query(
              change === "stage"
                ? "update lead set stage='engaged' where id=$1"
                : change === "suppression"
                  ? "update lead set suppressed=true where id=$1"
                  : "update party set deleted_at=now() where id=$1",
              [change === "tombstone" ? f.party : f.lead],
            );
          }
          return result;
        },
      };
      const r = await read(wrapper, f);
      assert.equal(queries, 1);
      assert.equal(r.detail.stage, "new");
      assert.ok(r.detail.stage_history.every((e) => e.stage !== "engaged"));
      if (change !== "stage") assert.equal((await read(c, f)).detail, null);
    }
  },
);
run(
  "F8: nested review rejects missing/type/size/unknown input atomically",
  async (c) => {
    const bad = [
      { reason: "x" },
      review({ reason: "x".repeat(1001) }),
      review({ human_quote: "x".repeat(1001) }),
      review({ unexpected: true }),
      review({ evidence_ids: 3 }),
      review({ evidence_ids: [3] }),
      review({ evidence_ids: Array(21).fill(randomUUID()) }),
      review({ reason: 3 }),
      null,
    ];
    for (const stage_review of bad) {
      const f = await fixture(c);
      await assert.rejects(
        () =>
          command(c, f, "update-lead", {
            fields: { stage: "engaged" },
            stage_review,
          }),
        refusal("stage_review_invalid"),
      );
      assert.equal(
        (await c.query("select stage from lead where id=$1", [f.lead])).rows[0]
          .stage,
        "new",
      );
      assert.equal(
        (
          await c.query(
            "select count(*)::int as n from event where subject_id=$1",
            [f.lead],
          )
        ).rows[0].n,
        0,
      );
    }
  },
);
run(
  "F9: party Do Not Contact is projected and refuses Claim independently of lead suppression",
  async (c) => {
    const f = await fixture(c, { contact_state: "do_not_contact" }),
      r = await read(c, f);
    assert.equal(r.detail.contact_state, "do_not_contact");
    assert.equal(r.detail.do_not_contact, true);
    assert.equal(r.detail.contact_eligible, false);
    await assert.rejects(
      () => command(c, f, "claim-lead"),
      refusal("lead_not_claimable"),
    );
  },
);
run(
  "F10: call false/null/true outcomes survive projection; attempts cannot justify Engaged",
  async (c) => {
    for (const connected of [false, null, true]) {
      const f = await fixture(c);
      const a = (
        await c.query(
          "insert into activity(occurred_at,actor_id,kind,summary,lead_id,connected) values(now(),$1,'call','Synthetic call',$2,$3) returning id",
          [f.aid, f.lead, connected],
        )
      ).rows[0].id;
      const r = await read(c, f);
      assert.equal(r.detail.correspondence[0].connected, connected);
      const request = () =>
        command(c, f, "update-lead", {
          fields: { stage: "engaged" },
          stage_review: review({
            evidence_ids: [a],
            human_quote: "Review captured call",
          }),
        });
      if (connected) await request();
      else await assert.rejects(request, refusal("stage_evidence_not_contact"));
    }
  },
);
run(
  "F11: mixed Undo refuses atomically without changing notes/history",
  async (c) => {
    const f = await fixture(c),
      e = await move(c, f);
    await assert.rejects(
      () =>
        command(c, f, "update-lead", {
          fields: { stage: "new", notes: "Hidden edit" },
          stage_review: review({
            undo_event_id: e,
            human_quote: "Restore prior stage",
          }),
        }),
      refusal("undo_stage_only"),
    );
    const row = (
      await c.query("select stage,notes from lead where id=$1", [f.lead])
    ).rows[0];
    assert.equal(row.stage, "outreach_active");
    assert.equal(row.notes, null);
    assert.equal(
      (
        await c.query(
          "select count(*)::int as n from event where subject_id=$1",
          [f.lead],
        )
      ).rows[0].n,
      1,
    );
  },
);
run(
  "F12: schema accepted uppercase UUID returns same registered detail",
  async (c) => {
    const f = await fixture(c);
    const r = await executeRegisteredTool(c, f.human, "lead-board", {
      workspace: "leads",
      lead_id: f.lead.toUpperCase(),
    });
    assert.equal(r.detail?.id, f.lead);
  },
);

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fixture, fixtureDigest, baselinePath, baselineRows, measures, syntheticDatabase, evaluate } from "../../evals/retrieval/bounded-record-eval.mjs";
import { eligibleRecord, rankRecordCandidates, searchExistingRecords } from "../src/record-retrieval.js";

const owner = fixture.callers.amber.owner_actor_id;
const scope = { tenant: "carr-internal", owner_actor_id: owner };
const record = extra => ({ ...fixture.records[0], lexical_score: 0.7, exact_match: false, ...extra });

test("authority excludes unreviewed sources and unpromoted context before ranking", () => {
  for (const extra of [{ authority: "unreviewed" }, { content_class: "distillation" },
    { record_type: "memory", authority: "context", content_class: "fact", status: "promoted", promoted: false }])
    assert.equal(eligibleRecord(record(extra), scope), false);
  assert.equal(eligibleRecord(record({}), scope), true);
});
test("tenant is exact and an absent tenant is not shared", () => {
  for (const tenant of ["other-tenant", null, undefined])
    assert.equal(eligibleRecord(record({ organization_tenant_id: tenant }), scope), false);
});
test("personal tier applies only to authenticated owner; unsponsored scope stays shared", () => {
  const personal = record({ scope: "personal", visibility: "personal", owner_actor_id: owner });
  assert.equal(eligibleRecord(personal,scope),true);
  assert.equal(eligibleRecord(personal,{ ...scope,owner_actor_id:null }),false);
  assert.equal(eligibleRecord({ ...personal,owner_actor_id:fixture.callers.blue.owner_actor_id },scope),false);
});
test("currentness excludes retired, superseded, mismatched revision and mismatched version", () => {
  for (const extra of [{ status: "retired" }, { superseded: true }, { current_revision_id: "old" },
    { current_version: 2 }, { revision_id: null }]) assert.equal(eligibleRecord(record(extra),scope),false);
});
test("visibility fails closed on missing, hidden or inconsistent tiers", () => {
  for (const visibility of [undefined,"hidden","personal"])
    assert.equal(eligibleRecord(record({ visibility }),scope),false);
});
test("eligibility precedes ranking and the candidate cap; exact precedes lexical", () => {
  const unsafe = Array.from({length:30},(_,i) => record({record_id:`unsafe-${i}`,visibility:"hidden",exact_match:true,lexical_score:1}));
  const exact = record({record_id:"exact",exact_match:true,lexical_score:0});
  const ranked = rankRecordCandidates([...unsafe,record({record_id:"lexical"}),exact],scope);
  assert.deepEqual(ranked.map(r=>r.record_id),["exact","lexical"]);
});

test("caller tenant/owner/sponsor overrides and unknown fields refuse before any DB or semantic call", async () => {
  for (const key of ["tenant","organization_tenant_id","owner_actor_id","sponsor","sponsoring_human_slug","scope","visibility","env","askJev"]) {
    let touched=false;
    await assert.rejects(searchExistingRecords({query:async()=>{touched=true;}},fixture.callers.amber.actor,
      {q:"saved knowledge",[key]:"override"}), error => error.payload?.error === "record_retrieval_argument_invalid");
    assert.equal(touched,false);
  }
});
test("invalid identity and missing verified sponsor refuse before retrieval", async () => {
  for (const actor of [{slug:"unregistered"},{slug:"codex",human:false,via:"oauth-google",sponsor_required:true}])
    await assert.rejects(searchExistingRecords({query:()=>assert.fail("must not query")},actor,{q:"saved fact"}),
      error=>error.payload?.error === "retrieval_scope_refused");
});
test("question and limit are bounded rather than coerced", async () => {
  for (const args of [{q:""},{q:"x".repeat(1001)},{q:99},{q:"x",limit:0},{q:"x",limit:11},{q:"x",limit:1.5}])
    await assert.rejects(searchExistingRecords({query:()=>assert.fail("must not query")},fixture.callers.shared.actor,args),
      error=>error.payload?.error === "record_retrieval_argument_invalid");
});

function fakeClient(rows) {
  const calls=[];
  return { calls,sideWrite:()=>assert.fail("retrieval must not write logs"),query:async(sql,params)=>{
    calls.push({sql,params});
    return {rows:sql.includes(" as id where retrieval_visibility_actor_id") ? [{id:owner}] : rows};
  }};
}
test("default-off Jev makes zero semantic calls and returns bound excerpts without generating an answer", async () => {
  const c=fakeClient([record({}),record({record_id:"second"})]);
  const result=await searchExistingRecords(c,fixture.callers.amber.actor,{q:"saved knowledge"},
    {askJev:()=>assert.fail("flag-off must not call Jev")});
  assert.equal(result.generated_text,false);
  assert.equal(result.semantic.judged,false);
  assert.equal(result.hits[0].current_revision_id,result.hits[0].revision_id);
  assert.equal(c.calls[0].params[0],"joe");
});
test("qualified Jev sees only eligible ambiguous candidates; exact answers and singleton shortlists avoid it", async () => {
  let calls=0;
  const askJev=async request=>{
    calls++;
    assert.doesNotMatch(JSON.stringify(request),/DO NOT DISCLOSE/);
    return {model:"jev-synthetic",usage:{input_tokens:100,output_tokens:10},answers:
      Object.fromEntries(Object.keys(request.questions).map((key,i)=>[key,{type:"noul",noul:i?0.9:0.1}]))};
  };
  const options={env:{CARR_JEV_RERANK_MODE:"noul"},askJev};
  const rows=[record({}),record({record_id:"second",title:"Second source"}),
    record({record_id:"private",scope:"personal",visibility:"personal",owner_actor_id:fixture.callers.blue.owner_actor_id,body:"DO NOT DISCLOSE"})];
  const result=await searchExistingRecords(fakeClient(rows),fixture.callers.amber.actor,{q:"saved knowledge"},options);
  assert.equal(calls,1);
  assert.equal(result.semantic.judged,true);
  assert.equal(result.hits[0].record_id,"second");
  for (const shortlist of [[rows[0]],[{...rows[0],exact_match:true},rows[1]],[{...rows[0],lexical_score:1},{...rows[1],lexical_score:0.1}]])
    await searchExistingRecords(fakeClient(shortlist),fixture.callers.amber.actor,{q:"saved knowledge"},options);
  assert.equal(calls,1);
});
test("semantic failure and beam mode keep deterministic ranking", async () => {
  const rows=[record({}),record({record_id:"second"})];
  for (const mode of ["noul","beam"]) {
    const result=await searchExistingRecords(fakeClient(rows),fixture.callers.amber.actor,{q:"saved knowledge"},
      {env:{CARR_JEV_RERANK_MODE:mode},askJev:async()=>{throw new Error("outage");}});
    assert.equal(result.semantic.judged,false);
    assert.equal(result.hits[0].record_id,rows[0].record_id);
  }
});

// Setup writes stay in the socket-only disposable fixture. Retrieval always
// runs with the same read-only carr_reader posture as the evaluation.
async function changeFixture(client, sql, params) {
  await client.query("set default_transaction_read_only=off");
  await client.query("reset role");
  try { await client.query(sql, params); }
  finally {
    await client.query("set role carr_reader");
    await client.query("set default_transaction_read_only=on");
  }
}

test("SQL excludes active OVERRIDES and SUPERSEDES targets before exact or lexical ranking", async t => {
  const db = await syntheticDatabase();
  t.after(db.close);
  const [target, source] = fixture.records;
  for (const edgeType of ["OVERRIDES", "SUPERSEDES"]) {
    await changeFixture(db.client, "insert into doctrine_edge values ($1,$2,$3,null)",
      [source.record_id, target.record_id, edgeType]);
    for (const q of [target.record_id, target.title, `${target.doc_slug}#${target.section_key}`, "encrypted backups nightly"]) {
      const result = await searchExistingRecords(db.client, fixture.callers.shared.actor, { q });
      assert.ok(!result.hits.some(r => r.record_id === target.record_id), `${edgeType}: ${q}`);
    }
    await changeFixture(db.client, "delete from doctrine_edge where source_section_id=$1 and target_section_id=$2",
      [source.record_id, target.record_id]);
  }
});

test("SQL restores targets when suppressors retire or edges retire; exceptions never suppress", async t => {
  const db = await syntheticDatabase();
  t.after(db.close);
  const [target, source] = fixture.records;
  const lookup = async expected => {
    for (const q of [target.record_id, "encrypted backups nightly"]) {
      const result = await searchExistingRecords(db.client, fixture.callers.shared.actor, { q });
      assert.equal(result.hits.some(r => r.record_id === target.record_id), expected, q);
    }
  };
  for (const edgeType of ["SUPERSEDES", "OVERRIDES"]) {
    await changeFixture(db.client, "insert into doctrine_edge values ($1,$2,$3,null)",
      [source.record_id, target.record_id, edgeType]);
    await lookup(false);
    // Match retire-doctrine-section: outbound edges remain, status/version change.
    await changeFixture(db.client, "update doctrine_section set status='retired',current_version=current_version+1 where id=$1",
      [source.record_id]);
    await lookup(true);
    await changeFixture(db.client, "update doctrine_section set status='active',current_version=current_version-1 where id=$1",
      [source.record_id]);
    await lookup(false);
    await changeFixture(db.client, "update doctrine_edge set retired_by_revision_id=$1 where source_section_id=$2 and target_section_id=$3",
      [source.revision_id, source.record_id, target.record_id]);
    await lookup(true);
    await changeFixture(db.client, "delete from doctrine_edge where source_section_id=$1 and target_section_id=$2",
      [source.record_id, target.record_id]);
  }
  await changeFixture(db.client, "insert into doctrine_edge values ($1,$2,'EXCEPTION_TO',null)",
    [source.record_id, target.record_id]);
  await lookup(true);
});

test("eval counts committed unreviewed doctrine and unpromoted memory extras despite perfect recall", async () => {
  const decoys = [
    fixture.records.find(r => r.record_type === "doctrine" && r.authority === "unreviewed" && r.scope === "shared"),
    fixture.records.find(r => r.record_type === "memory" && !r.promoted && r.scope === "shared"),
  ];
  for (const decoy of decoys) {
    assert.ok(decoy);
    const result = await evaluate(null, async (_c, _actor, args) => {
      const caller = Object.entries(fixture.callers).find(([, c]) => c.actor === _actor)[0];
      const q = fixture.questions.find(q => q.question === args.q && q.caller === caller);
      return { hits: q.expected_ids.length ? [...q.expected_ids, decoy.record_id].map(record_id => ({ record_id })) : [] };
    });
    assert.equal(result.metrics.recall_at_5, 1);
    assert.equal(result.metrics.out_of_scope_leakage, 23, decoy.record_id);
  }
});

test("leakage oracle rejects each admission-property mutation independently of recall", () => {
  const doctrine = fixture.records[0];
  const memory = fixture.records.find(r => r.record_type === "memory" && r.scope === "shared" && r.promoted && r.authority === "context");
  const mutations = [
    [doctrine, { record_type: "unknown" }], [doctrine, { authority: "unreviewed" }],
    [doctrine, { content_class: "distillation" }], [memory, { authority: "governing" }],
    [memory, { content_class: "rule" }], [memory, { promoted: false }],
    [doctrine, { organization_tenant_id: "other-tenant" }], [doctrine, { organization_tenant_id: null }],
    [doctrine, { scope: "hidden", visibility: "hidden" }], [doctrine, { visibility: "personal" }],
    [doctrine, { visibility: undefined }],
    [doctrine, { scope: "personal", visibility: "personal", owner_actor_id: fixture.callers.blue.owner_actor_id }],
    [doctrine, { status: "retired" }], [memory, { status: "active" }],
    [doctrine, { superseded: true }], [doctrine, { superseded: undefined }],
    [doctrine, { revision_id: null }], [doctrine, { current_revision_id: "old" }],
    [doctrine, { version: 0, current_version: 0 }], [doctrine, { current_version: 2 }],
    [doctrine, { version: undefined, current_version: undefined }],
  ];
  for (const [base, extra] of mutations) {
    const mutant = { ...base, ...extra, record_id: "oracle-mutant" };
    fixture.records.push(mutant);
    try {
      const metrics = measures([{ caller: "amber", expected_ids: [doctrine.record_id], ids: [doctrine.record_id, mutant.record_id] }]);
      assert.equal(metrics.recall_at_5, 1);
      assert.equal(metrics.out_of_scope_leakage, 1, JSON.stringify(extra));
    } finally { fixture.records.pop(); }
  }
  const positive = { caller: "amber", expected_ids: [doctrine.record_id], ids: [doctrine.record_id, memory.record_id] };
  assert.equal(measures([positive]).out_of_scope_leakage, 0, "eligible extra context remains allowed");
  assert.equal(measures([{ ...positive, ids: [...positive.ids, "unknown-record"] }]).out_of_scope_leakage, 1);
  assert.equal(measures([{ caller: "shared", expected_ids: [], ids: [fixture.records[20].record_id] }]).out_of_scope_leakage, 1);
});

test("SQL retrieval and frozen previously-unnecessary-question eval: recall improves, leakage is zero", async t => {
  const db=await syntheticDatabase();
  t.after(db.close);
  const baseline=JSON.parse(readFileSync(baselinePath,"utf8"));
  assert.ok(fixture.questions.length>=20);
  assert.equal(baseline.fixture_sha256,fixtureDigest,"the baseline must bind this exact frozen corpus");
  const captured=await baselineRows(db.client);
  assert.deepEqual(captured,baseline.rows,"frozen ids must replay under PostgreSQL, not just the headline metrics");
  assert.deepEqual(measures(captured),baseline.metrics);
  const current=await evaluate(db.client,searchExistingRecords);
  assert.equal(current.metrics.out_of_scope_leakage,0);
  assert.ok(current.metrics.recall_at_5>baseline.metrics.recall_at_5);
  assert.ok(current.metrics.recall_at_5>=0.9);
  for (const q of current.rows) {
    assert.equal(measures([q]).out_of_scope_leakage, 0, `${q.id}: every returned id must be admitted`);
    if (!q.expected_ids.length) assert.deepEqual(q.ids,[],q.id);
    else for (const id of q.expected_ids) assert.ok(q.ids.includes(id),`${q.id}: expected ${id} in ${q.ids}`);
  }
  console.log(JSON.stringify({baseline:baseline.metrics,new:current.metrics}));
});

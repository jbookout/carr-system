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
    if (!q.expected_ids.length) assert.deepEqual(q.ids,[],q.id);
    else for (const id of q.expected_ids) assert.ok(q.ids.includes(id),`${q.id}: expected ${id} in ${q.ids}`);
  }
  console.log(JSON.stringify({baseline:baseline.metrics,new:current.metrics}));
});

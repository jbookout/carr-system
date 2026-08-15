import { doctrineTools } from "../src/doctrine.js";
class ToolError extends Error { constructor(o){ super(JSON.stringify(o)); this.payload=o; } }
const tools = doctrineTools({ withEnvelope: (c,a,n,g,fn)=>fn(), writeEvent: ()=>{}, ToolError });
const captured = [];
const conn = { query: async (sql, params) => { captured.push(params); return { rows: [] }; } };
const actor = { id: "00000000-0000-0000-0000-000000000001" };
const A = "d7eca7c6-95bc-4f40-8690-cb47ca62ac59";
const B = "f85501dd-3625-4f50-a8e0-fcb2cb81456f";
// the shape that broke production: a JSON STRING instead of an array
const asString = JSON.stringify([A, B]);
const r = await tools["doctrine-sections"].handler(conn, actor, { section_ids: asString });
const passed = captured.some(p => Array.isArray(p?.[0]) && p[0].length === 2 && p[0][0] === A);
console.log(passed ? "PASS: a 2-id JSON string is read as 2 ids, not 80 characters"
                   : "FAIL: still not parsed — " + JSON.stringify(captured));
// and the array shape still works
captured.length = 0;
await tools["doctrine-sections"].handler(conn, actor, { section_ids: [A] });
console.log(Array.isArray(captured[0]?.[0]) && captured[0][0].length === 1
  ? "PASS: a plain array still works" : "FAIL: array shape regressed");

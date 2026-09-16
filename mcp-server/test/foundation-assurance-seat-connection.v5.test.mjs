import assert from "node:assert/strict";
import test from "node:test";

import {
  FOUNDATION_ASSURANCE_APPLICATION_NAME,
  FOUNDATION_ASSURANCE_WRITER_SECRET_NAME,
  foundationAssuranceSeatConnection,
} from "../src/foundation-assurance-seat-connection.v5.js";

test("Foundation Assurance connections identify their workload to PostgreSQL", async () => {
  let options;
  const queries = [];
  class Pool {
    constructor(value) { options = value; }
    async connect() {
      return {
        query: async sql => { queries.push(sql); },
        release() {},
      };
    }
    async end() {}
  }

  const connect = foundationAssuranceSeatConnection({
    [FOUNDATION_ASSURANCE_WRITER_SECRET_NAME]: "postgresql://example.invalid/db",
  }, Pool);
  const value = await connect(async () => "ok");

  assert.equal(value, "ok");
  assert.deepEqual(options, {
    connectionString: "postgresql://example.invalid/db",
    application_name: FOUNDATION_ASSURANCE_APPLICATION_NAME,
  });
  assert.deepEqual(queries, ["begin", "commit"]);
});

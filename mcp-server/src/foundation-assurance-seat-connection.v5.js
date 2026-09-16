import { FOUNDATION_ASSURANCE_ORACLE_SECRET } from
  "./foundation-assurance-minimum-registration.v5.js";

export const FOUNDATION_ASSURANCE_WRITER_SECRET_NAME =
  FOUNDATION_ASSURANCE_ORACLE_SECRET;
export const FOUNDATION_ASSURANCE_APPLICATION_NAME =
  "doctorcre-foundation-assurance";

// One short transaction authenticated as carr_foundation_assurance_oracle.
// The ordinary writer connection is never a fallback.
export function foundationAssuranceSeatConnection(env, PoolConstructor) {
  const connectionString = env?.[FOUNDATION_ASSURANCE_WRITER_SECRET_NAME];
  if (!connectionString || typeof PoolConstructor !== "function") return null;
  return async function withFoundationAssuranceSeatConnection(run) {
    const pool = new PoolConstructor({
      connectionString,
      application_name: FOUNDATION_ASSURANCE_APPLICATION_NAME,
    });
    const client = await pool.connect();
    try {
      await client.query("begin");
      const result = await run(client);
      await client.query("commit");
      return result;
    } catch (error) {
      await client.query("rollback").catch(() => {});
      throw error;
    } finally {
      client.release();
      const ended = pool.end();
      if (env?.ctx?.waitUntil) env.ctx.waitUntil(ended);
      else await ended.catch(() => {});
    }
  };
}

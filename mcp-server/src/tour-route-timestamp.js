// Route appointments round-trip through PostgreSQL timestamptz JSON, which
// uses numeric offsets and up to six fractional digits. Keep that precision
// intact for the database's locked-appointment equality check.
export function validRouteTimestamp(value) {
  if (typeof value !== "string" || value.length > 64) return false;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (!match || (match[3] !== "Z" && (Number(match[4]) > 23 || Number(match[5]) > 59))) return false;
  // Validate the wall-clock fields separately so Date.parse's normalization
  // cannot quietly turn February 30 into a different appointment date.
  const wallClock = `${match[1]}.${(match[2] || "").padEnd(3, "0").slice(0, 3)}Z`;
  const wallTime = Date.parse(wallClock);
  return Number.isFinite(wallTime) && new Date(wallTime).toISOString() === wallClock && Number.isFinite(Date.parse(value));
}

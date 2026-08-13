-- 0107_loop_domain_grant.sql — the grant add-loop's domain validation needed.
--
-- THE OUTAGE. Every `add-loop` call of kind open_loop that passed a `domain`
-- failed with a bare {"error":"internal error"}, deterministically, for any
-- payload. Six attempts on 2026-08-13 varied owner, marker, blocker class,
-- idempotency key and body length; all six returned the identical bare error.
-- Filing loops was effectively down, which also silently disables the standing
-- rule that a blocked action is filed rather than dropped (1b8e7f43): a session
-- trying to obey it got an unexplained failure.
--
-- WHY IT LOOKED LIKE A CODE BUG AND WAS NOT. 752d2eb had just added marker and
-- domain validation to add-loop plus pgConstraintError, a translator that turns
-- Postgres integrity violations (SQLSTATE class 23) into a named
-- invalid_field_value. A PERMISSION failure is SQLSTATE 42501, which is not
-- class 23, so the translator correctly returns null and the error falls through
-- to the generic handler as a bare internal error. The new translator made the
-- constraint cases legible and left this one exactly as opaque as before.
--
-- HOW IT WAS ISOLATED, since the bare error named nothing:
--   * kind team_loop and kind idea both SUCCEEDED through the same Worker. Both
--     were called without a `domain`, and the domain lookup is guarded by
--     `if (args.domain !== undefined && args.domain !== null)` — so they never
--     ran the failing query. That is what made this look kind-specific.
--   * the identical open_loop payload SUCCEEDED through mcp-server/local-verb.mjs
--     against this same production database, which runs the real handler over an
--     OWNER connection. Same code, same data, different role, different outcome:
--     that is a permissions fault, not a logic fault.
--   * an invalid marker still returned a proper unknown_marker, proving the
--     deployed Worker was current and the fault sat after marker validation, at
--     the handler's FIRST database query: select slug from loop_domain.
--
-- THE CAUSE, one row of grant metadata: loop_domain was created with SELECT for
-- carr_reader only. Its sibling loop_block — queried by the same handler, a few
-- lines later, successfully — carries carr_exporter, carr_reader AND carr_writer.
-- The Worker runs as carr_writer. So add-loop could read the block it renders
-- into but not the domain taxonomy it validates against.
--
-- EXACT PRECEDENT, same failure shape, ten migrations ago: 0072 granted
-- vendor_category to carr_writer after update-vendor's new category_slug branch
-- died the same way, and recorded the same lesson — a rehearsal run under a
-- stronger role proves nothing about the production role. vendor_stage and
-- party_link_kind (0020) already carry the writer grant for this reason.
-- loop_domain is the outlier, not the rule.
--
-- carr_exporter IS INCLUDED DELIBERATELY, not for symmetry: exporters/dictionary.py
-- reads loop_domain directly, and carr_exporter cannot SELECT it today either.
-- That is the same defect one consumer downstream, and fixing one door while
-- leaving the other shut is how this class keeps recurring.
--
-- carr_jobs is NOT included: it holds no SELECT on loop_item either, so the
-- scheduled jobs that file loops do so through carr_writer, which this covers.
-- Granting a role a table it never reads widens the surface for nothing.

begin;
grant select on loop_domain to carr_writer, carr_exporter;
commit;

do $$
begin
  if not has_table_privilege('carr_writer', 'loop_domain', 'select') then
    raise exception '0107: carr_writer still cannot select loop_domain — add-loop stays broken';
  end if;
  if not has_table_privilege('carr_exporter', 'loop_domain', 'select') then
    raise exception '0107: carr_exporter still cannot select loop_domain — dictionary export stays broken';
  end if;
  -- the pre-existing reader grant must survive untouched
  if not has_table_privilege('carr_reader', 'loop_domain', 'select') then
    raise exception '0107: carr_reader lost its select on loop_domain';
  end if;
end $$;

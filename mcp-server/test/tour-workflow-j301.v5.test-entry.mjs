// V5-J301 — THE DEDICATED TEST-ONLY ENTRY, and the only file in this repository
// permitted to touch __V5_J301_TEST_ONLY__.
//
// WHY IT EXISTS. The staging rules in tour-workflow-j301.v5.js rest on one
// deterministic classification: given a journal, which stage is the workflow
// sitting in? That classification is real and it is worth proving case by case.
// It is ALSO the one thing in the slice that could be mistaken for permission,
// because "resume at deterministic_generation" reads like a decision that
// something may run. There is no durable journal in this repository, so such a
// decision cannot honestly be made; only the hypothetical can.
//
// SO THE HYPOTHETICAL IS ALL THAT LEAVES THIS FILE, and it says so in its own
// field name: `would_resume_at_if_authoritative`. The production module keeps
// the classifier module-private, keeps this member off V5_J301_PUBLIC_SURFACE,
// and the suite proves no other file under mcp-server/src mentions it.
//
// Nothing here may be imported by production code, and the suite enforces that
// rather than asking politely.

import { __V5_J301_TEST_ONLY__ } from "../src/tour-workflow-j301.v5.js";

/**
 * The resume classification, under a name no consumer can mistake for a grant.
 *
 * Returns `would_resume_at_if_authoritative` (the stage an authoritative
 * journal would place the workflow in), `would_next_stage_be_if_authoritative`,
 * the completed stages read out of the view, and the view's step keys. Every
 * result carries `journal_authority: "caller_supplied_view"` and
 * `governed_state_applied: false`.
 */
export function wouldResumeAtIfAuthoritative(journal_view) {
  return __V5_J301_TEST_ONLY__.wouldResumeAtIfAuthoritative(journal_view);
}

/** The member name this file exists to quarantine, so the guard test can name it. */
export const J301_TEST_ONLY_MEMBER_NAME = "__V5_J301_TEST_ONLY__";

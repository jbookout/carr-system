import { readNeedsJoe } from "./needs-joe.js";
import { ToolError } from "./tool-error.js";
import { readRuleEnforcementCoverage } from "./lifecycle-assurance.v5.js";
import { versionGuard, withEnvelope, writeEvent } from "./versioned-write.js";
import { require0066, resolveCampaign, resolvePartyByRef, resolveSubject } from "./verb-support.js";
import { personalScopeForActor } from "./identity.js";

// ---------- code subjects (0101, loop #211) ----------

// THE ONE REPO. CARR CLAUDE.md rev 10: "the code lives in ONE repo:
// jbookout/carr-system". A caller who writes `commit:<sha>` and names no repo means
// this one, because there is no other. The schema deliberately does NOT hard-code
// it — a second repo is a real possibility and a schema forbidding it would be a
// lie — so the default lives here, at the caller's edge, where it is a convenience
// rather than a constraint.
const DEFAULT_REPO = "jbookout/carr-system";

const SHA_RE = /^[0-9a-f]{7,40}$/i;

const REPO_RE = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;

// Parse the forms a reviewing seat actually writes, in the order it writes them:
//   commit:f7abde7               -> the one repo at that commit
//   jbookout/carr-system@f7abde7 -> any repo at that commit
//   repo:jbookout/carr-system    -> the repo itself (a finding about the codebase,
//   jbookout/carr-system            not about one change)
// Returns { repo, sha } with sha null for a repo-level subject.
function parseCodeRef(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  let body = s;
  let forcedKind = null;
  const m = /^(commit|repo)\s*:\s*(.*)$/i.exec(s);
  if (m) { forcedKind = m[1].toLowerCase(); body = m[2].trim(); }
  let repo = null, sha = null;
  const at = body.lastIndexOf("@");
  if (at > 0) {
    repo = body.slice(0, at).trim();
    sha = body.slice(at + 1).trim();
  } else if (SHA_RE.test(body)) {
    // A bare sha only reads as a sha when the caller said `commit:`. Otherwise a
    // seven-character word like 'deadbee' would silently become a commit.
    if (forcedKind !== "commit") return null;
    repo = DEFAULT_REPO; sha = body;
  } else {
    repo = body;
  }
  if (!repo) return null;
  repo = repo.toLowerCase();
  if (!REPO_RE.test(repo)) return null;
  if (sha !== null) {
    if (!SHA_RE.test(sha)) return null;
    sha = sha.toLowerCase();
  }
  if (forcedKind === "repo") sha = null;
  if (forcedKind === "commit" && sha === null) return null;
  return { repo, sha };
}

async function require0101(c) {
  const r = await c.query(
    `select to_regclass('public.code_subject')  is not null as registry,
            to_regclass('public.v_code_finding') is not null as read_side`);
  const s = r.rows[0];
  if (s.registry && s.read_side) return;
  throw new ToolError({ error: "migration_not_applied",
    migration: "0101_code_review_subject", present: s,
    hint: "filing a finding against code needs 0101 (code_subject plus the repo/commit " +
          "branches on record_flag and v_record_flag_subject). Apply it " +
          "(`~/carr-system/run.sh migrate --apply --yes`) and retry. NOTHING was written." });
}

// MINTED ON DEMAND, and that is the deliberate difference from marketing_subject.
// 0066 refuses to mint because a typo'd slug would invent a pillar and pollute a
// taxonomy. A commit sha invents nothing: it either names an object in the repo or
// it does not, and the CHECK constraints in 0101 are what stop a sentence becoming
// a subject. Requiring a human to pre-register every reviewed commit would put a
// gate in front of the one thing this fix exists to make automatic.
async function resolveCodeSubject(c, ref, actorId) {
  const parsed = parseCodeRef(ref);
  if (!parsed) throw new ToolError({ error: "code_subject_unparseable", got: String(ref || "").slice(0, 80),
    hint: "write it the way it is written everywhere else: 'commit:<sha>' for the one repo, " +
          "'owner/name@<sha>' for another repo, or 'repo:owner/name' for the codebase itself. " +
          "A sha is 7-40 hex characters." });
  await require0101(c);
  const { repo, sha } = parsed;
  const found = await c.query(
    "select id from code_subject where repo=$1 and coalesce(commit_sha,'')=coalesce($2,'')",
    [repo, sha]);
  if (found.rows.length) return { type: sha ? "commit" : "repo", id: found.rows[0].id, repo, sha };
  const ins = await c.query(
    `insert into code_subject (repo, commit_sha, created_by) values ($1,$2,$3)
     on conflict (repo, coalesce(commit_sha, '')) do nothing returning id`,
    [repo, sha, actorId || null]);
  if (ins.rows.length) return { type: sha ? "commit" : "repo", id: ins.rows[0].id, repo, sha };
  // Lost the race to a concurrent write — read the winner rather than failing.
  const again = await c.query(
    "select id from code_subject where repo=$1 and coalesce(commit_sha,'')=coalesce($2,'')",
    [repo, sha]);
  if (again.rows.length) return { type: sha ? "commit" : "repo", id: again.rows[0].id, repo, sha };
  throw new ToolError({ error: "code_subject_not_minted", repo, commit_sha: sha,
    hint: "the registry accepted neither an insert nor a read for this repo/sha — nothing was written" });
}

// ---------- rule id resolution (loop #261) ----------
//
// THE DEFECT THIS CLOSES. Every rule verb took `rule_id` and passed it straight
// into SQL as a uuid. But the ONLY rule id a session can see is the 8-character
// short form: that is what the gist index prints, what standing-context returns,
// and what every rule cross-reference in the doctrine is written in. Passing the
// form the system itself publishes made Postgres reject it as a malformed uuid,
// which surfaced as a bare "internal error" — a validation failure wearing the
// costume of an outage. Measured 2026-08-09 by hitting it live: `teach` with
// supersedes:'179be4b8' died that way, and the fix cost a database tap to find
// the full uuid by hand.
//
// WHY IT MATTERS MORE FOR DELL THAN FOR JOE. Joe has a db-tap habit and can
// resolve a short id himself. Dell does not, so following a rule pointer — the
// thing the whole gist index exists to enable — is not awkward for him, it is
// impossible. A pointer nobody can follow is not a pointer.
//
// AMBIGUITY IS REPORTED, NEVER GUESSED. A prefix that matches two rules returns
// the candidates rather than picking one, because silently activating or
// retiring the wrong binding rule is worse than any error message.
// V5-A02: the named refusals ops.record_rule_enforcement_fallback raises
// (migration 0721). Each is mapped to a ToolError of the same name.
const RULE_ENFORCEMENT_FALLBACK_REFUSALS = Object.freeze(new Set([
  "rule_enforcement_fallback_requires_joe_authority",
  "rule_enforcement_fallback_kind_unknown",
  "rule_enforcement_fallback_fields_required",
  "rule_enforcement_fallback_rule_not_found",
  "rule_enforcement_fallback_actor_unregistered",
  "rule_enforcement_fallback_idempotency_conflict",
  "rule_enforcement_fallback_already_recorded",
  "rule_enforcement_fallback_receipts_append_only",
]));

export async function resolveRuleId(c, value, field = "rule_id") {
  const raw = String(value || "").trim();
  if (!raw) throw new ToolError({ error: "rule_id_required", field });

  // A full uuid is used as-is: the fast path stays exactly what it was.
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)) return raw;

  // Anything else must look like a hex prefix before it reaches SQL. This is the
  // check whose absence turned a typo into "internal error".
  if (!/^[0-9a-f]{4,}$/i.test(raw))
    throw new ToolError({ error: "rule_id_malformed", field, got: raw,
      hint: "a rule id is either the full 36-character uuid or the 8-character short form the gist index prints, e.g. '179be4b8'" });

  const m = await c.query(
    "select id, status, left(statement, 70) as gist from rule where id::text like $1 || '%' order by id",
    [raw.toLowerCase()]);
  if (!m.rows.length)
    throw new ToolError({ error: "rule_not_found", field, got: raw,
      hint: "no rule id begins with that prefix — check the gist index, and note a RETIRED rule still resolves" });
  if (m.rows.length > 1)
    throw new ToolError({ error: "ambiguous_rule_id", field, got: raw,
      candidates: m.rows.map(r => ({ rule_id: r.id, status: r.status, gist: r.gist })),
      hint: "that prefix matches more than one rule; pass more characters or the full uuid" });
  return m.rows[0].id;
}

export function ruleTools() {
  return {
  // ===== reads (carr_reader connection) =====

    "read-v5-a02-rule-enforcement-coverage": {
      discoveryOrder: 0,
      write: false,
      description: "Read DoctorCRE V5-A02's server-derived coverage of every active rule. The record layer enumerates active rules and checks that every control its current exact approval names is installed and bound, with test verification that is not future-dated and still equals the value the approval captured, and an immutable fallback receipt written on Joe's authority database connection (the receipt proves which authority connection recorded it, not that a human chose it). Empty input only: caller evidence cannot make coverage green. Each uncovered rule is a named gap (amended after approval, control unmapped, an approved control not installed, tests missing, test evidence future-dated or changed since approval, fallback absent); zero active rules reads coverage_state `empty`, never complete; an unreadable or inconsistent record is a fail-closed unavailable result.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c) => readRuleEnforcementCoverage(c),
    },

  // ===== writes (carr_writer connection, envelope enforced) =====

    "record-rule-enforcement-fallback": {
      discoveryOrder: 21,
      write: true,
      authorityOnly: true,
      description: "Joe-authority-only V5-A02 fallback receipt for one rule. Records what the system must do when that rule's installed control is unavailable; it never guesses from enforcement class and never rewrites an earlier receipt. The current rule version and statement hash, the authority connection's actor, procedure reference, reason and idempotency key are bound by the database. Refusals are named: rule_enforcement_fallback_requires_joe_authority (Dell's authority connection), rule_enforcement_fallback_already_recorded (this rule version already has a receipt), rule_enforcement_fallback_idempotency_conflict (key reused for a different request). A later rule version needs a new receipt.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        rule_id: { type: "string", description: "Full UUID or current short rule id." },
        fallback_kind: { type: "string", enum: [
          "degraded_read_only", "documented_manual_procedure",
          "escalate_to_verified_partner", "refuse_closed",
        ] },
        procedure_ref: { type: "string" },
        reason: { type: "string" },
      }, required: ["idempotency_key", "rule_id", "fallback_kind", "procedure_ref", "reason"] },
      handler: async (c, actor, args) => withEnvelope(
        c, actor, "record-rule-enforcement-fallback", args, async () => {
          const ruleId = await resolveRuleId(c, args.rule_id);
          let recorded;
          try {
            recorded = await c.query(
              "select ops.record_rule_enforcement_fallback($1,$2,$3,$4,$5) as result",
              [ruleId, args.fallback_kind, args.procedure_ref,
               args.idempotency_key, args.reason]);
          } catch (e) {
            // The database names every refusal; surface that name, never a
            // raw driver error.
            if (RULE_ENFORCEMENT_FALLBACK_REFUSALS.has(e?.message))
              throw new ToolError({ error: e.message, rule_id: ruleId,
                ...(typeof e.detail === "string" ? { detail: e.detail } : {}) });
            throw e;
          }
          const result = recorded.rows[0]?.result;
          if (!result?.receipt_id)
            throw new ToolError({ error: "rule_enforcement_fallback_not_recorded", rule_id: ruleId });
          await writeEvent(c, actor, "record-rule-enforcement-fallback", "rule", ruleId, {
            new: { fallback_kind: result.fallback_kind,
              fallback_receipt_id: result.receipt_id,
              rule_version: result.rule_version },
            agent_rationale: args.reason,
            idempotency_key: args.idempotency_key,
          });
          return result;
        }),
    },

    "record-finding": {
      discoveryOrder: 53,
      write: true,
      description: "Land ONE open-source research or enrichment finding as a record_flag row. This is the only path a verification result becomes part of the record — findings do not go into a markdown report (Joe, 2026-08-02: 'we dont write to markdown in the new system only the database'). IT NEVER EDITS AN IDENTITY FIELD. A finding is stored BESIDE the record with its source; a disagreement with name/phone/email/title/specialty is passed as proposes_correction, which is recorded as a proposal for the owning partner and applied by them, never by this verb. STORE NOTHING-FOUND TOO: pass found:false and the empty result becomes a real row, so a record nobody searched is distinguishable from one that was searched and came up dry — that difference is the whole meaning of a verified stamp. source is REQUIRED on every row; provenance is binding, and a finding without it is a rumour. Pass expires_on for anything volatile: title and company change with promotions and job moves, so an expired verification reads as unverified rather than as fact. Common kinds: verified (an identity pass, value lists what was checked), email, cell, office_phone, social, website, npi, license_status, title, entity_filing, address, discrepancy. A near-match on a similar name is contamination, not confirmation — record both candidates and pick neither. Also writes an event, so the finding shows up in catch-me-up without a second read surface. NOT ONLY PEOPLE SINCE 0066: subject_kind campaign / platform / pillar / format files a finding against a THING — a platform, a content pillar, a format, a campaign — which is how the marketing seat's measured conclusions finally get a home. Read them back through v_record_flag_subject, which resolves every branch to a name. AND NOT ONLY BUSINESS RECORDS SINCE 0101: a finding can be filed against CODE — pass 'commit:<sha>' (the one repo at that commit), 'owner/name@<sha>', or 'repo:owner/name' (the codebase itself) and the subject is minted on first use. That is how a code review's result — INCLUDING its failure finding, which is the one a reader most needs — becomes part of the record instead of surviving only in a local sidecar. Read code findings back through v_code_finding, which carries repo and commit_sha as their own columns.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        subject: { type: "string", description: "C-127 / L-204 / V-CPA-006 / P-0301, an exact deal name, or — when subject_kind is campaign/platform/pillar/format — a campaign name or a marketing_subject slug ('twitter', 'reel'). CODE (0101): 'commit:<sha>' files against the one repo at that commit, 'owner/name@<sha>' against another repo, 'repo:owner/name' against the codebase itself." },
        subject_kind: { type: "string", enum: ["auto","party","campaign","platform","pillar","format","repo","commit"], default: "auto",
          description: "'party' pins the flag to the person/org behind a ref instead of the client/lead/vendor record. THE FOUR MARKETING KINDS (0066) are how a finding about a THING rather than a PERSON gets recorded: 'X has returned no analytics for any of 42 placements' is a platform finding, 'reels outperform statics on reach' is a format finding. Before 0066 those had no subject at all and the marketing seat's core output went nowhere. A platform/pillar/format subject must already exist in marketing_subject — this verb registers nothing, because a typo'd slug minting a new pillar is how a taxonomy becomes noise. THE TWO CODE KINDS (0101) are 'commit' (subject is a sha, or 'owner/name@<sha>') and 'repo' (subject is 'owner/name'). Unlike the marketing kinds these ARE minted on demand: a sha is self-evidencing rather than a taxonomy, so there is no vocabulary a typo can pollute. You rarely need to pass these — a subject written as 'commit:<sha>' or 'owner/name@<sha>' is recognised under 'auto'." },
        kind: { type: "string", description: "what was looked for: verified, email, cell, social, npi, title, discrepancy..." },
        value: { type: "object", description: "the finding, structured. Omit when found:false." },
        found: { type: "boolean", default: true, description: "false records a searched-and-empty result" },
        epistemic_status: { type: "string",
          enum: ["proposed","observed","reproduced","accepted","disputed","superseded","inferred","source_backed","speculative"],
          description: "what CLASS of knowledge this row is, so a reader can tell a verified invariant from a provisional interpretation from a hypothesis (idea #57, from the repo-centric agent-stack study 2026-08-07). Defaults: source_backed when found with an external source; inferred when internal. Set disputed/superseded when filing against an earlier finding. Stored in value; query as value->>'epistemic_status'." },
        source: { type: "string", description: "REQUIRED. Where it came from: a URL, 'NPPES', 'Sunbiz', 'practice website'. For EXTERNAL findings this must be a re-verifiable LOCATOR (a URL, a registry name + identifier, a file+section, a thread + date) — wave 1 C5, decision a317439f: the re-verify queue is only as good as the pointer it re-checks. A bare label like 'research' is refused." },
        internal: { type: "boolean", description: "true = an internally observed finding (derived from the record layer itself, a session's own computation, or partner testimony) — exempt from the external-locator requirement, and stored flagged so readers know no outside source backs it." },
        observed_at: { type: "string", description: "when the source was read (ISO); defaults to now" },
        expires_on: { type: "string", description: "date after which this reads as unverified again (volatile fields)" },
        proposes_correction: { type: "object",
          description: "{field, current, proposed} — RECORDED ONLY. The owning partner applies it." } },
        required: ["idempotency_key","subject","kind","source"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-finding", args, async () => {
        // [wave 1 C5, decision a317439f — scoped per the Codex judge: external and
        // decision-bearing findings need a re-verifiable locator; internal
        // observations are exempt but flagged. A universal requirement would
        // manufacture placeholder citations, which is worse than none.]
        if (!args.internal) {
          const src = String(args.source || "");
          const locator = /https?:\/\//i.test(src)
            || /\b(nppes|sunbiz|npi|bbb|linkedin|zoominfo|rocketreach|facebook|instagram|chamber|arxiv|github)\b/i.test(src)
            || /[\/#§@]|\bp\.?\s?\d|\bevent\b|\bthread\b|\bv_[a-z_]+/i.test(src);
          if (!locator || src.trim().length < 12)
            throw new ToolError({ error: "source_not_a_locator",
              got: src.slice(0, 80),
              hint: "an external finding's source must be re-verifiable: a URL, a registry name + identifier, a file+section, or a thread+date. If this finding was derived internally (from the record itself or partner testimony), resubmit with internal:true and it will be stored flagged as such." });
        }
        const src = String(args.source || "").trim();
        if (!src) throw new ToolError({ error: "source_required",
          hint: "every finding carries its provenance; a finding without a source is a rumour" });

        const found = args.found !== false;
        if (found && (!args.value || typeof args.value !== "object" || !Object.keys(args.value).length))
          throw new ToolError({ error: "value_required",
            hint: "pass the finding as value{}, or pass found:false to record a searched-and-empty result" });

        let subjectType, subjectId;
        const MARKETING_KINDS = ["campaign", "platform", "pillar", "format"];
        const CODE_KINDS = ["repo", "commit"];
        // [0101, loop #211] THE CODE BRANCH. Placed FIRST among the special kinds and
        // sniffed even under subject_kind:'auto', because the whole defect this closes is
        // that a reviewing seat writes `commit:<sha>` and gets subject_not_found. Requiring
        // it to also know about a subject_kind flag would leave the reported failure in
        // place for every caller who writes what they already write. The sniff is narrow —
        // parseCodeRef returns null for anything that is not unmistakably a repo or a
        // commit ref, and a bare hex word is only a sha when the caller said `commit:`.
        const codeRef = CODE_KINDS.includes(args.subject_kind)
          ? parseCodeRef(args.subject_kind === "repo"
              ? `repo:${args.subject}` : `commit:${args.subject}`)
          : (args.subject_kind && args.subject_kind !== "auto" ? null : parseCodeRef(args.subject));
        if (CODE_KINDS.includes(args.subject_kind) && !codeRef)
          throw new ToolError({ error: "code_subject_unparseable", got: String(args.subject || "").slice(0, 80),
            subject_kind: args.subject_kind,
            hint: args.subject_kind === "commit"
              ? "with subject_kind:'commit', subject is a sha ('f7abde7') or 'owner/name@<sha>'"
              : "with subject_kind:'repo', subject is 'owner/name'" });
        if (codeRef) {
          const cs = await resolveCodeSubject(c, args.subject_kind === "repo"
            ? `repo:${codeRef.repo}`
            : (codeRef.sha ? `${codeRef.repo}@${codeRef.sha}` : `repo:${codeRef.repo}`), actor.id);
          subjectType = cs.type; subjectId = cs.id;
        } else if (args.subject_kind === "party") {
          subjectType = "party";
          subjectId = await resolvePartyByRef(c, args.subject);
        } else if (MARKETING_KINDS.includes(args.subject_kind)) {
          // [0066] The non-party branch. It resolves through the SAME
          // (subject_type, subject_id) pointer every other branch uses — a campaign
          // already has a uuid, and marketing_subject exists to give platforms,
          // pillars and formats one, rather than bolting a second pointer column
          // onto record_flag.
          await require0066(c);
          if (args.subject_kind === "campaign") {
            subjectType = "campaign";
            subjectId = (await resolveCampaign(c, args.subject)).id;
          } else {
            const slug = String(args.subject || "").trim().toLowerCase();
            const r = await c.query(
              "select id, retired_at from marketing_subject where subject_type=$1 and slug=$2",
              [args.subject_kind, slug]);
            if (!r.rows.length) {
              const known = await c.query(
                "select slug from marketing_subject where subject_type=$1 and retired_at is null order by slug",
                [args.subject_kind]);
              throw new ToolError({ error: "marketing_subject_not_found",
                subject_kind: args.subject_kind, slug,
                known_slugs: known.rows.map(x => x.slug),
                hint: known.rows.length
                  ? "use one of the registered slugs, or register a new one deliberately — this verb never mints one"
                  : `no ${args.subject_kind} is registered yet. 0066 deliberately seeds ZERO pillars ` +
                    "because none is evidenced anywhere in the record; naming the first one is a " +
                    "human modelling act, not a side effect of filing a finding." });
            }
            subjectType = args.subject_kind;
            subjectId = r.rows[0].id;
            if (r.rows[0].retired_at)
              throw new ToolError({ error: "marketing_subject_retired", slug,
                retired_at: r.rows[0].retired_at,
                hint: "findings stay readable against a retired subject, but new ones do not attach to it" });
          }
        } else {
          const s = await resolveSubject(c, args.subject);
          subjectType = s.type; subjectId = s.id;
        }

        // The correction is DATA, not an instruction. It rides inside value so it is
        // impossible to store one without its provenance, and no code path applies it.
        const value = {
          found,
          ...(found ? args.value : { searched_for: args.kind }),
          ...(args.proposes_correction
              ? { proposes_correction: { ...args.proposes_correction, applied: false,
                                         note: "proposal only — the owning partner applies identity changes" } }
              : {}),
          // [wave 1 C5] an internally observed finding is stored FLAGGED, so a
          // reader knows no outside source backs it — the exemption is visible,
          // never silent.
          ...(args.internal ? { internal: true } : {}),
          // Epistemic status (idea #57): explicit wins; otherwise internal
          // observations are inferences and externally-sourced rows are
          // source-backed. Never silently "known".
          epistemic_status: args.epistemic_status
            || (args.internal ? "inferred" : "source_backed"),
        };

        const r = await c.query(
          `insert into record_flag (subject_type, subject_id, kind, value, source, observed_at, expires_on, created_by)
         values ($1,$2,$3,$4,$5, coalesce($6::timestamptz, now()), $7::date, $8) returning id, observed_at`,
          [subjectType, subjectId, args.kind, JSON.stringify(value), src,
           args.observed_at || null, args.expires_on || null, actor.id]);

        await writeEvent(c, actor, "record-finding", subjectType, subjectId, {
          occurred_at: args.observed_at || null,
          field: args.kind,
          new: { found, source: src, expires_on: args.expires_on || null,
                 proposes_correction: args.proposes_correction ? args.proposes_correction.field : null },
          agent_rationale: found ? null : "searched, nothing found",
          idempotency_key: args.idempotency_key });

        return { ok: true, flag_id: r.rows[0].id, subject_type: subjectType, subject_id: subjectId,
                 kind: args.kind, found, observed_at: r.rows[0].observed_at,
                 correction_proposed: !!args.proposes_correction };
      }),
    },

    "find-rule": {
      discoveryOrder: 54,
      description: "Find proposed, active, or retired rules by their words when the id is unknown. Matches a literal substring or all whitespace-separated words, case-insensitively. Returns bounded statement previews and full ids for amend-rule or retire-rule. Read-only; status defaults to any.",
      inputSchema: { type: "object", properties: {
        text: { type: "string", minLength: 1 },
        status: { type: "string", enum: ["proposed", "active", "retired", "any"], default: "any" },
        limit: { type: "integer", minimum: 1, maximum: 100, default: 20 },
      }, required: ["text"] },
      handler: async (c, actor, args) => {
        const text = String(args.text || "").trim();
        if (!text) throw new ToolError({ error: "rule_search_text_required" });
        const status = args.status ?? "any";
        if (!["proposed", "active", "retired", "any"].includes(status))
          throw new ToolError({ error: "invalid_rule_status" });
        const limit = args.limit ?? 20;
        if (!Number.isInteger(limit) || limit < 1 || limit > 100)
          throw new ToolError({ error: "invalid_rule_search_limit" });
        const personalScope = personalScopeForActor(actor);
        if (personalScope.status === "error")
          throw new ToolError({ error: personalScope.error });
        const matches = await c.query(
          `select id, left(id::text,8) as short_id, status, version, created_at,
                scope, left(statement,200) as statement
           from v_rule_lookup
          where (personal_to is null or personal_to=$5::text)
            and ($2='any' or status=$2)
            and (strpos(lower(statement),lower($1))>0 or not exists (
              select 1 from unnest($3::text[]) as terms(word)
               where strpos(lower(statement),lower(word))=0))
          order by created_at desc, id
          limit $4`, [text, status, text.split(/\s+/u), limit, personalScope.sponsor]);
        return { ok: true, rules: matches.rows };
      },
    },

    "teach": {
      discoveryOrder: 55, serialization: "idempotency-key",
      write: true,
      description: "Write a rule from the human's own words (status: proposed — after exact enforcement is built and verified, one explicit human approve-rule act atomically activates the enforced policy). Capture the verbatim quote. Personal-scope rules (voice, format) set personal_to. WHEN TO CALL IT — the test is 'would the system have to ask this again?', NOT whether the partner phrased it as 'always X' or 'never Y'. Standing lessons arrive as ordinary sentences: a modeling ruling ('cadence studio is one national account'), a correction to a fact in the record, a choice between options you offered with the reasoning attached, a rejection of a draft. Capture on the spot, never at 'session close' — the same event-not-session-close rule protocol 27b already settles. Pass supersedes when this rule replaces an earlier one; the old rule is retired with an immutable receipt in the same transaction. Superseding an ACTIVE rule requires a human caller and the same authority connection as retire-rule. ENFORCEMENT-FIRST BIRTH (WR-000019 slice S10): every teach REQUIRES enforcement_home, one of 'gate' (a deny/stop control will carry it — name carrying_control), 'jit' (delivered just-in-time by pack/moment), 'core' (always-loaded), or 'judgment_advisory' (no mechanical control ever will — say why_no_machine in one line). This is a refusal, not a default: a rule captured with nobody having said where it will live is exactly how guidance debt piled up before this slice, and a silent default would be indistinguishable from a considered choice. THIS IS CLERICAL WORK, NOT SELF-MODIFICATION, AND IT IS NEVER REFUSED ON THAT GROUND. Joe's ruling 2026-08-10, verbatim: 'You didn't make your own rule. You applied my rule to the system.' A session INVENTING a standing rule for itself would be self-modification and would be gated. A session TRANSCRIBING what a partner just said is the entire purpose of this verb, and the gate is already built into it: the rule lands as PROPOSED, binds nobody, and takes effect through one human approve-rule act only when enforcement is ready. A session that declines to record a partner's instruction because writing rules 'feels like' changing itself has not been careful, it has lost the instruction — which is the one outcome this verb exists to prevent. Recorded because a session hit exactly this on the day the ruling was made and stopped three routes early.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, statement: { type: "string" },
        human_quote: { type: "string" }, scope: { type: "object" },
        personal: { type: "boolean", description: "true = applies to this partner only" },
        supersedes: { type: "string", description: "rule_id this one replaces and retires atomically; ACTIVE rules require a human caller" },
        enforcement_home: { type: "string", enum: ["gate","jit","core","judgment_advisory"],
          description: "REQUIRED. Where this rule will be enforced: 'gate' (a deny/stop control — pass carrying_control), 'jit' (delivered just-in-time by pack/moment), 'core' (always-loaded), or 'judgment_advisory' (no mechanical control — pass why_no_machine)." },
        carrying_control: { type: "string", description: "REQUIRED when enforcement_home is 'gate'. The control this rule's enforcement will carry — an existing control_key, or the one about to be built." },
        why_no_machine: { type: "string", description: "REQUIRED when enforcement_home is 'judgment_advisory'. One line: why no mechanical control can carry this rule." } },
        required: ["idempotency_key","statement","human_quote","enforcement_home"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "teach", args, async () => {
        // ENFORCEMENT-FIRST BIRTH (WR-000019 slice S10). See the description
        // above: a clear, named refusal rather than a silent default, so an
        // existing caller that has not been told about this yet gets an error
        // that IS the migration path, not a rule quietly filed with no home.
        const ENFORCEMENT_HOMES = ["gate", "jit", "core", "judgment_advisory"];
        const enforcementHome = args.enforcement_home;
        if (!ENFORCEMENT_HOMES.includes(enforcementHome))
          throw new ToolError({ error: "enforcement_home_required",
            hint: "pass enforcement_home: one of 'gate' (a deny/stop control will carry it — also pass carrying_control), 'jit' (delivered just-in-time by pack/moment), 'core' (always-loaded), or 'judgment_advisory' (no mechanical control — also pass why_no_machine)" });
        const carryingControl = String(args.carrying_control || "").trim();
        if (enforcementHome === "gate" && !carryingControl)
          throw new ToolError({ error: "carrying_control_required",
            hint: "enforcement_home 'gate' means a deny/stop control carries this rule; name it in carrying_control — an existing control_key, or the one about to be built" });
        const whyNoMachine = String(args.why_no_machine || "").trim();
        if (enforcementHome === "judgment_advisory" && !whyNoMachine)
          throw new ToolError({ error: "why_no_machine_required",
            hint: "enforcement_home 'judgment_advisory' means no mechanical control will ever back this rule; say why not in why_no_machine, one line" });

        let supersedes = args.supersedes || null;
        // A supersedes pointer at a rule that does not exist is a silent lie in the
        // audit trail, so it is checked rather than trusted.
        if (supersedes) {
          // Short form accepted (loop #261): the gist index prints 8 characters and
          // that is the only id a session can quote back.
          supersedes = await resolveRuleId(c, supersedes, "supersedes");
          const prior = await c.query("select id, status from rule where id=$1 for update", [supersedes]);
          if (!prior.rows.length) throw new ToolError({ error: "supersedes_not_found",
            rule_id: supersedes, hint: "pass the id of a real rule, or omit supersedes" });
          if (prior.rows[0].status === "retired")
            throw new ToolError({ error: "already_retired", rule_id: supersedes });
          if (prior.rows[0].status === "active" && actor.human !== true)
            throw new ToolError({ error: "human_only", verb: "teach", rule_id: supersedes,
              hint: "superseding an ACTIVE rule requires a human caller" });
        }
        const r = await c.query(
          `insert into rule (statement, human_quote, taught_by, scope, personal_to, supersedes)
         values ($1,$2,$3,$4,$5,$6) returning id, personal_to`,
          [args.statement, args.human_quote, actor.id, JSON.stringify(args.scope || {}),
           // STRICT, matching the two response lines below (loop 353). The
           // boundary coercer already guarantees a real boolean here, so this is
           // belt-and-braces: it makes the storage line and the echo lines
           // structurally incapable of disagreeing, which is the disagreement that
           // stored a shared rule as personal while reporting otherwise.
           args.personal === true ? actor.id : null, supersedes || null]);
        // This definer door preserves retirement receipts and the active-rule
        // authority guard. Failure rolls back the new rule and its envelope too.
        let retirement = null;
        if (supersedes) {
          const retired = await c.query("select ops.retire_superseded_rule($1,$2) as result",
            [r.rows[0].id, args.idempotency_key]);
          retirement = retired.rows[0].result;
        }
        // Capture precedes authority. Every proposed rule enters the same intake
        // state machine immediately, but this row is deliberately only CAPTURED:
        // neither a model nor the transcription verb can make it binding.
        await c.query(
          `insert into ops.guidance_intake
           (lane,source_kind,source_ref,statement,state,captured_by)
         values ('rule','human',$1,$2,'captured',$3)`,
          [`rule:${r.rows[0].id}`, args.statement, actor.id]);
        await writeEvent(c, actor, "teach", "rule", r.rows[0].id,
          { new: { statement: args.statement, supersedes: supersedes || null,
                   enforcement_home: enforcementHome,
                   carrying_control: enforcementHome === "gate" ? carryingControl : null,
                   why_no_machine: enforcementHome === "judgment_advisory" ? whyNoMachine : null },
            human_quote: args.human_quote, idempotency_key: args.idempotency_key });
        // SCOPE IS ECHOED BACK, added 2026-08-03, because it defaulted silently
        // once and nothing in the response could show it. A rule taught with
        // personal:true landed SHARED, and the only thing that caught it was a
        // row-count comparison between two exported files minutes after it was
        // already ACTIVE and binding both partners. `personal_to` is derived from
        // args.personal AND a resolved actor, so a caller genuinely cannot know
        // which scope it got from an envelope that says only {ok, rule_id,
        // status}. The failure direction is always toward binding MORE people
        // than intended, which is the direction that matters least to the caller
        // and most to the other partner. So the verb now states what it did.
        const scopeApplied = r.rows[0].personal_to ? `personal:${actor.slug}` : "shared";
        const scopeMismatch = args.personal === true && !r.rows[0].personal_to;
        return { ok: true, rule_id: r.rows[0].id, status: "proposed",
                 next_authority_action: "approve-rule",
                 scope_applied: scopeApplied,
                 personal_requested: args.personal === true,
                 supersedes: supersedes || null,
                 retirement,
                 enforcement_home: enforcementHome,
                 carrying_control: enforcementHome === "gate" ? carryingControl : null,
                 why_no_machine: enforcementHome === "judgment_advisory" ? whyNoMachine : null,
                 ...(scopeMismatch ? { warning:
                   "personal:true was requested but this rule was stored SHARED — activating it " +
                   "will bind BOTH partners, including any wording specific to one of them or to " +
                   "one machine. Retire it and re-teach before activating if that is wrong." } : {}) };
      }),
    },

    "admit-rule": {
      discoveryOrder: 56,
      write: true,
      description: "Normalize and admit one PROPOSED rule into executable authority. Capture remains free; this is the separate human gate. Applicability, projection, reachability, input contract, binding moment, fixtures, and enforcement points are all explicit. A machine-enforceable rule is refused unless at least one installed enforcement point and fixture are named. Admission writes an immutable authority receipt but does not activate the rule; activate-rule remains a second explicit human act.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        rule_id: { type: "string" },
        enforcement_class: { type: "string", enum: ["machine_enforceable","judgment_advisory","human_only"] },
        binding_moment: { type: "string" },
        applicability: { type: "object" },
        projection: { type: "object" },
        reachability: { type: "object" },
        input_contract: { type: "object" },
        fixture_refs: { type: "array", items: { type: "string" } },
        enforcement_points: { type: "array", items: { type: "object", properties: {
          control_key: { type: "string" }, implementation_ref: { type: "string" },
          test_ref: { type: "string" },
          enforcement_class: { type: "string", enum: ["deny_gate","stop_gate","schema","surfacing","transactional_schema","judgment_ambient"] },
          installed: { type: "boolean" },
        }, required: ["control_key","implementation_ref","test_ref","enforcement_class","installed"] } },
        reason: { type: "string" },
      }, required: ["idempotency_key","rule_id","enforcement_class","binding_moment",
                    "applicability","projection","reachability","input_contract",
                    "fixture_refs","enforcement_points","reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "admit-rule", args, async () => {
        args.rule_id = await resolveRuleId(c, args.rule_id);
        const rule = await c.query("select status,statement from rule where id=$1", [args.rule_id]);
        if (!rule.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
        if (rule.rows[0].status !== "proposed") throw new ToolError({
          error: "rule_not_proposed", rule_id: args.rule_id,
          current_status: rule.rows[0].status,
          hint: "admission is a pre-activation contract; active or retired history is not rewritten",
        });
        const reason = String(args.reason || "").trim();
        const binding = String(args.binding_moment || "").trim();
        if (!reason || !binding) throw new ToolError({ error: "admission_explanation_required" });
        if (args.enforcement_class === "machine_enforceable") {
          if (!args.fixture_refs.length) throw new ToolError({ error: "fixture_required" });
          if (!args.enforcement_points.some(p => p.installed === true))
            throw new ToolError({ error: "installed_enforcement_point_required" });
        }

        let intake = await c.query(
          "select id from ops.guidance_intake where lane='rule' and source_ref=$1 order by captured_at limit 1",
          [`rule:${args.rule_id}`]);
        if (!intake.rows.length) intake = await c.query(
          `insert into ops.guidance_intake
           (lane,source_kind,source_ref,statement,state,captured_by)
         values ('rule','system',$1,$2,'captured',$3) returning id`,
          [`rule:${args.rule_id}`, rule.rows[0].statement, actor.id]);

        const normalized = {
          enforcement_class: args.enforcement_class, binding_moment: binding,
          applicability: args.applicability, projection: args.projection,
          reachability: args.reachability, input_contract: args.input_contract,
          fixture_refs: args.fixture_refs, enforcement_points: args.enforcement_points,
        };
        await c.query(
          `update ops.guidance_intake
            set state='admitted',normalized_contract=$1,updated_at=now(),version=version+1
          where id=$2`, [JSON.stringify(normalized), intake.rows[0].id]);
        await c.query(
          `insert into ops.rule_admission
           (rule_id,guidance_intake_id,enforcement_class,binding_moment,applicability,
            projection,reachability,input_contract,fixture_refs,state,admitted_by,
            admitted_at,reason)
         values ($1,$2,$3,$4,$5,$6,$7,$8,$9,'admitted',$10,now(),$11)
         on conflict (rule_id) do update set
           guidance_intake_id=excluded.guidance_intake_id,
           enforcement_class=excluded.enforcement_class,binding_moment=excluded.binding_moment,
           applicability=excluded.applicability,projection=excluded.projection,
           reachability=excluded.reachability,input_contract=excluded.input_contract,
           fixture_refs=excluded.fixture_refs,state='admitted',admitted_by=excluded.admitted_by,
           admitted_at=excluded.admitted_at,reason=excluded.reason,
           version=ops.rule_admission.version+1,updated_at=now()`,
          [args.rule_id,intake.rows[0].id,args.enforcement_class,binding,
           JSON.stringify(args.applicability),JSON.stringify(args.projection),
           JSON.stringify(args.reachability),JSON.stringify(args.input_contract),
           args.fixture_refs,actor.id,reason]);
        // A revision is the complete current contract. Controls omitted from the
        // new contract must stop counting as installed evidence; retaining their
        // rows preserves audit history without silently retaining authority.
        await c.query(
          `update ops.rule_enforcement_point
            set installed=false,verified_at=null
          where rule_id=$1 and not (control_key = any($2::text[]))`,
          [args.rule_id,args.enforcement_points.map(point => point.control_key)]);
        for (const point of args.enforcement_points) await c.query(
          `insert into ops.rule_enforcement_point
           (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
         values ($1,$2,$3,$4,$5,$6,case when $6 then now() else null end)
         on conflict (rule_id,control_key) do update set
           implementation_ref=excluded.implementation_ref,test_ref=excluded.test_ref,
           enforcement_class=excluded.enforcement_class,installed=excluded.installed,
           verified_at=excluded.verified_at`,
          [args.rule_id,point.control_key,point.implementation_ref,point.test_ref,
           point.enforcement_class,point.installed]);
        await c.query(
          `insert into ops.authority_receipt
           (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
         values ($1,'admission','rule',$2,$3,$4,
                 encode(digest($5::text,'sha256'),'hex'),$6)`,
          [`admission:${args.idempotency_key}`,args.rule_id,actor.id,reason,
           JSON.stringify(normalized),args.fixture_refs]);
        await writeEvent(c, actor, "admit-rule", "rule", args.rule_id, {
          new: { admission_state: "admitted", enforcement_class: args.enforcement_class },
          agent_rationale: reason, idempotency_key: args.idempotency_key,
        });
        return { ok: true, rule_id: args.rule_id, admission_state: "admitted",
                 enforcement_class: args.enforcement_class,
                 installed_controls: args.enforcement_points.filter(p => p.installed).length };
      }),
    },

    "approve-rule": {
      discoveryOrder: 57,
      write: true, authorityOnly: true,
      description: "Approve one captured system rule in a single Joe-authority act. Approval means the server atomically verifies exact registered enforcement, records the immutable authority receipt, and activates the rule in the same transaction. There is no approved-but-inactive or active-but-pending state. If enforcement is missing, approval refuses so the system must build and verify the control before carrying Joe's already-recorded approval. Dell retains teaching, review and optional participation capability but cannot replace Joe as the required system authority. Advisory guidance is not mislabeled as an unbreakable rule.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        rule_id: { type: "string", description: "Full UUID or the short id printed by standing-context." },
        policy_kind: { type: "string", enum: ["machine_enforceable","human_only"] },
        control_keys: { type: "array", items: { type: "string" }, description: "Compiler-selected registered controls. Unknown or unverified controls refuse approval; callers cannot supply implementation or test evidence." },
        reason: { type: "string" },
      }, required: ["idempotency_key","rule_id","policy_kind","control_keys","reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "approve-rule", args, async () => {
        args.rule_id = await resolveRuleId(c, args.rule_id);
        const approved = await c.query(
          "select ops.approve_rule($1,$2,$3,$4,$5) as result",
          [args.rule_id,args.policy_kind,args.control_keys,args.idempotency_key,args.reason]);
        const result = approved.rows[0]?.result;
        if (!result || result.policy_status !== "active")
          throw new ToolError({ error: "rule_approval_failed", rule_id: args.rule_id });
        await writeEvent(c, actor, "approve-rule", "rule", args.rule_id, {
          new: { status: "active", enforcement_status: result.enforcement_status,
            installed_controls: result.installed_controls,
            pending_controls: result.pending_controls },
          agent_rationale: args.reason, idempotency_key: args.idempotency_key,
        });
        return result;
      }),
    },

    "activate-rule": {
      discoveryOrder: 58,
      write: true,
      description: "Retired compatibility verb. Direct activation is forbidden because it could separate human approval from verified enforcement. Use approve-rule, which succeeds only when it can enforce and activate the rule atomically.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        rule_id: { type: "string", description: "Accepts either the full 36-character uuid or the 8-character SHORT FORM the gist index and standing-context print (e.g. '179be4b8'); an ambiguous prefix returns the candidates rather than guessing." } },
        required: ["idempotency_key","rule_id"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "activate-rule", args, async () => {
        throw new ToolError({ error: "direct_rule_activation_retired", rule_id: args.rule_id,
          hint: "use approve-rule; approval succeeds only with exact installed enforcement and activates atomically" });
      }),
    },

    "applicable-rules": {
      discoveryOrder: 59,
      write: false,
      description: "Compile the active admitted rule set for a finite workflow, surface and tier. This is deterministic applicability selection from stored tags; no model performs routing or decides which authority applies.",
      inputSchema: { type: "object", properties: {
        workflow: { type: "string" }, surface: { type: "string" }, tier: { type: "string" },
      } },
      handler: async (c, _actor, args) => {
        const r = await c.query(
          "select * from ops.applicable_rules($1,$2,$3)",
          [args.workflow || null,args.surface || null,args.tier || null]);
        return { ok: true, count: r.rows.length, rules: r.rows };
      },
    },

  // BATCH REVIEW QUEUE (WR-000019 slice S6). Every pending governance decision
    // in one payload, so Joe reviews on his own schedule rather than one verb at
    // a time: rules admitted and waiting on approve-rule, guidance import
    // batches staged and waiting on decide-guidance-import-batch, retrieval
    // proposals waiting on approve-retrieval-proposals. Read-only projection —
    // it opens no new write path and changes nothing. carr_reader (the read-verb
    // credential) has no grant on `rule` or `retrieval_proposal`, so this reads
    // through a SECURITY DEFINER function (migration 0345), the same reason
    // read-execution-environment-providers is a function rather than a direct
    // multi-table read.
    "governance-queue": {
      discoveryOrder: 60,
      write: false,
      description: "Read every pending decision in one payload: rules admitted and awaiting approve-rule, guidance import batches staged and awaiting decide-guidance-import-batch, retrieval proposals awaiting approve-retrieval-proposals, and needs_joe — the ONE list of every open item waiting on Joe (action-required and Joe-owned loops, Work Requests in needs_joe, unanswered board questions, rule approvals, and the locally published PR and tabled items), each with a plain title, why only Joe can do it, the one action, link, age and what it blocks, ordered by what it blocks. Items the system can decide itself are excluded and counted by reason. Read-only; grants no authority and performs no decision itself.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c, actor, _args, { now } = {}) => {
        const row = (await c.query("select ops.read_governance_queue() as queue /* governance-queue */")).rows[0];
        const queue = row?.queue || {};
        const rules = queue.pending_rule_approvals || [];
        const batches = queue.pending_guidance_import_batches || [];
        const proposals = queue.pending_retrieval_proposals || [];
        return {
          ok: true,
          pending_rule_approvals: rules,
          pending_guidance_import_batches: batches,
          pending_retrieval_proposals: proposals,
          counts: {
            pending_rule_approvals: rules.length,
            pending_guidance_import_batches: batches.length,
            pending_retrieval_proposals: proposals.length,
            total: rules.length + batches.length + proposals.length,
          },
          needs_joe: await readNeedsJoe(c, actor, queue, { now }),
        };
      },
    },
    "accept-workflow": {
      discoveryOrder: 61,
      write: true, authorityOnly: true,
      description: "Authority acceptance of a completed workflow run. Shadow acceptance remains available to either admitted human partner; canary acceptance is Joe-only and is enforced by the authenticated authority database session, never a caller field. Uses the authority connection, derives the partner from that connection's authenticated session, and refuses an arbitrary receipt reference.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, workflow_key: { type: "string" },
        mode: { type: "string", enum: ["shadow", "canary"] }, receipt_ref: { type: "string" },
      }, required: ["idempotency_key", "workflow_key", "mode", "receipt_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "accept-workflow", args, async () => {
        const accepted = await c.query(
          "select ops.record_workflow_acceptance($1,$2,'accepted',$3) as id",
          [args.workflow_key, args.mode, args.receipt_ref]);
        await writeEvent(c, actor, "accept-workflow", "system", accepted.rows[0].id,
          { new: { workflow_key: args.workflow_key, mode: args.mode, receipt_ref: args.receipt_ref },
            idempotency_key: args.idempotency_key });
        return { ok: true, acceptance_id: accepted.rows[0].id };
      }),
    },

    "disable-legacy-schedule": {
      discoveryOrder: 62,
      write: true, authorityOnly: true,
      description: "Joe-only authority readback after native legacy schedules are disabled. Requires accepted shadow/canary evidence plus immutable enabled and disabled observations for the exact registered surface; a duplicate group additionally requires all four observations for both surfaces. It never performs a native disable.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, workflow_key: { type: "string" }, reason: { type: "string" },
        surface_id: { type: "string" }, locator: { type: "string" },
        pre_observation_ref: { type: "string" }, post_observation_ref: { type: "string" },
        sibling_surface_id: { type: "string" }, sibling_locator: { type: "string" },
        sibling_pre_observation_ref: { type: "string" }, sibling_post_observation_ref: { type: "string" },
      }, required: ["idempotency_key", "workflow_key", "surface_id", "locator", "reason", "pre_observation_ref", "post_observation_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "disable-legacy-schedule", args, async () => {
        const sibling = [args.sibling_surface_id, args.sibling_locator,
          args.sibling_pre_observation_ref, args.sibling_post_observation_ref];
        if (sibling.some(value => value != null) && !sibling.every(value => typeof value === "string" && value.length > 0))
          throw new ToolError({ error: "duplicate_scheduler_evidence_incomplete", workflow_key: args.workflow_key });
        const retired = await c.query("select ops.disable_legacy_schedule($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) as receipt_ref",
          [args.workflow_key, args.surface_id, args.locator, args.reason,
           args.pre_observation_ref, args.post_observation_ref,
           args.sibling_surface_id || null, args.sibling_locator || null,
           args.sibling_pre_observation_ref || null, args.sibling_post_observation_ref || null,
           args.idempotency_key]);
        if (!retired.rows[0].receipt_ref)
          throw new ToolError({ error: "legacy_schedule_not_disabled", workflow_key: args.workflow_key });
        // THE EVENT SUBJECT IS A UUID, AND THIS VERB PASSED A NAME. Until 2026-08-28
        // the audit write below received args.workflow_key, so every call — however
        // good its evidence — died on `invalid input syntax for type uuid` AFTER the
        // receipt row was written, and the rollback took the receipt with it. The
        // verb had therefore never once succeeded, and no legacy schedule could be
        // retired through the only sanctioned path. Its sibling accept-workflow got
        // this right four lines earlier by passing the returned row's id.
        const disabled = await c.query(
          "select id from ops.legacy_schedule_disable_receipt where receipt_ref=$1",
          [retired.rows[0].receipt_ref]);
        if (!disabled.rows[0])
          throw new ToolError({ error: "legacy_schedule_receipt_not_readable", workflow_key: args.workflow_key });
        await writeEvent(c, actor, "disable-legacy-schedule", "system", disabled.rows[0].id,
          { new: { workflow_key: args.workflow_key, surface_id: args.surface_id, locator: args.locator,
                   reason: args.reason, pre_observation_ref: args.pre_observation_ref,
                   post_observation_ref: args.post_observation_ref,
                   sibling_surface_id: args.sibling_surface_id || null,
                   sibling_locator: args.sibling_locator || null,
                   sibling_pre_observation_ref: args.sibling_pre_observation_ref || null,
                   sibling_post_observation_ref: args.sibling_post_observation_ref || null,
                   receipt_ref: retired.rows[0].receipt_ref }, idempotency_key: args.idempotency_key });
        return { ok: true, workflow_key: args.workflow_key, disabled: true, receipt_ref: retired.rows[0].receipt_ref };
      }),
    },

    "activate-guidance-registry": {
      discoveryOrder: 63,
      write: true, authorityOnly: true,
      description: "Activate the typed Guidance Registry after its 5–10-item constitution and complete coverage pass. Uses the human authority connection, derives the approving partner from its authenticated database session, and atomically records the registry-bound manifest-digest receipt and activation event.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, registry_id: { type: "string" },
        manifest_digest: { type: "string", description: "Exact lowercase SHA-256 digest of the reviewed activation manifest." },
        reason: { type: "string" },
      }, required: ["idempotency_key", "registry_id", "manifest_digest", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "activate-guidance-registry", args, async () => {
        const activated = await c.query(
          "select ops.activate_guidance_registry($1,$2,$3,$4) as id",
          [args.registry_id, args.manifest_digest, args.idempotency_key, args.reason]);
        await writeEvent(c, actor, "activate-guidance-registry", "guidance_registry", args.registry_id,
          { new: { manifest_digest: args.manifest_digest, state: "active" },
            agent_rationale: args.reason, idempotency_key: args.idempotency_key });
        return { ok: true, registry_id: args.registry_id, activation_event_id: activated.rows[0].id,
                 manifest_digest: args.manifest_digest };
      }),
    },

    "decide-guidance-import-batch": {
      discoveryOrder: 64,
      write: true, authorityOnly: true,
      description: "Joe-only authority decision for one staged typed-guidance import batch. The human authority database session derives Joe; the caller supplies only the exact reviewed batch id, manifest digest, idempotency key, and recorded reason. It activates the batch's immutable decisions but does not activate the registry itself.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, batch_id: { type: "string" },
        manifest_digest: { type: "string", description: "Exact lowercase SHA-256 digest of the reviewed activation manifest." },
        reason: { type: "string" },
      }, required: ["idempotency_key", "batch_id", "manifest_digest", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "decide-guidance-import-batch", args, async () => {
        const decided = await c.query(
          "select ops.decide_guidance_import_batch($1,$2,$3,$4,$5) as id",
          [args.batch_id, args.manifest_digest, "active", args.idempotency_key, args.reason]);
        await writeEvent(c, actor, "decide-guidance-import-batch", "guidance_import_batch", args.batch_id,
          { new: { manifest_digest: args.manifest_digest, state: "active" },
            agent_rationale: args.reason, idempotency_key: args.idempotency_key });
        return { ok: true, batch_id: args.batch_id, decision_event_id: decided.rows[0].id,
                 manifest_digest: args.manifest_digest, state: "active" };
      }),
    },

    "deactivate-guidance-registry": {
      discoveryOrder: 65,
      write: true, authorityOnly: true,
      description: "Joe-only authority operation to deactivate the active typed Guidance Registry. The authority database session derives Joe and the supplied digest must exactly bind the registry activation being withdrawn. This is append-only history; it never edits a guidance revision.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" }, registry_id: { type: "string" },
        manifest_digest: { type: "string", description: "Exact lowercase SHA-256 digest of the activation being withdrawn." },
        reason: { type: "string" },
      }, required: ["idempotency_key", "registry_id", "manifest_digest", "reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "deactivate-guidance-registry", args, async () => {
        const deactivated = await c.query(
          "select ops.deactivate_guidance_registry($1,$2,$3,$4) as id",
          [args.registry_id, args.manifest_digest, args.idempotency_key, args.reason]);
        await writeEvent(c, actor, "deactivate-guidance-registry", "guidance_registry", args.registry_id,
          { new: { manifest_digest: args.manifest_digest, state: "inactive" },
            agent_rationale: args.reason, idempotency_key: args.idempotency_key });
        return { ok: true, registry_id: args.registry_id, registry_event_id: deactivated.rows[0].id,
                 manifest_digest: args.manifest_digest, state: "inactive" };
      }),
    },

    "retire-rule": {
      discoveryOrder: 66,
      write: true, authorityOnly: true,
      description: "Joe-authority retirement of a proposed or active rule through one database transaction. It writes an immutable retirement receipt bound to the exact rule version, statement hash, prior approval, reason and replacement before changing status. Direct writer updates cannot retire a rule. Retirement preserves the frozen rule text and history; a changed rule must be taught and approved separately.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        rule_id: { type: "string", description: "Accepts either the full 36-character uuid or the 8-character SHORT FORM the gist index and standing-context print (e.g. '179be4b8'); an ambiguous prefix returns the candidates rather than guessing." },
        reason: { type: "string", description: "REQUIRED. Why it is being withdrawn — wrong scope, duplicate, superseded, never wanted." },
        superseded_by: { type: "string", description: "rule_id of the replacement, when there is one" } },
        required: ["idempotency_key","rule_id","reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "retire-rule", args, async () => {
        const reason = String(args.reason || "").trim();
        if (!reason) throw new ToolError({ error: "reason_required",
          hint: "an unexplained retirement reads as a mistake later; say why in one line" });

        args.rule_id = await resolveRuleId(c, args.rule_id);          // loop #261
        const cur = await c.query("select status, statement, personal_to from rule where id=$1", [args.rule_id]);
        if (!cur.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
        if (cur.rows[0].status === "retired") throw new ToolError({ error: "already_retired",
          rule_id: args.rule_id, hint: "nothing was written; the rule is already withdrawn" });

        if (args.superseded_by) {
          args.superseded_by = await resolveRuleId(c, args.superseded_by, "superseded_by"); // loop #261
          const rep = await c.query("select id from rule where id=$1", [args.superseded_by]);
          if (!rep.rows.length) throw new ToolError({ error: "superseded_by_not_found",
            rule_id: args.superseded_by, hint: "pass the id of a real replacement rule, or omit it" });
          if (args.superseded_by === args.rule_id) throw new ToolError({ error: "self_supersede",
            hint: "a rule cannot replace itself" });
        }

        const was = cur.rows[0].status;
        const retired = await c.query(
          "select ops.retire_rule($1,$2,$3,$4) as result",
          [args.rule_id, reason, args.superseded_by || null, args.idempotency_key]);
        const result = retired.rows[0].result;
        await writeEvent(c, actor, "retire-rule", "rule", args.rule_id, {
          field: "status", old: { status: was }, new: { status: "retired" },
          agent_rationale: reason,
          idempotency_key: args.idempotency_key });
        return { ...result, rule_id: args.rule_id, was, now: "retired", reason,
                 superseded_by: args.superseded_by || null,
                 note: was === "active"
                   ? "this rule was BINDING — re-export compiled-rules so sessions stop loading it"
                   : "it bound nobody; no re-export needed" };
      }),
    },

  // ---------- amend-rule (2026-08-02) ----------
    // The store shipped one-way: teach -> activate -> retire. There was no way to
    // fix the WORDS of a rule that was otherwise right, so every wording fix meant
    // retire + re-teach: a new id (breaking every citation), a lost created_at and
    // activation event, and a REQUIRED fresh human_quote — forcing the partner to
    // re-say something he already said, to fix prose he never wrote.
    //
    // 53 of the 54 proposed rules carry no quote at all; they were imported from
    // ai-operating-notes.md by a pipeline that correctly refused to fabricate one.
    // Their statement is our articulation, not his testimony. `update-decision`
    // already established that a durable record can be corrected rather than
    // re-litigated; rules simply never got the same affordance.
    "amend-rule": {
      discoveryOrder: 67,
      write: true, authorityOnly: true,
      description: "Correct the WORDS of a rule in place, keeping its id, created_at, taught_by and quote. THE LINE: amend = same rule, better words; teach + retire = a different rule. A PROPOSED rule's statement, human_quote (fill-only) and scope may all be corrected directly, no authority principal required beyond the write itself. An ACTIVE rule's quote, scope, audience and enforcement preimage stay frozen — changing any of them would make the old approval receipt appear to bless new substance — but its STATEMENT ALONE may now be amended (WR-000019 slice S10) through a Joe-authority-guarded, receipted path (ops.amend_rule_statement): an append-only ops.rule_amendment_receipt hashes the prior words, and the enforced rule keeps reciting under its old approval. To change an active rule's scope, quote or audience, still teach and approve a corrected replacement, then retire this one. Requires base_version from a fresh read; a conflict is never retried blind.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        rule_id: { type: "string" },
        base_version: { type: "integer", description: "the rule's version, from a fresh read" },
        statement: { type: "string", description: "the corrected rule text. Omit to leave it as-is." },
        human_quote: { type: "string", description: "ONLY to fill a quote that is currently absent — the partner's literal words. Refused if the rule already carries one. Never paraphrase into this field." },
        scope: { type: "object", description: "replacement scope object, e.g. {\"section\":\"...\"}. Omit to leave it as-is." },
        reason: { type: "string", description: "REQUIRED. Why the wording is being corrected — an unexplained edit to a binding rule is indistinguishable from drift." } },
        required: ["idempotency_key","rule_id","base_version","reason"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "amend-rule", args, async () => {
        const reason = String(args.reason || "").trim();
        if (!reason) throw new ToolError({ error: "reason_required",
          hint: "say in one line why the wording is wrong; a silent edit to a binding rule reads as drift later" });

        args.rule_id = await resolveRuleId(c, args.rule_id);          // loop #261
        const cur = await c.query(
          "select status, statement, human_quote, scope, version from rule where id=$1", [args.rule_id]);
        if (!cur.rows.length) throw new ToolError({ error: "rule_not_found", rule_id: args.rule_id });
        const row = cur.rows[0];

        // A retired rule is history. Editing a tombstone rewrites the past instead
        // of correcting the present.
        if (row.status === "retired") throw new ToolError({ error: "rule_retired",
          rule_id: args.rule_id, current_status: row.status,
          hint: "a withdrawn rule stays as written; teach a new one rather than editing the tombstone" });

        await versionGuard(c, "rule", args.rule_id, args.base_version);

        const hasQuote = !!String(row.human_quote || "").trim();
        const quoteIn  = args.human_quote === undefined ? undefined : String(args.human_quote).trim();
        if (quoteIn !== undefined && hasQuote && quoteIn !== String(row.human_quote).trim())
          throw new ToolError({ error: "human_quote_immutable",
            current_quote: row.human_quote,
            hint: "the partner's words are testimony, not prose. Amend the statement instead; to record something DIFFERENT he said, teach a new rule and retire this one." });

        const nextStatement = args.statement === undefined ? row.statement : String(args.statement).trim();
        if (!nextStatement) throw new ToolError({ error: "empty_statement",
          hint: "a rule with no words binds nothing — pass the corrected text, or omit the field to leave it alone" });

        const nextQuote = (!hasQuote && quoteIn) ? quoteIn : row.human_quote;
        const nextScope = args.scope === undefined ? row.scope : args.scope;

        const changed = [];
        if (nextStatement !== row.statement) changed.push("statement");
        if (nextQuote !== row.human_quote) changed.push("human_quote");
        if (JSON.stringify(nextScope) !== JSON.stringify(row.scope)) changed.push("scope");
        if (!changed.length) throw new ToolError({ error: "no_change",
          hint: "nothing was written; the rule already reads exactly this way" });

        if (row.status === "active") {
          // The old approval's preimage — quote, scope, audience, enforcement —
          // stays frozen exactly as before. Only the ONE axis the versioned
          // amendment path exists to correct (statement) may move; a request
          // that also touches scope or quote still refuses, same as always.
          if (changed.includes("scope"))
            throw new ToolError({ error: "active_rule_scope_frozen", rule_id: args.rule_id,
              hint: "scope is part of the approval preimage and stays frozen on an active rule; teach the corrected rule, approve it, and retire this one to change scope" });
          if (changed.includes("human_quote"))
            throw new ToolError({ error: "active_rule_quote_frozen", rule_id: args.rule_id,
              hint: "the partner's words are testimony, not prose, and stay fixed once a rule is active" });

          // changed is now exactly ["statement"] — ops.amend_rule_statement,
          // guarded like ops.approve_rule (Joe authority, refuses a retired
          // rule), writes the receipt and updates rule.statement atomically.
          const amended = await c.query(
            "select ops.amend_rule_statement($1,$2,$3,$4) as result",
            [args.rule_id, nextStatement, args.idempotency_key, reason]);
          const result = amended.rows[0]?.result;
          if (!result || !result.ok)
            throw new ToolError({ error: "rule_amendment_failed", rule_id: args.rule_id });

          await writeEvent(c, actor, "amend-rule", "rule", args.rule_id, {
            field: "statement",
            old: { statement: row.statement },
            new: { statement: nextStatement },
            agent_rationale: reason,
            idempotency_key: args.idempotency_key });

          return { ok: true, rule_id: args.rule_id, status: "active",
                   changed: ["statement"], version: result.rule_version_after, reason,
                   replayed: !!result.replayed,
                   amendment_receipt_id: result.amendment_receipt_id,
                   note: "amended under Joe authority and receipted; the rule stays active and keeps reciting under its old approval" };
        }

        await c.query("update rule set statement=$1, human_quote=$2, scope=$3 where id=$4",
          [nextStatement, nextQuote, JSON.stringify(nextScope), args.rule_id]);

        await writeEvent(c, actor, "amend-rule", "rule", args.rule_id, {
          field: changed.join(","),
          old: { statement: row.statement, human_quote: row.human_quote, scope: row.scope },
          new: { statement: nextStatement, human_quote: nextQuote, scope: nextScope },
          human_quote: nextQuote || null,
          agent_rationale: reason,
          idempotency_key: args.idempotency_key });

        const after = await c.query("select version from rule where id=$1", [args.rule_id]);
        return { ok: true, rule_id: args.rule_id, status: row.status,
                 changed, version: after.rows[0].version, reason,
                 note: "it binds nobody yet; approve-rule is the atomic enforcement and activation gate" };
      }),
    },
  };
}

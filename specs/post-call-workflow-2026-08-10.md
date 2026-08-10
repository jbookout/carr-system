# Deal Room post-call workflow

Status: Call Mode foundation implemented; extraction, report review, and Outlook
draft approval remain a separate release.

## Outcome

After Joe and Dell's weekly deal call, the Deal Room should show one review pack:

- deal-by-deal status and proposed record changes;
- Joe's actions and Dell's actions for the week;
- what each attached client, vendor, or other participant needs to know;
- one proposed email per allowed recipient, created in Outlook Drafts only after
  a human approves it;
- unresolved references that need a person to identify the deal or recipient.

Nothing sends automatically. Nothing changes the record layer until Joe or Dell
confirms it.

## Safe data flow

1. Call Mode starts the existing Quill recorder after the operator confirms that
   everyone has been told the call will be recorded.
2. Quill records separate microphone and system-audio channels, announces the
   recording, and transcribes locally.
3. The authenticated Deal Room supplies a short-lived exact index of open deal
   IDs and each deal's current participant IDs, roles, names, and allowed email
   addresses to the loopback Call Mode service.
4. A no-tools local model receives only the local transcript and that exact
   index. Its strict JSON output may reference only IDs in the index. Ambiguous
   references become review questions, never guessed writes.
5. A deterministic validator rejects invented IDs, recipients not attached to
   the deal, unsupported phase values, oversized evidence, and malformed tasks
   or drafts.
6. The validator writes a mode-0600 local review pack. Only sanitized phase,
   next-step, activity, and meeting proposals enter the existing capture queue.
   Raw transcript and full email bodies stay local.
7. The Deal Room renders the local report and email-draft cards. Approving an
   email card calls the loopback service, which invokes `bin/outlook-draft.py`.
   That helper has no send path.
8. Audio and transcript become purge-eligible only after every proposal is
   dispositioned and a session-level aggregate report is confirmed.

## Required contracts for the next release

### Exact call index

The browser builds this from the existing board and deal-detail reads. Each
entry contains an immutable deal ID, display name, and current participants.
The local validator treats this as the entire allowlist for attribution and
recipient selection.

### Local review pack

The pack has these top-level sections:

- `deal_updates`: exact deal ID, summary, proposed phase/next-step/activity, and
  confidence/evidence;
- `assigned_actions`: exact owner (`joe` or `dell`), exact deal ID, action, and
  due date when stated;
- `external_drafts`: exact deal and participant IDs, recipient email from the
  index, subject, body, and why the recipient needs the update;
- `unresolved`: the source reference and the question a human must answer;
- `call_summary`: an attributed aggregate narrative that replaces the raw call.

### Human gates

- Record changes: existing capture candidate confirmation.
- Joe/Dell assignment: explicit owner-aware action confirmation.
- External draft: explicit per-draft approval before Outlook Draft creation.
- Send: always performed by a human in Outlook.

### Retention gate

The current `ingested.json` marker is insufficient for a multi-deal call because
one confirmed meeting activity can create it while other candidates are still
pending. The next release must require both `pending == 0` and a confirmed
session-level aggregate report before writing the purge marker.

## Non-goals

- biometric voice recognition or retained third-party voiceprints;
- fuzzy deal or recipient assignment when an exact ID is unavailable;
- storing raw transcript or full draft bodies in the capture Worker;
- automatic external sending;
- a second audio recorder or transcription engine.

---
name: engineering-slice
description: On-demand Engineering Passport execution contract; never scheduled.
---

# Engineering Passport Slice

This is an on-demand control-plane definition, not a recurring task. Never
create a provider schedule, cron entry, automation, or fallback launcher for
it. Admission occurs only through `admit-engineering-slice` after an exact
accepted Work Request plan has been projected into a typed slice plan.

The server selects the Codex Desktop adapter, binds a fresh native session,
issues the immutable execution envelope, and enqueues the fixed shadow-mode
`ops.job`. The executor may return only a lease-bound typed receipt. Its claim
does not complete the slice until a different actor records an evidence-backed
review with `review-engineering-slice`.

Never accept caller-selected tenant, sponsor, identity, authority, model,
provider, adapter, or session continuity. Never use inherited transcripts,
silently widen a slice, bypass unresolved dependencies, or treat an executor
claim as independent verification.

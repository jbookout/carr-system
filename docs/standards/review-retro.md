# Standards from the 2026-10-05 review retro

Read these when changing the corresponding code. Counts are distinct PRs in the dated [evidence snapshot](../../audits/review-retro-standards.v1.json), including resolved findings; they are not current defect counts.

- Reuse the owning policy predicate/configuration; adapters may translate inputs and errors but must not redefine eligibility, limits, dates, or permissions. (20 PRs; `single-policy-authority`.)
- Exercise the owning production entry point without replacing the predicate/transaction under test; include a counterexample that fails if that behavior is removed or replaced. (7 PRs; `behavior-binding-tests`.)
- For changed inventoried source, run the current entry-class frontier check and deliver its required bindings; preserve frozen seals and honor current exclusions. (3 PRs; `sealed-source-cochange`.)
- Read Git filenames as raw NUL-delimited bytes through the owning path reader; preserve whitespace and undecodable bytes, and test CR/LF, quoting, non-ASCII, and literal pathspecs. (5 PRs; `lossless-git-paths`.)
- Record dedup/seen success only after the required durable write or delivery; retain retryable per-destination state after partial failure. (3 PRs; `ack-after-delivery`.)
- Prove changed SQL through the registered handler with the deployed role and actor/tenant context on disposable PostgreSQL; include helper, column, and sequence privileges. (4 PRs; `runtime-role-proof`.)
- Exercise new verbs through executeRegisteredTool and ship required operation/source registrations with the handler; raw-handler tests alone do not prove reachability. (3 PRs; `registered-ingress`.)
- Stop and reap the entire owned process tree on success, failure, timeout, and cancellation before releasing locks or publishing cleanup success. (6 PRs; `process-tree-ownership`.)
- Compare live selectors to the current contract and historical assertions to frozen versions; update exporter/acceptance consumers when a successor changes their contract. (3 PRs; `successor-consumers`.)
- Check caps/holds and acquire the paid-work claim in one cross-process transaction; test synchronized concurrent admissions and refuse unreadable accounting. (3 PRs; `atomic-paid-admission`.)
- Serialize shared-file read/modify/write and publish through a unique temporary file plus atomic replacement; reject stale writers and preserve the last good file on failure. (4 PRs; `atomic-file-publication`.)
- Verify the expected affected rows and saved acknowledgement before reporting success or changing UI/event state; preserve retry identity for an uncertain outcome. (3 PRs; `persisted-effect-readback`.)
- Bound the entire attempt with one monotonic deadline propagated across redirects, body reads, probes, and shutdown; test slow-drip and nested-call stalls. (4 PRs; `elapsed-deadlines`.)

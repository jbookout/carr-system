# Secrets Inventory + Send-Capability Audit

*Created 2026-07-30 by the record-layer build session (addendum A14/A15). Lives in the repo because ops config belongs beside the code that uses it; contains NO secret VALUES, ever — names, scopes, locations, blast radius only. Update on every credential added/rotated; the quarterly restore drill re-verifies this table.*

## The rule this file enforces (A15)

"No send tool" is true of the MCP server and exactly one hop wide. So every credential is classified by OUTBOUND CAPABILITY, and send-capable secrets live ONLY behind the human-gated route — never in the Worker, never in cron jobs, never in an automation scope.

| class | meaning | where allowed |
|---|---|---|
| SEND | can publish/message/email externally | human-gated route only (Joe triggers) |
| WRITE | mutates our own stores | MCP server write path, guarded jobs |
| READ | read/analytics only | cron jobs, Worker, read sessions |

## Current credentials (pre-build, exists today)

| name | class | scope | location | rotated | blast radius if leaked |
|---|---|---|---|---|---|
| Blotato API key | **SEND** (posts to X/LinkedIn/FB/IG) | Joe's connected socials | Cowork connector + TO VERIFY any local copy | never | posts as Joe on every platform; drain credits |
| GitHub PAT (jbookout/carr-system) | WRITE | scope TO VERIFY — A14 requires single-repo scope; verify at next use, narrow if broader | local git credential store | never | repo tamper; NO PII once dumps are age-encrypted (A9), which is why encryption starts with the FIRST dump |
| Salesforce login (Joe) | WRITE (in SF) | Joe's profile, export disabled | Joe's head + browser profile | corp policy | CARR corporate data; browser-bound by design, never stored by us |
| CoStar / GCCMLS / CREXI logins | READ | market data | Joe's browser profile (automation Chrome profile) | never | data-license exposure; browser-bound by design |
| Google Drive (both partners) | WRITE | the vault | OS-level (Drive clients) | n/a | the whole vault; governed by Google account 2FA |
| Apple Notes MCP | READ (of Notes) | call recordings sweep (addendum E) | local Mac only | n/a | call transcripts; local-only by design |
| Make.com scenarios | TO AUDIT | which scenarios can EMIT (mail/webhook out)? | Make account | never | any emit-capable scenario is SEND; audit before exporters go live (A15) |
| MailerLite (planned, #95) | **SEND** (email) | newsletter | not yet created | — | mass email as Joe; human-gated route only |

## To be created at build (Mon 8/3) — with their rules

| name | class | scope | location | rule |
|---|---|---|---|---|
| Neon: carr_writer URL | WRITE | base tables, MCP write path only | Worker secret (wrangler) | NEVER handed to a build/chat session; sessions get branch creds |
| Neon: carr_reader URL | READ | views only, zero base-table grants | Worker secret + cron env | the default connection for everything that reads |
| Neon: branch creds | WRITE (branch) | rehearsal branches | issued per build session, short-lived | the ONLY creds a build session touches (A14) |
| Neon API key | READ (control plane) | consumption polling (A13) | cron env | budget alerts at 50/80%; works while compute suspended |
| Cloudflare: Worker OAuth (IdP) | n/a (auth layer) | allow-list of exactly TWO identities (joe, dell) | Worker config | token issuance rejects everyone else (A10); short access, long refresh |
| Cloudflare: R2 keys | WRITE (objects) | attachments bucket | Worker secret | weekly R2 → second-store copy (attachments otherwise have zero backup) |
| Cloudflare: 2FA recovery codes | n/a | account recovery | OFFLINE with Joe (paper/vault, not a file) | A14 |
| age keypair (dump encryption) | n/a | decrypt pg_dumps | private key OFFLINE with Joe + one sealed copy; public key in repo | pg_dump encrypted from the FIRST dump, before any git commit (A9) |
| Healthchecks.io ping URLs | READ-ish (pings out to the service) | dead-man checks per job | cron env | the service does the alert-sending, so the no-send rule holds (A13) |
| ingest per-source tokens | WRITE (into inbox only) | one token per source (Notes sweep, share-sheet Shortcut, Make, MailerLite webhooks) | each sender + Worker secret | HMAC where the sender supports it; size cap; rate-limited (A11) |
| Blotato API key (metrics scope) | READ | analytics pull for placement_metric | cron env | if Blotato has no scoped keys (TO VERIFY at signup), the metrics job holds the full key and is therefore SEND-capable: it then runs ONLY in the human-gated environment, never open cron — decide at build, do not fudge |

## Standing obligations

- **Secret-scanning in CI** (A14): add a scan step to `tools/check.sh` or CI before Mon build ends; the repo must reject a commit containing a connection string.
- **Quarterly restore drill** (A14): decrypt last dump into a scratch branch, count rows vs `export_run`; log the drill in decision-history.
- **Legacy-consumer send audit** (A15): before exporters go live, walk every consumer of the exported files and record here which can send. Known today: the social pipeline reads calendar/batch files and posts via Blotato (SEND, human-gated at Joe's Blotato review); the Monday brief and heartbeat write files only (WRITE-local).
- **This file never contains a value.** A credential value found in the repo, the vault, or a chat transcript is an incident: rotate it, log it.

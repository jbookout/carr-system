# WO-3 — Deal Room route, browser gate, and PWA shell

## Contract

- `dealroom.doctorcre.com` is a third custom domain on the existing `carr-mcp`
  Worker. Host dispatch happens before the existing `OAuthProvider`, so the API
  custom domains and their provider-issued bearer-token behavior are unchanged.
- Static assets use one Workers Assets binding rooted at the config constant
  `../dealroom`. The Worker checks the no-build bundle at the root, also accepts
  a `dist/` bundle, and serves `public-shell/index.html` when neither exists.
- `/auth/login` and `/auth/callback` reuse the existing Google PKCE URL builder,
  code exchange, signed ID-token verifier, and `identity.js` allow-list. The
  required Google redirect URI is
  `https://dealroom.doctorcre.com/auth/callback`.
- Only the two emails already present in `identity.js` can create a session.
  Refused identities receive the static 403 page; it contains no email, actor,
  pipeline, or deal bytes.
- The session cookie is `__Host-dealroom_session`: opaque random value, Secure,
  HttpOnly, SameSite=Lax, and Path=/. The server stores only its SHA-256 lookup
  key in the existing `OAUTH_KV`. Sessions have a 12-hour idle lifetime, refresh
  inside the last hour of that window, and an unextendable seven-day maximum.
  `/auth/signout` deletes the KV session and expires both browser cookies.
- Cookie-authenticated `/pipeline/changes` calls the existing
  `pipelineChanges(request, client, actor)`. Cookie-authenticated `/mcp` calls
  the existing `dispatch(request, env, ctx, actor)`, including every existing
  verb and capability check. Both receive the same `actorFromProps` actor shape
  as provider bearer auth. Cookie-authenticated MCP POSTs additionally require
  the exact Deal Room origin to prevent same-site sibling-subdomain CSRF.
- Manifest, service worker, offline page, and icons are public shell resources.
  Deal data endpoints are network-only in the service worker; network failure
  returns an explicit 503 `{ state: "reconnecting", live: false }`. No cached
  deal response is used. Navigations fall back to a page that explicitly says
  no deal data is available offline.

## Structure decision

One Worker with one Assets binding is smaller than a second Worker and keeps the
actor-producing adapter adjacent to the unchanged API handlers. Host dispatch
fully separates browser-cookie auth from the existing API-host OAuthProvider.
No auth library, Worker, secret, database surface, or deployment unit was added.

## Human provisioning (not executed)

1. In Google Cloud Console, open the existing web OAuth client whose ID is in
   `GOOGLE_CLIENT_ID`. Add this exact authorized redirect URI without removing
   the existing API callback URI:
   `https://dealroom.doctorcre.com/auth/callback`.
2. Confirm the existing Worker secrets `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET` are present. No new secret is required. If either is
   absent, the authorized operator should run, from `mcp-server/`,
   `npx wrangler secret put GOOGLE_CLIENT_ID` and/or
   `npx wrangler secret put GOOGLE_CLIENT_SECRET`, entering values only at the
   prompt.
3. Confirm `doctorcre.com` remains an active Cloudflare zone and that no
   conflicting DNS record/custom hostname already claims
   `dealroom.doctorcre.com`. The orchestrating session can then run its normal
   `npx wrangler deploy`; the `custom_domain = true` route provisions the Worker
   custom domain. Do not create a separate proxied CNAME on top of it.
4. After deployment, open `https://dealroom.doctorcre.com/manifest.webmanifest`,
   complete sign-in once as each partner, verify a non-partner gets the 403
   refusal page, exercise sign-out, and install the PWA from each phone.

## Evidence

- `cd mcp-server && npm test`: 9 tests, 9 passed, 0 failed.
- `cd mcp-server && WRANGLER_LOG_PATH=/tmp/dealroom-wo3-wrangler.log npx wrangler deploy --dry-run --outdir /tmp/dealroom-wo3-dry-run`: passed; 10 static files read; `ASSETS`, `OAUTH_KV`, and `carr_documents` bindings resolved.
- `git diff --check`: passed.

No DNS change, secret write, database access, push, merge, or deployment was
performed.

## Deviation

The requested granular commits could not be created in this managed session.
`git add` failed before staging with:
`Unable to create '/Users/booko/carr-system/.git/worktrees/dealroom-wo3-route/index.lock': Operation not permitted`.
The patch and evidence are complete, but the orchestrating session must stage
and commit the files after it regains write access to the worktree Git metadata.

# Program 5 bounded forward-fix staging rebuild

Stable plan reference: `runbooks/program5-forward-fix-staging-rebuild.md#bounded-prefix-contract`.

This runbook governs a staging rehearsal only. It never approves, deploys, or
promotes Production traffic.

## Preconditions

- Use a newly provisioned isolated staging database/project. The historical
  project has the unrecoverable order hole `0314, 0316, 0317 applied; 0315
  absent`. Do not apply `0315` or `0315a` late, delete ledger rows, edit ledger
  content, or run direct repair SQL there.
- The replacement's applied ledger must be a contiguous prefix through
  `0315a_program5_bounded_forward_fix_rehearsal.sql`, and must contain neither
  `0316_rule_delivery_audit_counts.sql` nor
  `0317_atomic_rule_delivery_cutover.sql`.
- Rebuild the full source manifest from the reviewed SHA. Bind one immutable
  no-traffic candidate provider version before composing the contract; never
  substitute an upload or provider id after contract construction.
- Provision the scoped verifier login only through the sanctioned credential
  door. The secret must not be printed, copied into a receipt, or given to a
  builder process.

The verifier login is a trusted, narrow evidence-writer boundary: it may record
only a controller-validated readback after the routine-only prepare has derived
the database prefix. It is not a provider attestation key, so the controller
must supply its inputs only from the bounded `/release` response, the provider
version listing, and immutable Git contract reconstruction. Routine jobs never
receive this login.

## Bounded contract

Build and independently verify one `staging-forward-fix-prefix` contract. It
must bind all of the following:

1. Full source SHA, Worker artifact digest, candidate provider version, and the
   full source schema identity.
2. The target prefix through `0315a`, including prefix ledger/count/highest and
   the exact adjacent selected pair `0315`, `0315a` with file digests/ordinals.
3. The complete held-back suffix `0316`, `0317`, again with ordinals and file
   digests.

The contract is not a Production manifest. A Worker sourced from the full tree
must never be called schema-compatible with a target that has held-back files;
the ordinary Production wrapper continues to require its full-tree identity.

## Stop, rollback, and unknown state

- Stop before provider mutation if the source manifest, contract digest,
  candidate provider identity, staging ledger prefix, or verifier membership
  differs. Record no result or bundle.
- Once a staging provider mutation is claimed, do not retry with another tag.
  Read back the exact tag/version; if it is not conclusively serving, treat the
  attempt as unknown and leave the replacement project intact for reviewed
  recovery.
- A failed or unknown replacement staging project is discarded only through its
  provider's sanctioned project teardown after evidence is preserved. It is not
  repaired in place and it does not change Production.
- Rollback means restoring the replacement staging project to its known clean
  baseline or replacing it again. It never means reverting an applied migration
  or copying a production credential.

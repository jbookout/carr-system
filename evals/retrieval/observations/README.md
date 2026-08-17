# Measured situation-retrieval observations

These artifacts are rollback-only measurements of the canonical
`search_doctrine_situations` function against the isolated staging database.
They contain D2 metadata only: case IDs, source addresses, ranks, policy and
provenance identifiers, and database/function digests. They deliberately omit
query text, snippets, document bodies, credentials, and client data.

The 2026-08-17 observation is an honest failing baseline. Both shipped policies
passed the lifecycle and near-miss negative cases but returned no hits for the
five positive cases, so the policy selector reports `fail`. Read-only follow-up
showed that staging still has all ten seeded retrieval-curation proposals in
`pending`, no promoted concept/phrase/mapping rows, and no copies of the two
shared target doctrine sections. This evidence must not be used to freeze or
complete WR-AI-006.

The next valid measurement requires the isolated staging doctrine targets to be
loaded through a sanctioned shared-only path and the seeded curation proposals
to receive their human-only approval. Re-run the committed collector afterward;
it refuses to overwrite these artifacts and will create a new UTC/SHA-bound set.

# carr-system — repo pointer for sessions

Code lives here. Business, brand, persona, and deal context does NOT — by
design. Before concluding something "doesn't exist," query the CARR Record
Layer (the MCP connector's verbs, or `./run.sh retrieve "<question>"` locally):
it is the source of truth for doctrine, records, and brand; the Drive .md
files are generated snapshots of it.

Naming trap that cost a real search (2026-08-08): the app persona is
**Dr. CRE** — "Doc" is only the spoken nickname. Search "Dr. CRE" or the
slugs `dr-cre-concept` / `dr-cre-voice-doctrine`; searching "Doc" returns
audio slugs and noise. The mascot/visual design is in `dr-cre-concept-2026-08-01`
(vault record-layer folder + doctrine store); the living-orb panel visual is
specced in open loop #250 (its origination conversation predates capture and
was never recovered — loop #250 documents that gap).

Drive gotcha: Google Drive File Stream serves online-only placeholders; a
grep over the Drive mirror can miss content that exists. Materialize files
offline before trusting a negative grep there.

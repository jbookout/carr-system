# corpus/ — the doctrine mirror

**Do not edit anything in this folder. The Drive is canonical.**

Every file under `corpus/DNA/...` is a copy of the same path in the vault
(`.../My Drive/CARR AI/DNA/...`). Nothing reads these copies — not Cowork, not
Dell, not a session, not a pipeline. They exist so that doctrine gains what the
records already have: history, diffs, and an answer to "what did this rule say
before we changed it?"

Editing a file here therefore changes nothing anyone will ever read, and the
next `--sync` would overwrite it. `tools/corpus-sync.py` refuses to do that
silently: a mirror whose content no longer matches the manifest is a **loud
error**, and restoring it takes an explicit `--force`.

## Using it

```
python3 tools/corpus-sync.py            # check: 0 clean, 1 the Drive moved on, 2 the mirror was edited
python3 tools/corpus-sync.py --sync     # re-mirror what changed on the Drive, rewrite the manifest
```

`run.sh health` carries a corpus row, so the heartbeat says when doctrine has
changed on the Drive and the mirror has not caught up.

## What is in it

`corpus-set.tsv` is the list, one row per file with the reason it counts as
doctrine, and it states the in/out test. `manifest.json` is generated: source
path, sha256, size, source mtime, and when each file was mirrored, plus
anything skipped for being binary or over 5 MB.

Records are not doctrine and are not here: rosters, registries, ledgers,
queues, briefs, dated research, generated exports, indexes, session state. The
SOP class is deliberately absent too — those files are instructions for running
pipelines the record layer is rewriting right now, and the doctrine-vs-record
call on them is an open question for Joe (ORDER 30's log lists them).

*Phase 1, additive, 2026-07-31 (ORDER 30). Nothing about the Drive workflow
changed when this landed.*

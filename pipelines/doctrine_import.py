#!/usr/bin/env python3
"""doctrine_import.py — P4/P5 of the doctrine-store build: migrate vault
markdown into the store, batch by batch, with a ledger row per batch.

Design: design/doctrine-store-build-2026-08-07.md §6 P4-P5 (council decision
82a2fb62). The migration ledger (doctrine_migration_batch) records source
hashes BEFORE import so reconciliation is a comparison, not a memory.

    DATABASE_URL=... python3 pipelines/doctrine_import.py \
        --batch-no 1 --phase forced_early --class playbook \
        --files "path/a.md" "path/b.md" [--apply]

Dry by default: prints the parse plan (documents, sections, sizes) and writes
nothing. --apply imports inside one transaction per file and finishes the
batch row 'verified' only when every check passes.

IMPORT IS THE ONE GATE-EXEMPT WRITE PATH, on purpose and on the record:
legacy content predates the gates (writing-rules alone would trip the
banned-phrases gate it defines), and an import that rewrites history to pass
lint is not an import. The council gated this shape to migration tooling
(session_key='migration'); every post-import EDIT goes through the verbs and
their gates like everything else.

PARSE MODEL (deliberately simple, per the P2 body contract):
  * document slug = file stem, kebab-cased; title = first '# ' heading or stem
  * sections split on '## ' headings; content before the first '## ' becomes
    section_key 'preamble'; heading text kebab-cases into the section_key
  * body_text = the section's raw markdown (history preserved verbatim;
    structure migration for special classes like index is its own later pass)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

try:
    import psycopg
except ImportError:
    sys.exit("needs the repo venv (psycopg): .venv/bin/python")

MIGRATION_ACTOR_SLUG = "joe"          # imports attribute to the operating partner
SESSION_KEY = "migration"


def kebab(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "section"


def parse_file(path):
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    sha = hashlib.sha256(raw).hexdigest()
    title = None
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m:
        title = m.group(1).strip()
    stem = Path(path).stem
    # Generic stems (INDEX.md lives in half the folders) take their parent
    # folder into the slug, or every index after the first dies on the unique
    # constraint — caught by the batch-2 dry run, 2026-08-08.
    if stem.lower() in ("index", "readme"):
        parent = Path(path).parent.name
        slug = kebab(f"{parent}-{stem}") if parent and parent != "CARR AI" \
            else kebab(f"root-{stem}")
    else:
        slug = kebab(stem)
    parts = re.split(r"^##\s+(.+)$", text, flags=re.M)
    sections = []
    seen = {}
    def add(key, title_, body):
        if not body.strip():
            return
        base = kebab(key)
        n = seen.get(base, 0) + 1
        seen[base] = n
        sections.append({
            "section_key": base if n == 1 else f"{base}-{n}",
            "title": title_.strip() if title_ else None,
            "body_text": body.strip(),
        })
    if parts[0].strip():
        add("preamble", None, parts[0])
    for i in range(1, len(parts), 2):
        add(parts[i], parts[i], parts[i + 1] if i + 1 < len(parts) else "")
    return {"path": str(path), "sha256": sha, "slug": slug,
            "title": title or stem, "sections": sections}


def apply_file(cur, actor_id, doc, content_class):
    # Slug collision across folders (session-operating-style lives in both
    # DNA/Team and 00_Context): disambiguate with the parent folder rather
    # than aborting the batch. The title keeps the human name.
    cur.execute("select 1 from doctrine_document where slug=%s "
                "union all select 1 from doctrine_slug_alias where alias_slug=%s",
                (doc["slug"], doc["slug"]))
    if cur.fetchone():
        parent = kebab(Path(doc["path"]).parent.name)
        doc["slug"] = f"{parent}-{doc['slug']}"
    cur.execute(
        """insert into doctrine_document (slug, title, content_class, created_by,
             review_policy_id)
           values (%s,%s,%s,%s,
             (select id from doctrine_review_policy where %s = any(content_classes) limit 1))
           returning id, (select max_age_days from doctrine_review_policy
                           where %s = any(content_classes) limit 1)""",
        (doc["slug"], doc["title"], content_class, actor_id, content_class, content_class))
    doc_id, policy_days = cur.fetchone()
    cur.execute(
        """insert into doctrine_change_set (actor_id, session_key, idempotency_key, state, committed_at)
           values (%s,%s,%s,'committed',now()) returning id""",
        (actor_id, SESSION_KEY, f"import-{doc['slug']}-{doc['sha256'][:16]}"))
    cs_id = cur.fetchone()[0]
    for i, s in enumerate(doc["sections"]):
        cur.execute(
            """insert into doctrine_section (document_id, section_key, title, ordinal)
               values (%s,%s,%s,%s) returning id""",
            (doc_id, s["section_key"], s["title"], (i + 1) * 10))
        sec_id = cur.fetchone()[0]
        body = json.dumps({"text": s["body_text"]})
        h = hashlib.sha256(s["body_text"].encode()).hexdigest()
        cur.execute(
            """insert into doctrine_revision (section_id, version, change_set_id, actor_id,
                 session_key, body, plain_text, content_hash, commit_message)
               values (%s,1,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (sec_id, cs_id, actor_id, SESSION_KEY, body, s["body_text"], h,
             f"imported from {doc['path']}"))
        rev_id = cur.fetchone()[0]
        cur.execute(
            """update doctrine_section set current_revision_id=%s, current_version=1,
                 body_hash=%s,
                 review_after = case when %s::int is not null
                   then now() + (%s::int || ' days')::interval end
               where id=%s""",
            (rev_id, h, policy_days, policy_days, sec_id))
    cur.execute("update doctrine_meta set generation = generation + 1, updated_at = now() "
                "where id = 1 returning generation")
    gen = cur.fetchone()[0]
    # snapshot rebuild — same SQL shape as the worker's rebuildSnapshot
    cur.execute(
        """insert into doctrine_snapshot (document_id, generation, snapshot_json, content_hash)
           select d.id, %s,
                  jsonb_build_object(
                    'document', jsonb_build_object('id', d.id, 'slug', d.slug, 'title', d.title,
                      'content_class', d.content_class, 'visibility', d.visibility),
                    'sections', coalesce((select jsonb_agg(jsonb_build_object(
                        'section_id', s.id, 'section_key', s.section_key, 'title', s.title,
                        'ordinal', s.ordinal, 'version', s.current_version, 'status', s.status,
                        'body', r.body, 'content_hash', r.content_hash) order by s.ordinal)
                      from doctrine_section s
                      left join doctrine_revision r on r.id = s.current_revision_id
                      where s.document_id = d.id and s.status='active'), '[]'::jsonb),
                    'edges_out', '[]'::jsonb),
                  md5((select coalesce(string_agg(s.body_hash,'' order by s.ordinal),'')
                        from doctrine_section s where s.document_id=d.id))
             from doctrine_document d where d.id = %s
           on conflict (document_id) do update set generation=excluded.generation,
             snapshot_json=excluded.snapshot_json, content_hash=excluded.content_hash,
             built_at=now()""", (gen, doc_id))
    return doc_id, len(doc["sections"])


def verify_file(cur, doc, doc_id):
    """The reconciliation check: what landed matches what was parsed."""
    cur.execute("select count(*) from doctrine_section where document_id=%s", (doc_id,))
    n = cur.fetchone()[0]
    if n != len(doc["sections"]):
        return f"section count mismatch: parsed {len(doc['sections'])}, landed {n}"
    cur.execute(
        """select s.section_key, r.plain_text from doctrine_section s
           join doctrine_revision r on r.id = s.current_revision_id
           where s.document_id=%s""", (doc_id,))
    landed = dict(cur.fetchall())
    for s in doc["sections"]:
        if landed.get(s["section_key"], "").strip() != s["body_text"].strip():
            return f"body mismatch on section '{s['section_key']}'"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-no", type=int, required=True)
    ap.add_argument("--phase", choices=["forced_early", "bounded", "cutoff"], required=True)
    ap.add_argument("--class", dest="content_class", required=True,
                    choices=["playbook", "sop", "index", "reference",
                             "dossier_narrative", "distillation"])
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    docs = [parse_file(p) for p in a.files]
    for d in docs:
        print(f"  {d['slug']}: {len(d['sections'])} section(s) "
              f"[{', '.join(s['section_key'] for s in d['sections'][:8])}"
              f"{'…' if len(d['sections']) > 8 else ''}] sha={d['sha256'][:12]}")
    empty = [d["slug"] for d in docs if not d["sections"]]
    if empty:
        sys.exit(f"REFUSED: no sections parsed from: {', '.join(empty)}")
    if not a.apply:
        print(f"dry run — {len(docs)} file(s) parse clean; pass --apply to import")
        return

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("select id from actor where slug=%s", (MIGRATION_ACTOR_SLUG,))
            actor_id = cur.fetchone()[0]
            cur.execute(
                """insert into doctrine_migration_batch (batch_no, phase, source_paths,
                     source_hashes, state, started_at)
                   values (%s,%s,%s,%s,'running',now()) returning id""",
                (a.batch_no, a.phase, a.files,
                 json.dumps({d["path"]: d["sha256"] for d in docs})))
            batch_id = cur.fetchone()[0]
            counts, errors = {}, []
            for d in docs:
                doc_id, n = apply_file(cur, actor_id, d, a.content_class)
                err = verify_file(cur, d, doc_id)
                counts[d["slug"]] = n
                if err:
                    errors.append(f"{d['slug']}: {err}")
            if errors:
                conn.rollback()
                with conn.cursor() as c2:
                    c2.execute(
                        """insert into doctrine_migration_batch (batch_no, phase, source_paths,
                             source_hashes, state, error, finished_at)
                           values (%s,%s,%s,%s,'failed',%s,now())""",
                        (a.batch_no, a.phase, a.files, json.dumps({}), "; ".join(errors)))
                    conn.commit()
                sys.exit("FAILED (rolled back): " + "; ".join(errors))
            cur.execute(
                """update doctrine_migration_batch set state='verified', row_counts=%s,
                     finished_at=now() where id=%s""",
                (json.dumps(counts), batch_id))
        conn.commit()
    print(f"batch {a.batch_no} VERIFIED: "
          + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()

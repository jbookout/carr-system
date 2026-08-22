#!/usr/bin/env python3
"""Collect rollback-only D2 measurements from situation retrieval.

The collector is staging-only.  Invoke it through the separately isolated
staging project so ``DATABASE_URL`` is derived without being printed:

    tools/db-tap.py --project staging run \
      ops/collect-situation-retrieval-observation.py \
      --environment staging --output-dir /tmp/situation-observations

It performs every canonical search in one explicit REPEATABLE READ transaction
and rolls that transaction back on both success and failure.  This matters
because ``search_doctrine_situations`` appends a hashed query receipt; the
collector intentionally leaves no durable receipt or other database mutation.
The emitted D2 metadata contains case IDs, source identifiers and provenance,
never fixture query text, titles, or snippets.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any, Callable
from urllib.parse import urlsplit


REPO = pathlib.Path(__file__).resolve().parent.parent
SUITE_SCHEMA = "carr-situation-retrieval-suite-v1"
GOLDEN_SCHEMA = "carr-retrieval-golden-v1"
OBSERVATION_SCHEMA = "carr-situation-retrieval-observation-v1"
POLICIES = ("lexical-dominant-v1", "coequal-normalized-v1")
# Six arguments since 0281: the trailing boolean is the zero-hit fallback,
# OFF by default, so every five-argument call in this collector still
# measures the strict lane the golden suite gates.
RANKER_SIGNATURE = "search_doctrine_situations(text,uuid,text[],integer,text,boolean)"
RETRIEVAL_MIGRATION = "0135_situation_retrieval.sql"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
COLLECTOR_REL_PATH = pathlib.Path("ops/collect-situation-retrieval-observation.py")
DEFAULT_SUITE_REL_PATH = pathlib.Path("evals/retrieval/situation-golden-queries.2026-08-16.v1.json")
DEFAULT_GOLDEN_REL_PATH = pathlib.Path("evals/retrieval/golden-queries.v1.json")
IDENTITY_PATHS = (COLLECTOR_REL_PATH, DEFAULT_SUITE_REL_PATH, DEFAULT_GOLDEN_REL_PATH)


class CollectorRefusal(ValueError):
    """The target or observed data cannot truthfully be marked measured."""


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def run_git(repo: pathlib.Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)


def collector_git_sha(repo: pathlib.Path = REPO,
                      git_runner: Callable[[pathlib.Path, list[str]], subprocess.CompletedProcess[str]] = run_git) -> str:
    """Bind to clean HEAD versions of the collector and its exact D2 fixtures.

    Global worktree cleanliness is intentionally irrelevant: other sessions'
    files are not evidence.  The three scoped inputs must nevertheless be
    tracked *at HEAD* and have no staged or unstaged difference from it.
    """
    try:
        result = git_runner(repo, ["rev-parse", "HEAD"])
    except (OSError, subprocess.SubprocessError) as exc:
        raise CollectorRefusal("collector git HEAD is unavailable") from exc
    if result.returncode != 0:
        raise CollectorRefusal("collector git HEAD is unavailable")
    sha = result.stdout.strip()
    if not SHA1.fullmatch(sha):
        raise CollectorRefusal("collector git HEAD must be an exact 40-character lowercase SHA")
    for rel_path in IDENTITY_PATHS:
        try:
            present_at_head = git_runner(repo, ["cat-file", "-e", f"HEAD:{rel_path.as_posix()}"])
            differs = git_runner(repo, ["diff", "--quiet", "HEAD", "--", rel_path.as_posix()])
        except (OSError, subprocess.SubprocessError) as exc:
            raise CollectorRefusal("could not verify collector evidence scope against HEAD") from exc
        if present_at_head.returncode != 0:
            raise CollectorRefusal(f"evidence scope file is not tracked at HEAD: {rel_path}")
        if differs.returncode == 1:
            raise CollectorRefusal(f"evidence scope file differs from HEAD: {rel_path}")
        if differs.returncode != 0:
            raise CollectorRefusal(f"could not compare evidence scope file to HEAD: {rel_path}")
    return sha


def utc_filename_timestamp(observed_at: str) -> str:
    parsed = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observed_at must carry a UTC offset")
    return parsed.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_observed_at(value: str | None = None) -> str:
    parsed = (dt.datetime.now(dt.timezone.utc) if value is None
              else dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise CollectorRefusal("observed_at must be UTC")
    return parsed.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: pathlib.Path, expected_schema: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != expected_schema:
        raise ValueError(f"{path}: unsupported fixture schema")
    if value.get("data_class") != "D2_internal_metadata":
        raise ValueError(f"{path}: fixture must be D2_internal_metadata")
    return value


def expectation(case: dict[str, Any]) -> dict[str, Any]:
    """Normalize the gate-relevant expectation shape before exact comparison."""
    return {
        "required_targets": case.get("required_targets", []),
        "expect_no_targets": case.get("expect_no_targets", []),
        "expect_no_hits": case.get("expect_no_hits", False),
        "require_all_targets": case.get("require_all_targets", False),
        "top_k": int(case.get("top_k", 3)),
    }


def bind_case_queries(suite: dict[str, Any], golden: dict[str, Any]) -> dict[str, str]:
    """Use existing D2 query text only when expectations exactly agree."""
    golden_cases = {str(case.get("id")): case for case in golden.get("cases", []) if isinstance(case, dict)}
    queries: dict[str, str] = {}
    for case in suite.get("cases", []):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError("situation suite has malformed case")
        source = golden_cases.get(case["id"])
        if not isinstance(source, dict):
            raise ValueError(f"{case['id']}: golden fixture case is missing")
        doctrine = source.get("engines", {}).get("doctrine_postgres_fts")
        if not isinstance(doctrine, dict) or expectation(case) != expectation(doctrine):
            raise ValueError(f"{case['id']}: situation expectations do not exactly match golden fixture")
        query = source.get("query")
        if not isinstance(query, str) or not query:
            raise ValueError(f"{case['id']}: golden fixture query is missing")
        queries[case["id"]] = query
    if not queries:
        raise ValueError("situation suite must contain cases")
    return queries


def refuse_unsafe_target(environment: str, dsn: str, inherited_env: dict[str, str]) -> None:
    """Only an explicitly staging-labelled, non-production target is permitted."""
    if environment != "staging":
        raise CollectorRefusal("only --environment staging is allowed; Production and non-staging targets are refused")
    if inherited_env.get("CARR_ENV", "").strip().lower() == "production":
        raise CollectorRefusal("CARR_ENV=production is refused")
    host = (urlsplit(dsn).hostname or "").lower()
    if "production" in host:
        raise CollectorRefusal("production-labelled database host is refused")


def policy_metadata(cursor: Any, policy: str) -> dict[str, Any]:
    cursor.execute(
        """select version, formula, config, status, is_default, golden_suite_digest
             from retrieval_ranking_policy
             where policy_id=%s and status in ('candidate', 'active')""",
        (policy,),
    )
    row = cursor.fetchone()
    if row is None:
        raise CollectorRefusal(f"shipped policy {policy} is absent or inactive")
    config = row["config"]
    if not isinstance(config, dict):
        raise CollectorRefusal(f"shipped policy {policy} has malformed config")
    golden_suite_digest = str(row["golden_suite_digest"])
    if not SHA256.fullmatch(golden_suite_digest):
        raise CollectorRefusal(f"shipped policy {policy} has malformed golden suite digest")
    return {
        "version": int(row["version"]),
        "formula": str(row["formula"]),
        "status": str(row["status"]),
        "is_default": bool(row["is_default"]),
        "golden_suite_digest": golden_suite_digest,
        "config_digest": canonical_digest(config),
    }


def database_metadata(cursor: Any) -> dict[str, Any]:
    """Bind the measured result to the applied migration and exact ranker body."""
    cursor.execute("select filename, sha256, applied_at from schema_migrations where filename=%s", (RETRIEVAL_MIGRATION,))
    migration = cursor.fetchone()
    if migration is None or migration.get("filename") != RETRIEVAL_MIGRATION or not SHA256.fullmatch(str(migration.get("sha256", ""))):
        raise CollectorRefusal(f"required schema migration {RETRIEVAL_MIGRATION} is not applied with a SHA-256")
    cursor.execute("select pg_get_functiondef(%s::regprocedure) as definition", (RANKER_SIGNATURE,))
    ranker = cursor.fetchone()
    definition = ranker.get("definition") if ranker else None
    if not isinstance(definition, str) or not definition:
        raise CollectorRefusal("canonical ranker definition is unavailable")
    return {
        "schema_migration": {
            "filename": RETRIEVAL_MIGRATION,
            "sha256": str(migration["sha256"]),
            "applied_at": str(migration.get("applied_at")),
        },
        "ranker_signature": RANKER_SIGNATURE,
        "ranker_definition_sha256": hashlib.sha256(definition.encode("utf-8")).hexdigest(),
    }


def verify_current_sources(cursor: Any, hits: list[dict[str, Any]]) -> None:
    """Verify each returned source is active, shared, and at its exact revision."""
    ids = [hit["source"]["section_id"] for hit in hits]
    if not ids:
        return
    cursor.execute(
        """select s.id as section_id, s.status, s.current_revision_id,
                  r.id as revision_id, d.slug as document_slug, d.visibility
             from doctrine_section s
             join doctrine_document d on d.id=s.document_id
             join doctrine_revision r on r.id=s.current_revision_id
             where s.id = any(%s::uuid[])""",
        (ids,),
    )
    verified = {str(row["section_id"]): row for row in cursor.fetchall()}
    for hit in hits:
        section_id = hit["source"]["section_id"]
        row = verified.get(section_id)
        if (row is None or row.get("status") != "active" or row.get("visibility") != "shared"
                or str(row.get("current_revision_id")) != str(row.get("revision_id"))
                or row.get("document_slug") != hit["source"]["document_slug"]):
            raise CollectorRefusal("canonical search returned a non-current, non-shared, or revision-mismatched source")
        # The ranker returns section_id as a column; bind that identity into the
        # provenance evidence only after the live current-source readback agrees.
        hit["provenance"] = {**hit["provenance"], "section_id": section_id}


def normalized_hits(rows: list[dict[str, Any]], policy: str, policy_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only source identity and complete canonical provenance (no content)."""
    hits: list[dict[str, Any]] = []
    for row in rows:
        doc_slug, section_key, section_id = row.get("doc_slug"), row.get("section_key"), row.get("section_id")
        provenance = row.get("provenance")
        if not isinstance(doc_slug, str) or not doc_slug or not isinstance(section_key, str) or not section_key or section_id is None:
            raise CollectorRefusal("canonical search returned an incomplete source identity")
        if not isinstance(provenance, dict) or provenance.get("complete") is not True:
            raise CollectorRefusal("canonical search returned incomplete provenance")
        if (provenance.get("policy_id") != policy
                or int(provenance.get("policy_version", -1)) != policy_metadata["version"]):
            raise CollectorRefusal("canonical search provenance does not bind the requested policy version")
        hits.append({
            "target": f"{doc_slug}#{section_key}",
            "source": {"section_id": str(section_id), "document_slug": doc_slug},
            # Canonical search derives candidates from current_retrievable_doctrine
            # before ranking; a returned row is therefore a current source.
            "current": True,
            "rank": len(hits) + 1,
            "provenance": provenance,
        })
    return hits


def canonical_search(cursor: Any, query: str, policy: str, limit: int) -> list[dict[str, Any]]:
    cursor.execute(
        """select section_id, doc_slug, section_key, lexical_score, concept_score,
                  final_score, provenance
             from search_doctrine_situations(%s, null, null, %s, %s)
             order by final_score desc, concept_score desc, lexical_score desc, section_key asc""",
        (query, limit, policy),
    )
    return [dict(row) for row in cursor.fetchall()]


def collect(connection: Any, suite: dict[str, Any], queries: dict[str, str], *, environment: str,
            fixture_digests: dict[str, str], collector_sha: str, observed_at: str | None = None) -> dict[str, dict[str, Any]]:
    """Collect both policies; refuse rather than label a failed replay measured."""
    observed_at = utc_observed_at(observed_at)
    observations: dict[str, dict[str, Any]] = {}
    with connection.cursor() as cursor:
        db_evidence = database_metadata(cursor)
        for policy in POLICIES:
            policy_evidence = policy_metadata(cursor, policy)
            cases: dict[str, list[dict[str, Any]]] = {}
            mismatches = 0
            for case in suite["cases"]:
                case_id = case["id"]
                limit = int(case.get("top_k", 3))
                first = normalized_hits(canonical_search(cursor, queries[case_id], policy, limit), policy, policy_evidence)
                verify_current_sources(cursor, first)
                second = normalized_hits(canonical_search(cursor, queries[case_id], policy, limit), policy, policy_evidence)
                verify_current_sources(cursor, second)
                if first != second:
                    mismatches += 1
                cases[case_id] = first
            if mismatches:
                raise CollectorRefusal(f"{policy}: deterministic replay mismatched for {mismatches} case(s)")
            observations[policy] = {
                "schema_version": OBSERVATION_SCHEMA,
                "suite_id": suite["suite_id"],
                "scope_ref": suite["scope_ref"],
                "data_class": "D2_internal_metadata",
                "status": "measured",
                "observed_at": observed_at,
                "collector_git_sha": collector_sha,
                "environment": environment,
                "source": "search_doctrine_situations",
                "fixture_digests": fixture_digests,
                "database_evidence": db_evidence,
                "scope_applied_before_rank": True,
                "deterministic_replay_mismatches": 0,
                "policy_id": policy,
                "policy_version": policy_evidence["version"],
                "policy_formula": policy_evidence["formula"],
                "policy_status": policy_evidence["status"],
                "policy_is_default": policy_evidence["is_default"],
                "policy_golden_suite_digest": policy_evidence["golden_suite_digest"],
                "policy_config_digest": policy_evidence["config_digest"],
                "cases": cases,
            }
    return observations


def collect_rollback_only(connection: Any, *args: Any, **kwargs: Any) -> dict[str, dict[str, Any]]:
    """Use a stable snapshot and always discard canonical query-log writes."""
    try:
        connection.execute("begin transaction isolation level repeatable read")
        return collect(connection, *args, **kwargs)
    finally:
        connection.rollback()


def write_observations(output_dir: pathlib.Path, observations: dict[str, dict[str, Any]]) -> list[pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    planned = []
    for policy, observation in observations.items():
        timestamp = utc_filename_timestamp(str(observation["observed_at"]))
        sha = str(observation["collector_git_sha"])
        if not SHA1.fullmatch(sha):
            raise CollectorRefusal("observation collector_git_sha must be an exact 40-character lowercase SHA")
        # Repository path hygiene rejects version-token filenames. The payload
        # retains the exact policy_id; the human filename uses its stable label.
        policy_label = policy.removesuffix("-v1")
        path = output_dir / f"situation-retrieval.{policy_label}.measured.{timestamp}.{sha}.json"
        if path.exists():
            raise FileExistsError(f"refusing to overwrite evidence artifact: {path}")
        planned.append((path, observation))
    written = []
    for path, observation in planned:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(observation, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", required=True, choices=("staging",))
    parser.add_argument("--dsn-env", default="DATABASE_URL", help="environment variable holding the staging DSN")
    parser.add_argument("--suite", type=pathlib.Path,
                        default=REPO / DEFAULT_SUITE_REL_PATH)
    parser.add_argument("--golden-queries", type=pathlib.Path,
                        default=REPO / DEFAULT_GOLDEN_REL_PATH)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    dsn = os.environ.get(args.dsn_env, "").strip()
    if not dsn:
        raise CollectorRefusal(f"DSN environment variable {args.dsn_env} is required")
    refuse_unsafe_target(args.environment, dsn, dict(os.environ))
    if (args.suite.resolve() != (REPO / DEFAULT_SUITE_REL_PATH).resolve()
            or args.golden_queries.resolve() != (REPO / DEFAULT_GOLDEN_REL_PATH).resolve()):
        raise CollectorRefusal("only the committed default D2 retrieval fixtures may produce measured evidence")
    suite = load_json(args.suite, SUITE_SCHEMA)
    golden = load_json(args.golden_queries, GOLDEN_SCHEMA)
    queries = bind_case_queries(suite, golden)
    fixture_digests = {"situation_suite": canonical_digest(suite), "golden_queries": canonical_digest(golden)}
    collector_sha = collector_git_sha()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("psycopg is required to collect measured observations") from exc
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        observations = collect_rollback_only(connection, suite, queries, environment=args.environment,
                                               fixture_digests=fixture_digests, collector_sha=collector_sha)
    write_observations(args.output_dir, observations)
    print(f"measured situation retrieval observations written for {len(observations)} policies")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CollectorRefusal, OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"situation-retrieval-observation: REFUSED — {exc}", file=sys.stderr)
        raise SystemExit(2)

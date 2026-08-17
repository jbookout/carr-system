#!/usr/bin/env python3
"""Seed exactly two shared D2 retrieval targets into isolated staging.

This is deliberately narrower than the production doctrine importer.  It has
no source/project/branch/DSN switches: the two canonical source files, their
hashes, the allowed classes, and the one permitted section from each are fixed
below.  The only database connection it derives is the isolated staging
project's ``app_writer`` role.  It never reads Production and it never touches
retrieval proposals.

Dry-run is the default.  ``--apply`` inserts both target sections in one
transaction and leaves a verified ``doctrine_migration_batch`` receipt in the
staging project.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Sequence

try:
    import psycopg
except ImportError:
    sys.exit("staging retrieval seed requires the repo virtualenv (psycopg)")


REPO = pathlib.Path(__file__).resolve().parent.parent
VAULT = pathlib.Path(
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/"
    "My Drive/CARR AI"
)


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


db_tap = load_module("staging_seed_db_tap", REPO / "tools" / "db-tap.py")
doctrine_import = load_module("staging_seed_doctrine_import", REPO / "pipelines" / "doctrine_import.py")
provision = load_module(
    "staging_seed_role_authority", REPO / "tools" / "provision-staging-app-writer.py"
)


class SeedRefusal(RuntimeError):
    """An input or environment would exceed the small staging seed authority."""


@dataclass(frozen=True)
class SeedTarget:
    source_path: pathlib.Path
    source_sha256: str
    slug: str
    section_key: str
    content_class: str

    @property
    def address(self) -> str:
        return f"{self.slug}#{self.section_key}"


@dataclass(frozen=True)
class ParsedTarget:
    target: SeedTarget
    document: dict[str, Any]
    body_sha256: str

    @property
    def address(self) -> str:
        return self.target.address


@dataclass(frozen=True)
class _FixtureManifestAuthority:
    """A hermetic-test-only authority for a temporary fixed manifest.

    The production CLI never constructs this object and accepts no argument
    that could select it.  It lets the self-test and disposable-DB gate use
    throwaway source files without weakening the production manifest check.
    """

    targets: tuple[SeedTarget, ...]


# These are the two shared D2 sections required by WR-AI-006.  The full files
# contain more shared doctrine; their fixed source hashes make a changed source
# a refusal rather than silently expanding this staging seed.
TARGETS: tuple[SeedTarget, ...] = (
    SeedTarget(
        VAULT / "DNA/Deal Management/record-layer/runbook.md",
        "78b80176482bfffa4fe463bd436d715f56e003d5e65f03c645e8b25f38c3a4e3",
        "runbook",
        "diagnosis-checklist-in-order-2-minutes",
        "sop",
    ),
    SeedTarget(
        VAULT / "00_Context/playbook-review.md",
        "e21ff9bbe18f994ea3173aa118e14e686753974cc2b09a81da4694d658f16d97",
        "playbook-review",
        "preamble",
        "playbook",
    ),
)
ALLOWED_CLASSES = {"playbook", "sop"}


def reject_unsafe_environment() -> None:
    if os.environ.get("CARR_BREAK_GLASS"):
        raise SeedRefusal("break-glass is forbidden for the staging retrieval seed")
    if os.environ.get("DATABASE_URL"):
        raise SeedRefusal("DATABASE_URL is forbidden; this seed derives only staging app_writer credentials")


def staging_app_writer_dsn() -> str:
    """Derive the one allowed connection without exposing it to argv or output."""
    reject_unsafe_environment()
    return db_tap.dsn(project="staging", role_name="app_writer")


def _target_identity(target: SeedTarget) -> tuple[pathlib.Path, str, str, str, str]:
    """Every field that constitutes the bounded source authority."""
    return (
        target.source_path,
        target.source_sha256,
        target.slug,
        target.section_key,
        target.content_class,
    )


def _validate_target_manifest(
    targets: Sequence[SeedTarget], approved_targets: Sequence[SeedTarget]
) -> None:
    if len(targets) != 2:
        raise SeedRefusal("the staging seed manifest must contain exactly two targets")
    if tuple(_target_identity(target) for target in targets) != tuple(
        _target_identity(target) for target in approved_targets
    ):
        raise SeedRefusal(
            "the staging seed manifest differs from the exact approved canonical "
            "path, source hash, slug, section, or class"
        )
    if len({target.source_path for target in targets}) != 2:
        raise SeedRefusal("each target must come from a distinct canonical source")
    for target in targets:
        if target.content_class not in ALLOWED_CLASSES:
            raise SeedRefusal(f"{target.address}: content class is not shared D2 doctrine")
        if not target.source_path.is_absolute() or not str(target.source_path).startswith(str(VAULT) + os.sep):
            raise SeedRefusal(f"{target.address}: source is outside the canonical CARR vault")
        if len(target.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in target.source_sha256):
            raise SeedRefusal(f"{target.address}: source hash is invalid")


def validate_target_manifest(targets: Sequence[SeedTarget]) -> None:
    """Validate only the immutable production manifest."""
    _validate_target_manifest(targets, TARGETS)


def _fixture_manifest_authority_for_test(
    targets: Sequence[SeedTarget],
) -> _FixtureManifestAuthority:
    """Create a hermetic-only authority; unreachable from the production CLI."""
    return _FixtureManifestAuthority(tuple(targets))


def _parse_manifest(
    targets: Sequence[SeedTarget], approved_targets: Sequence[SeedTarget]
) -> list[ParsedTarget]:
    """Parse the canonical files, then retain only the explicitly named section."""
    _validate_target_manifest(targets, approved_targets)
    parsed: list[ParsedTarget] = []
    for target in targets:
        raw = target.source_path.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        if actual_sha != target.source_sha256:
            raise SeedRefusal(
                f"{target.address}: canonical source hash mismatch "
                f"(expected {target.source_sha256}, got {actual_sha})"
            )
        document = doctrine_import.parse_file(str(target.source_path))
        if document["slug"] != target.slug:
            raise SeedRefusal(f"{target.address}: parsed source slug is {document['slug']!r}")
        sections = [section for section in document["sections"] if section["section_key"] == target.section_key]
        if len(sections) != 1:
            raise SeedRefusal(f"{target.address}: required section was not uniquely parsed")
        filtered_document = {**document, "sections": [sections[0].copy()]}
        body_sha = hashlib.sha256(sections[0]["body_text"].encode()).hexdigest()
        parsed.append(ParsedTarget(target, filtered_document, body_sha))
    return parsed


def parse_targets() -> list[ParsedTarget]:
    """Parse precisely the two immutable production targets; no target injection."""
    return _parse_manifest(TARGETS, TARGETS)


def _parse_fixture_targets_for_test(
    targets: Sequence[SeedTarget], authority: _FixtureManifestAuthority
) -> list[ParsedTarget]:
    """Hermetic seam used only by repo tests and the disposable DB gate."""
    if not isinstance(authority, _FixtureManifestAuthority):
        raise SeedRefusal("fixture manifest authority is required for test-only target injection")
    return _parse_manifest(targets, authority.targets)


def manifest(parsed: Sequence[ParsedTarget]) -> dict[str, Any]:
    return {
        "schema_version": "carr-staging-retrieval-doctrine-seed-v1",
        "environment": "staging",
        "data_class": "D2_internal_metadata",
        "targets": [
            {
                "address": item.address,
                "content_class": item.target.content_class,
                "source_path": str(item.target.source_path),
                "source_sha256": item.target.source_sha256,
                "body_sha256": item.body_sha256,
            }
            for item in parsed
        ],
    }


def require_staging_app_writer(cur) -> str:
    cur.execute("select session_user,current_user")
    identity = cur.fetchone()
    if identity != ("app_writer", "app_writer"):
        raise SeedRefusal(
            f"staging seed requires direct app_writer session/current identity, not {identity!r}"
        )
    profile = next(item for item in provision.PROFILES if item.label == "writer")
    grants = provision.snapshot_grants.load_current_grants_to_role(
        provision.SCHEMA, provision.MIGRATIONS, profile.bundle_role
    )
    try:
        provision.validate_profile_closure(
            provision.collect_profile_closure(cur, profile), profile, grants,
            exact=True, expected_creator="neondb_owner",
        )
    except provision.ProvisioningRefusal as exc:
        raise SeedRefusal("app_writer authority is not the exact reviewed least-authority closure") from exc
    return "app_writer"


def preflight_targets(cur, parsed: Sequence[ParsedTarget]) -> None:
    """Refuse duplicate/colliding sources before the batch row exists."""
    for item in parsed:
        cur.execute(
            """select exists (select 1 from doctrine_document where slug=%s)
                    or exists (select 1 from doctrine_slug_alias where alias_slug=%s)""",
            (item.target.slug, item.target.slug),
        )
        if cur.fetchone()[0]:
            raise SeedRefusal(f"{item.address}: canonical document slug already exists in staging")
        cur.execute(
            """select exists (
                   select 1 from doctrine_migration_batch
                    where state='verified' and %s = any(source_paths)
                 )""",
            (str(item.target.source_path),),
        )
        if cur.fetchone()[0]:
            raise SeedRefusal(f"{item.address}: canonical source already has a verified staging batch")


def verify_seeded_target(cur, item: ParsedTarget, document_id) -> None:
    error = doctrine_import.verify_file(cur, item.document, document_id)
    if error:
        raise SeedRefusal(f"{item.address}: importer reconciliation failed: {error}")
    cur.execute(
        """select d.slug, d.visibility, d.content_class,
                         array_agg(s.section_key order by s.ordinal)
                    from doctrine_document d
                    join doctrine_section s on s.document_id=d.id
                   where d.id=%s
                   group by d.id, d.slug, d.visibility, d.content_class""",
        (document_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise SeedRefusal(f"{item.address}: seeded document disappeared before verification")
    slug, visibility, content_class, keys = row
    if (slug, visibility, content_class, keys) != (
        item.target.slug, "shared", item.target.content_class, [item.target.section_key]
    ):
        raise SeedRefusal(f"{item.address}: post-import scope is not exactly one shared target section")


def apply_parsed_seed(cur, batch_no: int, parsed: Sequence[ParsedTarget]) -> dict[str, Any]:
    """Apply already-authorized targets through the existing importer primitives.

    The caller owns one transaction, so an error in either target rolls back
    both documents, their snapshots, and their one batch receipt together.
    """
    preflight_targets(cur, parsed)
    cur.execute("select id from actor where slug='joe' and kind='human'")
    actor = cur.fetchone()
    if actor is None:
        raise SeedRefusal("staging has no human joe actor for importer attribution")
    actor_id = actor[0]
    cur.execute(
        """insert into doctrine_migration_batch
               (batch_no, phase, source_paths, source_hashes, state, started_at)
           values (%s, 'bounded', %s, %s, 'running', now()) returning id""",
        (
            batch_no,
            [str(item.target.source_path) for item in parsed],
            json.dumps({str(item.target.source_path): item.target.source_sha256 for item in parsed}),
        ),
    )
    batch_id = cur.fetchone()[0]
    counts: dict[str, int] = {}
    for item in parsed:
        document_id, section_count = doctrine_import.apply_file(
            cur, actor_id, item.document.copy(), item.target.content_class
        )
        verify_seeded_target(cur, item, document_id)
        if section_count != 1:
            raise SeedRefusal(f"{item.address}: importer attempted {section_count} sections")
        counts[item.address] = section_count
    cur.execute(
        """update doctrine_migration_batch
              set state='verified', row_counts=%s, finished_at=now()
            where id=%s returning state""",
        (json.dumps(counts, sort_keys=True), batch_id),
    )
    state = cur.fetchone()[0]
    if state != "verified":
        raise SeedRefusal("staging seed receipt did not verify")
    return {
        **manifest(parsed),
        "batch_id": str(batch_id),
        "batch_no": batch_no,
        "state": state,
        "proposal_mutations": 0,
    }


def apply_seed(cur, batch_no: int, parsed: Sequence[ParsedTarget]) -> dict[str, Any]:
    """Authorize the fixed staging writer before applying the atomic seed."""
    require_staging_app_writer(cur)
    return apply_parsed_seed(cur, batch_no, parsed)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-no", type=int, required=True,
                        help="unused, staging-local doctrine_migration_batch number")
    parser.add_argument("--apply", action="store_true",
                        help="write the two fixed targets; without this flag the script only validates")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        reject_unsafe_environment()
        parsed = parse_targets()
        if not args.apply:
            print(json.dumps({**manifest(parsed), "state": "dry_run", "proposal_mutations": 0},
                             sort_keys=True))
            return 0
        dsn = staging_app_writer_dsn()
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            receipt = apply_seed(cur, args.batch_no, parsed)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (OSError, ValueError, SeedRefusal, psycopg.Error) as exc:
        print(f"staging-retrieval-doctrine-seed: REFUSED — {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Hermetic contract tests for the Joe-only calendar prebrief provisioner."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlsplit

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "provision-calendar-prebrief-joe.py"


def load() -> Any:
    return load_path("calendar_prebrief_joe_provision", TOOL)


def load_path(name: str, path: pathlib.Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Joe provisioner is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    mod = load()
    checked = 0

    def check(label: str, condition: bool) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            raise AssertionError(label)
        print(f"  ok  {label}")

    host = "ep-prod.c-2.us-east-1.aws.neon.tech"
    root_temp = tempfile.TemporaryDirectory()
    bootstrap_root = pathlib.Path(root_temp.name) / "root.pem"
    bootstrap_root.write_text("fixture root\n"); bootstrap_root.chmod(0o600)
    root_text = str(bootstrap_root)
    scope = mod.production_scope(
        [{"id": mod.PRODUCTION_PROJECT_ID, "name": "CARR Production"}],
        [{"id": "br-prod", "project_id": mod.PRODUCTION_PROJECT_ID,
          "name": mod.PRODUCTION_BRANCH_NAME, "default": True}],
        [{"id": "ep-prod", "branch_id": "br-prod", "type": "read_write", "host": host}],
        host, 5432, "neondb",
    )
    check("pins the exact production project, default branch, and endpoint host",
          scope.endpoint_host == host and scope.branch_id == "br-prod")
    for bad in (
        ([{"id": "other", "name": "CARR Production"}], [{"id": "br-prod", "project_id": "other", "name": mod.PRODUCTION_BRANCH_NAME, "default": True}], [{"id": "ep-prod", "branch_id": "br-prod", "type": "read_write", "host": host}]),
        ([{"id": mod.PRODUCTION_PROJECT_ID, "name": "CARR Production"}], [{"id": "br-prod", "project_id": mod.PRODUCTION_PROJECT_ID, "name": "other", "default": True}], [{"id": "ep-prod", "branch_id": "br-prod", "type": "read_write", "host": host}]),
        ([{"id": mod.PRODUCTION_PROJECT_ID, "name": "CARR Production"}], [{"id": "br-prod", "project_id": mod.PRODUCTION_PROJECT_ID, "name": mod.PRODUCTION_BRANCH_NAME, "default": True}], [{"id": "ep-prod", "branch_id": "br-prod", "type": "read_write", "host": "other.neon.tech"}]),
    ):
        try:
            mod.production_scope(*bad, host, 5432, "neondb")
        except mod.ProvisioningRefusal:
            continue
        raise AssertionError("un-pinned production provider scope was accepted")
    check("refuses project, branch, and endpoint substitution", True)

    root_dsn = f"postgresql://neondb_owner:owner-secret@{host}/neondb?sslmode=verify-full&sslrootcert={root_text}&channel_binding=require"  # ci-secret-scan: allow -- inert fixture
    parsed = mod.parse_owner_dsn(root_dsn, root_text)
    check("owner DSN accepts only the owner role and verify-full trust root",
          parsed.role == "neondb_owner" and parsed.host == host)
    for candidate in (
        root_dsn.replace("neondb_owner", "other", 1),
        root_dsn.replace("sslmode=verify-full", "sslmode=require"),
        root_dsn.replace("sslrootcert=" + root_text, "sslrootcert=relative.pem"),
        root_dsn + "&host=evil.example",
    ):
        try:
            mod.parse_owner_dsn(candidate, root_text)
        except mod.ProvisioningRefusal:
            continue
        raise AssertionError("unsafe owner URI was accepted")
    check("rejects untrusted owner URI rewrites and service overrides", True)

    authority = f"postgresql://carr_authority_joe:authority-secret@{host}/neondb?sslmode=require&channel_binding=require"  # ci-secret-scan: allow -- inert fixture
    jobs = f"postgresql://carr_jobs:jobs-secret@{host}/neondb?sslmode=require&channel_binding=require"  # ci-secret-scan: allow -- inert fixture
    transformed = mod.transform_dsn(authority, "carr_authority_joe", parsed, root_text)
    parts = urlsplit(transformed)
    query = parse_qs(parts.query)
    check("rewrites existing Joe authority credentials to exact verify-full trust",
          parts.hostname == host and query == {"sslmode": ["verify-full"], "sslrootcert": [root_text], "channel_binding": ["require"]})
    try:
        mod.transform_dsn(authority.replace(host, "elsewhere.neon.tech"), "carr_authority_joe", parsed, root_text)
    except mod.ProvisioningRefusal:
        check("refuses an authority credential from another endpoint", True)
    else:
        raise AssertionError("wrong endpoint authority credential was accepted")

    with tempfile.TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        trust = root / "root.pem"; trust.write_text("fixture root\n"); trust.chmod(0o600)
        base = root / "existing.env"
        base.write_text(f"CARR_DB_AUTHORITY_JOE_URL='{authority}'\nCARR_DB_JOBS_URL=\"{jobs}\"\n")
        base.chmod(0o600)
        got = mod.load_existing_identities(base, parsed, trust)
        check("loads only existing Joe authority and jobs identities from a sealed file",
              set(got) == {"CARR_DB_AUTHORITY_JOE_URL", "CARR_DB_JOBS_URL"})
        link = root / "existing-link.env"; link.symlink_to(base)
        try:
            mod.load_existing_identities(link, parsed, trust)
        except mod.ProvisioningRefusal:
            check("refuses a symlinked existing credential file", True)
        else:
            raise AssertionError("symlinked credential input was accepted")

        passwords = {profile.role: profile.role + "-password" for profile in mod.NEW_PROFILES}
        all_dsns = mod.compose_identity_dsns(parsed, trust, got, passwords)
        paths = mod.ProfilePaths(root / "activation.env", root / "child.env", root / "runtime.env",
                                 root / "private.pem", root / "public.pem", root / "allowlist.json")
        fingerprint = "a" * 64
        mod.write_profiles(paths, all_dsns, fingerprint, home=root)
        activation = paths.activation.read_text()
        child = paths.child.read_text()
        runtime = paths.runtime.read_text()
        check("atomically writes only Joe live activation, child, and runtime profiles",
              "CARR_DB_AUTHORITY_JOE_URL=" in activation and "CARR_DB_JOBS_URL=" in activation
              and "ATTESTOR_JOE" in child and "RESOLVER_JOE" in child and "PREBRIEF_JOE" in child
              and "CARR_CALENDAR_PREBRIEF_ENABLED=false" in runtime
              and "dell" not in (activation + child + runtime).lower()
              and all(os.stat(path).st_mode & 0o777 == 0o600 for path in (paths.activation, paths.child, paths.runtime)))
        check("runtime profile carries no child credential and remains explicitly disabled",
              "ATTESTOR" not in runtime and "RESOLVER" not in runtime and "PREBRIEF_JOE_URL" not in runtime)
        runtime_module = load_path("calendar_prebrief_joe_runtime_for_provision_test",
                                   REPO / "tools" / "calendar-prebrief-joe-runtime.py")
        coordinator_module = load_path("calendar_prebrief_coordinator_for_provision_test",
                                       REPO / "tools" / "calendar-prebrief-coordinator.py")
        loaded_runtime = runtime_module.load_profile(paths.runtime, home=root)
        loaded_child = coordinator_module._profile_file(paths.child, "joe", "live")
        check("written profiles satisfy the dedicated 0249 runtime and child interfaces",
              loaded_runtime["CARR_DB_JOBS_URL"] == all_dsns["CARR_DB_JOBS_URL"]
              and set(loaded_child) == {"CARR_DB_CALENDAR_PREBRIEF_ATTESTOR_JOE_URL",
                                        "CARR_DB_CALENDAR_PREBRIEF_RESOLVER_JOE_URL",
                                        "CARR_DB_CALENDAR_PREBRIEF_JOE_URL"})

    class Cursor:
        def __init__(self): self.calls: list[tuple[str, object]] = []
        def execute(self, sql, args=None): self.calls.append((str(sql), args))
        def fetchall(self): return [("carr_calendar_prebrief_attestors", False), ("carr_calendar_prebrief_email_resolver", False), ("carr_calendar_prebrief_jobs", False)]
        def fetchone(self): return ("carr_calendar_prebrief_attestor_joe", True, False, False, False, False, True)
        def __enter__(self): return self
        def __exit__(self, *_): return False
    cursor = Cursor()
    mod.verify_bundles(cursor)
    check("requires each exact privilege bundle to remain NOLOGIN", True)
    class BadCursor(Cursor):
        def fetchall(self): return [("carr_calendar_prebrief_attestors", True), ("carr_calendar_prebrief_email_resolver", False), ("carr_calendar_prebrief_jobs", False)]
    try:
        mod.verify_bundles(BadCursor())
    except mod.ProvisioningRefusal:
        check("refuses a login-capable privilege bundle", True)
    else:
        raise AssertionError("LOGIN bundle was accepted")

    class RoleCursor:
        def __init__(self, profile):
            self.profile, self.created, self.last, self.calls = profile, False, "", []
        def execute(self, sql, args=None):
            self.last = str(sql); self.calls.append((self.last, args))
            if self.last.startswith("create role "): self.created = True
        def fetchone(self):
            if "from pg_roles where rolname=%s" in self.last:
                return (True, False, False, False, False, False, True) if self.created else None
            raise AssertionError("unexpected fake DB one-row query")
        def fetchall(self):
            if "pg_auth_members" in self.last:
                return [(self.profile.bundle,)] if self.created else []
            raise AssertionError("unexpected fake DB rows query")
    profile = mod.NEW_PROFILES[0]
    role_cursor = RoleCursor(profile)
    mod.ensure_login_role(role_cursor, profile, "fixture-password")
    rendered_sql = "\n".join(sql for sql, _ in role_cursor.calls)
    check("fake DB proves role creation grants only its exact NOLOGIN bundle",
          f"create role {profile.role}" in rendered_sql and f"grant {profile.bundle} to {profile.role}" in rendered_sql
          and "fixture-password" not in rendered_sql
          and all(args != "fixture-password" for _, args in role_cursor.calls))

    class MismatchedRoleCursor(RoleCursor):
        def __init__(self, profile): super().__init__(profile); self.created = True
        def fetchall(self): return [("unexpected_bundle",)]
    try:
        mod.ensure_login_role(MismatchedRoleCursor(profile), profile, "fixture-password")
    except mod.ProvisioningRefusal:
        check("fake DB refuses an existing login with a widened capability", True)
    else:
        raise AssertionError("widened existing login was accepted")

    check("role contract is Joe-only with three exact new identities",
          {profile.role for profile in mod.NEW_PROFILES} == {
              "carr_calendar_prebrief_attestor_joe", "carr_calendar_prebrief_resolver_joe", "carr_calendar_prebrief_joe"}
          and all("dell" not in str(profile).lower() and "canary" not in str(profile).lower() for profile in mod.NEW_PROFILES))
    root_temp.cleanup()
    print(f"PASS: Joe calendar prebrief provisioner self-test ({checked} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

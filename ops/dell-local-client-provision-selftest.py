#!/usr/bin/env python3
"""Hermetic static contract for the two local-machine actor provisioners."""
from __future__ import annotations

import pathlib
import re


REPO = pathlib.Path(__file__).resolve().parents[1]
DELL_PROVISIONER = REPO / "pipelines" / "provision-dell-local-client.sql"
RUNBOOKS = (
    "pipelines/provision-local-client.sql",
    "pipelines/provision-dell-local-client.sql",
)
REFERENCES = (
    REPO / "secrets-inventory.md",
    REPO / "mcp-server" / "src" / "index.js",
    REPO / "mcp-server" / "wrangler.toml",
    REPO / "mcp-server" / "local-verb.mjs",
)


def main() -> int:
    checked = 0

    def check(label: str, condition: bool) -> None:
        nonlocal checked
        checked += 1
        if not condition:
            raise AssertionError(label)
        print(f"  ok  {label}")

    sql = DELL_PROVISIONER.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    )

    check("Dell provisioner names the dell-local automation actor",
          "values ('dell-local', 'automation', 'Dell (local)')" in executable)
    check("Dell provisioner is repeatable without changing an existing actor",
          executable.count("on conflict (slug) do nothing") == 1
          and "on conflict (slug) do update" not in executable)
    check("Dell provisioner refuses an existing actor with mismatched identity fields",
          re.search(
              r"select count\(\*\) into n from actor\s+"
              r"where slug = 'dell-local'\s+"
              r"and kind = 'automation'\s+"
              r"and display_name = 'Dell \(local\)'\s+"
              r"and active",
              executable,
          ) is not None
          and "if n <> 1 then" in executable)
    check("Dell provisioner contains no credential or token value",
          "LOCAL_TOKENS" not in executable
          and "CARR_MCP_LOCAL_TOKEN" not in executable
          and re.search(r"(?i)\b[0-9a-f]{32,}\b", sql) is None
          and re.search(r"CARR_MCP_LOCAL_TOKEN=(?!<token>)\S+", sql) is None)
    obsolete_psql_output = (
        "--   BEGIN\n",
        "--   INSERT 0 1\n",
        "--   NOTICE:",
        "--   DO\n",
        "--   COMMIT\n",
        "A SECOND run prints INSERT 0 0",
    )
    check("Dell runbook describes db-tap output, not obsolete psql command tags",
          all(fragment not in sql for fragment in obsolete_psql_output)
          and "exit status 0" in sql
          and "dell-local|automation|Dell (local)|True" in sql
          and "BREAK-GLASS ENGAGED" in sql
          and "out/break-glass-receipts.log" in sql)

    for reference in REFERENCES:
        text = reference.read_text(encoding="utf-8")
        check(f"{reference.relative_to(REPO)} points to both local actor runbooks",
              all(runbook in text for runbook in RUNBOOKS))

    print(f"dell-local-client-provision-selftest: {checked} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

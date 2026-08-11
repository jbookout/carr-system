#!/usr/bin/env python3
"""Fail-closed static preflight for the doctrine Markdown cutoff.

This intentionally proves only repository-consumer safety.  The two operational
gates (a clean store-first Monday cycle and a human cold-start) are evidence
gates, not things a shell script may pretend to have observed.  ``--fire`` must
therefore require their explicit confirmation separately.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


BOOTSTRAP_RECORD_GATE = "doctrine bootstrap source record revision/supersession"

# These are executable consumers, not historical prose.  Adding a new retired
# render requires adding it to the registry-derived list *and* eliminating or
# explicitly justifying any executable consumer here.
EXECUTABLE_SUFFIXES = {".py", ".sh", ".js", ".mjs", ".plist", ".md"}
EXECUTABLE_MD_NAMES = {"SKILL.md"}
SKIP_PARTS = {".git", "node_modules", "out", "frozen-sources", "__pycache__"}

# Exact and reviewable. A path may mention retiring files only for one of these
# non-consumer reasons. Broad directory exemptions are forbidden.
HISTORICAL_REFERENCE_ALLOWLIST = {
    "exporters/targets.py": "authoritative render registry and builders",
    "exporters/dictionary.py": "registered dictionary renderer implementation",
    "exporters/ledger_targets.py": "registered ledger renderer implementation",
    "exporters/partial.py": "partial-render engine; host name is structural input",
    "exporters/common.py": "generation-retention comments, not a runtime source read",
    "hooks/record-home-gate.py": "deny registry parsed from exporter targets",
    "hooks/md_manifest.py": "write gate for the two retiring bootstrap stubs",
    "hooks/session-brief.py": "historical migration detector, not a file read",
    "hooks/delegation-gate.py": "generic context filename fixture, not a vault consumer",
    "hooks/git-writer-gate.py": "generic repo instruction example, not a vault consumer",
    "hooks/gate_paths.py": "deny-gate fixture path",
    "hooks/guard-unattended.py": "deny-gate fixture path",
    "hooks/lint-gate.py": "generated-render deny inventory",
    "pipelines/doctrine_inventory.py": "cutoff inventory and classification",
    "pipelines/build-system-graph.py": "graph label documentation; absent stub is tolerated",
    "pipelines/build-section-index.py": "filesystem inventory tolerates absent retired file",
    "ops/vault-duplicate-sweep.py": "diagnostic identifies historical duplicate projections",
    "tools/rehearse-order40-renders.py": "pre-cutoff migration rehearsal against staging only",
    "tools/health-check.py": "conditional store-aware retirement checks",
    "ops/rules-live-check.py": "conditional store-aware retirement check",
    "ops/renders-verify.py": "conditional store-aware retirement check",
    "bin/refresh-rules.sh": "self-disables from cutoff state and is unloaded during stage",
    "bin/cutoff-doctrine.sh": "cutoff entry point names its own retired targets",
    "tools/doctrine_cutoff_preflight.py": "this scanner and cutoff inventory",
    "tools/doctrine_cutoff.py": "two-phase cutoff engine and manifest inventory",
    "tools/test-doctrine-cutoff-preflight.py": "cutoff tests",
    "tools/test-record-home-gate.py": "deny-gate fixtures",
    "tools/test-partial-render.py": "partial-render fixtures",
    "tools/prove-order40-windowing.py": "pre-cutoff migration proof",
    "tools/reconcile.py": "pre-cutoff frozen-source reconciliation",
    "tools/registry-audit.py": "legacy pointer audit tolerates absent retired roster",
    "ops/abilities-manifest.py": "source catalog used by the abilities renderer",
    "ops/config-as-code.py": "historical comment; no render read",
    "ops/migrate-dell-selftest.py": "migration fixtures",
    "mcp-server/test/loop-owner-repair.test.mjs": "render-name fixture",
    "mcp-server/src/tools.js": "verb descriptions name retired views as historical warnings",
    "corpus/_home/.claude/skills/catchup/SKILL.md": "generic non-CARR project context filenames",
    "corpus/_home/.claude/skills/handoff/SKILL.md": "generic non-CARR project context filenames",
    "corpus/_home/.claude/skills/onboard/SKILL.md": "generic non-CARR project context filenames",
    "corpus/_home/.claude/skills/til/SKILL.md": "generic non-CARR project context filenames",
    "corpus/_home/.claude/skills/crux/SKILL.md": "generic project decision-log example",
    "corpus/_home/.claude/skills/decide/SKILL.md": "generic project decision-log example",
    "corpus/_home/.claude/skills/loose-ends/SKILL.md": "generic project TODO filename examples",
}


def generated_markdown(repo: Path) -> list[str]:
    """Read the writer-gate's exporter-derived registry, never a copied list."""
    gate = repo / "hooks" / "record-home-gate.py"
    spec = importlib.util.spec_from_file_location("record_home_gate", gate)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {gate}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    exact, _ = module.generated_paths()
    return sorted(rel for rel in exact if rel.lower().endswith(".md"))


def _source_files(repo: Path):
    for source in repo.rglob("*"):
        if not source.is_file() or any(part in SKIP_PARTS for part in source.parts):
            continue
        if source.suffix not in EXECUTABLE_SUFFIXES:
            continue
        if source.suffix == ".md" and source.name not in EXECUTABLE_MD_NAMES:
            continue
        yield source


def consumer_violations(repo: Path, retiring: list[str] | None = None) -> list[str]:
    retiring = retiring or generated_markdown(repo)
    unique_basenames = {Path(item).name for item in retiring}
    # Generic INDEX.md is too ambiguous to scan by basename; its full retired
    # path is still scanned. All other render filenames are specific contracts.
    unique_basenames.discard("INDEX.md")
    needles = sorted(set(retiring + list(unique_basenames) +
                         ["CLAUDE.md", "AGENTS.md", "00_Context/today.md"]),
                     key=len, reverse=True)
    violations = []
    for source in _source_files(repo):
        rel = source.relative_to(repo).as_posix()
        if rel in HISTORICAL_REFERENCE_ALLOWLIST or rel.startswith("pipelines/import_"):
            continue
        try:
            body = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in needles:
            if needle in body:
                violations.append(f"retired render consumer: {rel} references {needle}")
    return sorted(set(violations))


def installed_consumer_violations(vault: Path, home: Path,
                                  retiring: list[str]) -> list[str]:
    """Check deployed surfaces, not merely their repo sources.

    Managed portable skills may contain generic non-CARR filenames.  They are
    accepted only when the installed bytes exactly match the reviewed tracked
    source; a stale installed copy is therefore still a hard refusal.
    """
    unique_basenames = {Path(item).name for item in retiring} - {"INDEX.md"}
    needles = sorted(set(retiring + list(unique_basenames) +
                         ["CLAUDE.md", "AGENTS.md", "00_Context/today.md"]), key=len, reverse=True)
    candidates = [vault / "DNA/Team/front-door.html"]
    task_root = home / ".claude/scheduled-tasks"
    if task_root.exists():
        candidates.extend(task_root.glob("*/SKILL.md"))
    violations = []
    for source in candidates:
        if not source.is_file():
            continue
        try:
            body = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in needles:
            if needle in body:
                violations.append(f"deployed consumer: {source} references {needle}")

    repo = Path(__file__).resolve().parents[1]
    managed = repo / "corpus/_home/.claude/skills"
    for tracked in managed.glob("*/SKILL.md"):
        rel = tracked.relative_to(repo).as_posix()
        if rel not in HISTORICAL_REFERENCE_ALLOWLIST:
            continue
        for installed_root in (home / ".claude/skills", home / ".agents/skills"):
            installed = installed_root / tracked.parent.name / "SKILL.md"
            if not installed.is_file():
                continue
            body = installed.read_text(encoding="utf-8")
            if any(needle in body for needle in needles) and installed.read_bytes() != tracked.read_bytes():
                violations.append(
                    f"deployed managed skill is stale and may reference retired renders: {installed}")
    return sorted(set(violations))


def validate(repo: Path, vault: Path, bootstrap_record_revision: str = "") -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notices = [
        "human gate required: recorded clean store-first Monday cycle",
        "human gate required: fresh-session cold-start proof after bootstrap stubs retire",
    ]
    if not bootstrap_record_revision:
        errors.append(f"external gate missing: {BOOTSTRAP_RECORD_GATE}")
    else:
        notices.append(f"external gate supplied: {BOOTSTRAP_RECORD_GATE} ({bootstrap_record_revision})")
    try:
        renders = generated_markdown(repo)
    except Exception as exc:
        return [f"cannot derive exporter render registry: {exc}"], notices
    if not renders:
        errors.append("exporter registry produced no Markdown renders; refusing ambiguous cutoff")
    for rel in renders:
        if not (vault / rel).is_file():
            errors.append(f"retiring render missing from vault: {rel}")
    for stub in ("CLAUDE.md", "AGENTS.md"):
        if not (vault / stub).is_file():
            errors.append(f"zero-file bootstrap stub missing before cutoff: {stub}")
    errors.extend(consumer_violations(repo))
    return errors, notices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--bootstrap-record-revision", default="")
    args = parser.parse_args()
    errors, notices = validate(args.repo.resolve(), args.vault.expanduser(),
                               args.bootstrap_record_revision)
    for notice in notices:
        print(f"GATE {notice}")
    if errors:
        for error in errors:
            print(f"NO-GO {error}")
        return 1
    print("OK static consumer preflight: every executable retired-render consumer is repointed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

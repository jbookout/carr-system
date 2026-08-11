#!/usr/bin/env python3
"""Two-phase, reversible doctrine Markdown cutoff.

Stage removes the file surfaces while keeping the durable server state in
``retiring``.  A human then proves a genuinely fresh session boots store-first.
Finalize accepts only verified evidence records and changes durable state to
``retired``.  Rollback is collision- and hash-checked before its first move.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.doctrine_cutoff_state import sentinel_path  # noqa: E402
from tools.doctrine_cutoff_preflight import (consumer_violations, generated_markdown,
                                             installed_consumer_violations)  # noqa: E402

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
CONFIG_RETIRING = "doctrine.md_renders_retiring"
CONFIG_RETIRED = "doctrine.md_renders_retired"


class Refusal(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_relative(rel: str) -> str:
    p = PurePosixPath(rel)
    if p.is_absolute() or not p.parts or any(part in ("", ".", "..") for part in p.parts):
        raise Refusal(f"unsafe manifest path: {rel!r}")
    return p.as_posix()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class Config:
    def __init__(self, repo: Path):
        self.repo = repo
        self.fake = Path(os.environ["CARR_CUTOFF_CONFIG_FILE"]) if os.environ.get("CARR_CUTOFF_CONFIG_FILE") else None

    def _fake_data(self):
        return json.loads(self.fake.read_text()) if self.fake and self.fake.exists() else {}

    def status(self) -> str:
        if self.fake:
            data = self._fake_data()
            if data.get(CONFIG_RETIRED, {}).get("value"): return "retired"
            if data.get(CONFIG_RETIRING, {}).get("value"): return "retiring"
            return "active"
        out = run([str(self.repo / "run.sh"), "call", "cutoff-status", "{}", "joe"],
                  capture=True, cwd=self.repo)
        try:
            body = json.loads(out)
        except json.JSONDecodeError as exc:
            raise Refusal("cutoff-status verb returned invalid JSON") from exc
        if body.get("state") not in {"active", "retiring", "retired"}:
            raise Refusal(f"cutoff-status returned invalid state: {body}")
        return body["state"]

    def transition(self, expected: str, target: str, *, approved_commit: str,
                   manifest_sha256: str, approval_decision_id: str, reason: str,
                   **evidence) -> None:
        if self.fake:
            data = self._fake_data()
            data[CONFIG_RETIRING] = {"value": target == "retiring", "note": reason}
            data[CONFIG_RETIRED] = {"value": target == "retired", "note": reason}
            data["last_transition"] = {"expected": expected, "target": target,
                                       "evidence": evidence}
            atomic_json(self.fake, data)
            return
        payload = {"idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL,
                   f"carr-cutoff:{expected}:{target}:{manifest_sha256}:{approval_decision_id}")),
                   "expected_state": expected, "target_state": target,
                   "approved_commit": approved_commit, "manifest_sha256": manifest_sha256,
                   "approval_decision_id": approval_decision_id, "reason": reason,
                   **evidence}
        out = run([str(self.repo / "run.sh"), "call", "transition-doctrine-cutoff",
                   json.dumps(payload, separators=(",", ":")), "joe"],
                  capture=True, cwd=self.repo)
        try:
            body = json.loads(out)
        except json.JSONDecodeError as exc:
            raise Refusal("cutoff transition verb returned invalid JSON") from exc
        if body.get("state") != target:
            raise Refusal(f"cutoff transition read-back mismatch: {body}")
        if self.status() != target:
            raise Refusal("durable cutoff-status did not confirm the transition")


def run(cmd: list[str], *, input_text: str | None = None, capture: bool = False,
        cwd: Path | None = None) -> str:
    result = subprocess.run(cmd, input=input_text, text=True, cwd=cwd,
                            capture_output=capture, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise Refusal(f"command failed ({result.returncode}): {' '.join(cmd)}"
                      + (f" :: {detail[-1]}" if detail else ""))
    return result.stdout if capture else ""


def verify_canonical(repo: Path, approved_commit: str, vault: Path) -> None:
    if os.environ.get("CARR_CUTOFF_TEST_MODE") == "1":
        roots = [Path("/tmp").resolve(), Path("/private/tmp").resolve(), Path("/var/folders").resolve()]
        fake_paths = [vault, Path(os.environ.get("CARR_CUTOFF_CONFIG_FILE", "/")),
                      Path(os.environ.get("CARR_CUTOFF_EVIDENCE_DIR", "/")),
                      Path(os.environ.get("CARR_CUTOFF_SENTINEL", "/"))]
        if all(any(path.resolve().is_relative_to(root) for root in roots if root.exists())
               for path in fake_paths):
            return
        raise Refusal("test mode is confined to temporary fake vault/config/evidence paths")
    canonical = (Path.home() / "carr-system").resolve()
    if repo.resolve() != canonical:
        raise Refusal(f"cutoff mutation must run from canonical checkout {canonical}, not {repo.resolve()}")
    head = run(["git", "rev-parse", "HEAD"], cwd=repo, capture=True).strip()
    remote = run(["git", "rev-parse", "origin/main"], cwd=repo, capture=True).strip()
    branch = run(["git", "branch", "--show-current"], cwd=repo, capture=True).strip()
    dirty = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, capture=True).strip()
    if branch != "main" or dirty or head != approved_commit or remote != head:
        raise Refusal(f"canonical checkout must be clean main at approved commit {approved_commit}; "
                      f"found branch={branch!r} head={head!r} origin/main={remote!r} dirty={bool(dirty)}")
    run(["git", "ls-files", "--error-unmatch", "bin/cutoff-doctrine.sh",
         "tools/doctrine_cutoff.py", "tools/doctrine_cutoff_preflight.py"], cwd=repo)


def read_evidence(repo: Path, record_id: str) -> dict:
    if not UUID.match(record_id):
        raise Refusal(f"evidence must be a full durable UUID: {record_id!r}")
    fixture_dir = os.environ.get("CARR_CUTOFF_EVIDENCE_DIR")
    if fixture_dir:
        path = Path(fixture_dir) / f"{record_id}.json"
        if not path.exists():
            raise Refusal(f"evidence record not found: {record_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = json.dumps({"record_id": record_id}, separators=(",", ":"))
        out = run([str(repo / "run.sh"), "call", "cutoff-evidence", payload, "joe"],
                  capture=True, cwd=repo)
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError as exc:
            raise Refusal(f"cutoff-evidence verb returned invalid JSON for {record_id}") from exc
        if not parsed or not isinstance(parsed.get("evidence"), dict):
            raise Refusal(f"cutoff-evidence verb returned no normalized record for {record_id}")
        data = parsed["evidence"]
    if data.get("id") != record_id:
        raise Refusal(f"evidence read-back ID mismatch for {record_id}")
    return data


def require_evidence(repo: Path, record_id: str, kind: str) -> dict:
    data = read_evidence(repo, record_id)
    text = " ".join(str(data.get(k) or "") for k in ("content", "human_quote", "provenance")).lower()
    if not data.get("provenance") or not data.get("actor"):
        raise Refusal(f"{kind} evidence lacks actor/provenance")
    if kind == "monday":
        if (data.get("record_type") != "finding" or data.get("verb") != "record-system-evidence"
                or not all(x in text for x in ("monday", "heartbeat", "store-first"))):
            raise Refusal("Monday evidence must be a finding proving heartbeat + Monday + store-first")
    elif kind == "bootstrap":
        if data.get("record_type") != "doctrine_revision" or data.get("current") is not True or "standing-context" not in text:
            raise Refusal("bootstrap evidence must be a doctrine revision naming standing-context")
        if "compiled-rules" in text or "claude.md" in text:
            raise Refusal("bootstrap revision still instructs a retired file read")
    elif kind in ("stage_approval", "final_approval", "rollback_approval"):
        sponsor = (data.get("sponsoring_human") or data.get("actor") or "").lower()
        word = kind.removesuffix("_approval")
        if (data.get("record_type") != "decision" or sponsor != "joe" or "cutoff" not in text
                or "approve" not in text or word not in text):
            raise Refusal(f"{kind} must be a Joe-attributed decision explicitly approving cutoff")
    elif kind == "cold_start":
        required = ("fresh session", "standing-context", "shared", "personal", "no file")
        if (data.get("record_type") != "finding" or data.get("verb") != "record-system-evidence"
                or not all(x in text for x in required)):
            raise Refusal("cold-start evidence must prove a fresh session, counts/scopes, and no file bootstrap")
    return data


def installed_rules_plist(repo: Path) -> Path:
    home = Path(os.environ.get("CARR_CUTOFF_HOME", str(Path.home())))
    plist = home / "Library/LaunchAgents/com.carr.rules-refresh.plist"
    if not plist.exists():
        raise Refusal(f"installed rules-refresh launchd plist missing: {plist}")
    with plist.open("rb") as fh:
        body = plistlib.load(fh)
    expected = str(repo / "bin/refresh-rules.sh")
    if expected not in body.get("ProgramArguments", []):
        raise Refusal(f"installed launchd does not point at patched canonical script: {expected}")
    return plist


def launchctl(repo: Path, action: str) -> None:
    plist = installed_rules_plist(repo)
    ctl = os.environ.get("CARR_CUTOFF_LAUNCHCTL", "launchctl")
    if action == "unload":
        run([ctl, "unload", "-w", str(plist)])
        probe = subprocess.run([ctl, "print", f"gui/{os.getuid()}/com.carr.rules-refresh"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            raise Refusal("rules-refresh launchd still registered after unload")
    else:
        run([ctl, "load", "-w", str(plist)])


def smoke(repo: Path) -> None:
    supplied = os.environ.get("CARR_CUTOFF_SMOKE_COMMANDS")
    commands = json.loads(supplied) if supplied else [
        [str(repo / "run.sh"), "call", "standing-context", "{}", "joe"],
        [str(repo / "run.sh"), "call", "read-doctrine", '{"document":"carr-workspace-bduf"}', "joe"],
        [str(repo / "run.sh"), "health"],
        [str(repo / "run.sh"), "brief-pack", "--section", "monday-agenda", "--quiet"],
        [str(repo / "run.sh"), "review-queue"],
    ]
    for command in commands:
        run([str(x) for x in command], cwd=repo)


def record_system_evidence(repo: Path, evidence_type: str, observations: dict,
                           provenance: str) -> str:
    fixture_dir = os.environ.get("CARR_CUTOFF_EVIDENCE_DIR")
    key = str(uuid.uuid5(uuid.NAMESPACE_URL,
              f"carr-cutoff-evidence:{evidence_type}:{json.dumps(observations, sort_keys=True)}"))
    if fixture_dir:
        evidence_id = key
        atomic_json(Path(fixture_dir) / f"{evidence_id}.json", {
            "id": evidence_id, "record_type": "finding", "verb": "record-system-evidence",
            "actor": "joe", "sponsoring_human": "joe", "content": json.dumps(observations),
            "provenance": provenance,
        })
        return evidence_id
    payload = {"idempotency_key": key, "evidence_type": evidence_type,
               "observations": observations, "provenance": provenance}
    out = run([str(repo / "run.sh"), "call", "record-system-evidence",
               json.dumps(payload, separators=(",", ":")), "joe"], capture=True, cwd=repo)
    try:
        evidence_id = json.loads(out).get("evidence_id")
    except json.JSONDecodeError as exc:
        raise Refusal("record-system-evidence returned invalid JSON") from exc
    if not evidence_id or not UUID.match(evidence_id):
        raise Refusal("record-system-evidence returned no durable UUID")
    return evidence_id


def retired_paths(repo: Path, vault: Path) -> list[str]:
    paths = generated_markdown(repo) + ["CLAUDE.md", "AGENTS.md", "00_Context/today.md"]
    return sorted({safe_relative(p) for p in paths if (vault / p).exists()})


def read_manifest(stage: Path) -> dict:
    path = stage / "manifest.json"
    if not path.is_file():
        raise Refusal(f"cutoff manifest missing: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files")
    if not isinstance(files, list) or data.get("file_count") != len(files):
        raise Refusal("manifest count is incomplete")
    for item in files:
        safe_relative(item.get("path", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
            raise Refusal("manifest contains an invalid hash")
    return data


def verify_stage_files(stage: Path, manifest: dict) -> None:
    for item in manifest["files"]:
        path = stage / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise Refusal(f"staged file hash/size mismatch: {item['path']}")


def fsync_copy(source: Path, snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, snapshot.open("xb") as dest:
        shutil.copyfileobj(src, dest, length=1024 * 1024)
        dest.flush()
        os.fsync(dest.fileno())


def atomic_retire(source: Path, staged: Path, item: dict) -> None:
    """Retire one source without ever hiding bytes that were not verified.

    A fsynced snapshot detects mutation during the read. The source is then
    atomically renamed only on the same filesystem. If the exact renamed bytes
    differ, they are immediately put back at the original path before refusal.
    """
    expected = item["sha256"]
    before = source.stat()
    if before.st_size != item["bytes"] or sha256(source) != expected:
        raise Refusal(f"source changed after manifest; nothing moved: {item['path']}")
    staged.parent.mkdir(parents=True, exist_ok=True)
    snapshot = staged.with_name(staged.name + ".verified-snapshot")
    try:
        fsync_copy(source, snapshot)
        after = source.stat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or sha256(source) != expected or sha256(snapshot) != expected):
            raise Refusal(f"source changed during verified snapshot; nothing moved: {item['path']}")
        if source.stat().st_dev != staged.parent.stat().st_dev:
            raise Refusal(f"stage is not on source filesystem; atomic rename unavailable: {item['path']}")
        os.replace(source, staged)
        if sha256(staged) != expected or sha256(snapshot) != sha256(staged):
            os.replace(staged, source)
            raise Refusal(f"source changed at atomic retirement; restored original path: {item['path']}")
    finally:
        snapshot.unlink(missing_ok=True)


def restore_local(repo: Path, vault: Path, stage: Path, *, reload_job=True) -> list[str]:
    manifest = read_manifest(stage)
    actual_hashes = {}
    mismatches = []
    for item in manifest["files"]:
        src, dest = stage / item["path"], vault / item["path"]
        current = src if src.exists() else dest
        if not current.exists():
            raise Refusal(f"rollback source missing: {item['path']}")
        actual_hashes[item["path"]] = sha256(current)
        if current.stat().st_size != item["bytes"] or actual_hashes[item["path"]] != item["sha256"]:
            mismatches.append(item["path"])
    collisions = [item["path"] for item in manifest["files"]
                  if (stage / item["path"]).exists() and (vault / item["path"]).exists()]
    if collisions:
        raise Refusal("rollback collision(s), nothing moved: " + ", ".join(collisions))
    atomic_json(sentinel_path(repo), {"phase": "rolling_back", "stage": str(stage)})
    for item in manifest["files"]:
        src, dest = stage / item["path"], vault / item["path"]
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    for item in manifest["files"]:
        dest = vault / item["path"]
        if sha256(dest) != actual_hashes[item["path"]]:
            raise Refusal(f"rollback verification failed; flags remain closed: {item['path']}")
    if reload_job:
        launchctl(repo, "load")
    return mismatches


def complete_rollback(repo: Path, vault: Path, stage: Path, cfg: Config,
                      *, reload_job=True) -> str:
    manifest = read_manifest(stage)
    mismatches = restore_local(repo, vault, stage, reload_job=reload_job)
    manifest_sha = sha256(stage / "manifest.json")
    evidence_id = record_system_evidence(repo, "cutoff_rollback", {
        "manifest_sha256": manifest_sha, "collision_preflight_passed": True,
        "restored_hashes_verified": True, "integrity_alarm": bool(mismatches),
        "manifest_mismatches": mismatches,
    }, f"local cutoff rollback verified from {stage}")
    before = cfg.status()
    if before not in {"retiring", "retired"}:
        raise Refusal(f"durable rollback expected retiring/retired, found {before}")
    cfg.transition(before, "active", approved_commit=manifest["approved_commit"],
                   manifest_sha256=manifest_sha,
                   approval_decision_id=manifest["evidence"]["rollback_approval"],
                   reason="verified doctrine cutoff rollback",
                   rollback_evidence_id=evidence_id)
    sentinel_path(repo).unlink()
    return evidence_id


def stage_cutoff(args) -> None:
    verify_canonical(args.repo, args.approved_commit, args.vault)
    retiring = generated_markdown(args.repo)
    home = Path(os.environ.get("CARR_CUTOFF_HOME", str(Path.home())))
    violations = (consumer_violations(args.repo, retiring)
                  + installed_consumer_violations(args.vault, home, retiring))
    if violations:
        raise Refusal("consumer preflight failed:\n" + "\n".join(violations))
    installed_rules_plist(args.repo)
    bootstrap = [require_evidence(args.repo, record_id, "bootstrap")
                 for record_id in args.bootstrap_revision]
    expected_docs = {"carr-workspace-bduf", "carr-control-room-bduf",
                     "carr-mature-software-end-state-bduf"}
    docs = {item.get("document") for item in bootstrap}
    if docs != expected_docs:
        raise Refusal("bootstrap revisions must cover Workspace, Control Room, and Mature End State; "
                      f"found {sorted(str(x) for x in docs)}")
    evidence = {
        "monday": require_evidence(args.repo, args.monday_evidence, "monday"),
        "bootstrap": bootstrap,
        "stage_approval": require_evidence(args.repo, args.stage_approval, "stage_approval"),
        "rollback_approval": require_evidence(args.repo, args.rollback_approval, "rollback_approval"),
    }
    stage = args.vault / "_to_delete" / f"md-renders-cutoff-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    if stage.exists():
        raise Refusal(f"stage already exists: {stage}")
    stage.mkdir(parents=True)
    cfg = Config(args.repo)
    unloaded = False
    transitioned = False
    try:
        # Quiesce every known local writer before even computing the manifest.
        # The preparing sentinel blocks the exporter immediately; launchctl's
        # post-unload absence check proves the scheduled compiler is stopped.
        atomic_json(sentinel_path(args.repo), {"phase": "preparing", "stage": str(stage),
                                               "approved_commit": args.approved_commit})
        launchctl(args.repo, "unload")
        unloaded = True
        paths = retired_paths(args.repo, args.vault)
        expected = len(retiring) + 2
        if len(paths) < expected:
            raise Refusal(f"retirement set incomplete: found {len(paths)}, expected at least {expected}")
        files = [{"path": rel, "bytes": (args.vault / rel).stat().st_size,
                  "sha256": sha256(args.vault / rel)} for rel in paths]
        manifest = {"schema_version": 2, "phase": "staged", "approved_commit": args.approved_commit,
                    "created_at": datetime.now(timezone.utc).isoformat(), "file_count": len(files),
                    "files": files, "evidence": {
                        "monday": evidence["monday"]["id"],
                        "bootstrap": [item["id"] for item in bootstrap],
                        "stage_approval": evidence["stage_approval"]["id"],
                        "rollback_approval": evidence["rollback_approval"]["id"],
                    }}
        atomic_json(stage / "manifest.json", manifest)
        manifest_sha = sha256(stage / "manifest.json")
        cfg.transition("active", "retiring", approved_commit=args.approved_commit,
                       manifest_sha256=manifest_sha,
                       approval_decision_id=evidence["stage_approval"]["id"],
                       reason="reversible doctrine cutoff stage",
                       monday_evidence_id=evidence["monday"]["id"],
                       bootstrap_revision_ids=[item["id"] for item in bootstrap])
        transitioned = True
        atomic_json(sentinel_path(args.repo), {"phase": "staged", "stage": str(stage),
                                               "approved_commit": args.approved_commit,
                                               "manifest_sha256": manifest_sha})
        hook = os.environ.get("CARR_CUTOFF_TEST_BEFORE_MOVE")
        if hook and os.environ.get("CARR_CUTOFF_TEST_MODE") == "1":
            run([str(x) for x in json.loads(hook)])
        for item in files:
            dest = stage / item["path"]
            atomic_retire(args.vault / item["path"], dest, item)
        verify_stage_files(stage, manifest)
        smoke(args.repo)
    except Exception:
        if transitioned:
            complete_rollback(args.repo, args.vault, stage, cfg, reload_job=unloaded)
        else:
            if unloaded:
                launchctl(args.repo, "load")
            sentinel_path(args.repo).unlink(missing_ok=True)
        raise
    stage_evidence = record_system_evidence(args.repo, "cutoff_stage_smoke", {
        "manifest_sha256": manifest_sha, "standing_context": True,
        "read_doctrine": True, "health": True, "consumers": True,
    }, f"post-stage smoke suite from {stage}")
    print(json.dumps({"ok": True, "phase": "staged", "stage": str(stage),
                      "file_count": len(files), "manifest_sha256": manifest_sha,
                      "stage_evidence_id": stage_evidence}, indent=2))


def finalize(args) -> None:
    manifest = read_manifest(args.stage)
    verify_canonical(args.repo, manifest["approved_commit"], args.vault)
    verify_stage_files(args.stage, manifest)
    for item in manifest["files"]:
        if (args.vault / item["path"]).exists():
            raise Refusal(f"finalize found restored/colliding file: {item['path']}")
    cold = require_evidence(args.repo, args.cold_start_evidence, "cold_start")
    stage_evidence = require_evidence(args.repo, args.stage_evidence, "stage_smoke")
    approval = require_evidence(args.repo, args.final_approval, "final_approval")
    cfg = Config(args.repo)
    atomic_json(sentinel_path(args.repo), {"phase": "finalizing", "stage": str(args.stage),
                                           "approved_commit": manifest["approved_commit"]})
    cfg.transition("retiring", "retired", approved_commit=manifest["approved_commit"],
                   manifest_sha256=sha256(args.stage / "manifest.json"),
                   approval_decision_id=approval["id"], reason="verified doctrine cutoff finalization",
                   stage_evidence_id=stage_evidence["id"], cold_start_evidence_id=cold["id"])
    atomic_json(sentinel_path(args.repo), {"phase": "finalized", "stage": str(args.stage),
                                           "approved_commit": manifest["approved_commit"],
                                           "cold_start_evidence": cold["id"],
                                           "final_approval": approval["id"]})
    try:
        smoke(args.repo)
    except Exception:
        complete_rollback(args.repo, args.vault, args.stage, cfg)
        raise
    print(json.dumps({"ok": True, "phase": "finalized", "stage": str(args.stage)}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--vault", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("stage")
    p.add_argument("--approved-commit", required=True)
    p.add_argument("--monday-evidence", required=True)
    p.add_argument("--bootstrap-revision", action="append", required=True)
    p.add_argument("--stage-approval", required=True)
    p.add_argument("--rollback-approval", required=True)
    p = sub.add_parser("finalize")
    p.add_argument("--stage", type=Path, required=True)
    p.add_argument("--cold-start-evidence", required=True)
    p.add_argument("--stage-evidence", required=True)
    p.add_argument("--final-approval", required=True)
    p = sub.add_parser("rollback")
    p.add_argument("--stage", type=Path, required=True)
    args = parser.parse_args()
    args.repo = args.repo.resolve(); args.vault = args.vault.expanduser().resolve()
    try:
        if args.action == "stage": stage_cutoff(args)
        elif args.action == "finalize": finalize(args)
        else:
            manifest = read_manifest(args.stage)
            verify_canonical(args.repo, manifest["approved_commit"], args.vault)
            evidence_id = complete_rollback(args.repo, args.vault, args.stage, Config(args.repo))
            print(json.dumps({"ok": True, "phase": "rolled_back",
                              "rollback_evidence_id": evidence_id}, indent=2))
        return 0
    except Refusal as exc:
        print(f"NO-GO {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

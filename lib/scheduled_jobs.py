"""Read-only scheduled-job inventory and actionable drift reports."""
from __future__ import annotations

import os
import re
import hashlib
import json
import plistlib
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "ops"))
from git_env import scrubbed_env
import machine_role

SCHEDULE_KEYS = ("StartInterval", "StartCalendarInterval", "KeepAlive", "RunAtLoad",
                 "WatchPaths", "QueueDirectories", "ThrottleInterval")


def schedule(plist):
    return {k: plist[k] for k in SCHEDULE_KEYS if k in plist}


def listed(text):
    result = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 3 and re.fullmatch(r"-?\d+", fields[1]):
            result[fields[2]] = {"pid": fields[0], "exit": int(fields[1])}
    return result


def disabled(text):
    return {label: value in ("true", "disabled") for label, value in
            re.findall(r'"([^"\n]+)"\s*=>\s*(true|false|disabled|enabled)', text)}


def cron_entries(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or re.match(r"^[A-Za-z_][\w]*\s*=", line):
            continue
        parts = line.split(None, 1 if line.startswith("@") else 5)
        if len(parts) not in (2, 6):
            raise ValueError("unrecognized crontab entry")
        command = parts[-1]
        label = "cron." + hashlib.sha256(command.encode()).hexdigest()[:16]
        result[label] = {"command": command, "interval": " ".join(parts[:-1])}
    return result


def expand(value):
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    if not isinstance(value, str):
        return value
    return os.path.expanduser(value)


def paths(text):
    return re.findall(r"(?<!\w)/(?:[^\s\"'{};]+)", text)


def runtime_paths(text):
    fields = re.findall(r"(?m)^\s*(?:program|working directory)\s*=\s*(.+)$", text)
    arguments = re.search(r"(?ms)^\s*arguments\s*=\s*\{(.*?)^\s*\}", text)
    if arguments:
        fields.append(arguments[1])
    fields.extend(re.findall(r"(?m)^\s*CARR_REPO(?:_ROOT)?\s*=>\s*(.+)$", text))
    return paths(" ".join(fields))


def render(rows):
    return [f"WARN scheduled jobs {r['label']} {r['code']}: {r['detail']} · on breach: "
            f"tools/job-watchdog.py scan files/updates loop {r['key']} · owner {r['owner']} · "
            f"fix: {r['fix']} · verify: python3 ops/scheduled-jobs-check.py · "
            f"auto-clear: next complete scan without {r['code']} for {r['label']}" for r in rows]


def report(manifest, snapshot, now):
    rows = []
    def emit(label, code, detail, fix, owner="orchestrator"):
        rows.append({"label": label, "code": code, "key": f"scheduled_jobs:{label}:{code}",
                     "owner": owner, "detail": detail, "fix": fix})
    registry = {j["label"]: j for j in manifest["jobs"]}
    live = listed(snapshot["launchctl_list"])
    overrides = disabled(snapshot["launchctl_disabled"])
    prefixes = tuple(manifest.get("scope", {}).get("launchd_label_prefixes", ["com.carr.", "local.carr-"]))
    cron = cron_entries(snapshot["cron"])
    observed = set(snapshot["plists"]) | {k for k in live.keys() | overrides.keys() if k.startswith(prefixes)} | cron.keys()
    for label in sorted(observed - registry.keys()):
        emit(label, "unknown_job", "job has no declared expectation", "review the job and declare its purpose/state or retire it through its owner")
    for error in snapshot.get("errors", []):
        emit(error, "evidence_unavailable", "live evidence could not be read", "restore read access to the named evidence source and rerun the checker")
    for job in manifest["jobs"]:
        label = job["label"]
        owner = job["owner"]
        role = snapshot.get("machine_role")
        enabled = job.get("expected_enabled_by_role", {}).get(role, job["expected_enabled"])
        expected_installed = job.get("expected_installed_by_role", {}).get(role, job.get("expected_installed", True))
        plist = snapshot["plists"].get(label)
        is_cron = job["scheduler"] == "cron"
        present = label in cron if is_cron else plist is not None
        if expected_installed and not present:
            emit(label, "missing_job", "declared job definition is absent", "restore the declared job definition from its repository source and verify registration", owner)
        if is_cron:
            actual = cron.get(label)
            if enabled and not actual:
                continue
            if actual and not enabled:
                emit(label, "unexpected_enabled", "cron entry is active despite disabled expectation", "retire or pause the entry through its owner", owner)
            if actual and actual["interval"] != job["interval"]:
                emit(label, "interval_drift", "cron cadence differs from manifest", "restore the declared cron cadence", owner)
            if actual and expand(job["program_path"]) not in paths(actual["command"]):
                emit(label, "wrong_checkout", "cron command does not use declared program", "restore the declared program and checkout in crontab", owner)
        else:
            is_disabled = overrides.get(label, bool((plist or {}).get("Disabled", False)))
            if enabled and is_disabled:
                emit(label, "disabled_but_expected", "launchd disabled override or plist Disabled is set", "restore expected enabled state through the agent owner and verify launchctl print-disabled", owner)
            if enabled and plist and label not in live:
                emit(label, "missing_registration", "plist exists but launchd has no registered job", "bootstrap the declared agent and verify launchctl print", owner)
            if not enabled and label in live and not is_disabled:
                emit(label, "unexpected_enabled", "job is registered despite disabled expectation", "retire or pause the agent through its owner", owner)
            if plist and schedule(plist) != expand(job["interval"]):
                emit(label, "interval_drift", "installed cadence differs from manifest", "restore declared cadence and reload the agent", owner)
            if plist and job.get("log_paths") and [plist.get(k) for k in ("StandardOutPath", "StandardErrorPath")] != [expand(p) if p else None for p in job["log_paths"]]:
                emit(label, "log_path_drift", "installed log destinations differ from manifest", "restore declared log paths and reload the agent", owner)
            runtime = snapshot["launchctl_print"].get(label, "")
            codes = re.findall(r"last exit (?:code|status)\s*=\s*(-?\d+)", runtime)
            code = int(codes[-1]) if codes else live.get(label, {}).get("exit", 0)
            if enabled and code:
                emit(label, "failing_exit", f"last exit status {code}", "inspect the job log and repair the failing command; verify a successful run", owner)
        if enabled and present and job.get("log_max_age_seconds") is not None:
            log = expand(job.get("activity_path") or job["log_path"])
            mtime = snapshot["log_mtimes"].get(log)
            if mtime is None or now - mtime > job["log_max_age_seconds"]:
                emit(label, "stale_log", "declared activity log is missing or older than its allowed cadence", "inspect the scheduler and job log; restore a run that writes the declared activity signal", owner)
        if plist is None or is_cron:
            continue
        runtime = snapshot["launchctl_print"].get(label, "")
        expected = expand(job["program_path"])
        disk_paths = paths(" ".join(plist.get("ProgramArguments", [])) + " " + plist.get("Program", ""))
        loaded_paths = runtime_paths(runtime)
        required = expand(job["required_checkout"])
        wrong = expected not in disk_paths or (runtime and expected not in loaded_paths)
        for path in disk_paths + loaded_paths + [plist.get("WorkingDirectory", "")]:
            checkout = snapshot.get("checkout_roots", {}).get(path)
            if checkout and os.path.realpath(checkout) != os.path.realpath(required):
                wrong = True
            if "/carr-system" in path and not (path == required or path.startswith(required + "/")):
                wrong = True
        if wrong:
            emit(label, "wrong_checkout", "installed or loaded runtime does not use declared checkout/program",
                 f"restore declared program {job['program_path']} in {job['required_checkout']} and reload the agent",
                 job["owner"])
    git = snapshot.get("git", {})
    if git.get("behind", 0):
        emit("canonical", "behind_main", f"canonical checkout is {git['behind']} commits behind origin/main",
             "repair canonical fleet-sync/fast-forward through bin/fleet-sync.sh and verify HEAD equals origin/main")
    if git.get("branch") != "main":
        emit("canonical", "wrong_branch", "canonical checkout does not select main", "restore canonical main through the repository hygiene owner without discarding local work")
    if git.get("remote_matches") is False:
        emit("canonical", "remote_ref_stale", "origin/main does not match GitHub main; behind count is only a cached lower bound",
             "restore fleet-sync's fetch and fast-forward, then verify against remote main")
    return rows


def collect(manifest):
    """Capture machine evidence without modifying git refs, schedules or logs."""
    snapshot = {"plists": {}, "launchctl_print": {}, "log_mtimes": {}, "errors": [], "git": {}, "checkout_roots": {}}
    snapshot["machine_role"] = "primary" if machine_role.is_primary(str(SOURCE), env=scrubbed_env()) else "secondary"
    def command(argv, source, absent_ok=False):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10,
                                    stdin=subprocess.DEVNULL, env=scrubbed_env())
            if result.returncode and not absent_ok:
                snapshot["errors"].append(source)
            return result
        except (OSError, subprocess.SubprocessError):
            snapshot["errors"].append(source)
            return subprocess.CompletedProcess(argv, 127, "", "")
    domain = f"gui/{os.getuid()}"
    snapshot["launchctl_list"] = command(["launchctl", "list"], "launchctl_list").stdout
    snapshot["launchctl_disabled"] = command(["launchctl", "print-disabled", domain], "launchctl_disabled").stdout
    prefixes = tuple(manifest.get("scope", {}).get("launchd_label_prefixes", ["com.carr.", "local.carr-"]))
    directories = [Path.home() / "Library/LaunchAgents", Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons")]
    for directory in directories:
        for path in sorted(directory.glob("*.plist")):
            if not path.name.startswith(prefixes):
                continue
            try:
                plist = plistlib.loads(path.read_bytes())
                label = plist["Label"]
                if label in snapshot["plists"]:
                    snapshot["errors"].append("duplicate_plist:" + label)
                snapshot["plists"][label] = plist
            except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
                snapshot["errors"].append("plist:" + path.name)
    for label in listed(snapshot["launchctl_list"]):
        if label.startswith(prefixes):
            snapshot["launchctl_print"][label] = command(["launchctl", "print", domain + "/" + label], "launchctl_print:" + label).stdout
    cron = command(["crontab", "-l"], "crontab", absent_ok=True)
    if cron.returncode and not (cron.returncode == 1 and "no crontab for" in cron.stderr):
        snapshot["errors"].append("crontab")
    snapshot["cron"] = cron.stdout
    directories_seen = {}
    candidate_paths = []
    for label, plist in snapshot["plists"].items():
        candidate_paths.extend(paths(" ".join(plist.get("ProgramArguments", []))))
        candidate_paths.extend(runtime_paths(snapshot["launchctl_print"].get(label, "")))
        if plist.get("WorkingDirectory"):
            candidate_paths.append(plist["WorkingDirectory"])
    candidate_paths.extend(paths(snapshot["cron"]))
    for path in set(candidate_paths):
        if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(path).name):
            continue  # The interpreter's dependency checkout is not the job's code checkout.
        directory = Path(path)
        if not directory.is_dir():
            directory = directory.parent
        while not directory.is_dir() and directory != directory.parent:
            directory = directory.parent
        directory = directory.resolve()
        if directory not in directories_seen:
            result = command(["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
                             "runtime_checkout", absent_ok=True)
            directories_seen[directory] = result.stdout.strip() if result.returncode == 0 else None
            if result.returncode not in (0, 128):
                snapshot["errors"].append("runtime_checkout")
        checkout = directories_seen[directory]
        if checkout:
            snapshot["checkout_roots"][path] = checkout
    logs = {expand(j.get("activity_path") or j["log_path"]) for j in manifest["jobs"] if j.get("log_path")}
    logs.update(p[k] for p in snapshot["plists"].values() for k in ("StandardOutPath", "StandardErrorPath") if p.get(k))
    for log in logs:
        try:
            snapshot["log_mtimes"][log] = Path(log).stat().st_mtime
        except FileNotFoundError:
            snapshot["log_mtimes"][log] = None
        except OSError:
            snapshot["errors"].append("log_stat:" + log)
    canonical = expand(manifest["canonical_checkout"])
    prefix = ["git", "-C", canonical]
    ab = command(prefix + ["rev-list", "--left-right", "--count", "HEAD...origin/main"], "canonical_revision").stdout.split()
    if len(ab) == 2:
        snapshot["git"].update(ahead=int(ab[0]), behind=int(ab[1]))
    snapshot["git"]["branch"] = command(prefix + ["symbolic-ref", "--short", "HEAD"], "canonical_branch").stdout.strip()
    local = command(prefix + ["rev-parse", "origin/main"], "origin_main").stdout.strip()
    remote = command(prefix + ["ls-remote", "origin", "refs/heads/main"], "remote_main").stdout.split()
    if remote:
        snapshot["git"]["remote_matches"] = local == remote[0]
    else:
        snapshot["errors"].append("remote_main")
    return snapshot


def load_manifest(path):
    manifest = json.loads(Path(path).read_text())
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported scheduled-job manifest")
    labels = set()
    for job in manifest["jobs"]:
        for field in ("label", "scheduler", "program_path", "required_checkout", "interval",
                      "expected_enabled", "log_path", "owner", "done_signal"):
            if field not in job:
                raise ValueError("job missing " + field)
        if job["label"] in labels or job["scheduler"] not in ("launchd", "cron") or not isinstance(job["expected_enabled"], bool):
            raise ValueError("invalid or duplicate scheduled job")
        if job.get("log_max_age_seconds") is not None and job["log_max_age_seconds"] <= 0:
            raise ValueError("invalid log freshness threshold")
        if job["required_checkout"] != manifest["canonical_checkout"] and not job.get("checkout_exception"):
            raise ValueError("noncanonical checkout needs a declared exception")
        labels.add(job["label"])
    return manifest


def check(path=None, snapshot=None, now=None):
    manifest = load_manifest(path or SOURCE / "ops/config/scheduled-jobs.v1.json")
    return report(manifest, collect(manifest) if snapshot is None else snapshot,
                  time.time() if now is None else now)


def capture_manifest():
    canonical = str(Path.home() / "carr-system")
    manifest = {"schema_version": 1, "canonical_checkout": "~/carr-system",
                "scope": {"launchd_label_prefixes": ["com.carr.", "local.carr-"],
                          "cron": "all current-user entries", "claude": "definition drift owned by config-as-code; execution owned by Control Plane"},
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "jobs": []}
    snapshot = collect(manifest)
    if snapshot["errors"]:
        raise ValueError("live inventory is incomplete")
    def portable(value):
        if isinstance(value, str):
            return value.replace(str(Path.home()), "~")
        if isinstance(value, list):
            return [portable(v) for v in value]
        if isinstance(value, dict):
            return {k: portable(v) for k, v in value.items()}
        return value
    for label, plist in sorted(snapshot["plists"].items()):
        argv = plist.get("ProgramArguments", [])
        programs = [v for v in argv if v.startswith(canonical + "/") and
                    (v.endswith((".py", ".sh")) or "/.build/" in v) and not v.endswith("run-scheduled.sh")]
        program = programs[0] if programs else plist.get("Program") or (argv[0] if argv else "")
        log = plist.get("StandardOutPath") or plist.get("StandardErrorPath") or canonical + "/out/run-scheduled.log"
        row = {"label": label, "scheduler": "launchd", "program_path": portable(program),
               "required_checkout": "~/carr-system", "interval": portable(schedule(plist)),
               "expected_enabled": None, "expected_installed": True, "log_path": portable(log),
               "log_paths": [portable(plist.get(k)) for k in ("StandardOutPath", "StandardErrorPath")],
               "log_max_age_seconds": None, "owner": "orchestrator",
               "done_signal": "No durable completion signal verified; inspect job-specific producer"}
        wrappers = [i for i, v in enumerate(argv) if v.endswith("/bin/run-scheduled.sh")]
        if wrappers:
            i = wrappers[0] + 1
            while i < len(argv) and argv[i].startswith("--"):
                i += 2
            service, key = argv[i:i+2]
            receipt = f"{hashlib.sha256(service.encode()).hexdigest()[:16]}.{hashlib.sha256(key.encode()).hexdigest()[:32]}.receipt"
            row["activity_path"] = "~/carr-system/out/run-scheduled-receipts/" + receipt
            row["done_signal"] = f"ops.run {service}/{key}; local run-scheduled receipt {row['activity_path']}"
        manifest["jobs"].append(row)
    for label, entry in cron_entries(snapshot["cron"]).items():
        command_paths = paths(entry["command"])
        manifest["jobs"].append({"label": label, "scheduler": "cron",
                                 "program_path": portable(command_paths[0] if command_paths else ""),
                                 "required_checkout": "~/carr-system", "interval": entry["interval"],
                                 "expected_enabled": None, "expected_installed": True,
                                 "log_path": None, "log_max_age_seconds": None,
                                 "owner": "orchestrator", "done_signal": "Requires producer review"})
    return manifest

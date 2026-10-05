"""Machine-local launchd holds and repo definitions that must stay off.

~/.config/carr/launchd-hold contains one `label reason` per line. Blank lines
and # comments are ignored. `label @2026-10-05T19:31:11Z reason` pins an
individual hold's start; otherwise age is explicitly the hold file's age.
Repo plists may declare `<!-- carr-launchd-definition-only: reason -->`.
Removing a hold permits the next installer run to activate the job again.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
DEFINITION_ONLY = re.compile(r"<!--\s*carr-launchd-definition-only:\s*(.*?)\s*-->", re.S)


@dataclass(frozen=True)
class Hold:
    label: str
    reason: str
    since: float
    age_source: str

    def describe(self, now=None):
        days = max(0, (time.time() if now is None else now) - self.since) / 86400
        return f"HELD {self.label}: {self.reason} · age {days:.1f}d ({self.age_source})"


def read_holds(home=None):
    path = (Path(home) if home is not None else Path.home()) / ".config/carr/launchd-hold"
    try:
        with path.open(encoding="utf-8") as source:
            mtime = os.fstat(source.fileno()).st_mtime
            text = source.read()
    except FileNotFoundError:
        return {}
    holds = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(None, 1)
        label, reason = fields[0], fields[1] if len(fields) == 2 else ""
        since, age_source = mtime, "hold file age"
        if reason.startswith("@"):
            parts = reason.split(None, 1)
            timestamp, reason = parts[0], parts[1] if len(parts) == 2 else ""
            try:
                at = datetime.fromisoformat(timestamp[1:].replace("Z", "+00:00"))
                if at.tzinfo is None or at.timestamp() > time.time():
                    raise ValueError("timestamp must have a timezone and must not be in the future")
                since, age_source = at.timestamp(), "hold start"
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: invalid hold timestamp") from exc
        if not LABEL.fullmatch(label) or not reason.strip() or label in holds:
            raise ValueError(f"{path}:{number}: expected one unique label and a reason")
        holds[label] = Hold(label, reason.strip(), since, age_source)
    return holds


def definition_only_reason(body):
    match = DEFINITION_ONLY.search(body or "")
    if not match:
        return None
    if not match.group(1).strip():
        raise ValueError("carr-launchd-definition-only requires a reason")
    return " ".join(match.group(1).split())


def ensure_off(label, launchctl="launchctl", domain=None):
    """Disable, boot out if loaded, and verify both launchd states."""
    if not LABEL.fullmatch(label):
        raise ValueError("invalid launchd label")
    domain = domain or f"gui/{os.getuid()}"
    target = f"{domain}/{label}"
    def run(*args):
        return subprocess.run([launchctl, *args], capture_output=True, text=True,
                              check=False, timeout=15)
    disabled = run("disable", target)
    if disabled.returncode:
        raise RuntimeError(f"disable failed for {label}: {disabled.stderr.strip()[:120]}")
    loaded = run("print", target)
    if loaded.returncode == 0:
        out = run("bootout", target)
        if out.returncode:
            raise RuntimeError(f"bootout failed for held {label}: {out.stderr.strip()[:120]}")
        loaded = run("print", target)
    detail = (loaded.stderr or "") + (loaded.stdout or "")
    if loaded.returncode != 113 or f'Could not find service "{label}"' not in detail:
        raise RuntimeError(f"held {label} is not verified unloaded")
    state = run("print-disabled", domain)
    key = re.escape(label)
    overrides = re.findall(r'(?m)^\s*(?:"' + key + r'"|' + key + r')\s*=>\s*([A-Za-z]+)\b', state.stdout)
    if state.returncode or len(overrides) != 1 or overrides[0].lower() not in {"true", "disabled"}:
        raise RuntimeError(f"held {label} is not verified disabled")


if __name__ == "__main__":
    import sys
    try:
        label = sys.argv[1]
        hold = read_holds().get(label)
        reason = None
        for source in sys.argv[4:]:
            try:
                reason = definition_only_reason(Path(source).read_text()) or reason
            except FileNotFoundError:
                continue
        if hold is None and reason is None:
            raise SystemExit(3)
        print(hold.describe() if hold else f"DEFINITION ONLY {label}: {reason}")
        ensure_off(label, sys.argv[2], sys.argv[3])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"launchd hold: REFUSED {exc}", file=sys.stderr)
        raise SystemExit(1)

"""The one Python reader of ~/.config/carr/*.env credential files.

These files have two consumers with one contract. Shell jobs load them with
`set -a; . db.env` (bin/nightly.sh, bin/migrate-prod.sh and the rest), so a
DSN carrying `&` must be single-quoted. Python jobs used to parse them with
their own `split("=", 1)` lines, at least nine of them, each slightly
different. On 2026-08-02 quoting a value for the shell broke four of those
readers at once and blinded the monitor that would have reported it.

read_env_file() reads a line the way the shell does: optional `export`,
single quotes literal (including rotate-credential's `'\\''` escape), double
quotes with backslash escapes, a trailing `# comment` dropped, the last
assignment winning. A line the shell could not parse (an unterminated quote)
sets nothing here either, but unlike the shell it does not take the rest of
the file down with it; tools/health-check.py's `zsh -n` row reports that.

Nothing here prints, logs or returns a value to anything but its caller.
"""
from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def carr_config(name: str = "") -> Path:
    """~/.config/carr (or a file in it), resolved against HOME when called."""
    return Path.home() / ".config" / "carr" / name


def read_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Every NAME the shell's `set -a; . path` would set, with the value it
    would hold. Raises OSError when the file cannot be read."""
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if not match:
            continue
        name, raw = match.groups()
        try:
            words = shlex.split(raw, comments=True, posix=True)
        except ValueError:
            continue
        values[name] = words[0] if words else ""
    return values


def credential(name: str, *, path: str | os.PathLike[str] | None = None,
               environ: Mapping[str, str] | None = None) -> str | None:
    """A job's named credential: the job's environment first, else the file
    (default ~/.config/carr/db.env). None when neither holds a non-empty value
    or the file cannot be read."""
    value = (os.environ if environ is None else environ).get(name)
    if value:
        return value
    try:
        return read_env_file(path or carr_config("db.env")).get(name) or None
    except OSError:
        return None

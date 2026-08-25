"""Fail-closed, read-only observation helpers for a user's launchd domain."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Literal

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
LabelState = Literal["loaded", "not_found"]
_CARR_LABEL = re.compile(r"\b(com\.carr\.[A-Za-z0-9._-]+)(?:\s|$)")
_NOT_FOUND = re.compile(r"could not find service|service .* not found|no such service", re.IGNORECASE)


class LaunchdObservationError(RuntimeError):
    """launchd could not provide a trustworthy observation."""


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)


def _uid(uid: int | None) -> int:
    return os.getuid() if uid is None else uid


def label_state(label: str, *, runner: Runner = _default_runner,
                uid: int | None = None) -> LabelState:
    """Read one label with exact native ``launchctl print`` semantics."""
    result = runner(["/bin/launchctl", "print", f"gui/{_uid(uid)}/{label}"])
    if result.returncode == 0:
        return "loaded"
    detail = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if _NOT_FOUND.search(detail):
        return "not_found"
    raise LaunchdObservationError(
        f"launchctl print failed for {label} with exit {result.returncode}: {detail.strip()}"
    )


def _labels_from_domain(text: str) -> set[str]:
    """Extract labels only from ``services``, never ``disabled services``."""
    labels: set[str] = set()
    in_services = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "services = {":
            in_services = True
            continue
        if in_services and stripped == "disabled services = {":
            break
        if in_services:
            match = _CARR_LABEL.search(line)
            if match:
                labels.add(match.group(1))
    return labels


def loaded_labels(*, runner: Runner = _default_runner,
                  uid: int | None = None) -> set[str]:
    """Return CARR labels loaded in the user's launchd domain.

    The domain dump supplies the inventory; each discovered CARR label is then
    confirmed with the exact per-label ``launchctl print`` read. Any native read
    failure raises instead of returning a partial or empty set.
    """
    result = runner(["/bin/launchctl", "print", f"gui/{_uid(uid)}"])
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stdout, result.stderr) if part)
        raise LaunchdObservationError(
            f"launchctl domain print failed with exit {result.returncode}: {detail.strip()}"
        )
    labels = _labels_from_domain(result.stdout)
    for label in sorted(labels):
        if label_state(label, runner=runner, uid=uid) != "loaded":
            raise LaunchdObservationError(
                f"launchctl domain listed {label}, but its exact print read was absent"
            )
    return labels

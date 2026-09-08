"""Disabled, fixture-only runtime allocation and entrant-lock contract.

This module deliberately has no command-line entry point and is not imported by
the helper runtime.  A future activation packet must supply its own consumer.
"""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from typing import Callable, Mapping


class IsolationRefusal(RuntimeError):
    """A safe refusal; callers preserve the registry instead of retrying it."""


DENIED_CREDENTIAL_NAMES = frozenset({
    "DATABASE_URL", "PRODUCTION_DATABASE_URL", "BREAKGLASS_TOKEN",
    "BREAK_GLASS_TOKEN", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY",
})
ALLOWED_FAKE_CREDENTIAL_NAMES = frozenset({"R09_FAKE_TOKEN", "R09_FAKE_DATABASE_URL"})


@dataclass(frozen=True)
class Owner:
    session: str
    worktree: str
    source_sha: str

    @property
    def key(self) -> str:
        return "|".join((self.session, self.worktree, self.source_sha))


@dataclass(frozen=True)
class Bundle:
    ports: tuple[int, ...]
    compose_name: str
    database_name: str


class RuntimeIsolation:
    """A small atomic JSON registry, intended solely for owned test fixtures.

    ``root`` is an already-authorized coordination root supplied by the
    registered-helper substrate.  It is never inferred from a caller path.
    """

    def __init__(self, root: Path, owner: Owner, *, clock: Callable[[], float] = time.time):
        self.root, self.owner, self.clock = Path(root), owner, clock
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.state_path = self.root / "r09-state.json"
        self.lock_path = self.root / "r09-registry.lock"

    def _locked(self):
        handle = self.lock_path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    def _read(self) -> dict:
        if not self.state_path.exists():
            return {"allocations": {}, "entrant": None, "receipts": []}
        with self.state_path.open() as handle:
            state = json.load(handle)
        if set(state) != {"allocations", "entrant", "receipts"}:
            raise IsolationRefusal("unknown registry state is preserved")
        return state

    def _write(self, state: dict) -> None:
        fd, name = tempfile.mkstemp(dir=self.root, prefix=".r09-", text=True)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.state_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def allocate(self, bundle: Bundle, *, ttl_seconds: int = 300) -> dict:
        if not bundle.ports or len(set(bundle.ports)) != len(bundle.ports):
            raise IsolationRefusal("bundle must contain distinct explicit ports")
        if not bundle.compose_name or not bundle.database_name:
            raise IsolationRefusal("bundle must be complete")
        with self._locked():
            state = self._read()
            used = {item for entry in state["allocations"].values() for item in entry["resources"]}
            resources = {f"port:{port}" for port in bundle.ports} | {
                f"compose:{bundle.compose_name}", f"database:{bundle.database_name}"}
            if resources & used:
                raise IsolationRefusal("complete bundle collision; no partial claim")
            run_id = secrets.token_hex(16)
            generation = secrets.token_hex(16)
            state["allocations"][run_id] = {
                "owner": self.owner.key, "generation": generation,
                "resources": sorted(resources), "expires_at": self.clock() + ttl_seconds,
                "claimed_at": self.clock(),
            }
            self._write(state)
            artifact = self.root / "runs" / run_id
            artifact.mkdir(mode=0o700, parents=True, exist_ok=False)
            return {"run_id": run_id, "generation": generation, "artifact": artifact}

    def write_fake_environment(self, allocation: Mapping[str, object], values: Mapping[str, str]) -> Path:
        # Names are validated before any mapping value is requested.
        names = tuple(values.keys())
        if any(name in DENIED_CREDENTIAL_NAMES or "PROD" in name or "BREAK" in name for name in names):
            raise IsolationRefusal("credential name refused before value access")
        if set(names) - ALLOWED_FAKE_CREDENTIAL_NAMES:
            raise IsolationRefusal("credential name is not allowlisted")
        path = Path(str(allocation["artifact"])) / "env"
        with path.open("x") as handle:
            for name in names:
                handle.write(f"{name}={values[name]}\n")
        os.chmod(path, 0o600)
        return path

    def acquire_entrant(self, run_id: str, *, stale_after_seconds: int, owner_alive: Callable[[str], bool | None]) -> str:
        with self._locked():
            state = self._read(); now = self.clock(); current = state["entrant"]
            if current:
                age = now - current["created_at"]
                alive = owner_alive(current["owner"])
                if age <= stale_after_seconds or alive is not False:
                    state["receipts"].append({"event": "entrant-refused", "at": now})
                    self._write(state)
                    raise IsolationRefusal("entrant active or liveness uncertain")
            generation = secrets.token_hex(16)
            state["entrant"] = {"owner": self.owner.key, "run_id": run_id,
                                "generation": generation, "created_at": now,
                                "takeover": current is not None}
            state["receipts"].append({"event": "entrant-acquired", "at": now})
            self._write(state)
            return generation

    def lock_posture(self) -> dict:
        """Narrow R07 read seam: returns a copy and never writes registry state."""
        with self._locked():
            current = self._read()["entrant"]
            return {"state": "none" if current is None else "active", "entrant": current.copy() if current else None}

    def teardown(self, allocation: Mapping[str, object], entrant_generation: str | None = None) -> None:
        run_id, generation = str(allocation["run_id"]), str(allocation["generation"])
        with self._locked():
            state = self._read(); entry = state["allocations"].get(run_id)
            if not entry or entry["owner"] != self.owner.key or entry["generation"] != generation:
                raise IsolationRefusal("allocation ownership or generation mismatch")
            current = state["entrant"]
            if entrant_generation is not None:
                if not current or current["owner"] != self.owner.key or current["generation"] != entrant_generation:
                    raise IsolationRefusal("entrant ownership or generation mismatch")
                state["entrant"] = None
            del state["allocations"][run_id]
            state["receipts"].append({"event": "teardown", "at": self.clock()})
            self._write(state)
        artifact = Path(str(allocation["artifact"]))
        for child in artifact.iterdir():
            child.unlink()
        artifact.rmdir()

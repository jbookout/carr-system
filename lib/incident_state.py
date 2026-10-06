"""Locked episode state persisted before remote effects."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


class IncidentState:
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.data = {}
        if not isinstance(self.data, dict):
            raise ValueError("incident state must be an object")

    def save(self):
        fd, name = tempfile.mkstemp(prefix=".incident-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as target:
                json.dump(self.data, target, sort_keys=True)
                target.flush()
                os.fsync(target.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)


@contextmanager
def locked_state(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield IncidentState(path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

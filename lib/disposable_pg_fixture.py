"""Bound concurrent disposable PostgreSQL proofs without sharing their clusters.

A proof may need two clusters to demonstrate role isolation. Hold the host-wide
budget before initializing its first cluster and through final shutdown. Nested
fixtures in that proof reuse the lease; unrelated processes wait for teardown.
The primary CI database is outside this lease because its child proofs need it.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import threading

LOCK_PATH = Path('/tmp') / f'carr-disposable-postgres-{os.getuid()}.lock'
_thread_lock = threading.RLock()
_depth = 0


@contextmanager
def postgres_fixture_group():
    global _depth
    with _thread_lock:
        if _depth:
            _depth += 1
            try:
                yield
            finally:
                _depth -= 1
            return
        # Never unlink: waiters must continue to lock the same inode. Kernel
        # ownership releases on exit/crash, so no stale PID cleanup is needed.
        with LOCK_PATH.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _depth = 1
            try:
                yield
            finally:
                _depth = 0
                fcntl.flock(handle, fcntl.LOCK_UN)


if __name__ == '__main__':
    import sys
    # Node fixtures hold this subprocess's stdin open until their PostgreSQL
    # teardown completes. EOF also releases the lease if the caller exits.
    with postgres_fixture_group():
        print('ready', flush=True)
        sys.stdin.read()

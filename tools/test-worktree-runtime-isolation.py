"""Fake-only fixture tests for the disabled R09 runtime-isolation contract."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "room-bridge"))
from worktree_runtime_isolation import Bundle, IsolationRefusal, Owner, RuntimeIsolation


def owner(name):
    return Owner(session=name, worktree=f"fixture-{name}", source_sha="fixture-source")


def test_atomic_overlap_and_exact_teardown(iterations=8):
    with tempfile.TemporaryDirectory() as tmp:
        witnessed = 0
        for iteration in range(iterations):
            root = Path(tmp) / str(iteration)
            start = threading.Barrier(2); windows = []; results = []; errors = []
            def contender(index):
                service = RuntimeIsolation(root, owner(f"s{iteration}-{index}"))
                start.wait(); attempted = time.monotonic()
                try:
                    allocation = service.allocate(Bundle((31000 + iteration * 2 + index,), f"c{iteration}-{index}", f"d{iteration}-{index}"))
                    windows.append((attempted, time.monotonic()))
                    results.append((service, allocation))
                except Exception as exc: errors.append(exc)
            threads = [threading.Thread(target=contender, args=(i,)) for i in range(2)]
            [t.start() for t in threads]; [t.join(10) for t in threads]
            assert not errors and not any(t.is_alive() for t in threads) and len(results) == 2
            assert len({x[1]["run_id"] for x in results}) == 2
            if max(w[0] for w in windows) <= min(w[1] for w in windows): witnessed += 1
            first, allocation = results[0]
            second = RuntimeIsolation(root, owner("other"))
            try: second.teardown(allocation)
            except IsolationRefusal: pass
            else: raise AssertionError("cross-owner teardown succeeded")
            first.teardown(allocation); results[1][0].teardown(results[1][1])
        assert witnessed >= min(8, iterations), f"only {witnessed} synchronized overlaps witnessed"


def test_credential_name_is_refused_without_value_access():
    class Poison(dict):
        def __getitem__(self, key): raise AssertionError("credential value was accessed")
    with tempfile.TemporaryDirectory() as tmp:
        service = RuntimeIsolation(Path(tmp), owner("s")); allocation = service.allocate(Bundle((32000,), "c", "d"))
        try: service.write_fake_environment(allocation, Poison(PRODUCTION_DATABASE_URL="nope"))
        except IsolationRefusal: pass
        else: raise AssertionError("production name was accepted")
        env = service.write_fake_environment(allocation, {"R09_FAKE_TOKEN": "fake"})
        assert env.stat().st_mode & 0o777 == 0o600
        service.teardown(allocation); assert not env.exists()


def test_entrant_lock_liveness_and_read_seam():
    with tempfile.TemporaryDirectory() as tmp:
        now = [1000.0]
        clock = lambda: now[0]
        first = RuntimeIsolation(Path(tmp), owner("a"), clock=clock); second = RuntimeIsolation(Path(tmp), owner("b"), clock=clock)
        allocation = first.allocate(Bundle((33000,), "c", "d")); generation = first.acquire_entrant(allocation["run_id"], stale_after_seconds=0, owner_alive=lambda _: False)
        before = second.lock_posture()
        try: second.acquire_entrant("other", stale_after_seconds=0, owner_alive=lambda _: True)
        except IsolationRefusal: pass
        else: raise AssertionError("second entrant succeeded")
        assert second.lock_posture() == before
        now[0] += 10
        for liveness in (True, None):
            try: second.acquire_entrant("other", stale_after_seconds=1, owner_alive=lambda _: liveness)
            except IsolationRefusal: pass
            else: raise AssertionError("live or uncertain entrant was reclaimed")
        takeover = second.acquire_entrant("other", stale_after_seconds=1, owner_alive=lambda _: False)
        assert second.lock_posture()["entrant"]["takeover"] is True
        try: first.teardown(allocation, "wrong")
        except IsolationRefusal: pass
        else: raise AssertionError("wrong generation released entrant")
        try: first.teardown(allocation, generation)
        except IsolationRefusal: pass
        else: raise AssertionError("old owner released taken-over entrant")
        second.teardown(second.allocate(Bundle((33001,), "c2", "d2")), takeover)


if __name__ == "__main__":
    test_atomic_overlap_and_exact_teardown(64); test_credential_name_is_refused_without_value_access(); test_entrant_lock_liveness_and_read_seam()
    print("worktree-runtime-isolation: fake-only contract passed")

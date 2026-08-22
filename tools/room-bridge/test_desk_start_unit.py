#!/usr/bin/env python3
"""Unit proof for `desk start` — the command that puts a desk on the line.

A DESK MUST OUTLIVE ITS FIRST ANSWER. Plain `claude -p` prints and exits, so a
session started that way is gone before the second task arrives; the live test
in this package passed exactly once by winning that race before it was fixed.
So starting a desk means starting a session that STAYS, and the two things
that keep it standing are streaming input and a stdin that never reaches
end-of-file. The launcher holds the second open by giving the session a FIFO
opened read-write, which cannot EOF while the process holds both ends.

No real Claude session is booted here and no tokens are spent: a stand-in
binds the socket and sits there, which is the only behaviour the launcher
actually depends on.

Run:  python3 tools/room-bridge/test_desk_start_unit.py
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import desks  # noqa: E402
import dispatch  # noqa: E402

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except AssertionError as e:
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(label)
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


STANDIN = '''#!/usr/bin/env python3
"""Stands in for a Claude session: binds the socket it was told to and waits."""
import os, socket, sys, time
sock = None
for i, a in enumerate(sys.argv):
    if a == "--messaging-socket-path":
        sock = sys.argv[i + 1]
open(os.environ["STANDIN_ARGV_LOG"], "w").write("\\n".join(sys.argv[1:]))
try:
    os.unlink(sock)
except FileNotFoundError:
    pass
os.makedirs(os.path.dirname(sock), exist_ok=True)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sock)
srv.listen(8)
while True:
    time.sleep(0.2)
'''


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="desk-start-test-")
    root = Path(tmp.name)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    argv_log = root / "argv.txt"
    fake = bin_dir / "claude"
    fake.write_text(STANDIN)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    reg = desks.Registry(root / "desks.json")
    state = root / "desks"
    sock_dir = root / "socks"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               STANDIN_ARGV_LOG=str(argv_log))

    def start_puts_a_desk_on_the_line():
        out = dispatch.desk_start(
            "test-desk", registry=reg, state_dir=state, sock_dir=sock_dir, env=env,
        )
        assert out["socket"] == str(sock_dir / "test-desk.sock"), out
        assert desks.is_live(out["socket"]), "the desk never bound its socket"
        assert reg.resolve("test-desk")["kind"] == "claude-session"
        assert Path(out["log"]).exists(), "no log file"
        assert (state / "test-desk.pid").read_text().strip().isdigit()

    check("start binds a labeled socket and registers the desk",
          start_puts_a_desk_on_the_line)

    def it_starts_a_session_that_stays():
        argv = argv_log.read_text().splitlines()
        assert "--input-format" in argv, argv
        assert argv[argv.index("--input-format") + 1] == "stream-json", argv
        assert "--output-format" in argv, argv
        assert argv[argv.index("--output-format") + 1] == "stream-json", argv
        assert "-p" in argv or "--print" in argv, argv

    check("the session is started in the form that outlives its first answer",
          it_starts_a_session_that_stays)

    def stdin_never_ends():
        fifo = state / "test-desk.stdin"
        assert fifo.exists(), "no stdin FIFO was made"
        assert stat.S_ISFIFO(fifo.stat().st_mode), "stdin is not a FIFO"

    check("the desk is given a stdin that cannot reach end-of-file", stdin_never_ends)

    def a_second_start_is_not_a_second_session():
        first = (state / "test-desk.pid").read_text().strip()
        out = dispatch.desk_start(
            "test-desk", registry=reg, state_dir=state, sock_dir=sock_dir, env=env,
        )
        assert out["already_running"] is True, out
        assert (state / "test-desk.pid").read_text().strip() == first, "it started a second one"

    check("starting a desk that is already up does not start a second one",
          a_second_start_is_not_a_second_session)

    def all_digit_names_are_refused():
        try:
            dispatch.desk_start("12345", registry=reg, state_dir=state,
                                sock_dir=sock_dir, env=env)
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("a name that becomes a pid-shaped socket was allowed")

    check("a name that would look like a pid socket is refused", all_digit_names_are_refused)

    def a_task_reaches_a_started_desk():
        row = dispatch.dispatch("test-desk", "do the thing", registry=reg,
                                results_path=root / "results.jsonl")
        assert row["status"] == "delivered", row

    check("a dispatched task reaches the desk that start put up",
          a_task_reaches_a_started_desk)

    def stop_takes_it_down():
        out = dispatch.desk_stop("test-desk", state_dir=state, sock_dir=sock_dir)
        assert out["stopped"] is True, out
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and desks.is_live(str(sock_dir / "test-desk.sock")):
            time.sleep(0.1)
        assert not desks.is_live(str(sock_dir / "test-desk.sock")), "still listening"
        try:
            reg.resolve("test-desk")
        except desks.DeskError as e:
            assert e.code == "desk_not_live", e.code
        else:
            raise AssertionError("a stopped desk still resolved")

    check("stop takes the desk down and it stops resolving", stop_takes_it_down)

    def stop_is_safe_to_repeat():
        out = dispatch.desk_stop("test-desk", state_dir=state, sock_dir=sock_dir)
        assert out["stopped"] is False, out

    check("stopping a desk that is already down says so instead of failing",
          stop_is_safe_to_repeat)

    tmp.cleanup()
    print()
    if FAILURES:
        print(f"desk-start unit: {len(FAILURES)} FAILED")
        return 1
    print("desk-start unit: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

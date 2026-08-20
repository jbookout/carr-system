#!/usr/bin/env python3
"""Unit proof for the Hermes dispatch desk. No live session, no model spend.

WHAT THIS IS FOR. Joe, 2026-08-20: "i want you to build a bridge with hermes
so that hermes can dispatch to sessions in claude or codex ... hermes will
delegate to the appropriate model for each task i want to do." Hermes is the
router; this is the wire it routes over. Two desk kinds so far:

  claude-session  a LIVE labeled Claude Code session, addressed by its socket
  codex-exec      a headless `codex exec` run at a named model and directory

THE REFUSAL THAT MAKES IT SAFE TO RUN ON JOE'S MAC. Every ordinary Claude Code
session binds /tmp/cc-socks/<pid>.sock, and Joe's real sessions sit in that
same directory. A pid names a process, not a desk, and a dispatcher that can
address a pid can walk into any window he has open. So a pid socket is refused
as a target — at registration AND again at resolve time, because the file can
be hand-edited between the two. A desk is a session deliberately started with
`claude --messaging-socket-path /tmp/cc-socks/<name>.sock`, which is a
statement of intent that a pid is not.

Run:  python3 spikes/hermes-dispatch/test_dispatch_unit.py
Exit 0 = every assertion held.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
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
        FAILURES.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n          {e}")
    except Exception as e:  # noqa: BLE001
        FAILURES.append(f"{label}: unexpected {e!r}")
        print(f"  FAIL  {label}\n          unexpected {e!r}")
    else:
        print(f"  ok    {label}")


class Listener:
    """A stand-in desk: binds a labeled socket and records what arrives."""

    def __init__(self, path: str):
        self.path = path
        self.lines: list[str] = []
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(4)
        self._srv.settimeout(2.0)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        # A loop, not one accept: resolve() probes liveness by connecting, and
        # that probe would otherwise eat the only accept the real turn needs.
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn) -> None:
        with conn:
            conn.settimeout(2.0)
            buf = b""
            while True:
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self.lines.append(line.decode())
            if buf.strip():
                self.lines.append(buf.decode())

    def close(self) -> None:
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


def main() -> int:
    tmp = tempfile.TemporaryDirectory(prefix="hermes-dispatch-test-")
    root = Path(tmp.name)
    reg_path = root / "desks.json"
    results = root / "results.jsonl"
    sock_dir = root / "socks"
    sock_dir.mkdir()

    reg = desks.Registry(reg_path)

    # ---- addressing -------------------------------------------------------

    def register_and_resolve():
        live = sock_dir / "claude-desk.sock"
        lis = Listener(str(live))
        try:
            reg.register("claude-desk", "claude-session", socket=str(live))
            got = reg.resolve("claude-desk")
            assert got["kind"] == "claude-session", got
            assert got["socket"] == str(live), got
        finally:
            lis.close()

    check("a labeled desk registers and resolves", register_and_resolve)

    def refuse_pid_at_register():
        try:
            reg.register("sneaky", "claude-session", socket="/tmp/cc-socks/79534.sock")
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("a pid socket was accepted at registration")

    check("a pid socket is refused as a target at registration", refuse_pid_at_register)

    def refuse_pid_at_resolve():
        # hand-edit the file the way a careless script would
        data = json.loads(reg_path.read_text())
        data["desks"]["handmade"] = {
            "kind": "claude-session",
            "socket": "/tmp/cc-socks/12345.sock",
        }
        reg_path.write_text(json.dumps(data))
        try:
            reg.resolve("handmade")
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("a hand-written pid socket resolved")

    check("a pid socket written by hand is refused again at resolve", refuse_pid_at_resolve)

    def refuse_unknown():
        try:
            reg.resolve("no-such-desk")
        except desks.DeskError as e:
            assert e.code == "unknown_desk", e.code
        else:
            raise AssertionError("an unregistered name resolved")

    check("an unregistered name is refused", refuse_unknown)

    def refuse_dead_socket():
        dead = sock_dir / "gone-desk.sock"
        reg.register("gone-desk", "claude-session", socket=str(dead))
        try:
            reg.resolve("gone-desk")
        except desks.DeskError as e:
            assert e.code == "desk_not_live", e.code
        else:
            raise AssertionError("a socket with no listener resolved")

    check("a desk whose session has exited is refused", refuse_dead_socket)

    def refuse_bad_name():
        try:
            reg.register("../etc/passwd", "claude-session", socket=str(sock_dir / "x.sock"))
        except desks.DeskError as e:
            assert e.code == "bad_name", e.code
        else:
            raise AssertionError("a path-shaped name was accepted")

    check("a path-shaped desk name is refused", refuse_bad_name)

    def refuse_unknown_kind():
        try:
            reg.register("weird", "telepathy", socket=str(sock_dir / "x.sock"))
        except desks.DeskError as e:
            assert e.code == "bad_kind", e.code
        else:
            raise AssertionError("an unknown desk kind was accepted")

    check("an unknown desk kind is refused", refuse_unknown_kind)

    # ---- dispatch to a live Claude session --------------------------------

    def dispatch_to_claude():
        live = sock_dir / "claude-desk.sock"
        lis = Listener(str(live))
        try:
            out = dispatch.dispatch(
                "claude-desk", "count the open loops", registry=reg, results_path=results
            )
            assert out["status"] == "delivered", out
            deadline = __import__("time").monotonic() + 3
            while not lis.lines and __import__("time").monotonic() < deadline:
                __import__("time").sleep(0.05)
            assert lis.lines, "nothing reached the desk socket"
            frame = json.loads(lis.lines[0])
            assert frame["type"] == "user", frame
            assert frame["message"]["content"] == "count the open loops", frame
            assert frame["origin"]["kind"] == "peer", frame
            assert frame["origin"]["from"].startswith("hermes:"), frame
        finally:
            lis.close()

    check("a task reaches a live Claude desk as one peer turn", dispatch_to_claude)

    # ---- dispatch to codex, with a stand-in binary ------------------------

    fake_bin = root / "bin"
    fake_bin.mkdir()
    argv_log = root / "codex-argv.json"
    fake = fake_bin / "codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys,os\n"
        f"open({str(argv_log)!r},'w').write(json.dumps(sys.argv[1:]))\n"
        "out=None\n"
        "for i,a in enumerate(sys.argv):\n"
        "    if a in ('-o','--output-last-message'): out=sys.argv[i+1]\n"
        "if out: open(out,'w').write('the cheap model answered')\n"
        "print('done')\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    def dispatch_to_codex():
        reg.register("codex-desk", "codex-exec", model="gpt-5.1-codex-mini", cwd=str(root))
        env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
        out = dispatch.dispatch(
            "codex-desk", "rename the variable", registry=reg,
            results_path=results, env=env,
        )
        assert out["status"] == "completed", out
        assert out["result"] == "the cheap model answered", out
        argv = json.loads(argv_log.read_text())
        assert argv[0] == "exec", argv
        assert "-m" in argv and argv[argv.index("-m") + 1] == "gpt-5.1-codex-mini", argv
        assert "-C" in argv and argv[argv.index("-C") + 1] == str(root), argv
        assert argv[-1] == "rename the variable", argv

    check("a task reaches codex headless at the desk's model and directory", dispatch_to_codex)

    def codex_out_of_credit_is_its_own_status():
        """Codex prints the limit on stdout and exits 0, so the exit code lies."""
        broke = fake_bin / "codex-broke"
        broke.write_text(
            "#!/usr/bin/env python3\n"
            "print(\"ERROR: You've hit your usage limit. Visit "
            "https://chatgpt.com/codex/settings/usage to purchase more credits "
            "or try again at 11:36 PM.\")\n"
            "raise SystemExit(0)\n"
        )
        broke.chmod(broke.stat().st_mode | stat.S_IXUSR)
        shim = fake_bin / "codex"
        saved = shim.read_text()
        shim.write_text(broke.read_text())
        try:
            env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
            out = dispatch.dispatch(
                "codex-desk", "anything", registry=reg, results_path=results, env=env,
            )
            assert out["status"] == "quota_exhausted", out
            assert "usage limit" in out["detail"], out
            assert out["retry_after"] == "11:36 PM", out
        finally:
            shim.write_text(saved)
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    check("a seat out of credit reports quota_exhausted, not success",
          codex_out_of_credit_is_its_own_status)

    # ---- the trail Hermes reads back --------------------------------------

    def results_are_ndjson():
        lines = [l for l in results.read_text().splitlines() if l.strip()]
        assert len(lines) == 3, f"expected one line per dispatch, got {len(lines)}"
        rows = [json.loads(l) for l in lines]
        assert rows[0]["desk"] == "claude-desk", rows[0]
        assert rows[0]["kind"] == "claude-session", rows[0]
        assert rows[1]["desk"] == "codex-desk", rows[1]
        assert rows[2]["status"] == "quota_exhausted", rows[2]
        for r in rows:
            assert r["task"], r
            assert r["status"], r
            assert r["dispatched_at"], r

    check("every dispatch leaves one NDJSON line for Hermes to read", results_are_ndjson)

    def refuses_pid_socket_through_dispatch():
        data = json.loads(reg_path.read_text())
        data["desks"]["backdoor"] = {"kind": "claude-session", "socket": "/tmp/cc-socks/1.sock"}
        reg_path.write_text(json.dumps(data))
        try:
            dispatch.dispatch("backdoor", "hi", registry=reg, results_path=results)
        except desks.DeskError as e:
            assert e.code == "unlabeled_target", e.code
        else:
            raise AssertionError("dispatch delivered to a pid socket")

    check("dispatch itself will not deliver to a pid socket", refuses_pid_socket_through_dispatch)

    tmp.cleanup()
    print()
    if FAILURES:
        print(f"hermes-dispatch unit: {len(FAILURES)} FAILED")
        return 1
    print("hermes-dispatch unit: DONE — every assertion held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

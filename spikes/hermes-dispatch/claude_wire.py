#!/usr/bin/env python3
"""The wire into a live Claude desk: one NDJSON turn over its unix socket.

WHERE THIS CAME FROM. The Idea 78 partner-line spike proved a plain process
can drop a turn onto a live Claude Code session, and this is the one function
this package uses out of that work, carried here so the bridge does not depend
on an unmerged branch. The spike's own inject.py stays the fuller article —
reply listeners, peer-credential handling, status frames — and if it lands,
this file should be replaced by an import of it rather than kept in parallel.

WHY THE CONNECTION IS HELD OPEN. The spike's first receipt half-closed and
closed immediately, and the recipient logged `peer pid unavailable`: a live
file descriptor is the only thing a peer-credential lookup has to ask about.
Closing straight after the write loses the sender's identity, so the caller
owns the returned socket and closes it when the turn is done.
"""

from __future__ import annotations

import json
import socket

CONNECT_TIMEOUT_S = 5.0


def inject_keepalive(
    sock_path: str, payload: dict, timeout: float = CONNECT_TIMEOUT_S
) -> socket.socket:
    """Write one NDJSON line and KEEP the connection open.

    The returned socket must be closed by the caller.
    """
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall(line.encode())
    return s

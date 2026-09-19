"""Bound DNS and every public HTTP socket operation by one absolute deadline."""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time


_DNS_SCRIPT = """import json,socket,sys
answers=socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)
print(json.dumps(answers))
"""


def remaining_seconds(deadline: float) -> float:
    """Refuse any operation once the whole request's deadline has expired."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("public fetch exceeded time limit")
    return remaining


def bounded_dns(host: str, port: int, *, deadline: float):
    """Resolve in a killable short-lived process, without orphaned DNS threads.

    The child performs DNS only, never HTTP. Its vetted result is the sole input
    to address-pinned connections. Isolated Python prevents loading project or
    user startup code into the helper.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _DNS_SCRIPT, host, str(port)],
            capture_output=True, text=True, check=False,
            timeout=remaining_seconds(deadline),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("public fetch DNS exceeded time limit") from exc
    remaining_seconds(deadline)
    if result.returncode != 0:
        raise OSError("public DNS resolution failed")
    return json.loads(result.stdout)


class _DeadlineReader(io.RawIOBase):
    """Check the total deadline on every socket receive, including header lines."""

    def __init__(self, owner):
        """Hold a file reference until the HTTP body reader is closed."""
        self.owner = owner
        self.sock = owner.sock
        self.deadline, self.read_timeout = owner.deadline, owner.read_timeout

    def readable(self):
        """Advertise the read-only raw stream interface."""
        return True

    def readinto(self, buffer):
        """Prevent slow header, chunk and body dribbles from renewing the timeout."""
        self.sock.settimeout(min(self.read_timeout, remaining_seconds(self.deadline)))
        count = self.sock.recv_into(buffer)
        remaining_seconds(self.deadline)
        return count

    def close(self):
        """Release the file reference exactly once, as socket.makefile does."""
        if not self.closed:
            try:
                super().close()
            finally:
                self.owner.release_reader()


class DeadlineSocket:
    """Expose socket I/O to http.client with absolute, non-renewable time bounds."""

    def __init__(self, sock, *, deadline: float, read_timeout: float):
        """Wrap an already connected (and, where required, TLS-authenticated) socket."""
        self.sock, self.deadline, self.read_timeout = sock, deadline, read_timeout
        self._reader_count = 0
        self._close_requested = False

    def makefile(self, mode):
        """Supply a buffered reader whose individual receives enforce the deadline."""
        if mode != "rb":
            raise ValueError("public response socket permits binary reads only")
        if self._close_requested:
            raise OSError("public connection is closed")
        self._reader_count += 1
        return io.BufferedReader(_DeadlineReader(self))

    def sendall(self, data):
        """Bound request transmission by the same total request deadline."""
        self.sock.settimeout(min(self.read_timeout, remaining_seconds(self.deadline)))
        self.sock.sendall(data)
        remaining_seconds(self.deadline)

    def close(self):
        """Release connection ownership, retaining any outstanding HTTP body file."""
        self._close_requested = True
        if self._reader_count == 0:
            self.sock.close()

    def release_reader(self):
        """Retire one body-reader reference and finish a previously requested close."""
        self._reader_count -= 1
        if self._close_requested and self._reader_count == 0:
            self.sock.close()

    def __getattr__(self, name):
        """Delegate socket metadata and compatibility operations without new lookup."""
        return getattr(self.sock, name)

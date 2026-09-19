"""Simulate DNS, header and body dribbles without opening network sockets."""
from __future__ import annotations

import socket
import subprocess

import pytest

import historical_context_search_research as research
import public_source_fetch as fetch
import public_source_deadline as deadline_io


class DribbleSocket:
    """Return one byte at a time while advancing only the test clock."""
    def __init__(self, clock, *, delay_headers):
        self.clock = clock
        self.header = bytearray(b'HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n')
        self.body = bytearray(b'x' * 40)
        self.delay_headers = delay_headers
        self.closed = False
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, data):
        pass

    def recv_into(self, buffer):
        if self.header and not self.delay_headers:
            count = len(self.header)
            buffer[:count] = self.header
            self.header.clear()
            return count
        source = self.header if self.header else self.body
        self.clock[0] += .1
        if not source:
            return 0
        buffer[0] = source.pop(0)
        return 1

    def close(self):
        self.closed = True


@pytest.mark.parametrize('delay_headers', [False, True])
def test_total_deadline_interrupts_dribbled_headers_and_body(monkeypatch, delay_headers):
    clock = [0.0]
    monkeypatch.setattr(deadline_io.time, 'monotonic', lambda: clock[0])
    sock = DribbleSocket(clock, delay_headers=delay_headers)
    monkeypatch.setattr(research.socket, 'create_connection', lambda *a, **k: sock)
    with pytest.raises(OSError):
        fetch.fetch_public('http://93.184.216.34/', timeout=1, test_only_allow_pinned_transport=True)
    assert clock[0] <= 1.100001
    assert sock.closed
    assert all(0 < value <= 1 for value in sock.timeouts)


def test_blocked_dns_is_killed_at_total_deadline(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(deadline_io.time, 'monotonic', lambda: clock[0])
    calls = []
    def run(*args, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(deadline_io.subprocess, 'run', run)
    with pytest.raises(TimeoutError, match='DNS exceeded time limit'):
        deadline_io.bounded_dns('public.example', 443, deadline=1.5)
    assert calls[0]['timeout'] == .5


def test_connect_retries_share_one_absolute_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(deadline_io.time, 'monotonic', lambda: clock[0])
    attempts = []
    def connect(address, *, timeout):
        attempts.append(timeout)
        clock[0] += timeout
        raise TimeoutError('offline connect timeout')
    def resolver(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 80)) for ip in ['93.184.216.34', '93.184.216.35', '93.184.216.36']]
    monkeypatch.setattr(research.socket, 'create_connection', connect)
    with pytest.raises(TimeoutError, match='time limit'):
        research._pinned_public_get('http://public.example/', headers={}, timeout=(.6, 1), resolver=resolver, deadline=1)
    assert attempts == [.6, .4]

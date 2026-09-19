"""Independent offline attacks against research bounds and review authority."""
from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

import public_source_fetch as fetch
from tools.generated_image_review_app.app import create_app


def _lan_request(app, *, authorization=None):
    headers = [(b"host", b"localhost")]
    if authorization:
        headers.append((b"authorization", authorization))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "scheme": "http", "method": "GET", "path": "/image/private.png",
        "raw_path": b"/image/private.png", "query_string": b"", "root_path": "",
        "headers": headers, "client": ("192.168.1.50", 51000),
        "server": ("192.168.1.20", 8765),
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def test_external_listener_cannot_use_forged_loopback_host_for_no_auth(tmp_path):
    image = tmp_path / "private.png"
    image.write_bytes(b"private review data")
    service = SimpleNamespace(image_path=lambda *_args: image)
    assert _lan_request(create_app(service)) in {401, 403}


def test_external_plaintext_listener_cannot_use_forged_loopback_host_to_skip_tls(tmp_path):
    image = tmp_path / "private.png"
    image.write_bytes(b"private review data")
    service = SimpleNamespace(image_path=lambda *_args: image)
    authorization = b"Basic " + base64.b64encode(b"owner:secret")
    app = create_app(service, username="owner", password="secret")
    assert _lan_request(app, authorization=authorization) == 403


@pytest.mark.parametrize("path", ["/grounding-api-redirect/../private", "/grounding-api-redirect/a/../../private", "/grounding-api-redirect/%2e%2e/private"])
def test_grounding_authority_rejects_path_traversal_outside_expected_endpoint(path):
    assert not fetch.is_google_grounding_url("https://vertexaisearch.cloud.google.com" + path)


def test_empty_public_response_arriving_after_total_deadline_is_rejected(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(fetch.time, "monotonic", lambda: clock[0])
    response = SimpleNamespace(status_code=200, headers={}, iter_content=lambda _: iter(()), close=lambda: None)

    def request(*args, **kwargs):
        clock[0] = 100.0
        return response

    with pytest.raises(TimeoutError, match="time limit"):
        fetch.fetch_public("https://public.example/", request=request, timeout=1.0)


def test_deadline_reader_keeps_connection_close_response_body_available():
    import http.client
    import time
    from public_source_deadline import DeadlineSocket

    class Socket:
        def __init__(self):
            self.closed = False
            self.chunks = [b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\n", b"body"]

        def settimeout(self, _timeout):
            pass

        def sendall(self, _data):
            pass

        def recv_into(self, buffer):
            if self.closed:
                raise OSError("socket closed before response body was consumed")
            chunk = self.chunks.pop(0) if self.chunks else b""
            buffer[:len(chunk)] = chunk
            return len(chunk)

        def close(self):
            self.closed = True

    raw = Socket()
    connection = http.client.HTTPConnection("public.example")
    connection.sock = DeadlineSocket(raw, deadline=time.monotonic() + 30, read_timeout=5)
    connection.request("GET", "/")
    response = connection.getresponse()
    try:
        assert response.read() == b"body"
    finally:
        response.close()
        connection.close()
    assert raw.closed

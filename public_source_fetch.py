"""Fetch bounded public research documents through the existing IP-pinned transport.

No cookies, credentials, environment proxies or automatic redirects are used.
Injected transports are reserved for explicit offline tests.
"""
from __future__ import annotations

import ipaddress
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import requests

from historical_context_search_research import (
    UnsafeURL, _address_is_public, _pinned_public_get, canonicalise_url,
)

MAX_REDIRECTS = 5
MAX_BODY_BYTES = 25 * 1024 * 1024
MAX_SECONDS = 90.0


def is_google_grounding_url(url: str) -> bool:
    """Recognise only Google's HTTPS grounding redirect endpoint."""
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.netloc == "vertexaisearch.cloud.google.com"
            and re.fullmatch(r"/grounding-api-redirect/[A-Za-z0-9_-]+", parsed.path) is not None
            and not parsed.fragment
        )
    except ValueError:
        return False


def public_url_syntax(url: str) -> str:
    """Reject unsafe URL syntax and literal addresses before any connection."""
    canonical = canonicalise_url(url)
    validated = urlsplit(canonical)
    original = urlsplit(url)
    # Validation and identity canonicalisation must not rewrite a signed query
    # or remove a slash required by the source's actual HTTP resource.
    path = quote(original.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(original.query, safe="/%?:@!$&'()*+,;=-._~")
    transport_url = urlunsplit((validated.scheme, validated.netloc, path, query, ""))
    host = validated.hostname or ""
    if host in {"localhost", "localhost.localdomain", "metadata.google.internal"} or host.endswith(".localhost"):
        raise UnsafeURL("local hostname rejected")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return transport_url
    if not _address_is_public(address):
        raise UnsafeURL("non-public address rejected")
    return transport_url


@dataclass(frozen=True)
class PublicResponse:
    """Bounded, closed response compatible with existing research consumers."""
    url: str
    status_code: int
    headers: Any
    content: bytes
    history: tuple[Any, ...] = ()

    @property
    def ok(self) -> bool:
        """Report successful HTTP status."""
        return 200 <= self.status_code < 400

    @property
    def text(self) -> str:
        """Decode a public document without unbounded buffering."""
        return self.content.decode("utf-8", errors="replace")

    def iter_content(self, chunk_size: int):
        """Yield already bounded bytes."""
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def raise_for_status(self) -> None:
        """Reject unsuccessful public fetches."""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self) -> None:
        """Underlying connection was already closed before publication."""


def fetch_public(
    url: str, *, request: Callable[..., Any] | None = None,
    max_bytes: int = MAX_BODY_BYTES, timeout: float = MAX_SECONDS,
    test_only_allow_pinned_transport: bool = False,
) -> PublicResponse:
    """Validate each redirect and connect to exactly the vetted public IPs."""
    # A caller's ordinary requests session is never authority to bypass pinning.
    injected = request is not None and not (
        request is requests.get or isinstance(getattr(request, "__self__", None), requests.Session)
    )
    test_mode = os.environ.get("MRS_TEST_MODE") == "1"
    if injected and not test_mode:
        raise ValueError("unpinned research transport requires explicit test mode")
    if test_mode and not injected and not test_only_allow_pinned_transport:
        raise ValueError("test mode requires an explicitly injected offline public transport")
    if not 0 < max_bytes <= MAX_BODY_BYTES or not 0 < timeout <= MAX_SECONDS:
        raise ValueError("invalid public fetch bounds")
    deadline = time.monotonic() + timeout
    current = public_url_syntax(url)
    history: list[PublicResponse] = []
    for hop in range(MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("public fetch exceeded time limit")
        headers = {"User-Agent": "MrsMThatcher-source-audit/1.0", "Accept-Encoding": "identity"}
        if injected:
            response = request(current, allow_redirects=False, timeout=(min(10, remaining), min(30, remaining)), stream=True, headers=headers)
        else:
            response = _pinned_public_get(current, headers=headers, timeout=(min(10, remaining), min(30, remaining)), deadline=deadline, preserve_transport_url=True)
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("public fetch exceeded time limit")
            status = int(response.status_code)
            if 300 <= status < 400:
                location = response.headers.get("Location") or response.headers.get("location")
                if status not in {301, 302, 303, 307, 308} or not location:
                    raise UnsafeURL("invalid public redirect")
                if hop == MAX_REDIRECTS:
                    raise UnsafeURL("public redirect limit exceeded")
                history.append(PublicResponse(current, status, {}, b""))
                current = public_url_syntax(urljoin(current, location))
                continue
            content = bytearray()
            length = response.headers.get("Content-Length") or response.headers.get("content-length")
            if length is not None and (not str(length).isdigit() or int(length) > max_bytes):
                raise ValueError("invalid or oversized public Content-Length")
            for block in response.iter_content(64 * 1024):
                if time.monotonic() > deadline:
                    raise TimeoutError("public fetch exceeded time limit")
                if len(content) + len(block) > max_bytes:
                    raise ValueError("public body exceeds size limit")
                content.extend(block)
            if length is not None and len(content) != int(length):
                raise requests.exceptions.ChunkedEncodingError("public response length mismatch")
            if time.monotonic() >= deadline:
                raise TimeoutError("public fetch exceeded time limit")
            return PublicResponse(current, status, response.headers, bytes(content), tuple(history))
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
    raise UnsafeURL("public redirect limit exceeded")

"""Keep provider credentials on known HTTPS origins, except explicit loopback tests."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

EXPECTED_HOSTS = {
    "x": frozenset({"api.x.com", "api.twitter.com", "upload.twitter.com", "upload.x.com"}),
    "openai": frozenset({"api.openai.com"}),
    "xai": frozenset({"api.x.ai"}),
}


def validate_provider_endpoint(url: str, *, provider: str, test_mode: bool) -> None:
    """Refuse plaintext and arbitrary credential recipients before transport setup."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("provider endpoint cannot contain credentials, query or fragment")
    try:
        port = parsed.port
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Invalid ports must still fail even for named loopback hosts.
        port = parsed.port
        loopback = host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost")
    if test_mode and loopback and parsed.scheme in {"http", "https"}:
        return
    if parsed.scheme != "https" or host not in EXPECTED_HOSTS[provider] or port not in {None, 443}:
        raise ValueError(f"{provider} endpoint must use an expected HTTPS provider origin")

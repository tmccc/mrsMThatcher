"""Offline regressions for public-source redirects and provider attempt ceilings."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest
import requests

import public_source_fetch as fetch
import historical_context_search_research as research
from provider_endpoint_policy import validate_provider_endpoint
import generate_all_openai_quote_images as images


class Response:
    """Minimal closed response for deterministic public fetch tests."""
    def __init__(self, status=200, headers=None, body=b"ok"):
        """Build one response without network access."""
        self.status_code, self.headers, self.body = status, headers or {}, body
        self.closed = False

    def iter_content(self, _size):
        """Yield one bounded test body."""
        yield self.body

    def close(self):
        """Record connection retirement."""
        self.closed = True


@pytest.mark.parametrize("target", [
    "http://127.0.0.1/", "http://10.2.3.4/", "http://172.16.4.5/", "http://192.168.1.2/",
    "http://169.254.169.254/", "http://[::1]/", "http://[fe80::1]/", "http://[ff02::1]/",
    "http://0.0.0.0/", "http://240.0.0.1/", "file:///etc/passwd", "https://user:pass@example.org/",
])
def test_every_redirect_is_rejected_before_destination_connection(target):
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return Response(302, {"Location": target})
    with pytest.raises((ValueError, research.UnsafeURL)):
        fetch.fetch_public("https://public.example/start", request=request)
    assert calls == ["https://public.example/start"]


def test_unique_redirects_are_bounded_and_closed():
    responses = []
    def request(url, **kwargs):
        response = Response(302, {"Location": f"/hop/{len(responses)}"})
        responses.append(response)
        return response
    with pytest.raises(research.UnsafeURL, match="limit"):
        fetch.fetch_public("https://public.example/", request=request)
    assert len(responses) == fetch.MAX_REDIRECTS + 1
    assert all(response.closed for response in responses)


@pytest.mark.parametrize("url", [
    "http://vertexaisearch.cloud.google.com/grounding-api-redirect/a",
    "https://evil.example/grounding-api-redirect/a",
    "https://vertexaisearch.cloud.google.com.evil.example/grounding-api-redirect/a",
    "https://user@vertexaisearch.cloud.google.com/grounding-api-redirect/a",
    "https://vertexaisearch.cloud.google.com/other/grounding-api-redirect/a",
])
def test_grounding_endpoint_requires_exact_origin_and_path(url):
    assert not fetch.is_google_grounding_url(url)


def test_grounding_endpoint_expected_url():
    assert fetch.is_google_grounding_url("https://vertexaisearch.cloud.google.com/grounding-api-redirect/a")


@pytest.mark.parametrize("private", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "fe80::1"])
def test_mixed_dns_answers_never_connect(monkeypatch, private):
    connections = []
    monkeypatch.setattr(research.socket, "create_connection", lambda *a, **k: connections.append(a))
    def resolver(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ("93.184.216.34", private)]
    with pytest.raises(research.UnsafeURL, match="non-public"):
        research._pinned_public_get("https://public.example/", headers={}, timeout=(1, 1), resolver=resolver)
    assert not connections


def test_pinned_transport_connects_to_validated_ip_without_second_dns(monkeypatch):
    calls = []
    class Sock:
        def settimeout(self, value): pass
        def close(self): pass
    class Connection:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): calls.append((a, k))
        def getresponse(self): return SimpleNamespace(status=200, headers={})
    monkeypatch.setattr(research.socket, "create_connection", lambda address, **kwargs: calls.append(address) or Sock())
    monkeypatch.setattr(research.http.client, "HTTPConnection", Connection)
    resolutions = []
    def resolver(*a, **k):
        resolutions.append(a)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
    research._pinned_public_get("http://public.example/", headers={}, timeout=(1, 1), resolver=resolver)
    assert len(resolutions) == 1
    assert calls[0] == ("93.184.216.34", 80)
    assert calls[1][1]["headers"]["Host"] == "public.example"


def test_response_size_and_declared_length_fail_closed():
    with pytest.raises(ValueError, match="size"):
        fetch.fetch_public("https://public.example/", request=lambda *a, **k: Response(body=b"123"), max_bytes=2)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        fetch.fetch_public("https://public.example/", request=lambda *a, **k: Response(headers={"Content-Length": "3"}, body=b"12"))


@pytest.mark.parametrize("provider,url", [
    ("x", "http://api.x.com"), ("x", "https://evil.example"),
    ("openai", "http://api.openai.com/v1"), ("openai", "https://evil.example/v1"),
    ("openai", "https://api.openai.com:8443/v1"), ("x", "http://127.0.0.1"),
])
def test_production_endpoint_recipient_rejected(provider, url):
    with pytest.raises(ValueError):
        validate_provider_endpoint(url, provider=provider, test_mode=False)


def test_loopback_http_override_requires_explicit_test_mode():
    validate_provider_endpoint("http://127.0.0.1:8123/v1", provider="openai", test_mode=True)
    validate_provider_endpoint("https://api.openai.com/v1", provider="openai", test_mode=False)


@pytest.mark.parametrize('origin', ['https://api.x.ai/v1', 'https://api.openai.com/v1'])
def test_generic_versioned_provider_normalizer_preserves_known_https_origins(origin, monkeypatch):
    """The generic root adapter supports both APIs without changing credential binding."""
    from tests.helpers.bot_runtime import bot
    monkeypatch.setattr(bot, 'TEST_MODE', False)
    assert bot.normalise_base_url(origin) == origin


@pytest.mark.parametrize('origin', [
    'https://api.x.ai/v1', 'http://api.openai.com/v1', 'https://evil.example/v1',
    'https://api.openai.com.evil.example/v1', 'https://api.openai.com:8443/v1',
])
def test_explicit_root_openai_binding_rejects_other_credential_recipients(origin, monkeypatch):
    """OpenAI credentials remain bound to OpenAI even when generic xAI parsing exists."""
    from tests.helpers.bot_runtime import bot
    monkeypatch.setattr(bot, 'TEST_MODE', False)
    with pytest.raises(ValueError, match='openai endpoint'):
        bot.normalise_base_url(origin, provider='openai')


@pytest.mark.parametrize('origin', ['http://api.x.ai/v1', 'https://api.x.ai:8443/v1', 'https://evil.example/v1'])
def test_generic_provider_normalizer_does_not_enable_plaintext_or_unknown_hosts(origin, monkeypatch):
    """Restoring generic compatibility does not admit arbitrary API origins."""
    from tests.helpers.bot_runtime import bot
    monkeypatch.setattr(bot, 'TEST_MODE', False)
    with pytest.raises(ValueError, match='expected HTTPS'):
        bot.normalise_base_url(origin)


def test_real_root_openai_configuration_rejects_xai_before_provider_setup(tmp_path):
    """A fresh-process OpenAI environment override cannot redirect its credentials to xAI."""
    environment = dict(os.environ)
    environment.update(
        MRS_TEST_MODE='1', MRS_BASE_DIR=str(tmp_path), MRS_LOG_FILE=str(tmp_path / 'test.log'),
        OPENAI_API_BASE_URL='https://api.x.ai/v1', X_API_BASE_URL='http://127.0.0.1:9',
        X_UPLOAD_BASE_URL='http://127.0.0.1:9',
    )
    result = subprocess.run(
        [sys.executable, '-c', 'import mrsMThatcher2'], env=environment,
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert 'openai endpoint must use an expected HTTPS provider origin' in result.stderr


@pytest.mark.parametrize("outcome", [requests.ReadTimeout(), requests.ConnectionError(), Response(500), Response(502)])
def test_image_generation_never_retries_ambiguous_post(outcome, monkeypatch, tmp_path):
    calls = []
    budget = images.AttemptBudget(tmp_path / "exposure.json", per_request=1, ceiling=4)
    def post(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["allow_redirects"] is False
        if isinstance(outcome, Exception): raise outcome
        outcome.text = "server failed"
        return outcome
    with pytest.raises(RuntimeError):
        images.request_image(SimpleNamespace(post=post), api_key="test", model="test", prompt="test", quality="low", size="1024x1024", max_retries=4, reserve_attempt=budget.reserve)
    assert len(calls) == 1 and budget.attempts == 1
    recovered = images.AttemptBudget(tmp_path / "exposure.json", per_request=1, ceiling=4)
    assert recovered.attempts == 1


def test_each_rejected_attempt_consumes_cost_reservation(tmp_path, monkeypatch):
    budget = images.AttemptBudget(tmp_path / "exposure.json", per_request=1, ceiling=2)
    calls = []
    monkeypatch.setattr(images.time, "sleep", lambda _: None)
    def post(*args, **kwargs):
        calls.append(1)
        response = Response(429, {"Retry-After": "0"})
        response.text = "rate limited"
        return response
    with pytest.raises(RuntimeError, match="ceiling"):
        images.request_image(SimpleNamespace(post=post), api_key="test", model="test", prompt="test", quality="low", size="1024x1024", max_retries=5, reserve_attempt=budget.reserve)
    assert len(calls) == 2 and budget.attempts == 2


def test_discovery_endlessly_unique_tokens_stop_at_page_ceiling():
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=200, json=lambda: {"engines": [], "nextPageToken": str(len(calls))})
    client = research.GoogleDiscoveryEngineResourceClient(session=SimpleNamespace(get=get), access_token_provider=lambda: "test")
    with pytest.raises(research.SearchBackendNotConfigured, match="page limit"):
        client.list_engines()
    assert len(calls) == 100


@pytest.mark.parametrize("owner", ["source", "grounding", "resolution", "hunt_page", "hunt_image"])
def test_real_research_adapters_never_connect_to_redirected_metadata(owner, tmp_path):
    import historical_context_source_gemini as gemini
    import historical_context_source_resolution as resolution
    import semantic_alignment.thatcher_image_hunt as hunt
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return Response(302, {"Location": "http://169.254.169.254/latest/meta-data/"})
    source_url = "https://www.margaretthatcher.org/document/123456"
    if owner == "source":
        result, reason = gemini.verify_source({"url": source_url, "source_quality_class": "primary"}, {}, request=request)
        assert result is None and reason.startswith("fetch_failed:")
    elif owner == "grounding":
        result, reason = gemini.verify_grounding_source({"url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/token"}, {}, request=request)
        assert result is None and reason.startswith("fetch_failed:")
    elif owner == "resolution":
        assert resolution._fetch_one(source_url, ["a"], {"a": {}}, request)["status"] == "request_failed"
    else:
        with pytest.raises(research.UnsafeURL):
            if owner == "hunt_page":
                hunt.fetch_source_page({"source_page_url": source_url}, SimpleNamespace(get=request))
            else:
                hunt.download_candidate({"direct_image_url": source_url, "identity_confidence": "high", "identity_evidence": "verified"}, tmp_path, SimpleNamespace(get=request))
    assert len(calls) == 1


def test_discovery_resource_item_ceiling_precedes_memory_growth():
    response = SimpleNamespace(status_code=200, json=lambda: {"engines": [{}] * 101})
    client = research.GoogleDiscoveryEngineResourceClient(session=SimpleNamespace(get=lambda *a, **k: response), access_token_provider=lambda: "test")
    with pytest.raises(research.SearchBackendNotConfigured, match="item limit"):
        client.list_engines()


def test_fetch_timeout_and_injected_transport_production_guard(monkeypatch):
    ticks = iter([0, 0, 91])
    monkeypatch.setattr(fetch.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError):
        fetch.fetch_public("https://public.example/", request=lambda *a, **k: Response())
    monkeypatch.setenv("MRS_TEST_MODE", "0")
    with pytest.raises(ValueError, match="test mode"):
        fetch.fetch_public("https://public.example/", request=lambda *a, **k: Response())


def test_public_redirect_preserves_required_slash_and_signed_query():
    calls = []
    destination = 'https://public.example/archive/?b=2&a=%2F&sig=opaque'
    def request(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return Response(302, {'Location': destination})
        assert url == destination
        return Response()
    response = fetch.fetch_public('https://public.example/start', request=request)
    assert response.url == destination
    assert calls == ['https://public.example/start', destination]


def test_pinned_get_preserves_validated_transport_path_and_query(monkeypatch):
    observed = []
    class Sock:
        def settimeout(self, value): pass
        def close(self): pass
    class Connection:
        def __init__(self, *a, **k): pass
        def request(self, method, target, **kwargs): observed.append(target)
        def getresponse(self): return SimpleNamespace(status=200, headers={})
    monkeypatch.setattr(research.socket, 'create_connection', lambda *a, **k: Sock())
    monkeypatch.setattr(research.http.client, 'HTTPConnection', Connection)
    research._pinned_public_get('http://93.184.216.34/archive/?b=2&a=%2F', headers={}, timeout=(1, 1), preserve_transport_url=True)
    assert observed == ['/archive/?b=2&a=%2F']

from __future__ import annotations

import ast
import copy
import contextlib
import gzip
import inspect
import json
import subprocess
import sys
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import historical_context_search_research as research
import historical_context_search_query_strategy as query_strategy


QUOTATION = (
    "There is no liberty unless economic freedom is preserved, and there can be "
    "no economic freedom without liberty"
)
VARIANT = (
    "There can be no liberty unless economic freedom is preserved, and there can be "
    "no economic freedom without liberty"
)


def _target() -> dict[str, object]:
    return {
        "quote_id": "quote-1",
        "quotation_text": QUOTATION,
        "target_origins": ["historical_context_semantic_gate"],
        "gate_disposition": "correction_required",
        "recorded_variants": [VARIANT],
        "distinctive_fragments": research.distinctive_fragments(QUOTATION),
    }


def _manifest() -> dict[str, object]:
    target = {**_target(), "queries": research.queries_for_target(_target())}
    body = {
        "schema_version": 1,
        "input_hashes": {"fixture": "a" * 64},
        "targets": [target],
        "global_request_order": [
            {
                "global_request_order": index,
                "quote_id": "quote-1",
                "quote_request_order": index,
            }
            for index in range(1, research.PLANNED_QUERY_STAGES_PER_QUOTE + 1)
        ],
    }
    return {
        **body,
        "manifest_hash": research.sha256_bytes(research.canonical_json_bytes(body)),
    }


def _manifest_for_targets(targets: list[dict[str, object]]) -> dict[str, object]:
    prepared = [
        {**target, "queries": research.queries_for_target(target)} for target in targets
    ]
    order: list[dict[str, object]] = []
    maximum_depth = research.PLANNED_QUERY_STAGES_PER_QUOTE
    for query_index in range(maximum_depth):
        for target in prepared:
            if query_index < len(target["queries"]):
                order.append(
                    {
                        "global_request_order": len(order) + 1,
                        "quote_id": target["quote_id"],
                        "quote_request_order": query_index + 1,
                    }
                )
    body = {
        "schema_version": 1,
        "input_hashes": {"fixture": "a" * 64},
        "targets": prepared,
        "global_request_order": order,
    }
    return {
        **body,
        "manifest_hash": research.sha256_bytes(research.canonical_json_bytes(body)),
    }


class FakeBackend:
    name = "fixture_search"
    version = "fixture-v1"
    engine_identity_hash = "b" * 64

    def __init__(self, results: list[dict[str, str]] | None = None) -> None:
        self.results = results or []
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, number: int) -> list[dict[str, str]]:
        self.calls.append((query, number))
        return copy.deepcopy(self.results[:number])


class FakeSearchResponse:
    def __init__(
        self,
        status_code: int,
        payload: object | None = None,
        *,
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.payload = payload
        self.json_error = json_error

    def json(self) -> object:
        if self.json_error is not None:
            raise self.json_error
        return copy.deepcopy(self.payload)


class FakeSearchSession:
    def __init__(
        self,
        response: FakeSearchResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response or FakeSearchResponse(200, {"web": {"results": []}})
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.trust_env = True

    def get(self, url: str, **kwargs) -> FakeSearchResponse:
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        if self.error is not None:
            raise self.error
        return self.response


class FakePostSession:
    def __init__(
        self,
        response: FakeSearchResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response or FakeSearchResponse(200, {"results": []})
        self.error = error
        self.calls: list[dict[str, object]] = []
        self.trust_env = True

    def post(self, url: str, **kwargs) -> FakeSearchResponse:
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        if self.error is not None:
            raise self.error
        return self.response


class FakeAccessTokenProvider:
    def __init__(self, token: str = "fixture-adc-access-token") -> None:
        self.token = token
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.token


class SequencedSearchSession:
    def __init__(self, responses: list[FakeSearchResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.trust_env = True

    def get(self, url: str, **kwargs) -> FakeSearchResponse:
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        if not self.responses:
            raise AssertionError("fixture search response sequence exhausted")
        return self.responses.pop(0)


class StaticFetcher:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.response = response or {
            "status": "fetched",
            "http_status": 200,
            "content_type": "text/plain",
            "final_url": "https://example.org/source",
            "body": b"This unrelated page contains no supporting passage.",
            "body_sha256": research.sha256_bytes(
                b"This unrelated page contains no supporting passage."
            ),
            "redirect_chain": [],
            "cache_hit": False,
        }
        self.budget = research.PageFetchBudget()
        self.calls: list[str] = []

    def fetch(self, url: str) -> dict[str, object]:
        self.calls.append(url)
        return copy.deepcopy(self.response)


class FakeDocumentResponse:
    """Raw-stream HTTP response fixture for restricted page-fetch tests."""

    class _RawBody:
        def __init__(self, body: bytes) -> None:
            self.body = body
            self.reads = 0

        def stream(self, chunk_size: int, *, decode_content: bool):
            assert decode_content is False
            self.reads += 1
            for offset in range(0, len(self.body), chunk_size):
                yield self.body[offset:offset + chunk_size]

    def __init__(
        self,
        status_code: int,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {
            "content-type": "text/html",
            "content-length": str(len(body)),
            **(headers or {}),
        }
        self.raw = self._RawBody(body)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeDocumentSession:
    """Deterministic response sequence with request-header capture."""

    def __init__(self, responses: list[FakeDocumentResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.trust_env = True

    def get(self, url: str, **kwargs) -> FakeDocumentResponse:
        self.calls.append({"url": url, **copy.deepcopy(kwargs)})
        if not self.responses:
            raise AssertionError("fixture document response sequence exhausted")
        return self.responses.pop(0)


def _runner(
    tmp_path: Path,
    *,
    manifest: dict[str, object] | None = None,
    backend: FakeBackend | None = None,
    state: dict[str, object] | None = None,
    budget: research.SearchBudget | None = None,
    cache: research.ResearchCache | None = None,
    fetcher: object | None = None,
    codex_reviews: dict[str, dict[str, object]] | None = None,
) -> research.SearchResearchRunner:
    manifest = manifest or _manifest()
    budget = budget or research.SearchBudget(price_per_1000=Decimal("5"))
    state = state or research.initialise_run_state(
        manifest, status="ready", budget=budget
    )
    cache = cache or research.ResearchCache(tmp_path / "cache")
    return research.SearchResearchRunner(
        manifest=manifest,
        state=state,
        backend=backend or FakeBackend(),
        budget=budget,
        fetcher=fetcher or StaticFetcher(),
        cache=cache,
        state_path=tmp_path / "run_state.json",
        ledger_path=tmp_path / "results.jsonl",
        maximum_retries=0,
        codex_reviews=codex_reviews,
    )


@pytest.fixture
def local_document_server(allow_loopback_network):
    request_counts: dict[str, int] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            path = urlsplit(self.path).path
            request_counts[path] = request_counts.get(path, 0) + 1
            if path == "/robots.txt":
                body = b"User-agent: *\nDisallow: /blocked\nAllow: /\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/page")
                self.end_headers()
                return
            if path == "/unsafe-redirect":
                self.send_response(302)
                self.send_header("Location", "/blocked")
                self.end_headers()
                return
            if path == "/robots-redirect":
                self.send_response(302)
                self.send_header("Location", "/blocked")
                self.end_headers()
                return
            if path == "/oversize":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(research.MAXIMUM_RESPONSE_BYTES + 1))
                self.end_headers()
                return
            if path == "/page":
                body = (
                    b"<html><head><title>Fixture speech</title></head><body>"
                    b"Margaret Thatcher said: There is no liberty unless economic "
                    b"freedom is preserved.</body></html>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/blocked":
                body = b"this destination must never be fetched"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = b"not found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", request_counts
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_query_generation_is_deterministic_and_includes_exact_and_fragments() -> None:
    target = _target()
    first = research.queries_for_target(target)
    second = research.queries_for_target(copy.deepcopy(target))

    assert first == second
    assert len(first) == research.MAX_QUERIES_PER_QUOTE
    assert first[0] == {
        "request_order": 1,
        "query": f'"{QUOTATION}"',
        "reason": "exact_full_wording",
        "maximum_results": 10,
    }
    fragment = target["distinctive_fragments"][0]
    assert first[1]["query"] == f'"Margaret Thatcher" "{QUOTATION}"'
    assert first[1]["reason"] == "margaret_thatcher_with_exact_full_wording"
    assert first[2]["query"] == f'"{fragment}"'
    assert first[2]["reason"] == "distinctive_contiguous_fragment"
    assert first[3]["query"] == f'"Margaret Thatcher" "{fragment}"'
    assert first[3]["reason"] == "margaret_thatcher_with_distinctive_fragment"
    assert first[4]["query"] == f'"{VARIANT}"'
    assert first[4]["reason"] == "existing_documented_variant"
    assert first[5]["reason"] == "second_distinctive_contiguous_fragment"
    assert all(row["maximum_results"] == research.TOP_RESULTS for row in first)


def test_scheduled_stages_map_directly_to_first_five_query_orders(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    runner.state["ledger_records"].append(
        {
            "target_quote_id": "quote-1",
            "query_request_order": 1,
            "result_rank": 1,
            "canonical_url": "https://example.org/archive-lead",
            "fetched_final_url": "https://archive.org/details/thatcher-book",
            "title": "Book: Thatcher speeches",
            "decision": "rejected_or_discovery_candidate",
        }
    )
    assert [
        runner._query_order_for_stage("quote-1", stage)
        for stage in range(1, research.PLANNED_QUERY_STAGES_PER_QUOTE + 1)
    ] == [1, 2, 3, 4, 5]
    assert "stage_four_query_selection" not in runner.state["cases"]["quote-1"]


def test_empty_results_complete_after_five_stages_despite_six_query_templates(
    tmp_path: Path,
) -> None:
    backend = FakeBackend([])
    budget = research.SearchBudget(price_per_1000=Decimal("5"))
    runner = _runner(tmp_path, backend=backend, budget=budget)
    assert len(runner.manifest["targets"][0]["queries"]) == 6

    state = runner.run()
    case = state["cases"]["quote-1"]
    assert state["run_status"] == "complete"
    assert case["status"] == "complete_search"
    assert len(backend.calls) == research.PLANNED_QUERY_STAGES_PER_QUOTE == 5
    assert [item["scheduled_stage"] for item in case["queries_attempted"]] == [
        1, 2, 3, 4, 5,
    ]
    assert [item["request_order"] for item in case["queries_attempted"]] == [
        1, 2, 3, 4, 5,
    ]
    assert budget.unique_queries_started == budget.requests_started == 5


def test_manifest_hash_is_deterministic_and_detects_tampering(monkeypatch) -> None:
    derived = {
        "counts": {"deduplicated_target_count": 1},
        "gate": {"available": True},
        "input_hashes": {"fixture": "a" * 64},
        "targets": [_target()],
    }
    monkeypatch.setattr(research, "derive_target_set", lambda _root: copy.deepcopy(derived))

    first = research.build_query_manifest(Path("/unused"))
    second = research.build_query_manifest(Path("/unused"))
    assert first == second
    research.validate_query_manifest(first)

    tampered = copy.deepcopy(first)
    tampered["targets"][0]["queries"][0]["query"] = '"invented wording"'
    with pytest.raises(research.ResearchError, match="manifest hash differs"):
        research.validate_query_manifest(tampered)


def test_fresh_real_manifest_has_18_targets_and_at_most_150_scheduled_queries() -> None:
    manifest = research.build_query_manifest(research.ROOT)
    assert manifest["target_derivation"]["deduplicated_target_count"] == 18
    assert len(manifest["targets"]) == 18
    assert research.MAX_QUERIES_PER_QUOTE == 6
    assert research.PLANNED_QUERY_STAGES_PER_QUOTE == 5
    assert research.PLANNED_SEARCH_REQUESTS == research.ABSOLUTE_SEARCH_REQUESTS == 150
    assert len(manifest["global_request_order"]) <= research.PLANNED_SEARCH_REQUESTS
    assert manifest["limits"]["scheduled_search_requests"] == len(
        manifest["global_request_order"]
    )
    assert all(len(target["queries"]) <= 6 for target in manifest["targets"])
    scheduled_per_quote: dict[str, int] = {}
    for row in manifest["global_request_order"]:
        quote_id = row["quote_id"]
        scheduled_per_quote[quote_id] = scheduled_per_quote.get(quote_id, 0) + 1
    assert all(count <= 5 for count in scheduled_per_quote.values())
    assert [row["global_request_order"] for row in manifest["global_request_order"]] == list(
        range(1, len(manifest["global_request_order"]) + 1)
    )
    research.validate_query_manifest(manifest)


def test_current_authoritative_derivation_reports_five_unresolved() -> None:
    counts = research.derive_target_set(research.ROOT)["counts"]
    assert counts["unresolved_count"] == 5
    assert counts["unresolved_expected_count"] == 5
    assert counts["unresolved_count_discrepancy"] == 0


def test_semantically_tampered_but_rehashed_manifest_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest()
    manifest["targets"][0]["queries"][0]["query"] = '"invented wording"'
    body = dict(manifest)
    body.pop("manifest_hash")
    manifest["manifest_hash"] = research.sha256_bytes(research.canonical_json_bytes(body))
    manifest_path = tmp_path / "manifest.json"
    research.atomic_write_json(manifest_path, manifest)
    monkeypatch.setattr(
        research,
        "current_input_hashes_match",
        lambda _manifest, _root: (True, dict(_manifest["input_hashes"])),
    )

    with pytest.raises(research.ResearchError, match="quer|semantic|deterministic"):
        research._manifest_for_command({"manifest": manifest_path}, root=tmp_path)


def test_gcloud_adc_token_provider_uses_fixed_command_caches_and_hides_failures(
    monkeypatch,
) -> None:
    token = "fixture-sensitive-adc-token"
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("CLOUDSDK_CORE_LOG_HTTP", "true")
    monkeypatch.setenv("CLOUDSDK_LOG_HTTP", "true")
    monkeypatch.setenv("FIXTURE_PRESERVED_ENVIRONMENT", "present")

    def successful_runner(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(returncode=0, stdout=f"{token}\n", stderr="")

    monkeypatch.setattr(research.time, "monotonic", lambda: 100.0)
    provider = research.GcloudADCAccessTokenProvider(
        command_runner=successful_runner,
        cache_seconds=60,
    )
    assert provider() == token
    assert provider() == token
    assert len(calls) == 1
    call = calls[0]
    assert tuple(call["command"]) == (
        "gcloud", "auth", "application-default", "print-access-token", "--quiet",
    )
    assert call["check"] is False
    assert call["capture_output"] is True
    assert call["text"] is True
    assert call["timeout"] == 30
    assert call["stdin"] is subprocess.DEVNULL
    command_environment = call["env"]
    assert isinstance(command_environment, dict)
    assert command_environment is not research.os.environ
    assert "CLOUDSDK_CORE_LOG_HTTP" not in command_environment
    assert "CLOUDSDK_LOG_HTTP" not in command_environment
    assert command_environment["CLOUDSDK_CORE_DISABLE_PROMPTS"] == "1"
    assert command_environment["CLOUDSDK_CORE_VERBOSITY"] == "error"
    assert command_environment["FIXTURE_PRESERVED_ENVIRONMENT"] == "present"
    assert research.os.environ["CLOUDSDK_CORE_LOG_HTTP"] == "true"
    assert research.os.environ["CLOUDSDK_LOG_HTTP"] == "true"

    failed_secret = "fixture-secret-from-gcloud-stderr"
    failed = research.GcloudADCAccessTokenProvider(
        command_runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=token,
            stderr=failed_secret,
        )
    )
    with pytest.raises(research.SearchBackendNotConfigured) as failure:
        failed()
    assert token not in str(failure.value)
    assert failed_secret not in str(failure.value)

    invalid = research.GcloudADCAccessTokenProvider(
        command_runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="token containing whitespace\n",
            stderr="",
        )
    )
    with pytest.raises(research.SearchBackendNotConfigured, match="invalid token"):
        invalid()


def test_discovery_backend_posts_fixed_nongenerative_request_and_maps_top_ten() -> None:
    valid_results: list[dict[str, object]] = []
    for index in range(1, 13):
        data = {
            "title": f"<b>Result &amp; {index}</b>",
            "link": f"https://example.org/{index}",
            "snippets": [{"snippet": f"<em>Snippet {index}</em>"}],
            "displayLink": "example.org",
            "mime": "text/html",
        }
        document: dict[str, object] = {"id": f"document-{index}"}
        if index == 1:
            document["structData"] = data
        elif index == 2:
            document["structData"] = {
                "link": data["link"],
                "displayLink": data["displayLink"],
                "mime": data["mime"],
            }
            document["derivedStructData"] = {
                "htmlTitle": data["title"],
                "snippets": data["snippets"],
            }
        else:
            document["derivedStructData"] = data
        valid_results.append({"document": document})
    session = FakePostSession(FakeSearchResponse(200, {
        "results": [{"not_a_document": True}, *valid_results],
        "nextPageToken": "must-not-be-followed",
    }))
    tokens = FakeAccessTokenProvider()
    backend = research.GoogleDiscoveryEngineBackend(
        access_token_provider=tokens,
        session=session,
    )

    results = backend.search("Margaret Thatcher quotation", number=10)

    assert len(results) == 10
    assert results[0] == {
        "title": "Result & 1",
        "result_url": "https://example.org/1",
        "snippet": "Snippet 1",
        "display_link": "example.org",
        "mime": "text/html",
        "document_id": "document-1",
    }
    assert results[-1]["document_id"] == "document-10"
    assert results[1]["title"] == "Result & 2"
    assert results[1]["result_url"] == "https://example.org/2"
    assert tokens.calls == 1
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == research.DISCOVERY_ENDPOINT
    assert call["json"] == {
        "query": "Margaret Thatcher quotation",
        "pageSize": 10,
        "queryExpansionSpec": {"condition": "DISABLED"},
        "spellCorrectionSpec": {"mode": "SUGGESTION_ONLY"},
        "contentSearchSpec": {
            "snippetSpec": {"returnSnippet": True, "maxSnippetCount": 1},
        },
    }
    assert call["headers"]["Authorization"] == "Bearer fixture-adc-access-token"
    assert call["headers"]["X-Goog-User-Project"] == research.DISCOVERY_PROJECT_ID
    assert call["headers"]["Accept"] == "application/json"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["allow_redirects"] is False
    assert session.trust_env is False
    with pytest.raises(ValueError, match="1..10"):
        backend.search("too many", number=11)
    assert tokens.calls == 1
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("status_code", "exception", "message"),
    [
        (302, research.ConfirmedSearchError, "redirect refused"),
        (400, research.ConfirmedSearchError, "HTTP 400"),
        (401, research.SearchAuthenticationConfigurationError, "HTTP 401"),
        (403, research.SearchAuthenticationConfigurationError, "HTTP 403"),
        (404, research.SearchAuthenticationConfigurationError, "HTTP 404"),
        (422, research.RejectedSearchQuery, "rejected"),
        (429, research.RetryableSearchError, "HTTP 429"),
        (500, research.RetryableSearchError, "HTTP 500"),
        (503, research.RetryableSearchError, "HTTP 503"),
    ],
)
def test_discovery_backend_classifies_http_failures_without_hidden_retry(
    status_code: int,
    exception: type[Exception],
    message: str,
) -> None:
    session = FakePostSession(FakeSearchResponse(status_code, {}))
    backend = research.GoogleDiscoveryEngineBackend(
        access_token_provider=FakeAccessTokenProvider(),
        session=session,
    )

    with pytest.raises(exception, match=message):
        backend.search("query")
    assert len(session.calls) == 1


def test_discovery_backend_treats_only_query_field_invalid_argument_as_local_rejection() -> None:
    query_error = FakeSearchResponse(400, {
        "error": {
            "status": "INVALID_ARGUMENT",
            "message": "sensitive provider detail must not be persisted",
            "details": [{"fieldViolations": [{"field": "query"}]}],
        },
    })
    backend = research.GoogleDiscoveryEngineBackend(
        access_token_provider=FakeAccessTokenProvider(),
        session=FakePostSession(query_error),
    )
    with pytest.raises(research.RejectedSearchQuery, match="rejected") as rejected:
        backend.search("fixture invalid query")
    assert "sensitive provider detail" not in str(rejected.value)

    resource_error = FakeSearchResponse(400, {
        "error": {
            "status": "INVALID_ARGUMENT",
            "details": [{"fieldViolations": [{"field": "servingConfig"}]}],
        },
    })
    resource_backend = research.GoogleDiscoveryEngineBackend(
        access_token_provider=FakeAccessTokenProvider(),
        session=FakePostSession(resource_error),
    )
    with pytest.raises(research.ConfirmedSearchError, match="HTTP 400"):
        resource_backend.search("query")

    fixed_policy_error = FakeSearchResponse(400, {
        "error": {
            "status": "INVALID_ARGUMENT",
            "details": [{
                "fieldViolations": [{"field": "queryExpansionSpec.condition"}],
            }],
        },
    })
    fixed_policy_backend = research.GoogleDiscoveryEngineBackend(
        access_token_provider=FakeAccessTokenProvider(),
        session=FakePostSession(fixed_policy_error),
    )
    with pytest.raises(research.ConfirmedSearchError, match="HTTP 400"):
        fixed_policy_backend.search("query")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (FakeSearchResponse(200, json_error=ValueError("bad JSON")), "invalid JSON"),
        (FakeSearchResponse(200, []), "not an object"),
        (FakeSearchResponse(200, {"results": {}}), "results are invalid"),
    ],
)
def test_discovery_backend_rejects_invalid_responses(
    response: FakeSearchResponse,
    message: str,
) -> None:
    session = FakePostSession(response)
    backend = research.GoogleDiscoveryEngineBackend(
        access_token_provider=FakeAccessTokenProvider(),
        session=session,
    )
    with pytest.raises(research.ResearchError, match=message):
        backend.search("query")
    assert len(session.calls) == 1

    ambiguous_session = FakePostSession(
        error=research.requests.Timeout("fixture timeout")
    )
    ambiguous = research.GoogleDiscoveryEngineBackend(
        access_token_provider=FakeAccessTokenProvider(),
        session=ambiguous_session,
    )
    with pytest.raises(research.AmbiguousSearchRequest, match="ambiguous"):
        ambiguous.search("query")
    assert len(ambiguous_session.calls) == 1


def test_discovery_configuration_is_fixed_scoped_request_only_and_identity_stable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first_tokens = FakeAccessTokenProvider("first-sensitive-token")
    session = FakePostSession()
    backend, budget, config = research.configured_discovery_engine_backend(
        tmp_path,
        environment={
            "GOOGLE_CUSTOM_SEARCH_API_KEY": "legacy-key-must-be-ignored",
            "BRAVE_API_KEY": "brave-key-must-be-ignored",
            research.SEARCH_BACKEND_NAME: "brave",
        },
        session=session,
        access_token_provider=first_tokens,
    )
    assert isinstance(backend, research.GoogleDiscoveryEngineBackend)
    assert first_tokens.calls == 0
    assert session.calls == []
    assert budget.pricing_known is False
    assert budget.price_per_1000 == Decimal("0")
    assert budget.planned_maximum == budget.absolute_maximum == 150
    assert config == {
        "backend": "google_discovery_engine",
        "backend_version": research.DISCOVERY_ENGINE_BACKEND_VERSION,
        "search_scope": "configured_website_data_store",
        "full_web_search": False,
        "project_id": research.DISCOVERY_PROJECT_ID,
        "location": research.DISCOVERY_LOCATION,
        "collection": research.DISCOVERY_COLLECTION,
        "engine_id": research.DISCOVERY_ENGINE_ID,
        "data_store_id": research.DISCOVERY_DATA_STORE_ID,
        "serving_config": research.DISCOVERY_SERVING_CONFIG,
        "quota_project_id": research.DISCOVERY_PROJECT_ID,
        "engine_identity_sha256": backend.engine_identity_hash,
        "authentication": "application_default_credentials_via_gcloud",
        "access_tokens_persisted": False,
        "generative_features_requested": False,
        "search_cost_estimate_available": False,
        "secrets_persisted": False,
    }
    serialized = json.dumps(config, sort_keys=True)
    assert "first-sensitive-token" not in serialized
    assert "legacy-key-must-be-ignored" not in serialized
    assert "brave-key-must-be-ignored" not in serialized
    second = research.GoogleDiscoveryEngineBackend(
        access_token_provider=FakeAccessTokenProvider("rotated-token"),
        session=FakePostSession(),
    )
    assert second.engine_identity_hash == backend.engine_identity_hash
    assert backend.endpoint == research.DISCOVERY_ENDPOINT
    assert backend.fixed_request_policy["generative_features"] is False

    selected_tokens = FakeAccessTokenProvider("selected-token")
    monkeypatch.setattr(
        research,
        "GcloudADCAccessTokenProvider",
        lambda: selected_tokens,
    )
    selected, selected_budget, selected_config = research.configured_search_backend(
        tmp_path,
        environment={
            "BRAVE_API_KEY": "ignored",
            research.SEARCH_BACKEND_NAME: "brave",
        },
        session=FakePostSession(),
    )
    assert isinstance(selected, research.GoogleDiscoveryEngineBackend)
    assert selected_budget.pricing_known is False
    assert selected_config["backend"] == "google_discovery_engine"
    assert selected_tokens.calls == 0


def test_brave_backend_maps_top_ten_and_sends_secret_only_in_subscription_header() -> None:
    secret = "fixture-brave-secret"
    items = [
        {
            "title": f"Result {index}",
            "url": f"https://example.org/{index}",
            "description": f"Description {index}",
        }
        for index in range(1, 13)
    ]
    session = FakeSearchSession(
        FakeSearchResponse(200, {"web": {"results": items}})
    )
    backend = research.BraveWebSearchBackend(secret, session=session)
    results = backend.search("Margaret Thatcher quotation", number=10)

    assert len(results) == 10
    assert results[0] == {
        "title": "Result 1",
        "result_url": "https://example.org/1",
        "snippet": "Description 1",
    }
    assert results[-1]["title"] == "Result 10"
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == research.BraveWebSearchBackend.endpoint
    assert call["params"] == {
        "q": "Margaret Thatcher quotation",
        "count": 10,
        **research.BraveWebSearchBackend.fixed_parameters,
    }
    assert call["headers"]["X-Subscription-Token"] == secret
    assert call["headers"]["Accept"] == "application/json"
    assert session.trust_env is False
    persisted_shape = {
        "results": results,
        "engine_identity_sha256": backend.engine_identity_hash,
        "backend": backend.name,
    }
    assert secret not in json.dumps(persisted_shape, sort_keys=True)
    with pytest.raises(ValueError, match="1..10"):
        backend.search("too many", number=11)
    assert len(session.calls) == 1


def test_brave_query_limit_adapter_preserves_short_query_and_uses_quoted_prefix() -> None:
    backend = research.BraveWebSearchBackend(
        "fixture-secret", session=FakeSearchSession()
    )
    under_limit = '"Margaret Thatcher defended individual liberty"'
    long_words = [f"word{index:02d}" for index in range(1, 61)]
    over_limit = f'"{" ".join(long_words)}"'

    assert backend.prepare_query(under_limit) == under_limit
    adapted = backend.prepare_query(over_limit)

    assert adapted != over_limit
    assert adapted.startswith('"') and adapted.endswith('"')
    adapted_words = adapted[1:-1].split()
    assert adapted_words == long_words[:len(adapted_words)]
    assert len(adapted_words) == research.BRAVE_QUERY_SAFE_EXCERPT_WORDS
    assert len(adapted) <= research.BRAVE_QUERY_MAXIMUM_CHARACTERS
    assert len(adapted.split()) <= research.BRAVE_QUERY_MAXIMUM_WORDS


def test_brave_limit_adapter_replaces_one_422_as_retry_and_reuses_other_cache(
    tmp_path: Path,
) -> None:
    long_text = " ".join(f"word{index:02d}" for index in range(1, 61))
    target = {
        "quote_id": "quote-long",
        "quotation_text": long_text,
        "target_origins": ["historical_context_semantic_gate"],
        "gate_disposition": "correction_required",
        "recorded_variants": [],
        "distinctive_fragments": research.distinctive_fragments(long_text),
    }
    manifest = _manifest_for_targets([target])
    long_query_row = manifest["targets"][0]["queries"][0]
    cached_query_row = manifest["targets"][0]["queries"][2]
    planned_long_query = long_query_row["query"]
    cached_short_query = cached_query_row["query"]
    session = FakeSearchSession(
        FakeSearchResponse(200, {"web": {"results": []}})
    )
    backend = research.BraveWebSearchBackend("fixture-secret", session=session)
    provider_query = backend.prepare_query(planned_long_query)
    assert provider_query != planned_long_query
    assert backend.prepare_query(cached_short_query) == cached_short_query

    cache = research.ResearchCache(tmp_path / "cache")
    rejected_cache_key = cache.search_key(
        backend, planned_long_query, research.TOP_RESULTS
    )
    replacement_cache_key = cache.search_key(
        backend, provider_query, research.TOP_RESULTS
    )
    completed_cache_key = cache.search_key(
        backend, cached_short_query, research.TOP_RESULTS
    )
    assert replacement_cache_key != rejected_cache_key
    old_rejected_id = "a" * 64
    old_completed_id = "b" * 64
    budget = research.SearchBudget(
        price_per_1000=Decimal("5"),
        unique_queries_started=2,
        requests_started=2,
        requests_completed=2,
    )
    state = research.initialise_run_state(
        manifest, status="running", budget=budget
    )
    state["search_operations"] = [
        {
            "operation_id": old_rejected_id,
            "cache_key": rejected_cache_key,
            "quote_id": "quote-long",
            "query_sha256": research.sha256_bytes(planned_long_query.encode("utf-8")),
            "query_request_order": 1,
            "attempt_number": 1,
            "status": "confirmed_error",
            "error_kind": "rejected_search_query",
            "error": "Brave structured search HTTP 422",
        },
        {
            "operation_id": old_completed_id,
            "cache_key": completed_cache_key,
            "quote_id": "quote-long",
            "query_sha256": research.sha256_bytes(cached_short_query.encode("utf-8")),
            "query_request_order": 3,
            "attempt_number": 1,
            "status": "completed",
            "result_count": 1,
        },
    ]
    cached_results = [{
        "title": "Previously cached result",
        "result_url": "https://example.org/cached",
        "snippet": "Previously completed ordinary search",
    }]
    cache.save_search(completed_cache_key, {
        "backend": backend.name,
        "backend_version": backend.version,
        "query": cached_short_query,
        "result_count": 1,
        "results": cached_results,
        "searched_at": "2026-07-22T00:00:00Z",
    })
    research.validate_resume_state(state, manifest)
    runner = _runner(
        tmp_path,
        manifest=manifest,
        backend=backend,
        state=state,
        budget=budget,
        cache=cache,
    )

    runner._process_query("quote-long", long_query_row)

    assert len(session.calls) == 1
    assert session.calls[0]["params"]["q"] == provider_query
    assert len(provider_query) <= research.BRAVE_QUERY_MAXIMUM_CHARACTERS
    assert len(provider_query.split()) <= research.BRAVE_QUERY_MAXIMUM_WORDS
    assert budget.requests_started == budget.requests_completed == 3
    assert budget.unique_queries_started == 2
    assert budget.retries == 1
    assert len(state["search_operations"]) == 3
    replacement = state["search_operations"][-1]
    assert replacement["status"] == "completed"
    assert replacement["cache_key"] == replacement_cache_key
    assert replacement["replacement_for_rejected_query_operation_id"] == (
        old_rejected_id
    )
    assert replacement["attempt_number"] == 2
    assert replacement["provider_query_adapted"] is True
    assert replacement["query_sha256"] == research.sha256_bytes(
        planned_long_query.encode("utf-8")
    )
    assert replacement["provider_query_sha256"] == research.sha256_bytes(
        provider_query.encode("utf-8")
    )
    attempted = state["cases"]["quote-long"]["queries_attempted"]
    assert attempted == [{
        "request_order": 1,
        "scheduled_stage": 1,
        "query": provider_query,
        "planned_query": planned_long_query,
        "query_adapted_for_backend": True,
        "result_count": 0,
        "search_cache_hit": False,
    }]

    repeated, repeated_cache_hit = runner._structured_search(
        "quote-long", long_query_row
    )
    assert repeated == []
    assert repeated_cache_hit is True
    reused, reused_cache_hit = runner._structured_search(
        "quote-long", cached_query_row
    )
    assert reused == cached_results
    assert reused_cache_hit is True
    assert len(session.calls) == 1
    assert budget.requests_started == 3
    assert budget.unique_queries_started == 2
    assert budget.retries == 1
    assert budget.cache_hits == 2
    assert len(state["search_operations"]) == 3


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_brave_backend_retryable_http_statuses(status_code: int) -> None:
    backend = research.BraveWebSearchBackend(
        "fixture-secret",
        session=FakeSearchSession(FakeSearchResponse(status_code, {})),
    )
    with pytest.raises(research.RetryableSearchError, match=str(status_code)):
        backend.search("query")


def test_brave_backend_confirmed_ambiguous_and_invalid_responses() -> None:
    confirmed = research.BraveWebSearchBackend(
        "fixture-secret",
        session=FakeSearchSession(FakeSearchResponse(400, {})),
    )
    with pytest.raises(research.ConfirmedSearchError, match="400"):
        confirmed.search("query")

    ambiguous = research.BraveWebSearchBackend(
        "fixture-secret",
        session=FakeSearchSession(error=research.requests.Timeout("fixture timeout")),
    )
    with pytest.raises(research.AmbiguousSearchRequest, match="ambiguous"):
        ambiguous.search("query")

    invalid_json = research.BraveWebSearchBackend(
        "fixture-secret",
        session=FakeSearchSession(
            FakeSearchResponse(200, json_error=ValueError("fixture invalid JSON"))
        ),
    )
    with pytest.raises(research.ResearchError, match="invalid JSON"):
        invalid_json.search("query")

    for payload in ([], {"web": []}, {"web": {"results": {}}}):
        malformed = research.BraveWebSearchBackend(
            "fixture-secret",
            session=FakeSearchSession(FakeSearchResponse(200, payload)),
        )
        with pytest.raises(research.ResearchError, match="invalid"):
            malformed.search("query")


def test_brave_422_is_quote_local_durable_and_not_rebilled_on_resume(
    tmp_path: Path,
) -> None:
    second_text = (
        "A nation must preserve responsibility if it wishes to preserve liberty"
    )
    second_target = {
        "quote_id": "quote-2",
        "quotation_text": second_text,
        "target_origins": ["final_unresolved_status"],
        "gate_disposition": None,
        "recorded_variants": [],
        "distinctive_fragments": research.distinctive_fragments(second_text),
    }
    manifest = _manifest_for_targets([_target(), second_target])
    scheduled_requests = len(manifest["global_request_order"])
    session = SequencedSearchSession([
        FakeSearchResponse(422, {}),
        *[
            FakeSearchResponse(200, {"web": {"results": []}})
            for _ in range(scheduled_requests - 1)
        ],
    ])
    backend = research.BraveWebSearchBackend("fixture-secret", session=session)
    budget = research.SearchBudget(price_per_1000=Decimal("5"))
    state = research.initialise_run_state(manifest, status="ready", budget=budget)
    runner = _runner(
        tmp_path,
        manifest=manifest,
        backend=backend,
        state=state,
        budget=budget,
    )

    completed = runner.run()

    assert completed["run_status"] == "complete"
    assert completed["failure_reason"] == ""
    assert len(session.calls) == scheduled_requests
    assert session.responses == []
    assert all(call["allow_redirects"] is False for call in session.calls)
    assert budget.requests_started == budget.requests_completed == scheduled_requests
    assert budget.requests_ambiguous == 0
    operations = completed["search_operations"]
    assert [operation["status"] for operation in operations].count(
        "confirmed_error"
    ) == 1
    assert operations[0]["error_kind"] == "rejected_search_query"
    assert operations[0]["error"] == "Brave structured search HTTP 422"

    rejected_case = completed["cases"]["quote-1"]
    unaffected_case = completed["cases"]["quote-2"]
    assert rejected_case["status"] == "complete_search_with_access_errors"
    assert rejected_case["recommended_outcome"] == "search_incomplete_due_to_access"
    assert len(rejected_case["search_access_errors"]) == 1
    assert rejected_case["search_access_errors"][0]["error_kind"] == (
        "provider_rejected_search_query"
    )
    assert len(rejected_case["queries_attempted"]) == (
        research.PLANNED_QUERY_STAGES_PER_QUOTE
    )
    rejected_attempts = [
        attempt for attempt in rejected_case["queries_attempted"]
        if attempt.get("search_error") == "provider_rejected_search_query"
    ]
    assert len(rejected_attempts) == 1
    assert rejected_attempts[0]["scheduled_stage"] == 1
    assert unaffected_case["status"] == "complete_search"
    assert unaffected_case["recommended_outcome"] == "no_reliable_evidence_found"
    assert "search_access_errors" not in unaffected_case
    assert len(unaffected_case["queries_attempted"]) == (
        research.scheduled_query_count(manifest["targets"][1])
    )

    evidence = research.advisory_evidence_document(manifest, completed)
    outcomes = {
        quotation["quote_id"]: quotation["recommended_outcome"]
        for quotation in evidence["quotations"]
    }
    assert outcomes == {
        "quote-1": "search_incomplete_due_to_access",
        "quote-2": "no_reliable_evidence_found",
    }

    persisted = research.load_run_state(tmp_path / "run_state.json", manifest)
    resumed_budget = research.SearchBudget.from_dict(persisted["search_accounting"])
    resumed_session = FakeSearchSession(
        FakeSearchResponse(200, {"web": {"results": []}})
    )
    resumed_backend = research.BraveWebSearchBackend(
        "fixture-secret", session=resumed_session
    )
    resumed = _runner(
        tmp_path,
        manifest=manifest,
        backend=resumed_backend,
        state=persisted,
        budget=resumed_budget,
    )
    resumed.run()
    rejected_query = manifest["targets"][0]["queries"][0]
    with pytest.raises(research.RejectedSearchQuery, match="previously rejected"):
        resumed._structured_search("quote-1", rejected_query)
    assert resumed_session.calls == []
    assert resumed_budget.requests_started == scheduled_requests
    assert resumed_budget.requests_completed == scheduled_requests
    assert len(resumed.state["search_operations"]) == scheduled_requests


def test_brave_backend_disables_redirects_and_rejects_3xx() -> None:
    session = FakeSearchSession(FakeSearchResponse(302, {}))
    backend = research.BraveWebSearchBackend(
        "fixture-brave-secret", session=session
    )
    with pytest.raises(research.ConfirmedSearchError, match="redirect refused"):
        backend.search("Margaret Thatcher quotation")
    assert len(session.calls) == 1
    assert session.calls[0]["allow_redirects"] is False


def test_url_canonicalisation_and_result_deduplication() -> None:
    variants = [
        "https://margaretthatcher.org/document/107352/",
        "https://www.margaretthatcher.org/document/107352#passage",
        "https://MARGARETTHATCHER.ORG/document/107352?utm_source=test",
    ]
    assert {research.canonicalise_url(value) for value in variants} == {
        "https://www.margaretthatcher.org/document/107352"
    }
    assert research.canonicalise_url(
        "https://Example.org/a/../speech/?b=2&utm_campaign=x&a=1#fragment"
    ) == "https://example.org/speech?a=1&b=2"

    rows = research.deduplicate_results(
        [{"result_url": value} for value in variants]
        + [{"result_url": "file:///etc/passwd"}]
    )
    assert [row["decision"] for row in rows] == [
        "candidate_for_fetch",
        "duplicate_result_url",
        "duplicate_result_url",
        "rejected_unsafe_result_url",
    ]


def test_malformed_and_invalid_utf8_result_urls_are_rejected_rows_not_exceptions() -> None:
    rows = research.deduplicate_results(
        [
            {"result_url": "https://[example.org/evidence"},
            {"result_url": "https://example.org/evidence/%FF"},
        ]
    )
    assert [row["decision"] for row in rows] == [
        "rejected_unsafe_result_url",
        "rejected_unsafe_result_url",
    ]
    assert all(row["canonical_url"] == "" for row in rows)
    assert all(row["decision_reason"] for row in rows)


def test_encoded_reserved_slash_remains_encoded_and_path_distinct() -> None:
    encoded = research.canonicalise_url(
        "https://example.org/archive/item%2Fpage?utm_source=fixture"
    )
    literal = research.canonicalise_url("https://example.org/archive/item/page")
    assert encoded == "https://example.org/archive/item%2Fpage"
    assert literal == "https://example.org/archive/item/page"
    assert encoded != literal


def test_search_request_and_cost_hard_stops_are_enforced_before_mutation() -> None:
    planned = research.SearchBudget(
        price_per_1000=Decimal("0"), planned_maximum=1, absolute_maximum=2
    )
    planned.reserve_request()
    with pytest.raises(research.SearchBudgetExceeded, match="planned"):
        planned.reserve_request()
    assert planned.requests_started == 1

    absolute = research.SearchBudget(
        price_per_1000=Decimal("0"), planned_maximum=3, absolute_maximum=1
    )
    absolute.reserve_request()
    with pytest.raises(research.SearchBudgetExceeded, match="absolute search-request"):
        absolute.reserve_request()
    assert absolute.requests_started == 1

    cost = research.SearchBudget(
        price_per_1000=Decimal("5001"), planned_maximum=10, absolute_maximum=10
    )
    with pytest.raises(research.SearchBudgetExceeded, match="search-cost"):
        cost.reserve_request()
    assert cost.requests_started == 0
    assert cost.projected_cost() == Decimal("0.000000")


def test_resume_cannot_raise_durable_search_or_page_hard_limits() -> None:
    persisted = research.SearchBudget(price_per_1000=Decimal("5")).as_dict()
    persisted["planned_request_limit"] = 999
    persisted["absolute_request_hard_stop"] = 999
    restored = research.SearchBudget.from_dict(persisted)
    assert restored.planned_maximum == research.PLANNED_SEARCH_REQUESTS
    assert restored.absolute_maximum == research.ABSOLUTE_SEARCH_REQUESTS
    assert restored.hard_cost_usd == research.HARD_COST_USD

    page = research.PageFetchBudget.from_dict(
        {"maximum_unique_page_fetches": 999, "requested_urls": []}
    )
    assert page.maximum == research.MAXIMUM_PAGE_FETCHES


def test_discovery_request_only_accounting_restores_for_resume_without_cost_claims() -> None:
    original = research.SearchBudget(
        price_per_1000=Decimal("0"),
        pricing_known=False,
        unique_queries_started=3,
        requests_started=4,
        requests_completed=3,
        requests_ambiguous=1,
        retries=1,
        cache_hits=2,
    )
    persisted = original.as_dict()

    assert persisted["search_cost_estimate_available"] is False
    assert persisted["price_per_1000_searches_usd"] is None
    assert persisted["paid_or_conservatively_priced_requests"] is None
    assert persisted["estimated_search_cost_usd"] is None
    assert persisted["remaining_authorised_budget_usd"] is None
    restored = research.SearchBudget.from_dict(persisted)
    assert restored.pricing_known is False
    assert restored.price_per_1000 == Decimal("0")
    assert restored.unique_queries_started == 3
    assert restored.requests_started == 4
    assert restored.requests_completed == 3
    assert restored.requests_ambiguous == 1
    assert restored.retries == 1
    assert restored.cache_hits == 2
    assert restored.planned_maximum == restored.absolute_maximum == 150


def test_runner_stops_at_request_cap_with_exact_outcome_and_status(
    tmp_path: Path,
) -> None:
    backend = FakeBackend([])
    budget = research.SearchBudget(
        price_per_1000=Decimal("0"),
        pricing_known=False,
        planned_maximum=1,
        absolute_maximum=1,
    )
    runner = _runner(tmp_path, backend=backend, budget=budget)

    state = runner.run()

    assert len(backend.calls) == 1
    assert budget.requests_started == budget.requests_completed == 1
    assert state["run_status"] == "request_cap_reached"
    case = state["cases"]["quote-1"]
    assert case["status"] == "incomplete_search"
    assert case["recommended_outcome"] == "search_incomplete_due_to_request_cap"
    assert research.final_report_status({"counts": {}}, state) == (
        "RESEARCH INCOMPLETE — REQUEST CAP REACHED"
    )


def test_search_cache_checkpoint_resume_and_no_repeat_billing(tmp_path: Path) -> None:
    backend = FakeBackend(
        [{"title": "Result", "result_url": "https://example.org/source", "snippet": "lead"}]
    )
    budget = research.SearchBudget(price_per_1000=Decimal("5"))
    cache = research.ResearchCache(tmp_path / "cache")
    runner = _runner(tmp_path, backend=backend, budget=budget, cache=cache)
    query = _manifest()["targets"][0]["queries"][0]

    results, cache_hit = runner._structured_search("quote-1", query)
    assert results[0]["title"] == "Result"
    assert cache_hit is False
    assert backend.calls == [(query["query"], research.TOP_RESULTS)]
    assert budget.requests_started == budget.requests_completed == 1
    assert json.loads((tmp_path / "run_state.json").read_text())["search_operations"][0][
        "status"
    ] == "completed"
    assert json.loads((tmp_path / "run_state.json").read_text())["search_accounting"][
        "llm_or_generative_provider_cost_usd"
    ] == "0"

    resumed_state = research.load_run_state(tmp_path / "run_state.json", _manifest())
    resumed_budget = research.SearchBudget.from_dict(resumed_state["search_accounting"])
    resumed_backend = FakeBackend()
    resumed = _runner(
        tmp_path,
        backend=resumed_backend,
        state=resumed_state,
        budget=resumed_budget,
        cache=cache,
    )
    repeated, resumed_cache_hit = resumed._structured_search("quote-1", query)
    assert repeated == results
    assert resumed_cache_hit is True
    assert resumed_backend.calls == []
    assert resumed_budget.requests_started == 1
    assert resumed_budget.cache_hits == 1


def test_resume_refuses_a_possibly_billed_operation() -> None:
    manifest = _manifest()
    budget = research.SearchBudget(
        price_per_1000=Decimal("0"),
        pricing_known=False,
        unique_queries_started=1,
        requests_started=1,
    )
    state = research.initialise_run_state(
        manifest, status="running", budget=budget
    )
    state["search_operations"].append({"cache_key": "key", "status": "sending"})
    with pytest.raises(research.AmbiguousSearchRequest, match="automatic repeat refused"):
        research.validate_resume_state(state, manifest)


def test_completed_paid_operation_with_missing_cache_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest()
    backend = FakeBackend()
    cache = research.ResearchCache(tmp_path / "cache")
    query = manifest["targets"][0]["queries"][0]
    key = cache.search_key(backend, query["query"], research.TOP_RESULTS)
    budget = research.SearchBudget(
        price_per_1000=Decimal("5"), requests_started=1, requests_completed=1
    )
    state = research.initialise_run_state(manifest, status="running", budget=budget)
    state["search_operations"].append(
        {"cache_key": key, "status": "completed", "request_number": 1}
    )
    runner = _runner(
        tmp_path,
        backend=backend,
        state=state,
        budget=budget,
        cache=cache,
    )

    with pytest.raises(research.ResearchError, match="cache|completed|repeat"):
        runner._structured_search("quote-1", query)
    assert backend.calls == []
    assert budget.requests_started == 1


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.org/source",
        "data:text/plain,evidence",
        "http://user:secret@example.org/source",
        "http://localhost/source",
        "http://127.0.0.1/source",
        "http://[::1]/source",
        "http://169.254.10.1/source",
        "http://10.10.10.10/source",
        "http://224.0.0.1/source",
        "http://[ff02::1]/source",
    ],
)
def test_ssrf_and_non_http_urls_are_rejected(url: str) -> None:
    with pytest.raises(research.UnsafeURL):
        research.validate_public_url(url)


def test_dns_validation_rejects_any_private_answer() -> None:
    def mixed_resolver(*_args, **_kwargs):
        return [
            (2, 1, 6, "", ("93.184.216.34", 80)),
            (2, 1, 6, "", ("192.168.1.4", 80)),
        ]

    with pytest.raises(research.UnsafeURL, match="non-public"):
        research.validate_public_url("https://evidence.example/source", resolver=mixed_resolver)


def test_pinned_connection_rejects_rebound_private_dns_without_opening_socket(
    monkeypatch,
) -> None:
    socket_attempted = False

    def private_resolver(*_args, **_kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 80))]

    def forbidden_connection(*_args, **_kwargs):
        nonlocal socket_attempted
        socket_attempted = True
        raise AssertionError("private destination socket must not be opened")

    monkeypatch.setattr(research.socket, "create_connection", forbidden_connection)
    with pytest.raises(research.UnsafeURL, match="connection time"):
        research._pinned_public_get(
            "http://evidence.example/source",
            headers={"User-Agent": "fixture"},
            timeout=(0.1, 0.1),
            resolver=private_resolver,
        )
    assert socket_attempted is False


def test_fetch_v6_ipv6_host_header_is_bracketed() -> None:
    assert research._http_host_header("2001:4860:4860::8888", 443, "https") == (
        "[2001:4860:4860::8888]"
    )
    assert research._http_host_header("2001:4860:4860::8888", 8443, "https") == (
        "[2001:4860:4860::8888]:8443"
    )


def test_fetch_v6_public_dns_address_fanout_is_bounded(monkeypatch) -> None:
    addresses = [
        "1.1.1.1", "8.8.8.8", "9.9.9.9", "208.67.222.222",
        "4.2.2.1", "8.8.4.4",
    ]
    connection_attempts: list[tuple[str, int]] = []

    def resolver(*_args, **_kwargs):
        return [
            (research.socket.AF_INET, research.socket.SOCK_STREAM, 6, "", (address, 443))
            for address in addresses
        ]

    def unavailable(address, *, timeout):
        del timeout
        connection_attempts.append(address)
        raise OSError("fixture black-hole address")

    monkeypatch.setattr(research.socket, "create_connection", unavailable)
    with pytest.raises(OSError, match="all validated public addresses failed"):
        research._pinned_public_get(
            "https://evidence.example/source",
            headers={"User-Agent": "fixture"},
            timeout=(0.1, 0.1),
            resolver=resolver,
        )

    assert len(connection_attempts) == research.MAXIMUM_PUBLIC_ADDRESS_ATTEMPTS


def test_fetch_v6_document_request_headers_are_honest_and_compression_bounded() -> None:
    headers = research._document_request_headers()

    assert headers == {
        "User-Agent": research.USER_AGENT,
        "Accept": research.DOCUMENT_ACCEPT,
        "Accept-Language": research.ACCEPT_LANGUAGE,
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }
    assert "Cookie" not in headers
    assert "Authorization" not in headers
    assert not any(name.casefold().startswith("sec-fetch-") for name in headers)


def test_fetch_v6_gzip_is_decoded_and_unadvertised_brotli_is_rejected(
    tmp_path: Path,
) -> None:
    page = b"<html><body>Margaret Thatcher archive evidence.</body></html>"
    compressed = gzip.compress(page)
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
    )

    decoded, error = fetcher._read_response(
        FakeDocumentResponse(
            200,
            compressed,
            headers={"content-encoding": "gzip"},
        ),
        research.MAXIMUM_RESPONSE_BYTES,
    )
    rejected, rejected_error = fetcher._read_response(
        FakeDocumentResponse(
            200,
            b"not actually Brotli",
            headers={"content-encoding": "br"},
        ),
        research.MAXIMUM_RESPONSE_BYTES,
    )

    assert decoded == page
    assert error is None
    assert rejected == b""
    assert rejected_error == "unsupported_content_encoding"


@pytest.mark.parametrize(
    "payload",
    [
        gzip.compress(b"first member") + gzip.compress(b"second member"),
        gzip.compress(b"document") + b"trailing non-gzip bytes",
    ],
)
def test_fetch_v6_rejects_concatenated_or_trailing_gzip_data(
    tmp_path: Path,
    payload: bytes,
) -> None:
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
    )

    body, error = fetcher._read_response(
        FakeDocumentResponse(
            200,
            payload,
            headers={"content-encoding": "gzip"},
        ),
        research.MAXIMUM_RESPONSE_BYTES,
    )

    assert body == b""
    assert error == "invalid_content_encoding"


@pytest.mark.parametrize(
    ("status_code", "headers", "body", "marker"),
    [
        (
            403,
            {"cf-mitigated": "challenge"},
            b"<html><title>Access denied</title><body>Denied.</body></html>",
            "cf_mitigated",
        ),
        (
            200,
            {"cf-mitigated": "challenge", "content-type": "text/plain"},
            b"",
            "cf_mitigated",
        ),
        (
            200,
            {},
            (
                b"<html><title>Just a moment... | Evidence</title><body>"
                b"<script src='/cdn-cgi/challenge-platform/cf-chl.js'></script>"
                b"Checking your browser</body></html>"
            ),
            "cf_challenge",
        ),
        (
            200,
            {},
            (
                b"<html><title>Checking your browser... | Evidence Archive</title><body>"
                b"<script src='/cdn-cgi/challenge-platform/cf-chl.js'></script>"
                b"Checking your browser</body></html>"
            ),
            "checking_browser",
        ),
        (
            200,
            {},
            (
                b"<html><title>Attention Required! | Cloudflare</title><body>"
                b"<script src='/cdn-cgi/challenge-platform/cf-chl.js'></script>"
                b"Checking your browser</body></html>"
            ),
            "cf_challenge",
        ),
        (
            200,
            {},
            (
                b"<html><title>Verify you are human | Evidence Archive</title><body>"
                b"<div class='hcaptcha'>Verify you are human</div>"
                b"</body></html>"
            ),
            "human_verification",
        ),
    ],
)
def test_fetch_v6_challenge_pages_are_rejected_without_retry_or_cache(
    tmp_path: Path,
    status_code: int,
    headers: dict[str, str],
    body: bytes,
    marker: str,
) -> None:
    url = "https://evidence.example/document"
    response = FakeDocumentResponse(status_code, body, headers=headers)
    session = FakeDocumentSession([response])
    sleeps: list[float] = []
    cache = research.ResearchCache(tmp_path / "cache")
    fetcher = research.SafeFetcher(
        cache=cache,
        budget=research.PageFetchBudget(),
        session=session,
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
        sleep=sleeps.append,
    )
    fetcher.robots_policy = lambda _url: {
        "allowed": True,
        "crawl_delay_seconds": 0,
        "reason": "fixture_allow",
    }

    result = fetcher.fetch(url)

    assert result["status"] == "access_challenge"
    assert result["network_attempt_count"] == 1
    assert result["retry_count"] == 0
    assert marker in result["challenge"]["markers"]
    assert response.closed is True
    assert len(session.calls) == 1
    assert sleeps == []
    assert cache.load_page(url) is None


def test_fetch_v6_transient_status_retries_once_and_records_attempts(
    tmp_path: Path,
) -> None:
    url = "https://evidence.example/transient"
    first = FakeDocumentResponse(
        503,
        b"temporarily unavailable",
        headers={"content-type": "text/plain", "retry-after": "7"},
    )
    second = FakeDocumentResponse(
        200,
        b"Margaret Thatcher evidence page",
        headers={"content-type": "text/plain"},
    )
    session = FakeDocumentSession([first, second])
    sleeps: list[float] = []
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        session=session,
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
        sleep=sleeps.append,
    )
    fetcher.robots_policy = lambda _url: {
        "allowed": True,
        "crawl_delay_seconds": 0,
        "reason": "fixture_allow",
    }

    result = fetcher.fetch(url)

    assert result["status"] == "fetched"
    assert result["body"] == b"Margaret Thatcher evidence page"
    assert result["network_attempt_count"] == 2
    assert result["retry_count"] == 1
    assert [attempt["http_status"] for attempt in result["network_attempts"]] == [503, 200]
    assert sleeps == [7.0]
    assert len(session.calls) == 2
    assert first.closed is True
    assert second.closed is True


def test_fetch_v6_old_page_and_robots_cache_records_are_invalidated(
    tmp_path: Path,
) -> None:
    cache = research.ResearchCache(tmp_path / "cache")
    page_url = "https://evidence.example/archive-page"
    body = b"cached evidence"
    cache.save_page(
        page_url,
        {
            "status": "fetched",
            "content_type": "text/plain",
            "fetch_policy_version": "historical-context-restricted-fetch-v5",
        },
        body,
    )

    assert cache.load_page(page_url) is None
    metadata_path, body_path = cache.page_paths(page_url)
    assert metadata_path.is_file()
    assert body_path.is_file()

    cache.save_page(
        page_url,
        {
            "status": "fetched",
            "content_type": "text/plain",
            "fetch_policy_version": research.FETCH_POLICY_VERSION,
        },
        body,
    )
    current_page = cache.load_page(page_url)
    assert current_page is not None
    assert current_page[1] == body

    origin = "https://evidence.example"
    research.atomic_write_json(
        cache.robots_path(origin),
        {
            "origin": origin,
            "policy_kind": "allow_all_not_found",
            "robots_text": "",
            "crawl_delay_seconds": 0,
            "robots_url": origin + "/robots.txt",
            "robots_http_status": 404,
            "fetched_at": research.utc_now(),
            "fetch_policy_version": "historical-context-restricted-fetch-v5",
        },
        mode=0o600,
    )
    assert cache.load_robots(origin) is None

    cache.save_robots(
        origin,
        {
            "policy_kind": "allow_all_not_found",
            "robots_text": "",
            "crawl_delay_seconds": 0,
            "robots_url": origin + "/robots.txt",
            "robots_http_status": 404,
            "fetched_at": research.utc_now(),
        },
    )
    current_robots = cache.load_robots(origin)
    assert current_robots is not None
    assert current_robots["fetch_policy_version"] == research.FETCH_POLICY_VERSION


def test_fetch_v6_unusable_robots_crawl_delays_fail_closed_without_sleeping(
    tmp_path: Path,
) -> None:
    body = b"User-agent: *\nAllow: /\nCrawl-delay: 999999999\n"
    session = FakeDocumentSession([
        FakeDocumentResponse(200, body, headers={"content-type": "text/plain"}),
    ])
    sleeps: list[float] = []
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        session=session,
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
        sleep=sleeps.append,
    )

    policy = fetcher.robots_policy("https://evidence.example/document")
    unparseable_body = (
        "User-agent: *\nAllow: /\nCrawl-delay: " + "9" * 5000 + "\n"
    ).encode("ascii")
    unparseable_fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "unparseable-cache"),
        budget=research.PageFetchBudget(),
        session=FakeDocumentSession([
            FakeDocumentResponse(
                200,
                unparseable_body,
                headers={"content-type": "text/plain"},
            ),
        ]),
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
        sleep=sleeps.append,
    )
    unparseable_policy = unparseable_fetcher.robots_policy(
        "https://large-robots.example/document"
    )
    corrupt_cached_policy = fetcher._evaluate_robots_record(
        {
            "policy_kind": "rules",
            "robots_text": unparseable_body.decode("ascii"),
            "crawl_delay_seconds": 0,
            "robots_url": "https://evidence.example/robots.txt",
            "robots_http_status": 200,
            "fetched_at": research.utc_now(),
        },
        "https://evidence.example/document",
        cache_hit=True,
    )

    assert policy["allowed"] is False
    assert policy["reason"] == "robots_crawl_delay_unusable_fail_closed"
    assert unparseable_policy["allowed"] is False
    assert unparseable_policy["reason"] == "robots_unparseable_fail_closed"
    assert corrupt_cached_policy["allowed"] is False
    assert corrupt_cached_policy["reason"] == "robots_unparseable_fail_closed"
    assert sleeps == []


def test_fetch_v6_transient_dns_validation_is_retried(
    tmp_path: Path,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def flaky_validator(value: str) -> str:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise research.TransientDNSFailure("fixture resolver timeout")
        return research.canonicalise_url(value)

    response = FakeDocumentResponse(
        200,
        b"Margaret Thatcher evidence",
        headers={"content-type": "text/plain"},
    )
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        session=FakeDocumentSession([response]),
        test_only_allow_unpinned_session=True,
        url_validator=flaky_validator,
        minimum_delay_seconds=0,
        sleep=sleeps.append,
    )
    fetcher.robots_policy = lambda _url: {
        "allowed": True,
        "crawl_delay_seconds": 0,
        "reason": "fixture_allow",
    }

    result = fetcher.fetch("https://evidence.example/document")

    assert result["status"] == "fetched"
    assert calls >= 3
    assert sleeps == [1.0, 2.0]


def test_fetch_v6_malformed_redirect_is_rejected_without_exception_or_cache(
    tmp_path: Path,
) -> None:
    url = "https://evidence.example/redirect"
    response = FakeDocumentResponse(
        302,
        b"",
        headers={"location": "http://["},
    )
    cache = research.ResearchCache(tmp_path / "cache")
    fetcher = research.SafeFetcher(
        cache=cache,
        budget=research.PageFetchBudget(),
        session=FakeDocumentSession([response]),
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
    )
    fetcher.robots_policy = lambda _url: {
        "allowed": True,
        "crawl_delay_seconds": 0,
        "reason": "fixture_allow",
    }

    result = fetcher.fetch(url)

    assert result["status"] == "unsafe_redirect"
    assert result["network_attempt_count"] == 1
    assert cache.load_page(url) is None


def test_fetch_v6_malformed_mtf_canonical_link_fails_closed() -> None:
    body = b"""
        <html><head><title>Fixture</title><link rel="canonical" href="http://["></head>
        <body><h1 class="doctitle">Speech</h1><article class="node-archive-document">
        Margaret Thatcher transcript body with enough repeated material to pass the
        minimum body-size test. Margaret Thatcher transcript body with enough repeated
        material to pass the minimum body-size test.
        </article></body></html>
    """

    result = research.inspect_mtf_document(
        "https://www.margaretthatcher.org/document/103384",
        "text/html",
        body,
    )

    assert result["valid"] is False
    assert result["reason"] == "official_document_canonical_url_is_invalid"


def test_fetch_v6_unexpected_partial_content_is_not_cached(
    tmp_path: Path,
) -> None:
    url = "https://evidence.example/partial"
    response = FakeDocumentResponse(
        206,
        b"partial quotation text",
        headers={
            "content-type": "text/plain",
            "content-range": "bytes 0-21/500",
        },
    )
    cache = research.ResearchCache(tmp_path / "cache")
    fetcher = research.SafeFetcher(
        cache=cache,
        budget=research.PageFetchBudget(),
        session=FakeDocumentSession([response]),
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
    )
    fetcher.robots_policy = lambda _url: {
        "allowed": True,
        "crawl_delay_seconds": 0,
        "reason": "fixture_allow",
    }

    result = fetcher.fetch(url)

    assert result["status"] == "unexpected_partial_content"
    assert result["http_status"] == 206
    assert result["network_attempt_count"] == 1
    assert result["retry_count"] == 0
    assert cache.load_page(url) is None


def test_fetch_v6_retry_count_excludes_redirect_hops(
    tmp_path: Path,
) -> None:
    start = "https://evidence.example/start"
    session = FakeDocumentSession([
        FakeDocumentResponse(302, b"", headers={"location": "/missing"}),
        FakeDocumentResponse(404, b"not found", headers={"content-type": "text/plain"}),
    ])
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        session=session,
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
    )
    fetcher.robots_policy = lambda _url: {
        "allowed": True,
        "crawl_delay_seconds": 0,
        "reason": "fixture_allow",
    }

    result = fetcher.fetch(start)

    assert result["status"] == "http_error"
    assert result["network_attempt_count"] == 2
    assert result["retry_count"] == 0


def test_local_fixture_fetch_redirect_cache_and_size_limit(
    tmp_path: Path, local_document_server
) -> None:
    origin, counts = local_document_server
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        session=research.requests.Session(),
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
    )

    redirected = fetcher.fetch(origin + "/redirect")
    assert redirected["status"] == "fetched"
    assert redirected["final_url"] == origin + "/page"
    assert redirected["redirect_chain"] == [origin + "/redirect", origin + "/page"]
    assert b"Margaret Thatcher" in redirected["body"]
    before = dict(counts)
    cached = fetcher.fetch(origin + "/redirect")
    assert cached["cache_hit"] is True
    assert counts == before
    direct_destination = fetcher.fetch(origin + "/page")
    assert direct_destination["cache_hit"] is True
    assert direct_destination["final_url"] == origin + "/page"
    assert counts == before

    oversize = fetcher.fetch(origin + "/oversize")
    assert oversize["status"] == "response_size_limit_exceeded"
    assert oversize["captured_body_bytes"] == 0


def test_redirect_target_is_validated_before_following(
    tmp_path: Path, local_document_server
) -> None:
    origin, counts = local_document_server

    def rejecting_validator(value: str) -> str:
        canonical = research.canonicalise_url(value)
        if urlsplit(canonical).path == "/blocked":
            raise research.UnsafeURL("test redirect target rejected")
        return canonical

    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        session=research.requests.Session(),
        test_only_allow_unpinned_session=True,
        url_validator=rejecting_validator,
        minimum_delay_seconds=0,
    )
    result = fetcher.fetch(origin + "/unsafe-redirect")
    assert result["status"] == "unsafe_redirect"
    assert counts.get("/blocked", 0) == 0


def test_redirect_destination_robots_policy_is_enforced_before_fetch(
    tmp_path: Path, local_document_server
) -> None:
    origin, counts = local_document_server
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
        session=research.requests.Session(),
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
    )
    result = fetcher.fetch(origin + "/robots-redirect")
    assert result["status"] == "robots_disallowed"
    assert result["final_url"] == origin + "/blocked"
    assert counts.get("/robots-redirect", 0) == 1
    assert counts.get("/blocked", 0) == 0


def test_page_fetch_hard_cap_propagates_to_stop_the_runner(
    tmp_path: Path, local_document_server
) -> None:
    origin, counts = local_document_server
    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(maximum=0),
        session=research.requests.Session(),
        test_only_allow_unpinned_session=True,
        url_validator=research.canonicalise_url,
        minimum_delay_seconds=0,
    )
    with pytest.raises(research.FetchLimitExceeded):
        fetcher.fetch(origin + "/page")
    assert counts == {}


def test_streaming_response_without_content_length_still_obeys_size_limit(
    tmp_path: Path,
) -> None:
    class StreamingResponse:
        headers: dict[str, str] = {}

        def iter_content(self, _chunk_size: int):
            yield b"a" * research.MAXIMUM_RESPONSE_BYTES
            yield b"b"

    fetcher = research.SafeFetcher(
        cache=research.ResearchCache(tmp_path / "cache"),
        budget=research.PageFetchBudget(),
    )
    body, error = fetcher._read_response(
        StreamingResponse(), research.MAXIMUM_RESPONSE_BYTES
    )
    assert body == b""
    assert error == "response_size_limit_exceeded"


def test_pdf_extraction_fails_closed_without_in_process_parser() -> None:
    extracted = research.extract_page_text(
        {
            "status": "fetched",
            "content_type": "application/pdf",
            "body": b"%PDF-1.7\nfixture",
        }
    )
    assert extracted["status"] == "inaccessible"
    assert extracted["text"] == ""
    assert "not_safely_configured" in extracted["error"]


def _modern_mtf_document_html(document_number: str = "107352") -> bytes:
    return f"""<!doctype html><html><head>
<title>Speech to Conservative Party Conference | Margaret Thatcher Foundation</title>
<link rel="canonical" href="https://www.margaretthatcher.org/document/{document_number}">
</head><body>
<nav>NAVIGATION CHROME MUST NOT ENTER THE EVIDENCE TEXT.</nav>
<article class="node-archive-document">
<div class="docdate"><time datetime="1988-10-14">14 October 1988</time></div>
<div class="docauthor">Margaret Thatcher</div>
<h1 class="doctitle">Speech to Conservative Party Conference</h1>
<p>Margaret Thatcher told the conference: {QUOTATION}.</p>
<p>This additional authentic transcript context makes the archive article long
enough to distinguish a real document body from a generic error or challenge page.</p>
</article></body></html>""".encode()


def _legacy_mtf_document_html(document_number: str = "103384") -> bytes:
    return f"""<!doctype html><html><head>
<title>Legacy archive speech | Margaret Thatcher Foundation</title>
<link rel="canonical" href="https://www.margaretthatcher.org/document/{document_number}">
</head><body>
<div id="documentheader"><h1>Legacy archive speech</h1></div>
<div id="docauthor">Margaret Thatcher</div>
<div id="docdate">7 June 1979</div>
<div id="documentbody">Margaret Thatcher delivered this legacy transcript.
{QUOTATION}. Further transcript context is retained here so that the structural
validator can distinguish the substantive archive document from generic chrome.</div>
</body></html>""".encode()


@pytest.mark.parametrize(
    ("url", "body_factory", "selector_kind", "expected_title"),
    [
        (
            "https://www.margaretthatcher.org/document/107352",
            _modern_mtf_document_html,
            "modern",
            "Speech to Conservative Party Conference",
        ),
        (
            "https://www.margaretthatcher.org/document/103384",
            _legacy_mtf_document_html,
            "legacy",
            "Legacy archive speech",
        ),
    ],
)
def test_fetch_v6_realistic_modern_and_legacy_mtf_documents_validate_and_extract(
    url: str,
    body_factory,
    selector_kind: str,
    expected_title: str,
) -> None:
    body = body_factory()
    validation = research.inspect_mtf_document(url, "text/html", body)

    assert validation["valid"] is True
    assert validation["selector_kind"] == selector_kind
    assert validation["title"] == expected_title
    assert validation["author"] == "Margaret Thatcher"
    assert validation["canonical_url"] == url
    assert len(validation["article_text_sha256"]) == 64

    extraction = research.extract_page_text(
        {
            "status": "fetched",
            "content_type": "text/html",
            "body": body,
            "mtf_document_validation": validation,
        }
    )
    assert extraction["status"] == "extracted"
    assert QUOTATION in extraction["text"]
    assert "NAVIGATION CHROME" not in extraction["text"]
    assert extraction["metadata"]["mtf_document_validated"] is True
    assert extraction["metadata"]["mtf_document_number"] == url.rsplit("/", 1)[-1]

    match = research.extract_supporting_passage(_target(), extraction["text"])
    classified = research.classify_candidate(
        _target(), url=url, extraction=extraction, match=match
    )
    assert match["match_type"] == "exact_quotation"
    assert classified["classification"] == "strong_primary_evidence"
    assert classified["accepted_as_evidence"] is True


def test_fetch_v6_generic_or_canonical_mismatch_is_not_a_valid_mtf_document() -> None:
    url = "https://www.margaretthatcher.org/document/107352"
    generic = b"""<html><head>
<title>Margaret Thatcher Foundation</title>
<link rel="canonical" href="https://www.margaretthatcher.org/document/107352">
</head><body><h1>Archive</h1><p>Generic site page, not a transcript.</p></body></html>"""
    mismatch = _modern_mtf_document_html("107353")

    generic_result = research.inspect_mtf_document(url, "text/html", generic)
    mismatch_result = research.inspect_mtf_document(url, "text/html", mismatch)
    non_html_result = research.inspect_mtf_document(url, "text/plain", QUOTATION.encode())

    assert generic_result == {
        "valid": False,
        "status": "invalid_official_document",
        "reason": "official_document_lacks_title_or_transcript",
        "document_number": "107352",
    }
    assert mismatch_result["valid"] is False
    assert mismatch_result["status"] == "document_identity_mismatch"
    assert mismatch_result["declared_canonical_url"].endswith("/107353")
    assert non_html_result["valid"] is False
    assert non_html_result["reason"] == "official_document_is_not_html"


def test_exact_and_recorded_variant_passage_extraction() -> None:
    target = _target()
    exact = research.extract_supporting_passage(
        target, f"Margaret Thatcher said: {QUOTATION}. The audience applauded."
    )
    assert exact["match_type"] == "exact_quotation"
    assert exact["supporting_passage"] == QUOTATION
    assert exact["wording_similarity"] == 1.0

    variant = research.extract_supporting_passage(
        target, f"The transcript records: {VARIANT}."
    )
    assert variant["match_type"] == "recorded_variant"
    assert variant["supporting_passage"] == VARIANT
    assert 0.8 < variant["wording_similarity"] < 1.0


def test_separate_primary_clauses_are_recorded_as_assembled_not_contiguous() -> None:
    quotation = (
        "First the government must preserve the liberty of every citizen. "
        "Then economic power must remain accountable to the people."
    )
    target = {
        "quotation_text": quotation,
        "recorded_variants": [],
        "distinctive_fragments": research.distinctive_fragments(quotation),
        "deterministic_clauses": research.deterministic_quote_clauses(quotation),
    }
    page = (
        "Margaret Thatcher said: First the government must preserve the liberty of every citizen. "
        + ("Intervening source material. " * 40)
        + "Then economic power must remain accountable to the people."
    )
    match = research.extract_supporting_passage(target, page)
    assert match["match_type"] == "assembled_clauses"
    assert len(match["component_spans"]) == 2
    assert match["assembled_gap_characters"][0] > 100


def test_ellipsis_composite_preserves_short_secondary_clause() -> None:
    quotation = (
        "There are still people in my party who believe in consensus politics. "
        "I regard them as Quislings, as traitors... I mean it."
    )
    clauses = research.deterministic_quote_clauses(quotation)
    assert clauses[-1] == "I mean it."
    target = {
        "quotation_text": quotation,
        "recorded_variants": [],
        "distinctive_fragments": research.distinctive_fragments(quotation),
        "deterministic_clauses": clauses,
    }
    page = " Historical interruption. ".join(clauses)
    match = research.extract_supporting_passage(target, page)
    assert match["match_type"] == "assembled_clauses"
    assert match["matched_clauses"][-1] == "I mean it."


def test_exact_primary_outcome_outranks_an_earlier_primary_variant() -> None:
    outcome, _rationale = research.outcome_for_candidates(
        [
            {
                "accepted_as_evidence": True,
                "classification": "strong_primary_evidence",
                "match_type": "recorded_variant",
            },
            {
                "accepted_as_evidence": True,
                "classification": "strong_primary_evidence",
                "match_type": "exact_quotation",
            },
        ],
        search_complete=False,
    )
    assert outcome == "exact_primary_wording_found"


def _classification(
    *,
    url: str,
    context: str,
    match_type: str = "exact_quotation",
    metadata: dict[str, str] | None = None,
) -> dict[str, object]:
    match = {
        "match_type": match_type,
        "supporting_passage": QUOTATION,
        "surrounding_context": context,
    }
    extraction = {
        "status": "extracted",
        "text": context,
        "metadata": metadata or {},
    }
    return research.classify_candidate(
        _target(), url=url, extraction=extraction, match=match
    )


def test_primary_secondary_recollection_aggregator_and_circular_classification() -> None:
    primary_url = "https://www.margaretthatcher.org/document/107352"
    primary = _classification(
        url=primary_url,
        context=f"Speech by Margaret Thatcher, 14 October 1988. {QUOTATION}.",
        metadata={
            "mtf_document_validated": True,
            "mtf_document_number": "107352",
            "mtf_canonical_url": primary_url,
        },
    )
    assert primary["classification"] == "strong_primary_evidence"
    assert primary["accepted_as_evidence"] is True
    assert primary["stable_locator"] == "Document 107352"

    secondary = _classification(
        url="https://www.bbc.co.uk/history/article",
        context=f"Margaret Thatcher said {QUOTATION}.",
    )
    assert secondary["classification"] == "reliable_secondary_evidence"
    assert secondary["accepted_as_evidence"] is True

    recollection = _classification(
        url="https://www.bl.uk/collection-items/ministerial-memoir",
        context=f"In his memoir he recalled Margaret Thatcher saying {QUOTATION}.",
        metadata={"title": "A ministerial memoir", "publisher": "British Library"},
    )
    assert recollection["classification"] == "secondary_recollection"
    assert recollection["accepted_as_evidence"] is True

    unverified_recollection = _classification(
        url="https://example.org/memoir",
        context=f"In his memoir he recalled Margaret Thatcher saying {QUOTATION}.",
    )
    assert unverified_recollection["classification"] == "discovery_only"
    assert unverified_recollection["accepted_as_evidence"] is False

    aggregator = _classification(
        url="https://www.brainyquote.com/quotes/margaret_thatcher_1",
        context=f"Margaret Thatcher: {QUOTATION}.",
    )
    assert aggregator["classification"] == "quotation_aggregation"
    assert aggregator["accepted_as_evidence"] is False

    aggregator_with_contradiction_language = _classification(
        url="https://www.brainyquote.com/quotes/margaret_thatcher_1",
        context=f"Margaret Thatcher: {QUOTATION}. This is wrongly attributed.",
    )
    assert aggregator_with_contradiction_language["classification"] == "quotation_aggregation"
    assert aggregator_with_contradiction_language["accepted_as_evidence"] is False

    circular = _classification(
        url="https://example.org/article",
        context=f"Margaret Thatcher: {QUOTATION}. Source: Wikiquote.",
    )
    assert circular["classification"] == "circular_attribution"
    assert circular["accepted_as_evidence"] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://hoopoequotes.com/quotes/margaret-thatcher/liberty",
        "https://meaningin.com/margaret-thatcher-quotes/liberty",
        "https://mindzip.net/fl/@margaret-thatcher/quotes/liberty",
        "https://quotationspage.com/quote/12345.html",
        "https://internetpoem.com/margaret-thatcher/liberty-quote",
        "https://quotegeek.com/people/margaret-thatcher/liberty",
        "https://imgflip.com/i/1xzkm4",
        "https://quote-coyote.com/quotes/authors/t/margaret-thatcher/liberty.html",
    ],
)
def test_known_quotation_repeaters_are_quotation_aggregations(url: str) -> None:
    classified = _classification(
        url=url,
        context=f"Margaret Thatcher: {QUOTATION}.",
    )

    assert classified["classification"] == "quotation_aggregation"
    assert classified["accepted_as_evidence"] is False
    assert classified["decision_reason"] == (
        "quotation aggregation is discovery-only and cannot establish attribution"
    )


def test_runner_initialisation_reconciles_cached_aggregation_without_io_or_billing(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    budget = research.SearchBudget(price_per_1000=Decimal("5"))
    state = research.initialise_run_state(
        manifest, status="running", budget=budget
    )
    candidate_id = "c" * 64
    candidate = {
        "candidate_id": candidate_id,
        "canonical_url": (
            "https://quotegeek.com/people/margaret-thatcher/constitutions"
        ),
        "fetch_status": "fetched",
        "classification": "discovery_only",
        "accepted_as_evidence": False,
        "decision_reason": "wording appears but source identity is uncertain",
        "classification_policy_version": "historical-context-evidence-classification-v3",
    }
    state["cases"]["quote-1"]["candidate_sources"] = [candidate]
    state["ledger_records"] = [
        {
            "result_record_id": "d" * 64,
            "candidate_id": candidate_id,
            "canonical_url": candidate["canonical_url"],
            "classification": "discovery_only",
            "decision": "rejected_or_discovery_candidate",
            "decision_reason": "wording appears but source identity is uncertain",
        },
        {
            "result_record_id": "e" * 64,
            "candidate_id": "f" * 64,
            "canonical_url": "https://example.org/unrelated",
            "classification": "discovery_only",
            "decision": "rejected_or_discovery_candidate",
            "decision_reason": "unrelated fixture row",
        },
    ]
    backend = FakeBackend()
    fetcher = StaticFetcher()
    original_accounting = budget.as_dict()

    runner = _runner(
        tmp_path,
        manifest=manifest,
        backend=backend,
        state=state,
        budget=budget,
        fetcher=fetcher,
    )

    reconciled = runner.state["cases"]["quote-1"]["candidate_sources"][0]
    assert reconciled["candidate_id"] == candidate_id
    assert reconciled["classification"] == "quotation_aggregation"
    assert reconciled["accepted_as_evidence"] is False
    assert reconciled["decision_reason"] == (
        "quotation aggregation is discovery-only and cannot establish attribution"
    )
    assert reconciled["classification_policy_version"] == (
        research.CLASSIFICATION_POLICY_VERSION
    )
    matching_row, unrelated_row = runner.state["ledger_records"]
    assert matching_row["candidate_id"] == candidate_id
    assert matching_row["classification"] == "quotation_aggregation"
    assert matching_row["decision"] == "rejected_or_discovery_candidate"
    assert matching_row["decision_reason"] == reconciled["decision_reason"]
    assert unrelated_row["classification"] == "discovery_only"
    assert unrelated_row["decision_reason"] == "unrelated fixture row"
    assert backend.calls == []
    assert fetcher.calls == []
    assert budget.as_dict() == original_accounting
    assert not (tmp_path / "run_state.json").exists()
    assert not (tmp_path / "results.jsonl").exists()


@pytest.mark.parametrize(
    ("url", "expected_classification"),
    [
        (
            "https://books.google.com/books?id=THATCHER1&pg=PA123",
            "strong_primary_evidence",
        ),
        ("https://books.google.com/books?id=THATCHER1", "discovery_only"),
        ("https://books.google.com/books", "discovery_only"),
        (
            "https://archive.org/details/thatcher-volume/page/n123/mode/2up",
            "strong_primary_evidence",
        ),
        ("https://archive.org/details/thatcher-volume", "discovery_only"),
    ],
)
def test_book_archive_primary_classification_requires_a_precise_page_locator(
    url: str, expected_classification: str
) -> None:
    classified = _classification(
        url=url,
        context=f"Margaret Thatcher wrote: {QUOTATION}.",
        metadata={
            "author": "Margaret Thatcher",
            "title": "A Thatcher-authored volume",
            "publisher": "HarperCollins",
        },
    )
    assert classified["classification"] == expected_classification
    assert classified["accepted_as_evidence"] is (
        expected_classification == "strong_primary_evidence"
    )


@pytest.mark.parametrize(
    ("author", "expected_classification"),
    [
        ("Denis Thatcher", "discovery_only"),
        ("Carol Thatcher", "discovery_only"),
        ("Mark Thatcher", "discovery_only"),
        ("Margaret Thatcher", "strong_primary_evidence"),
        ("Lady Thatcher", "strong_primary_evidence"),
        ("Baroness Thatcher", "strong_primary_evidence"),
    ],
)
def test_precise_publication_primary_status_requires_margaret_thatcher_authorship(
    author: str, expected_classification: str
) -> None:
    classified = _classification(
        url="https://books.google.com/books?id=THATCHER1&pg=PA123",
        context=f"Margaret Thatcher wrote: {QUOTATION}.",
        metadata={
            "author": author,
            "title": "A publication in the Thatcher family catalogue",
        },
    )
    assert classified["classification"] == expected_classification
    assert classified["accepted_as_evidence"] is (
        expected_classification == "strong_primary_evidence"
    )


def test_assembled_clauses_in_precise_thatcher_book_are_contradictory_not_exact() -> None:
    classified = _classification(
        url="https://books.google.com/books?id=THATCHER1&pg=PA123",
        context=(
            "Margaret Thatcher wrote the stored clauses in separate passages; "
            f"{QUOTATION}."
        ),
        match_type="assembled_clauses",
        metadata={
            "author": "Margaret Thatcher",
            "title": "A Thatcher-authored volume",
        },
    )
    assert classified["classification"] == "contradictory_evidence"
    assert classified["accepted_as_evidence"] is True
    assert "composite_wording_diagnosis" in classified["evidence_roles"]


def test_search_snippet_is_discovery_only_and_never_becomes_evidence(tmp_path: Path) -> None:
    backend = FakeBackend(
        [
            {
                "title": "Unverified result",
                "result_url": "https://example.org/source",
                "snippet": f"Margaret Thatcher said {QUOTATION}",
            }
        ]
    )
    runner = _runner(tmp_path, backend=backend, fetcher=StaticFetcher())
    query = _manifest()["targets"][0]["queries"][0]
    runner._process_query("quote-1", query)

    ledger = runner.state["ledger_records"]
    assert ledger[0]["snippet_is_evidence"] is False
    candidate = runner.state["cases"]["quote-1"]["candidate_sources"][0]
    assert candidate["classification"] == "no_support"
    assert candidate["accepted_as_evidence"] is False
    assert candidate["supporting_passage"] == ""


def test_same_cached_page_is_evaluated_independently_for_each_quotation(
    tmp_path: Path,
) -> None:
    second_text = (
        "We must choose the difficult path today to preserve freedom for tomorrow"
    )
    second_target = {
        "quote_id": "quote-2",
        "quotation_text": second_text,
        "target_origins": ["final_unresolved_status"],
        "gate_disposition": None,
        "recorded_variants": [],
        "distinctive_fragments": research.distinctive_fragments(second_text),
    }
    manifest = _manifest_for_targets([_target(), second_target])
    source_url = "https://www.margaretthatcher.org/document/107352"
    page = (
        f"Speech by Margaret Thatcher. {QUOTATION}. Later Margaret Thatcher said: "
        f"{second_text}."
    ).encode()

    class CachedPageFetcher:
        def __init__(self) -> None:
            self.budget = research.PageFetchBudget()
            self.calls: list[str] = []

        def fetch(self, url: str) -> dict[str, object]:
            self.calls.append(url)
            return {
                "status": "fetched",
                "http_status": 200,
                "content_type": "text/plain",
                "final_url": url,
                "body": page,
                "body_sha256": research.sha256_bytes(page),
                "redirect_chain": [url],
                "cache_hit": len(self.calls) > 1,
            }

    backend = FakeBackend(
        [{"title": "Speech", "result_url": source_url, "snippet": "lead"}]
    )
    fetcher = CachedPageFetcher()
    budget = research.SearchBudget(price_per_1000=Decimal("5"))
    state = research.initialise_run_state(manifest, status="ready", budget=budget)
    runner = _runner(
        tmp_path,
        manifest=manifest,
        backend=backend,
        state=state,
        budget=budget,
        fetcher=fetcher,
    )
    for target in manifest["targets"]:
        runner._process_query(target["quote_id"], target["queries"][0])

    first_candidate = state["cases"]["quote-1"]["candidate_sources"][0]
    second_candidate = state["cases"]["quote-2"]["candidate_sources"][0]
    assert first_candidate["supporting_passage"] == QUOTATION
    assert second_candidate["supporting_passage"] == second_text
    assert first_candidate["fetch_cache_hit"] is False
    assert second_candidate["fetch_cache_hit"] is True
    assert fetcher.calls == [source_url, source_url]


def test_mid_query_resume_processes_remaining_results_without_duplicate_ledger(
    tmp_path: Path,
) -> None:
    results = [
        {
            "title": f"Result {index}",
            "result_url": f"https://example.org/source-{index}",
            "snippet": "lead",
        }
        for index in range(1, 4)
    ]

    class EchoFetcher:
        def __init__(self, *, interrupt_on: int | None = None) -> None:
            self.budget = research.PageFetchBudget()
            self.calls: list[str] = []
            self.interrupt_on = interrupt_on

        def fetch(self, url: str) -> dict[str, object]:
            self.calls.append(url)
            if self.interrupt_on == len(self.calls):
                raise KeyboardInterrupt("fixture interruption")
            body = b"No evidence is present on this fixture page."
            return {
                "status": "fetched",
                "http_status": 200,
                "content_type": "text/plain",
                "final_url": url,
                "body": body,
                "body_sha256": research.sha256_bytes(body),
                "redirect_chain": [url],
                "cache_hit": False,
            }

    backend = FakeBackend(results)
    first_fetcher = EchoFetcher(interrupt_on=2)
    runner = _runner(tmp_path, backend=backend, fetcher=first_fetcher)
    query = _manifest()["targets"][0]["queries"][0]
    with pytest.raises(KeyboardInterrupt, match="fixture interruption"):
        runner._process_query("quote-1", query)

    persisted = research.load_run_state(tmp_path / "run_state.json", _manifest())
    assert len(persisted["ledger_records"]) == 3
    assert len(persisted["cases"]["quote-1"]["query_runs"]["1"]["processed_record_ids"]) == 1

    resumed_budget = research.SearchBudget.from_dict(persisted["search_accounting"])
    resumed_backend = FakeBackend()
    resumed_fetcher = EchoFetcher()
    resumed = _runner(
        tmp_path,
        backend=resumed_backend,
        state=persisted,
        budget=resumed_budget,
        cache=runner.cache,
        fetcher=resumed_fetcher,
    )
    resumed._process_query("quote-1", query)

    ledger_ids = [row["result_record_id"] for row in persisted["ledger_records"]]
    assert len(ledger_ids) == len(set(ledger_ids)) == 3
    query_run = persisted["cases"]["quote-1"]["query_runs"]["1"]
    assert len(query_run["processed_record_ids"]) == 3
    assert len(persisted["cases"]["quote-1"]["candidate_sources"]) == 3
    assert len(persisted["cases"]["quote-1"]["queries_attempted"]) == 1
    assert resumed_backend.calls == []
    assert resumed_fetcher.calls == [
        "https://example.org/source-2",
        "https://example.org/source-3",
    ]


def test_bots_own_x_account_is_excluded_from_results(tmp_path: Path) -> None:
    own_post = "https://x.com/MrsMThatcher/status/123456789"
    backend = FakeBackend(
        [{"title": "Bot post", "result_url": own_post, "snippet": QUOTATION}]
    )
    fetcher = StaticFetcher()
    runner = _runner(tmp_path, backend=backend, fetcher=fetcher)
    query = _manifest()["targets"][0]["queries"][0]
    runner._process_query("quote-1", query)

    row = runner.state["ledger_records"][0]
    assert row["decision"] == "excluded_bot_x_account"
    assert row["fetch_status"] == "not_selected_for_fetch"
    assert fetcher.calls == []
    assert runner.state["cases"]["quote-1"]["candidate_sources"] == []


def test_automatically_inferred_primary_variant_does_not_stop_future_search(
    tmp_path: Path,
) -> None:
    inferred = QUOTATION.replace("economic freedom is preserved", "financial freedom is preserved")
    body = f"Speech by Margaret Thatcher. {inferred}.".encode()
    source_url = "https://www.margaretthatcher.org/document/107352"
    fetcher = StaticFetcher(
        {
            "status": "fetched",
            "http_status": 200,
            "content_type": "text/plain",
            "final_url": source_url,
            "body": body,
            "body_sha256": research.sha256_bytes(body),
            "redirect_chain": [source_url],
            "cache_hit": False,
        }
    )
    backend = FakeBackend(
        [{"title": "Speech", "result_url": source_url, "snippet": "discovery lead"}]
    )
    runner = _runner(tmp_path, backend=backend, fetcher=fetcher)
    first_query = _manifest()["targets"][0]["queries"][0]
    runner._process_query("quote-1", first_query)

    case = runner.state["cases"]["quote-1"]
    assert case["candidate_sources"][0]["match_type"] == "near_exact_variant"
    assert not str(case["status"]).startswith("complete_decisive")
    assert len(case["queries_attempted"]) == 1
    assert len(_manifest()["targets"][0]["queries"]) > 1


def test_exact_primary_pauses_then_complete_reject_review_resumes_without_rebilling(
    tmp_path: Path,
) -> None:
    source_url = "https://www.margaretthatcher.org/document/107352"
    body = _modern_mtf_document_html("107352")
    validation = research.inspect_mtf_document(source_url, "text/html", body)
    assert validation["valid"] is True
    fetcher = StaticFetcher(
        {
            "status": "fetched",
            "http_status": 200,
            "content_type": "text/html",
            "final_url": source_url,
            "body": body,
            "body_sha256": research.sha256_bytes(body),
            "redirect_chain": [source_url],
            "cache_hit": False,
            "mtf_document_validation": validation,
        }
    )
    first_backend = FakeBackend(
        [{"title": "Speech", "result_url": source_url, "snippet": "lead"}]
    )
    first_budget = research.SearchBudget(price_per_1000=Decimal("5"))
    first = _runner(
        tmp_path,
        backend=first_backend,
        budget=first_budget,
        fetcher=fetcher,
    )
    paused = first.run()
    case = paused["cases"]["quote-1"]
    assert paused["run_status"] == "awaiting_codex_review"
    assert case["status"] == "awaiting_codex_review"
    assert len(case["queries_attempted"]) == 1
    assert case["queries_attempted"][0]["scheduled_stage"] == 1
    assert len(first_backend.calls) == 1
    assert first_budget.unique_queries_started == first_budget.requests_started == 1
    candidate_id = case["provisional_candidate_ids"][0]

    reject_review = {
        candidate_id: {
            "decision": "reject",
            "rationale": "Manual inspection rejected the provisional speaker attribution.",
        }
    }
    resumed_budget = research.SearchBudget.from_dict(paused["search_accounting"])
    resumed_backend = FakeBackend([])
    resumed = _runner(
        tmp_path,
        backend=resumed_backend,
        state=paused,
        budget=resumed_budget,
        cache=first.cache,
        fetcher=StaticFetcher(),
        codex_reviews=reject_review,
    )
    assert resumed.state["cases"]["quote-1"]["status"] == "pending"
    completed = resumed.run()
    completed_case = completed["cases"]["quote-1"]

    assert completed["run_status"] == "complete"
    assert completed_case["status"] == "complete_search"
    assert [item["scheduled_stage"] for item in completed_case["queries_attempted"]] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert len(first_backend.calls) == 1
    assert len(resumed_backend.calls) == 4
    assert resumed_budget.unique_queries_started == resumed_budget.requests_started == 5
    assert resumed_budget.requests_completed == 5
    assert candidate_id in completed_case["provisional_rejected_candidate_ids"]


def test_sequential_provisional_rejections_retain_the_union_of_candidate_ids(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    budget = research.SearchBudget(price_per_1000=Decimal("5"))
    state = research.initialise_run_state(manifest, status="awaiting_codex_review", budget=budget)
    case = state["cases"]["quote-1"]
    case.update(
        {
            "status": "awaiting_codex_review",
            "provisional_candidate_ids": ["candidate-A"],
        }
    )
    reject_a = {
        "candidate-A": {
            "decision": "reject",
            "rationale": "Candidate A failed manual speaker verification.",
        }
    }
    first = _runner(
        tmp_path,
        manifest=manifest,
        state=state,
        budget=budget,
        codex_reviews=reject_a,
    )
    assert first.state["cases"]["quote-1"]["provisional_rejected_candidate_ids"] == [
        "candidate-A"
    ]

    case = first.state["cases"]["quote-1"]
    case.update(
        {
            "status": "awaiting_codex_review",
            "provisional_candidate_ids": ["candidate-B"],
        }
    )
    reject_both = {
        **reject_a,
        "candidate-B": {
            "decision": "reject",
            "rationale": "Candidate B failed manual source verification.",
        },
    }
    second = _runner(
        tmp_path,
        manifest=manifest,
        state=state,
        budget=budget,
        codex_reviews=reject_both,
    )
    assert second.state["cases"]["quote-1"]["provisional_rejected_candidate_ids"] == [
        "candidate-A",
        "candidate-B",
    ]


def test_ready_status_requires_explicit_completed_codex_evidence_review() -> None:
    evidence = {
        "counts": {"recommended_outcomes": {"exact_primary_wording_found": 1}},
        "quotations": [
            {
                "recommended_outcome": "exact_primary_wording_found",
                "codex_evidence_review": {"status": "pending_manual_codex_review"},
            }
        ],
    }
    state = {"run_status": "complete"}
    assert research.final_report_status(evidence, state) == "RESEARCH TOOL INCOMPLETE OR UNSAFE"

    reviewed = copy.deepcopy(evidence)
    reviewed["quotations"][0]["codex_evidence_review"] = {
        "status": "completed_manual_codex_review",
        "source_identity_verified": True,
        "speaker_verified": True,
        "wording_and_semantics_verified": True,
        "reviewed_outcome": "exact_primary_wording_found",
    }
    assert research.final_report_status(reviewed, state) == (
        "AUTOMATED DISCOVERY-ENGINE RESEARCH COMPLETE — "
        "READY FOR EVIDENCE REMEDIATION REVIEW"
    )


def test_exact_discovery_terminal_statuses_and_no_evidence_outcome() -> None:
    assert research.final_report_status({}, {
        "run_status": "discovery_engine_authentication_or_configuration_failed",
    }) == "DISCOVERY ENGINE AUTHENTICATION OR CONFIGURATION FAILED"
    assert research.final_report_status({}, {
        "run_status": "request_cap_reached",
    }) == "RESEARCH INCOMPLETE — REQUEST CAP REACHED"
    assert research.final_report_status({}, {
        "run_status": "search_backend_error",
    }) == "RESEARCH TOOL INCOMPLETE OR UNSAFE"
    no_evidence = {
        "counts": {"recommended_outcomes": {"no_reliable_evidence_found": 1}},
        "quotations": [],
    }
    assert research.final_report_status(no_evidence, {"run_status": "complete"}) == (
        "AUTOMATED DISCOVERY-ENGINE RESEARCH COMPLETE — "
        "NO NEW RELIABLE EVIDENCE FOUND"
    )
    outcome, rationale = research.outcome_for_candidates(
        [], search_complete=True
    )
    assert outcome == "no_reliable_evidence_found"
    assert rationale == (
        "No reliable evidence found within the configured Discovery Engine website collection."
    )


def test_review_rejection_removes_automatic_primary_outcome() -> None:
    manifest = _manifest()
    state = research.initialise_run_state(manifest, status="complete")
    case = state["cases"]["quote-1"]
    case.update({
        "status": "complete_search",
        "recommended_outcome": "exact_primary_wording_found",
        "recommendation_rationale": "automatic",
        "candidate_sources": [{
            "candidate_id": "candidate-1",
            "classification": "strong_primary_evidence",
            "match_type": "exact_quotation",
            "accepted_as_evidence": True,
        }],
    })
    evidence = research.advisory_evidence_document(
        manifest,
        state,
        {"candidate-1": {"decision": "reject", "rationale": "Speaker block does not support attribution."}},
    )
    quotation = evidence["quotations"][0]
    assert quotation["automatic_recommended_outcome"] == "exact_primary_wording_found"
    assert quotation["recommended_outcome"] == "no_reliable_evidence_found"
    assert quotation["accepted_candidates"] == []
    assert quotation["codex_evidence_review"]["status"] == (
        "completed_manual_codex_review_no_accepted_resolution"
    )


def test_manifest_bound_quotation_review_records_manual_non_resolution_triage(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    state = research.initialise_run_state(manifest, status="complete")
    state["run_status"] = "complete"
    state["cases"]["quote-1"].update({
        "status": "complete_search_with_access_errors",
        "recommended_outcome": "search_incomplete_due_to_access",
        "recommendation_rationale": "automatic access result",
        "candidate_sources": [{
            "candidate_id": "partial-secondary",
            "classification": "similar_sentiment_only",
            "match_type": "distinctive_fragment_only",
            "evidence_roles": ["source_event"],
            "accepted_as_evidence": False,
        }],
    })
    review_path = tmp_path / "review.json"
    research.atomic_write_json(review_path, {
        "manifest_hash": manifest["manifest_hash"],
        "candidate_reviews": {
            "partial-secondary": {
                "decision": "reject",
                "rationale": "A reliable source contains only part of the quotation.",
                "reviewed_classification": "contemporary_report",
                "reviewed_evidence_roles": ["attribution_support"],
            },
        },
        "quotation_reviews": {
            "quote-1": {
                "recommended_outcome": "promising_but_insufficient",
                "rationale": "The partial report is a lead, not a full resolution.",
            },
        },
    })

    reviews = research.load_codex_reviews(review_path, manifest)
    quotation = research.advisory_evidence_document(manifest, state, reviews)[
        "quotations"
    ][0]

    assert quotation["automatic_recommended_outcome"] == (
        "search_incomplete_due_to_access"
    )
    assert quotation["recommended_outcome"] == "promising_but_insufficient"
    assert quotation["codex_quotation_review"]["rationale"].startswith(
        "The partial report"
    )
    candidate = quotation["candidate_sources"][0]
    assert candidate["automatic_classification"] == "similar_sentiment_only"
    assert candidate["effective_classification"] == "contemporary_report"
    assert candidate["automatic_evidence_roles"] == ["source_event"]
    assert candidate["effective_evidence_roles"] == ["attribution_support"]


@pytest.mark.parametrize(
    ("quote_id", "outcome"),
    [
        ("quote-1", "exact_primary_wording_found"),
        ("outside-manifest", "promising_but_insufficient"),
    ],
)
def test_quotation_review_cannot_promote_or_escape_manifest(
    tmp_path: Path, quote_id: str, outcome: str,
) -> None:
    manifest = _manifest()
    review_path = tmp_path / "review.json"
    research.atomic_write_json(review_path, {
        "manifest_hash": manifest["manifest_hash"],
        "candidate_reviews": {},
        "quotation_reviews": {
            quote_id: {
                "recommended_outcome": outcome,
                "rationale": "Manual triage fixture.",
            },
        },
    })

    with pytest.raises(research.ResearchError):
        research.load_codex_reviews(review_path, manifest)


def test_pending_secondary_positive_keeps_terminal_status_incomplete() -> None:
    manifest = _manifest()
    state = research.initialise_run_state(manifest, status="complete")
    state["run_status"] = "complete"
    state["cases"]["quote-1"].update(
        {
            "status": "complete_search",
            "recommended_outcome": "promising_but_insufficient",
            "recommendation_rationale": "Automatic secondary evidence awaits review.",
            "candidate_sources": [
                {
                    "candidate_id": "secondary-positive",
                    "accepted_as_evidence": True,
                    "classification": "reliable_secondary_evidence",
                    "match_type": "exact_quotation",
                }
            ],
        }
    )
    evidence = research.advisory_evidence_document(manifest, state)
    quotation = evidence["quotations"][0]
    assert quotation["recommended_outcome"] == "promising_but_insufficient"
    assert quotation["codex_evidence_review"]["status"] == "pending_manual_codex_review"
    assert research.final_report_status(evidence, state) == "RESEARCH TOOL INCOMPLETE OR UNSAFE"


def test_every_automatic_positive_must_be_reviewed_and_only_accepted_reviews_set_outcome() -> None:
    manifest = _manifest()
    state = research.initialise_run_state(manifest, status="complete")
    state["run_status"] = "complete"
    state["cases"]["quote-1"].update(
        {
            "status": "complete_search",
            "recommended_outcome": "contradictory_or_misattributed",
            "recommendation_rationale": "automatic fixture result",
            "candidate_sources": [
                {
                    "candidate_id": "exact-primary",
                    "accepted_as_evidence": True,
                    "classification": "strong_primary_evidence",
                    "match_type": "exact_quotation",
                },
                {
                    "candidate_id": "contradiction",
                    "accepted_as_evidence": True,
                    "classification": "contradictory_evidence",
                    "match_type": "exact_quotation",
                },
            ],
        }
    )
    required_true = {
        "actual_passage_verified": True,
        "source_identity_verified": True,
        "speaker_verified": True,
        "wording_and_semantics_verified": True,
        "actor_direction_polarity_verified": True,
        "dates_and_quantities_verified": True,
        "match_type_verified": True,
    }
    accept_exact_only = {
        "exact-primary": {
            "decision": "accept",
            "rationale": "The exact primary passage and source identity were checked.",
            **required_true,
        }
    }
    partial = research.advisory_evidence_document(manifest, state, accept_exact_only)
    quotation = partial["quotations"][0]
    assert quotation["codex_evidence_review"]["status"] == "pending_manual_codex_review"
    assert research.final_report_status(partial, state) == "RESEARCH TOOL INCOMPLETE OR UNSAFE"

    accept_contradiction = {
        "exact-primary": {
            "decision": "reject",
            "rationale": "The apparent exact source failed manual source verification.",
        },
        "contradiction": {
            "decision": "accept",
            "rationale": "The contradictory attribution and passage were checked.",
            **required_true,
        },
    }
    contradiction = research.advisory_evidence_document(
        manifest, state, accept_contradiction
    )["quotations"][0]
    assert contradiction["codex_evidence_review"]["status"] == (
        "completed_manual_codex_review"
    )
    assert contradiction["recommended_outcome"] == "contradictory_or_misattributed"
    assert [item["candidate_id"] for item in contradiction["accepted_candidates"]] == [
        "contradiction"
    ]

    accept_primary = {
        "exact-primary": {
            "decision": "accept",
            "rationale": "The exact primary passage and source identity were checked.",
            **required_true,
        },
        "contradiction": {
            "decision": "reject",
            "rationale": "The apparent contradiction did not survive manual review.",
        },
    }
    exact = research.advisory_evidence_document(manifest, state, accept_primary)[
        "quotations"
    ][0]
    assert exact["recommended_outcome"] == "exact_primary_wording_found"
    assert [item["candidate_id"] for item in exact["accepted_candidates"]] == [
        "exact-primary"
    ]


def test_live_backend_requires_explicit_execution_switch(tmp_path: Path, capsys) -> None:
    arguments = [
        "run",
        "--manifest",
        str(tmp_path / "manifest.json"),
        "--state",
        str(tmp_path / "state.json"),
        "--ledger",
        str(tmp_path / "ledger.jsonl"),
        "--evidence",
        str(tmp_path / "evidence.json"),
        "--report",
        str(tmp_path / "report.md"),
        "--cache",
        str(tmp_path / "cache"),
    ]
    assert research.main(arguments) == 2
    assert "live search requires the explicit --execute-search option" in capsys.readouterr().out
    assert not any(tmp_path.iterdir())


def test_unconfigured_resume_preserves_prior_accounting_and_case_outcome(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest()
    budget = research.SearchBudget(
        price_per_1000=Decimal("5"),
        unique_queries_started=2,
        requests_started=2,
        requests_completed=2,
    )
    state = research.initialise_run_state(
        manifest,
        status="search_backend_error",
        backend_config={"backend": "fixture_search"},
        budget=budget,
    )
    case = state["cases"]["quote-1"]
    case.update(
        {
            "status": "incomplete_search",
            "recommended_outcome": "promising_but_insufficient",
            "recommendation_rationale": "A primary archive lead still requires access.",
        }
    )
    accounting_before = copy.deepcopy(state["search_accounting"])
    paths = {
        "state": tmp_path / "state.json",
        "ledger": tmp_path / "ledger.jsonl",
        "evidence": tmp_path / "evidence.json",
        "report": tmp_path / "report.md",
        "review": tmp_path / "review.json",
    }
    captured: dict[str, object] = {}

    def capture_outputs(_manifest, written_state, **_kwargs):
        captured["state"] = copy.deepcopy(written_state)
        return {}, ""

    monkeypatch.setattr(research, "write_advisory_outputs", capture_outputs)
    assert research._write_unconfigured(
        manifest,
        paths,
        {"configuration_error": "fixture backend unavailable"},
        root=tmp_path,
        existing_state=state,
    ) == 3

    persisted = json.loads(paths["state"].read_text())
    assert persisted["search_accounting"] == accounting_before
    assert persisted["prior_run_status_before_unconfigured_resume"] == "search_backend_error"
    assert persisted["cases"]["quote-1"]["recommended_outcome"] == (
        "promising_but_insufficient"
    )
    assert persisted["cases"]["quote-1"]["recommendation_rationale"] == (
        "A primary archive lead still requires access."
    )
    evidence = research.advisory_evidence_document(manifest, persisted)
    quotation = evidence["quotations"][0]
    assert quotation["recommended_outcome"] == "promising_but_insufficient"
    assert quotation["recommendation_rationale"] == (
        "A primary archive lead still requires access."
    )
    assert captured["state"]["search_accounting"] == accounting_before


def test_zero_request_unconfigured_state_can_bind_to_discovery_but_used_state_cannot(
    tmp_path: Path, monkeypatch
) -> None:
    manifest = _manifest()
    paths = {
        "manifest": tmp_path / "manifest.json",
        "state": tmp_path / "state.json",
        "ledger": tmp_path / "ledger.jsonl",
        "evidence": tmp_path / "evidence.json",
        "report": tmp_path / "report.md",
        "cache": tmp_path / "cache",
        "review": tmp_path / "review.json",
    }
    args = SimpleNamespace(
        **{name: str(path) for name, path in paths.items()},
        execute_search=True,
    )
    tokens = FakeAccessTokenProvider("fixture-command-adc-token")
    discovery_session = FakePostSession(FakeSearchResponse(200, {"results": []}))
    discovery_backend, discovery_budget, discovery_config = (
        research.configured_discovery_engine_backend(
            tmp_path,
            session=discovery_session,
            access_token_provider=tokens,
        )
    )
    assert isinstance(discovery_backend, research.GoogleDiscoveryEngineBackend)
    assert tokens.calls == 0

    def fake_outputs(_manifest, state, **_kwargs):
        return (
            {
                "counts": {
                    "recommended_outcomes": {"no_reliable_evidence_found": 1}
                },
                "quotations": [],
            },
            "",
        )

    monkeypatch.setattr(research, "write_advisory_outputs", fake_outputs)
    monkeypatch.setattr(
        research,
        "_manifest_for_command",
        lambda _paths, *, root: manifest,
    )
    monkeypatch.setattr(
        research,
        "_backend_or_unconfigured",
        lambda _root: (discovery_backend, discovery_budget, discovery_config),
    )

    assert research._write_unconfigured(
        manifest,
        paths,
        {
            "backend": "ordinary_structured_search",
            "configuration_error": "no backend was configured",
            "secrets_persisted": False,
        },
        root=tmp_path,
    ) == 3
    pristine = json.loads(paths["state"].read_text())
    assert pristine["search_accounting"]["requests_started"] == 0
    assert pristine["search_operations"] == []

    assert research._command_run_or_resume_unlocked(
        args, resume=True, root=tmp_path
    ) == 0
    rebound = json.loads(paths["state"].read_text())
    assert rebound["run_status"] == "complete"
    assert rebound["backend_config"]["backend"] == "google_discovery_engine"
    assert rebound["search_accounting"]["requests_started"] == 5
    assert rebound["search_accounting"]["search_cost_estimate_available"] is False
    assert len(discovery_session.calls) == 5
    assert tokens.calls == 5  # preflight token is consumed by the first request
    assert "fixture-command-adc-token" not in json.dumps(rebound, sort_keys=True)

    prior_budget = research.SearchBudget(
        price_per_1000=Decimal("5"),
        unique_queries_started=1,
        requests_started=1,
        requests_completed=1,
    )
    used_state = research.initialise_run_state(
        manifest,
        status="search_backend_error",
        backend_config={
            "backend": "brave_web_search_json",
            "backend_version": research.BRAVE_BACKEND_VERSION,
            "engine_identity_sha256": "c" * 64,
        },
        budget=prior_budget,
    )
    used_state["search_operations"] = [
        {"status": "completed", "operation_id": "used-request"}
    ]
    research.atomic_write_json(paths["state"], used_state, mode=0o600)
    before = paths["state"].read_bytes()
    with pytest.raises(research.ResearchError, match="backend differs"):
        research._command_run_or_resume_unlocked(args, resume=True, root=tmp_path)
    assert paths["state"].read_bytes() == before
    assert len(discovery_session.calls) == 5
    assert tokens.calls == 5


def test_output_paths_cannot_target_authoritative_research_packets(tmp_path: Path) -> None:
    protected = research.ROOT / research.RESEARCH_RELATIVE / "research_packets.json"
    before = research.file_sha256(protected)
    args = SimpleNamespace(
        manifest=str(protected),
        state=str(tmp_path / "state.json"),
        ledger=str(tmp_path / "ledger.jsonl"),
        evidence=str(tmp_path / "evidence.json"),
        report=str(tmp_path / "report.md"),
        cache=str(tmp_path / "cache"),
    )
    with pytest.raises(research.ResearchError, match="protected|authoritative|output path"):
        research._paths_from_args(args)
    assert research.file_sha256(protected) == before


def test_real_default_paths_accept_the_private_cache_symlink() -> None:
    args = research.build_argument_parser().parse_args(["plan"])

    paths = research._paths_from_args(args, project_root=research.ROOT)

    assert paths["state"] == research.DEFAULT_RUN_STATE.resolve()
    assert paths["cache"] == research.DEFAULT_CACHE.resolve()
    assert paths["manifest"] == research.DEFAULT_QUERY_MANIFEST.resolve()


def test_project_root_override_does_not_redirect_default_outputs(
    tmp_path: Path,
) -> None:
    authoritative_root = tmp_path / "authoritative"
    authoritative_root.mkdir()
    args = research.build_argument_parser().parse_args(["plan"])

    paths = research._paths_from_args(
        args,
        project_root=authoritative_root,
    )

    assert paths["manifest"] == research.DEFAULT_QUERY_MANIFEST.resolve()
    assert paths["state"] == research.DEFAULT_RUN_STATE.resolve()
    assert not paths["manifest"].is_relative_to(authoritative_root)
    assert not paths["state"].is_relative_to(authoritative_root)


def test_output_path_inside_overridden_project_root_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    authoritative_root = tmp_path / "authoritative"
    authoritative_root.mkdir()
    separate_temporary_root = tmp_path / "allowed-temporary"
    separate_temporary_root.mkdir()
    monkeypatch.setattr(
        research.tempfile,
        "gettempdir",
        lambda: str(separate_temporary_root),
    )
    args = SimpleNamespace(
        manifest=str(authoritative_root / "manifest.json"),
        state=str(tmp_path / "state.json"),
        ledger=str(tmp_path / "ledger.jsonl"),
        evidence=str(tmp_path / "evidence.json"),
        report=str(tmp_path / "report.md"),
        cache=str(tmp_path / "cache"),
        review=str(tmp_path / "review.json"),
    )

    with pytest.raises(research.ResearchError, match="authoritative project"):
        research._paths_from_args(args, project_root=authoritative_root)


def test_main_passes_environment_project_root_without_redirecting_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    selected_root = tmp_path / "selected-project"
    captured: dict[str, Path] = {}

    monkeypatch.setattr(
        research,
        "resolve_project_root",
        lambda code_root: selected_root,
    )

    def fake_plan(args, *, root):
        captured["root"] = root
        captured["manifest"] = Path(args.manifest)
        captured["state"] = Path(args.state)
        return 0

    monkeypatch.setattr(research, "command_plan", fake_plan)

    assert research.main(["plan"]) == 0
    assert captured["root"] == selected_root
    assert captured["manifest"] == research.DEFAULT_QUERY_MANIFEST
    assert captured["state"] == research.DEFAULT_RUN_STATE


def test_codex_review_path_cannot_collide_with_generated_outputs(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    args = SimpleNamespace(
        manifest=str(tmp_path / "manifest.json"),
        state=str(tmp_path / "state.json"),
        ledger=str(tmp_path / "ledger.jsonl"),
        evidence=str(evidence),
        report=str(tmp_path / "report.md"),
        cache=str(tmp_path / "cache"),
        review=str(evidence),
    )
    with pytest.raises(research.ResearchError, match="distinct|review|output"):
        research._paths_from_args(args)


def test_exclusive_run_lock_refuses_a_second_process(tmp_path: Path) -> None:
    state_path = tmp_path / "run_state.json"
    child_code = "\n".join(
        [
            "import sys",
            "from pathlib import Path",
            "from historical_context_search_research import ResearchError, exclusive_run_lock",
            "try:",
            "    with exclusive_run_lock(Path(sys.argv[1])):",
            "        pass",
            "except ResearchError as exc:",
            "    print(str(exc))",
            "    raise SystemExit(23)",
            "raise SystemExit(0)",
        ]
    )

    with research.exclusive_run_lock(state_path):
        child = subprocess.run(
            [sys.executable, "-c", child_code, str(state_path)],
            cwd=research.ROOT,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    assert child.returncode == 23
    assert "lock" in child.stdout.casefold() or "already" in child.stdout.casefold()


def test_run_and_report_wrappers_use_the_same_global_lock_without_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    args = SimpleNamespace(
        manifest=str(tmp_path / "manifest.json"),
        state=str(tmp_path / "state.json"),
        ledger=str(tmp_path / "ledger.jsonl"),
        evidence=str(tmp_path / "evidence.json"),
        report=str(tmp_path / "report.md"),
        cache=str(tmp_path / "cache"),
        review=str(tmp_path / "review.json"),
        execute_search=True,
    )
    acquired: list[Path] = []

    @contextlib.contextmanager
    def capture_lock(path: Path):
        acquired.append(Path(path))
        yield

    monkeypatch.setattr(research, "exclusive_run_lock", capture_lock)
    monkeypatch.setattr(
        research,
        "_command_run_or_resume_unlocked",
        lambda _args, *, resume, root: 17 if not resume else 18,
    )
    monkeypatch.setattr(
        research,
        "_command_report_unlocked",
        lambda _args, *, root: 19,
    )

    assert research.command_run_or_resume(args, resume=False, root=tmp_path) == 17
    assert research.command_report(args, root=tmp_path) == 19
    expected = research.DEFAULT_CACHE / "paid_research_run"
    assert acquired == [expected, expected]


def test_real_plan_does_not_mutate_authoritative_production_inputs(
    tmp_path: Path,
) -> None:
    derived_before = research.derive_target_set(research.ROOT)
    input_paths = {
        "corpus_manifest": research.ROOT / research.RESEARCH_RELATIVE / "corpus_manifest.json",
        "research_packets": research.ROOT / research.RESEARCH_RELATIVE / "research_packets.json",
        "final_research_status": research.ROOT
        / research.RESEARCH_RELATIVE
        / "final_unresolved/final_research_status.json",
        "unresolved_cases": research.ROOT
        / research.RESEARCH_RELATIVE
        / "final_unresolved/unresolved_cases.json",
        "semantic_review_ledger": research.ROOT
        / "historical_context_published_reply_semantic_review.json",
        "source_role_audit": research.ROOT
        / research.RESEARCH_RELATIVE
        / "historical_context_source_role_audit.json",
    }
    before = {
        name: (research.file_sha256(path), path.stat().st_mtime_ns)
        for name, path in input_paths.items()
    }
    output_paths = {
        name: tmp_path / filename
        for name, filename in {
            "manifest": "manifest.json",
            "state": "state.json",
            "ledger": "ledger.jsonl",
            "evidence": "evidence.json",
            "report": "report.md",
            "cache": "cache",
        }.items()
    }
    args = SimpleNamespace(**{name: str(path) for name, path in output_paths.items()})

    assert research.command_plan(args, root=research.ROOT) == 0
    manifest = research.load_manifest(output_paths["manifest"])
    counts = manifest["target_derivation"]
    assert counts["blocked_count"] == derived_before["counts"]["blocked_count"]
    assert counts["unresolved_count"] == derived_before["counts"]["unresolved_count"]
    assert counts["deduplicated_target_count"] == len(manifest["targets"])
    assert output_paths["manifest"].is_file()
    assert not output_paths["state"].exists()
    assert not output_paths["ledger"].exists()
    assert not output_paths["evidence"].exists()
    assert not output_paths["report"].exists()
    assert {
        name: (research.file_sha256(path), path.stat().st_mtime_ns)
        for name, path in input_paths.items()
    } == before


def _engine_resource(
    engine_id: str,
    data_store_ids: list[str],
    *,
    solution_type: str = "SOLUTION_TYPE_SEARCH",
) -> dict[str, object]:
    return {
        "name": (
            "projects/spatial-motif-393119/locations/global/collections/"
            f"default_collection/engines/{engine_id}"
        ),
        "displayName": engine_id,
        "dataStoreIds": data_store_ids,
        "solutionType": solution_type,
        "searchEngineConfig": {"searchTier": "SEARCH_TIER_ENTERPRISE"},
    }


def test_discovery_engine_selection_requires_one_attached_search_solution() -> None:
    target_store = "thatcher-search-store-2_1784787419356"
    selected = research.select_discovery_engine_for_data_store(
        [
            _engine_resource("unrelated", ["other-store"]),
            _engine_resource("thatcher-search-2", [target_store]),
        ],
        target_store,
    )
    assert selected.engine_id == "thatcher-search-2"
    assert selected.data_store_ids == (target_store,)
    assert selected.solution_type == "SOLUTION_TYPE_SEARCH"
    assert selected.search_tier == "SEARCH_TIER_ENTERPRISE"
    assert selected.endpoint.endswith(
        "/engines/thatcher-search-2/servingConfigs/default_serving_config:search"
    )

    with pytest.raises(
        research.SearchBackendNotConfigured, match="no Discovery Engine"
    ):
        research.select_discovery_engine_for_data_store(
            [_engine_resource("unrelated", ["other-store"])], target_store,
        )
    with pytest.raises(
        research.SearchBackendNotConfigured, match="more than one"
    ):
        research.select_discovery_engine_for_data_store(
            [
                _engine_resource("first", [target_store]),
                _engine_resource("second", [target_store]),
            ],
            target_store,
        )
    with pytest.raises(
        research.SearchBackendNotConfigured, match="not a search solution"
    ):
        research.select_discovery_engine_for_data_store(
            [
                _engine_resource(
                    "recommendations",
                    [target_store],
                    solution_type="SOLUTION_TYPE_RECOMMENDATION",
                )
            ],
            target_store,
        )


def test_read_only_discovery_resource_client_follows_engine_pagination() -> None:
    class ListSession:
        def __init__(self) -> None:
            self.trust_env = True
            self.calls: list[dict[str, object]] = []
            self.responses = [
                FakeSearchResponse(200, {
                    "engines": [_engine_resource("first", ["other-store"])],
                    "nextPageToken": "page-2",
                }),
                FakeSearchResponse(200, {
                    "engines": [
                        _engine_resource(
                            "second", ["thatcher-search-store-2_1784787419356"]
                        )
                    ],
                }),
            ]

        def get(self, url: str, **kwargs) -> FakeSearchResponse:
            self.calls.append({"url": url, **copy.deepcopy(kwargs)})
            return self.responses.pop(0)

    session = ListSession()
    token_provider = FakeAccessTokenProvider()
    client = research.GoogleDiscoveryEngineResourceClient(
        session=session,
        access_token_provider=token_provider,
    )
    selected = client.discover_engine("thatcher-search-store-2_1784787419356")
    assert selected.engine_id == "second"
    assert len(session.calls) == 2
    assert session.calls[0]["params"] == {"pageSize": 100}
    assert session.calls[1]["params"] == {
        "pageSize": 100, "pageToken": "page-2",
    }
    assert all(
        call["headers"]["X-Goog-User-Project"] == research.DISCOVERY_PROJECT_ID
        for call in session.calls
    )
    assert token_provider.calls == 2
    assert session.trust_env is False


def _target_site(pattern: str, index: int, *, status: str = "") -> dict[str, object]:
    row: dict[str, object] = {
        "name": f"targetSites/{index}",
        "providedUriPattern": pattern,
        "generatedUriPattern": pattern,
        "type": "INCLUDE",
    }
    if status:
        row["indexingStatus"] = status
    return row


def test_target_site_manifests_require_fifty_disjoint_include_patterns() -> None:
    first_rows = [
        _target_site(f"*.first-{index}.example/*", index)
        for index in range(50)
    ]
    second_rows = [
        _target_site(f"WWW.SECOND-{index}.EXAMPLE/*", index)
        for index in range(50)
    ]
    first = research.build_target_site_manifest("store-1", first_rows)
    second = research.build_target_site_manifest("store-2", second_rows)
    validation = research.validate_dual_target_site_manifests(first, second)
    assert validation["valid"] is True
    assert validation["cross_store_normalised_overlap"] == []
    assert first["include_count"] == second["include_count"] == 50
    assert first["explicit_indexing_failures"] == []
    assert len(validation["combined_manifest_sha256"]) == 64

    failed_rows = copy.deepcopy(first_rows)
    failed_rows[0]["indexingStatus"] = "FAILED"
    failed_rows[0]["failureReason"] = "fixture indexing failure"
    failed = research.build_target_site_manifest("store-1", failed_rows)
    assert failed["explicit_indexing_failures"][0]["failure_reason"] == (
        "fixture indexing failure"
    )

    overlap_rows = copy.deepcopy(second_rows)
    overlap_rows[0]["providedUriPattern"] = "HTTPS://*.FIRST-0.EXAMPLE/*"
    overlap_rows[0]["generatedUriPattern"] = "*.first-0.example/*"
    overlap = research.build_target_site_manifest("store-2", overlap_rows)
    with pytest.raises(research.ResearchError, match="overlap"):
        research.validate_dual_target_site_manifests(first, overlap)
    with pytest.raises(research.ResearchError, match="expected 50"):
        research.build_target_site_manifest("short", first_rows[:-1])


def test_dual_engine_merge_deduplicates_urls_and_preserves_each_rank() -> None:
    query = '"there is no such thing as society"'
    merged = research.merge_dual_engine_results(query, [
        {
            "engine_id": "engine-1",
            "data_store_id": "store-1",
            "results": [
                {
                    "title": "First-only",
                    "result_url": "https://example.org/first",
                    "snippet": "lead only",
                    "mime": "text/html",
                },
                {
                    "title": "Shared from first",
                    "result_url": "https://example.org/shared/?utm_source=fixture",
                    "snippet": "not evidence",
                    "mime": "text/html",
                },
            ],
        },
        {
            "engine_id": "engine-2",
            "data_store_id": "store-2",
            "results": [
                {
                    "title": "Shared from second",
                    "result_url": "https://example.org/shared#fragment",
                    "snippet": "also not evidence",
                    "mime": "text/html",
                },
                {
                    "title": "Second-only",
                    "result_url": "https://example.org/second",
                    "snippet": "",
                    "mime": "application/pdf",
                },
            ],
        },
    ])
    assert [row["canonical_url"] for row in merged] == [
        "https://example.org/first",
        "https://example.org/shared",
        "https://example.org/second",
    ]
    shared = next(
        row for row in merged if row["canonical_url"] == "https://example.org/shared"
    )
    assert shared["logical_query"] == query
    assert shared["appeared_in_both_engines"] is True
    assert shared["best_rank"] == 1
    assert {
        (row["engine_id"], row["data_store_id"], row["rank"])
        for row in shared["engine_results"]
    } == {
        ("engine-1", "store-1", 2),
        ("engine-2", "store-2", 1),
    }
    second_only = next(
        row for row in merged if row["canonical_url"] == "https://example.org/second"
    )
    assert second_only["appeared_in_both_engines"] is False


def test_dual_engine_query_executes_both_and_preserves_zero_or_partial_failure() -> None:
    class Backend:
        version = "fixture"
        engine_identity_hash = "fixture"

        def __init__(
            self,
            engine_id: str,
            data_store_id: str,
            response: list[dict[str, str]] | Exception,
        ) -> None:
            self.engine_id = engine_id
            self.data_store_id = data_store_id
            self.response = response
            self.calls: list[tuple[str, int]] = []

        def search(self, query: str, *, number: int = 10) -> list[dict[str, str]]:
            self.calls.append((query, number))
            if isinstance(self.response, Exception):
                raise self.response
            return copy.deepcopy(self.response)

    first = Backend("engine-1", "store-1", [])
    second = Backend(
        "engine-2",
        "store-2",
        [{
            "title": "Second-engine lead",
            "result_url": "https://example.org/lead",
            "snippet": "discovery only",
            "mime": "text/html",
        }],
    )
    complete = research.execute_dual_engine_query("fixture query", [first, second])
    assert first.calls == second.calls == [("fixture query", 10)]
    assert complete["complete_two_engine_response"] is True
    assert complete["engine_failures"] == []
    assert complete["engine_batches"][0]["results"] == []  # valid zero result
    assert complete["merged_results"][0]["canonical_url"] == (
        "https://example.org/lead"
    )

    failed = Backend(
        "engine-1",
        "store-1",
        research.ConfirmedSearchError("fixture confirmed failure"),
    )
    partial = research.execute_dual_engine_query("fixture query", [failed, second])
    assert partial["complete_two_engine_response"] is False
    assert partial["engine_failures"][0]["engine_id"] == "engine-1"
    assert partial["engine_batches"][1]["results"][0]["title"] == (
        "Second-engine lead"
    )
    assert partial["merged_results"][0]["appeared_in_both_engines"] is False


def test_search_cache_identity_separates_engine_store_site_and_policy_context(
    tmp_path: Path,
) -> None:
    first_descriptor = research.DiscoveryEngineDescriptor(
        project_id=research.DISCOVERY_PROJECT_ID,
        location=research.DISCOVERY_LOCATION,
        collection=research.DISCOVERY_COLLECTION,
        engine_id="engine-1",
        display_name="Engine one",
        data_store_ids=("store-1",),
        solution_type="SOLUTION_TYPE_SEARCH",
        search_tier="SEARCH_TIER_ENTERPRISE",
    )
    second_descriptor = research.DiscoveryEngineDescriptor(
        **{
            **first_descriptor.__dict__,
            "engine_id": "engine-2",
            "display_name": "Engine two",
            "data_store_ids": ("store-2",),
        }
    )
    first = research.GoogleDiscoveryEngineBackend(
        descriptor=first_descriptor,
        target_site_manifest_hash="a" * 64,
        access_token_provider=FakeAccessTokenProvider(),
        session=FakePostSession(),
    )
    first_sites_changed = research.GoogleDiscoveryEngineBackend(
        descriptor=first_descriptor,
        target_site_manifest_hash="b" * 64,
        access_token_provider=FakeAccessTokenProvider(),
        session=FakePostSession(),
    )
    second = research.GoogleDiscoveryEngineBackend(
        descriptor=second_descriptor,
        target_site_manifest_hash="c" * 64,
        access_token_provider=FakeAccessTokenProvider(),
        session=FakePostSession(),
    )
    cache = research.ResearchCache(tmp_path / "cache")
    keys = {
        cache.search_key(first, "same query", 10),
        cache.search_key(first_sites_changed, "same query", 10),
        cache.search_key(second, "same query", 10),
    }
    assert len(keys) == 3
    assert first.engine_id == "engine-1"
    assert first.data_store_id == "store-1"
    assert second.endpoint.endswith(
        "/engines/engine-2/servingConfigs/default_serving_config:search"
    )


def test_hansard_json_is_parsed_as_separate_attributed_contributions() -> None:
    body = json.dumps({
        "Overview": {
            "Id": 42,
            "ExtId": "debate-fixture",
            "Title": "Fixture Debate",
            "Date": "1981-03-10T00:00:00",
            "Location": "Commons Chamber",
            "House": "Commons",
            "VolumeNo": 1000,
        },
        "Items": [
            {
                "ItemType": "Contribution",
                "HRSTag": "hs_ColumnNumber",
                "Value": "<span>Column 123</span>",
            },
            {
                "ItemType": "Contribution",
                "ItemId": 1,
                "ExternalId": "first-contribution",
                "AttributedTo": "The Prime Minister (Mrs. Margaret Thatcher)",
                "OrderInSection": 2,
                "Value": "<p>There is no liberty unless economic freedom is preserved.</p>",
            },
            {
                "ItemType": "Contribution",
                "ItemId": 2,
                "ExternalId": "second-contribution",
                "AttributedTo": "Another Member",
                "OrderInSection": 3,
                "Value": "<p>There can be no economic freedom without liberty.</p>",
            },
        ],
    }).encode("utf-8")
    parsed = research.parse_hansard_json(body)
    assert parsed["status"] == "parsed"
    assert parsed["metadata"]["title"] == "Fixture Debate"
    assert len(parsed["contributions"]) == 2
    first, second = parsed["contributions"]
    assert first["speaker"] == "The Prime Minister (Mrs. Margaret Thatcher)"
    assert first["column"] == "123"
    assert first["contribution_id"] == "first-contribution"
    assert "contribution first-contribution" in first["stable_locator"]
    assert second["speaker"] == "Another Member"
    assert "text" not in parsed  # no synthetic cross-speaker text stream
    assert research.normalise_wording(QUOTATION) not in research.normalise_wording(
        first["text"]
    )
    assert research.normalise_wording(QUOTATION) not in research.normalise_wording(
        second["text"]
    )


def test_hansard_json_recurses_child_debates_without_cross_speaker_joining() -> None:
    body = json.dumps({
        "Overview": {
            "ExtId": "root-debate",
            "Title": "Commons Chamber",
            "Date": "1983-11-03T00:00:00",
            "House": "Commons",
            "VolumeNo": 47,
        },
        "Items": [],
        "ChildDebates": [{
            "Overview": {
                "ExtId": "child-debate",
                "Title": "Foreign Affairs",
            },
            "Items": [
                {
                    "ItemType": "Contribution",
                    "HRSTag": "hs_ColumnNumber",
                    "Value": "<span>Column 984</span>",
                },
                {
                    "ItemType": "Contribution",
                    "ExternalId": "child-contribution",
                    "AttributedTo": "The Prime Minister (Mrs Margaret Thatcher)",
                    "Value": "<p>The spirit of freedom is too strong to be crushed.</p>",
                },
            ],
            "ChildDebates": [],
        }],
    }).encode("utf-8")
    parsed = research.parse_hansard_json(body)
    assert parsed["status"] == "parsed"
    assert len(parsed["contributions"]) == 1
    contribution = parsed["contributions"][0]
    assert contribution["debate_id"] == "child-debate"
    assert contribution["debate_title"] == "Foreign Affairs"
    assert contribution["date"] == "1983-11-03T00:00:00"
    assert contribution["chamber"] == "Commons"
    assert contribution["column"] == "984"
    assert contribution["speaker"] == (
        "The Prime Minister (Mrs Margaret Thatcher)"
    )


def test_explicit_evidence_semantics_keep_usable_leads_out_of_resolution_support() -> None:
    candidate = {
        "candidate_id": "bbc-secondary",
        "fetch_status": "fetched",
        "http_status": 200,
        "page_sha256": "d" * 64,
        "snippet_used_as_evidence": False,
        "classification": "reliable_secondary_evidence",
        "match_type": "recorded_variant",
        "supporting_passage": "A later attributed wording.",
        "accepted_as_evidence": False,
    }
    corrected = research.apply_explicit_evidence_semantics(
        {
            "quote_id": "quote-secondary",
            "recommended_outcome": "promising_but_insufficient",
            "candidate_sources": [candidate],
        },
        usable_evidence_candidate_ids=["bbc-secondary"],
        resolution_support_candidate_ids=[],
        sufficient_for_historical_context=False,
    )
    assert corrected["usable_evidence_candidate_ids"] == ["bbc-secondary"]
    assert corrected["resolution_support_candidate_ids"] == []
    assert corrected["sufficient_for_historical_context"] is False
    assert corrected["candidate_sources"][0]["accepted_as_evidence"] is True
    document = {
        "counts": {
            "targeted_quotation_count": 1,
            "recommended_outcomes": {"promising_but_insufficient": 1},
        },
        "quotations": [corrected],
    }
    validation = research.validate_explicit_evidence_document(document)
    assert validation["valid"] is True

    broken = copy.deepcopy(document)
    broken["quotations"][0]["resolution_support_candidate_ids"] = [
        "missing-candidate"
    ]
    invalid = research.validate_explicit_evidence_document(broken)
    assert invalid["valid"] is False
    assert any("nonexistent resolution-support" in error for error in invalid["errors"])


def _query_strategy_corpus_index():
    """Return a deterministic eligible-corpus index without production reads."""
    controls = [
        (
            "One reason I'm not a Marxist or a Socialist is because I know you "
            "can't fit people into theories."
        ),
        (
            "Political myths cherished by commentators die hard, while expert "
            "analysis and prediction quickly replaces them."
        ),
        (
            "Only a Conservative government will cut red tape, bureaucratic "
            "interference and lethal taxes."
        ),
        (
            "The spirit of freedom is too strong and too resilient to be crushed "
            "by the tanks of tyrants."
        ),
        (
            "There is nothing necessarily benevolent about European integration "
            "and grand utopian plans can threaten freedom."
        ),
        (
            "There are still people who believe in consensus politics; I regard "
            "them as Quislings and traitors."
        ),
        (
            "The better I do, the more is expected of me. I am ready for that. "
            "I think I have the strength to do anything that has to be done."
        ),
    ]
    filler = [
        (
            f"Fixture corpus statement {index} records an ordinary discussion "
            "of public administration, government and the people."
        )
        for index in range(
            query_strategy.EXPECTED_ELIGIBLE_CORPUS_SIZE - len(controls)
        )
    ]
    return query_strategy.CorpusDistinctivenessIndex.from_texts(
        controls + filler,
    )


def _query_strategy_target(
    quotation: str,
    *,
    date: str = "",
    date_relation: str = "unknown",
    event: str = "",
    locator: str = "",
    origins: list[str] | None = None,
    gate_disposition: str = "insufficient_to_assess",
    variant: str = "",
) -> dict[str, object]:
    quote_id = research.sha256_bytes(quotation.encode("utf-8"))
    variant_records: list[dict[str, object]] = []
    if variant:
        variant_records.append({
            "original_stored_text": quotation,
            "search_variant": variant,
            "variant_provenance": {
                "kind": "authoritative_research_packet_field",
                "path": f"research_packets.json#items/{quote_id}/verified_text",
                "stable_locator": locator,
            },
            "transformation_type": "authoritative_verified_text_difference",
            "substantive": True,
        })
    return {
        "quote_id": quote_id,
        "quotation_text": quotation,
        "target_origins": origins or ["final_unresolved_status"],
        "gate_disposition": gate_disposition,
        "source_metadata": {
            "packet_available": True,
            "date": {
                "raw": date,
                "known": bool(date),
                "iso_date": date if len(date) == 10 else "",
                "year": int(date[:4]) if len(date) >= 4 else None,
                "precision": "day" if len(date) == 10 else (
                    "year" if date else "unknown"
                ),
            },
            "date_relation_to_marriage": date_relation,
            "source_event": event,
            "stable_locator": locator,
            "entities": ["Margaret Thatcher"],
            "verification_status": "variant" if variant else "unknown",
            "text_variation_notes": "",
        },
        "documented_variant_records": variant_records,
        "recorded_variants": [variant] if variant else [],
    }


def _query_strategy_plan(
    quotation: str,
    **target_kwargs: object,
) -> tuple[dict[str, object], object, list[dict[str, object]], dict[str, object]]:
    target = _query_strategy_target(quotation, **target_kwargs)
    index = _query_strategy_corpus_index()
    rows, diagnostics = query_strategy.build_query_plan(target, index)
    query_strategy.validate_query_plan(target, rows, diagnostics, index)
    return target, index, rows, diagnostics


def test_query_strategy_uses_exactly_the_611_eligible_quotes_for_frequency() -> None:
    index = query_strategy.load_eligible_corpus_index(research.ROOT)
    policy = query_strategy.policy_document(index)
    assert policy["corpus_index"]["corpus_size"] == 611
    assert policy["corpus_index"]["index_sha256"] == index.summary()["index_sha256"]


def test_query_strategy_every_long_quote_receives_an_identity_fragment() -> None:
    quotation = (
        "One reason I'm not a Marxist or a Socialist is because I know you "
        "can't fit people into theories. I was trained as a scientist and a "
        "chemist, so I know a real theory when I see one."
    )
    _target_row, _index, rows, _diagnostics = _query_strategy_plan(quotation)
    fragments = [
        row for row in rows
        if row["query_lane"] == "attribution_discovery"
        and row.get("fragment_text")
    ]
    assert fragments
    assert any(row["identity_alias"] == "Margaret Thatcher" for row in fragments)
    assert all(row["execution_round"] >= 2 for row in fragments)
    quotation_tokens = research.word_tokens(quotation)
    assert all(
        any(
            quotation_tokens[start:start + len(research.word_tokens(
                str(row["fragment_text"])
            ))] == research.word_tokens(str(row["fragment_text"]))
            for start in range(len(quotation_tokens))
        )
        for row in fragments
    )


def test_query_strategy_retains_source_neutral_and_multi_anchor_discovery() -> None:
    quotation = (
        "Only a Conservative government will cut out the red tape, the "
        "bureaucratic interference, and the lethal taxes which poisoned business."
    )
    _target_row, _index, rows, _diagnostics = _query_strategy_plan(quotation)
    neutral = [
        row for row in rows
        if row["query_lane"] == "source_and_misattribution_discovery"
        and row["query_category"] != "complete_quotation_unqualified"
    ]
    assert len(neutral) == 1
    assert neutral[0]["identity_alias"] is None
    multi_anchor = query_strategy.select_multi_anchor(quotation, _index)
    assert multi_anchor is not None
    phrases = multi_anchor["anchor_phrases"]
    assert isinstance(phrases, list) and len(phrases) == 2
    assert all(2 <= len(research.word_tokens(phrase)) <= 6 for phrase in phrases)
    quotation_tokens = research.word_tokens(quotation)
    assert all(
        any(
            quotation_tokens[start:start + len(research.word_tokens(phrase))]
            == research.word_tokens(phrase)
            for start in range(len(quotation_tokens))
        )
        for phrase in phrases
    )
    assert multi_anchor["source_neutral_fragment_acceptable"] is True
    assert neutral[0]["query_category"] in {
        "source_neutral_distinctive_clause",
        "source_neutral_multi_anchor",
    }


def test_query_strategy_uses_separate_anchored_and_neutral_thresholds() -> None:
    quotation = (
        "The better I do, the more is expected of me. I am ready for that. "
        "I think I have the strength to do anything that I feel has to be done."
    )
    _target_row, _index, rows, _diagnostics = _query_strategy_plan(quotation)
    anchored = [
        row for row in rows
        if row.get("fragment_text")
        and row["identity_alias"] == "Margaret Thatcher"
        and row["query_category"] != "exact_quotation_margaret_thatcher"
    ]
    assert anchored
    assert any(
        row["fragment_distinctiveness"]["anchored_fragment_acceptable"] is True
        and row["fragment_distinctiveness"][
            "source_neutral_fragment_acceptable"
        ] is False
        for row in anchored
    )


def test_query_strategy_allows_exceptionally_distinctive_short_neutral_phrase() -> None:
    quotation = (
        "Some colleagues still favour consensus politics; I regard them as "
        "Quislings and traitors."
    )
    _target_row, _index, rows, _diagnostics = _query_strategy_plan(quotation)
    neutral = next(
        row for row in rows
        if row["query_lane"] == "source_and_misattribution_discovery"
        and row["query_category"] != "complete_quotation_unqualified"
    )
    phrases = (
        (neutral.get("multi_anchor") or {}).get("anchor_phrases")
        or [neutral["fragment_text"]]
    )
    assert any(len(research.word_tokens(phrase)) < 10 for phrase in phrases)
    assert (
        (neutral.get("multi_anchor") or {}).get(
            "source_neutral_fragment_acceptable"
        )
        or neutral["fragment_distinctiveness"][
            "source_neutral_fragment_acceptable"
        ]
    ) is True
    distinctiveness = neutral["fragment_distinctiveness"]
    assert (
        distinctiveness["rare_content_word_count"] >= 2
        or distinctiveness["average_content_idf_milli"] >= 2200
    )


def test_query_strategy_forbids_unqualified_i_am_ready_for_that() -> None:
    quotation = (
        "The better I do, the more is expected of me. I am ready for that. "
        "I think I have the strength to do anything that I feel has to be done."
    )
    _target_row, index, rows, diagnostics = _query_strategy_plan(quotation)
    assert '"I am ready for that"' not in {
        str(row["query"]) for row in rows if row["identity_alias"] is None
    }
    assert all(
        row["identity_alias"] is not None
        or row["query_category"] == "complete_quotation_unqualified"
        or (
            (row.get("multi_anchor") or {}).get(
                "source_neutral_fragment_acceptable"
            )
            or (row.get("fragment_distinctiveness") or {}).get(
                "source_neutral_fragment_acceptable"
            )
        ) is True
        for row in rows
    )
    rejected = query_strategy.fragment_metrics("I am ready for that", index)
    assert rejected["generic_conversational_phrase"] is True
    assert rejected["source_neutral_fragment_acceptable"] is False


def test_query_strategy_formal_alias_is_conditional_not_universal() -> None:
    quotation = (
        "Constitutional safeguards restrain arbitrary executive coercion during "
        "prolonged democratic emergencies abroad."
    )
    target, index, rows, diagnostics = _query_strategy_plan(quotation)
    assert all(row["identity_alias"] != "Margaret Hilda Thatcher" for row in rows)
    no_fallback = query_strategy.append_runtime_formal_alias_fallback(
        target,
        rows,
        index,
        earlier_useful_result=True,
        decisive_primary_validated=False,
    )
    assert no_fallback == rows
    fallback = query_strategy.append_runtime_formal_alias_fallback(
        target,
        rows,
        index,
        earlier_useful_result=False,
        decisive_primary_validated=False,
    )
    added = fallback[len(rows):]
    assert len(added) == 1
    assert added[0]["execution_round"] == 4
    assert added[0]["identity_alias"] == "Margaret Hilda Thatcher"


@pytest.mark.parametrize(
    ("date", "relation", "event", "locator", "expected"),
    [
        ("1951-12-12", "pre_marriage", "", "", True),
        ("1951-12-13", "post_marriage", "", "", False),
        ("1979-05-02", "post_marriage", "", "", False),
        ("", "unknown", "Dartford candidate selection", "", True),
        ("", "unknown", "", "", False),
    ],
)
def test_query_strategy_roberts_alias_is_date_and_context_aware(
    date: str,
    relation: str,
    event: str,
    locator: str,
    expected: bool,
) -> None:
    quotation = (
        "Constitutional safeguards restrain arbitrary executive coercion during "
        "prolonged democratic emergencies abroad."
    )
    target, index, _rows, diagnostics = _query_strategy_plan(
        quotation,
        date=date,
        date_relation=relation,
        event=event,
        locator=locator,
    )
    has_roberts = any(
        row["identity_alias"] in {"Margaret Roberts", "Miss Margaret Roberts"}
        for row in _rows
    )
    assert has_roberts is expected


def test_query_strategy_variant_and_context_require_provenance() -> None:
    quotation = (
        "European history shows there is nothing necessarily benevolent about "
        "integration and utopian plans may threaten freedom."
    )
    variant = (
        "The lessons of European history show there is nothing necessarily "
        "benevolent about integration and grand utopian plans threaten freedom."
    )
    target, index, rows, diagnostics = _query_strategy_plan(
        quotation,
        event="Speech to the Conservative Party Conference",
        locator="Conference reference",
        origins=["historical_context_semantic_gate"],
        gate_disposition="future_correction_needed",
        variant=variant,
    )
    variant_row = next(
        row for row in rows if "documented_variant" in row["query_category"]
    )
    assert variant_row["variant_provenance"]["variant_provenance"]["path"]
    context_row = next(
        row for row in rows
        if row["query_category"] == "metadata_justified_source_context"
    )
    assert context_row["context_anchor"] == "conference"
    assert "source-event" in context_row["context_anchor_rationale"]

    broken = copy.deepcopy(rows)
    broken[broken.index(variant_row)]["variant_provenance"] = None
    with pytest.raises(query_strategy.QueryStrategyError, match="provenance"):
        query_strategy.validate_query_plan(target, broken, diagnostics, index)


def test_query_strategy_cap_deduplication_and_order_are_deterministic() -> None:
    quotation = (
        "Political myths cherished by political commentators die hard; after "
        "events explode one myth, another supplies expert analysis and prediction."
    )
    kwargs = {
        "date": "1950-01-01",
        "date_relation": "pre_marriage",
        "event": "Dartford conference speech",
        "locator": "Formal archive catalogue",
        "variant": (
            "Political myths cherished by commentators die hard; after events "
            "explode one myth, another supports expert analysis and prediction."
        ),
    }
    target, index, first, diagnostics = _query_strategy_plan(quotation, **kwargs)
    second, second_diagnostics = query_strategy.build_query_plan(target, index)
    assert first == second
    assert diagnostics == second_diagnostics
    assert len(first) <= 7
    normalised = [
        query_strategy.normalise_query(str(row["query"])) for row in first
    ]
    assert len(normalised) == len(set(normalised))
    assert [
        (row["execution_round"], row["request_order"]) for row in first
    ] == sorted(
        (row["execution_round"], row["request_order"]) for row in first
    )
    query_strategy.validate_query_plan(target, first, diagnostics, index)


def test_query_strategy_stops_only_for_fetched_reviewed_primary_evidence() -> None:
    base_review = {
        "fetched": True,
        "inspected": True,
        "validated": True,
        "source_classification": "strong_primary_evidence",
        "outcome": "exact_primary_wording_found",
        "snippet_only": False,
    }
    stop = query_strategy.decisive_primary_review(base_review)
    assert stop is True

    snippet = {**base_review, "snippet_only": True}
    assert query_strategy.decisive_primary_review(snippet) is False
    inaccessible = {**base_review, "fetched": False}
    assert query_strategy.decisive_primary_review(inaccessible) is False
    secondary = {
        **base_review,
        "source_classification": "reliable_secondary_evidence",
    }
    assert query_strategy.decisive_primary_review(secondary) is False

    quotation = (
        "A validated primary source should stop later catalogue fallback "
        "queries after its body and attribution are reviewed."
    )
    target, index, _rows, diagnostics = _query_strategy_plan(quotation)
    assert query_strategy.append_runtime_formal_alias_fallback(
        target,
        _rows,
        index,
        earlier_useful_result=False,
        decisive_primary_validated=stop,
    ) == _rows


def test_query_strategy_has_no_guardrail_source_leak_or_quote_specific_branch() -> None:
    signature = inspect.signature(query_strategy.build_query_plan)
    assert list(signature.parameters) == ["target", "index"]
    source = inspect.getsource(query_strategy)
    forbidden_values = {
        "00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae",
        "268ab7ec8f0d8688966d1008443f3cc3ed7a34293981c40f3b83ea5225f1dba1",
        "6037112de070bb4455915a61e36ef2d173eaa51316372c5fd52f64016226dfd0",
        "880a2f32c7d03b24c72c6e4e3d8c5799c6a7af14a9497f11881aeddb123d5be7",
        "f4323817daee5cef16fa5d83879823f2da5506152fcb7b1b1ce5c777ac036d4d",
        "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351",
        "/document/102770",
        "/document/103384",
        "/document/103618",
        "/document/108381",
        "fbc65422-f7a1-4160-8cf2-48c7c6a94fbf",
        "11112523000569",
    }
    constants = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not any(
        forbidden in constant
        for forbidden in forbidden_values
        for constant in constants
    )


def test_query_strategy_planning_is_network_free(monkeypatch) -> None:
    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("query planning must not perform network I/O")

    monkeypatch.setattr(
        research.requests.sessions.Session,
        "request",
        forbidden_network,
    )
    monkeypatch.setattr(research.socket, "create_connection", forbidden_network)
    _target_row, index, rows, diagnostics = _query_strategy_plan(
        "Political myths are replaced by expert analysis and prediction after "
        "events reveal that the original claims were false."
    )
    assert rows
    assert query_strategy.policy_document(index)["network_capability"] is False
    assert "network" not in diagnostics

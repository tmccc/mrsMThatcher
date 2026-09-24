from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import semantic_alignment.thatcher_image_hunt as hunt


BASELINE = Path("image_analysis_baseline_69.json")


def test_cli_profile_defaults_to_the_frozen_research_baseline(tmp_path, monkeypatch):
    """Keep the one-off image hunt bound to its original 69-image input."""
    selected = []
    monkeypatch.setattr(hunt, "load_project_environment", lambda _path: None)
    monkeypatch.setattr(
        hunt, "write_coverage_outputs",
        lambda baseline, research: selected.append((baseline, research)) or {},
    )

    assert hunt.main(["profile", "--project-dir", str(tmp_path)]) == 0
    assert selected == [(
        tmp_path / "image_analysis_baseline_69.json",
        tmp_path / "image_discovery_research" / hunt.RUN_ID,
    )]


def test_schema_v3_baseline_and_coverage_profile_are_deterministic():
    baseline = hunt.load_baseline(BASELINE)
    assert len(baseline["current_hashes"]) == 69
    first = hunt.build_coverage_profile(baseline)
    second = hunt.build_coverage_profile(baseline)
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
    assert first["observed_concentrations"]["indoor_images"] == 60
    assert first["observed_concentrations"]["one_person_images"] == 66


def test_search_briefs_are_deterministic_and_need_no_ai():
    profile = hunt.build_coverage_profile(hunt.load_baseline(BASELINE))
    first = hunt.build_discovery_briefs(profile)
    assert first == hunt.build_discovery_briefs(profile)
    assert len(first) == 6
    assert all(row["brief_hash"] for row in first)


def test_grounded_developer_payload_uses_json_mime_and_local_schema_validation():
    client = hunt.GeminiHuntClient.__new__(hunt.GeminiHuntClient)
    payload = client.developer_grounded_payload("prompt", hunt.DISCOVERY_RESPONSE_SCHEMA)
    assert payload["tools"] == [{"googleSearch": {}}]
    assert "responseJsonSchema" not in payload["generationConfig"]
    assert "responseMimeType" not in payload["generationConfig"]
    assert payload["generationConfig"]["thinkingConfig"] == {"thinkingBudget": hunt.THINKING_BUDGET}
    assert payload["contents"][0]["parts"] == [{"text": "prompt"}]
    assert hunt.DISCOVERY_RESPONSE_SCHEMA["properties"]["records"]["maxItems"] == 20


def discovery_record(url: str = "https://example.org/archive/thatcher") -> dict:
    return {
        "source_page_url": url,
        "publisher": "Example archive",
        "page_title": "Margaret Thatcher visit",
        "archive_or_collection": "Photographs",
        "event_or_period": "Factory visit",
        "approximate_date": "1984",
        "people_or_context_named_by_source": ["Margaret Thatcher"],
        "caption_or_catalogue_text": "Margaret Thatcher speaking with workers",
        "rights_or_licence_hint": "Rights unclear",
        "why_this_source_fills_a_gap": "Active industrial scene",
        "search_queries_used": ["Margaret Thatcher factory archive"],
    }


def test_discovery_url_must_be_linked_by_provider_grounding():
    record = discovery_record()
    source = {"original_url": record["source_page_url"], "resolved_url": None, "supports": ["archive result"]}
    accepted, rejected = hunt.validate_grounded_discovery_records(
        {"records": [record]}, {"sources": []}, [source],
    )
    assert len(accepted) == 1 and not rejected
    invented = discovery_record("https://invented.example/photo")
    accepted, rejected = hunt.validate_grounded_discovery_records(
        {"records": [invented]}, {"sources": []}, [source],
    )
    assert not accepted
    assert rejected[0]["reason"] == "source_url_not_linked_by_provider_grounding"


def test_grounding_resolution_cache_avoids_reopening_saved_redirect():
    class NoRequestSession:
        def get(self, *_args, **_kwargs):
            raise AssertionError("cached grounding redirect was reopened")

    original = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/saved"
    final = "https://commons.wikimedia.org/wiki/Category:Margaret_Thatcher"
    rows = hunt.resolve_grounded_sources(
        {"sources": [{"url": original, "supports": ["Margaret Thatcher archive"]}]},
        NoRequestSession(), resolution_cache={original: final},
    )
    assert rows[0]["resolved_url"] == final
    assert rows[0]["resolution_from_cache"] is True


def test_discovery_normalises_harmless_singleton_list_only():
    record = discovery_record()
    record["people_or_context_named_by_source"] = "Margaret Thatcher"
    source = {"original_url": record["source_page_url"], "resolved_url": None, "supports": ["archive result"]}
    accepted, rejected = hunt.validate_grounded_discovery_records({"records": [record]}, {}, [source])
    assert not rejected
    assert accepted[0]["people_or_context_named_by_source"] == ["Margaret Thatcher"]
    assert accepted[0]["local_normalisations"] == ["people_or_context_named_by_source:string_to_singleton_list"]


def test_json_parser_repairs_only_one_missing_outer_object_brace():
    raw = {"candidates": [{"content": {"parts": [{"text": '{"records": []'}]}}]}
    parsed, repairs = hunt.parse_json_response(raw)
    assert parsed == {"records": []}
    assert repairs == ["appended_single_missing_outer_object_brace"]
    unsafe = {"candidates": [{"content": {"parts": [{"text": '{"records": ['}]}}]}
    with pytest.raises(ValueError, match="missing only"):
        hunt.parse_json_response(unsafe)


def test_saved_triage_response_is_recovered_from_preserved_raw(tmp_path):
    research = tmp_path / "run"
    normal = research / "triage_batches" / "normalised" / "triage-01.json"
    raw = research / "triage_batches" / "raw" / "triage-01.json"
    normal.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    hunt.atomic_write_json(normal, {"parsed": None, "parse_error": "truncated JSON object", "normalisation": []})
    hunt.atomic_write_json(raw, {"candidates": [{"content": {"parts": [{"text": '{"records": []'}]}}]})
    recovered = hunt.recover_saved_triage_response(research, normal)
    assert recovered["parsed"] == {"records": []}
    assert recovered["original_parse_error"] == "truncated JSON object"
    assert recovered["parse_error"] is None
    assert hunt.read_json(raw)["candidates"][0]["content"]["parts"][0]["text"] == '{"records": []'


def test_image_extraction_covers_src_srcset_open_graph_and_jsonld():
    page = """<html><head><title>Margaret Thatcher archive</title>
    <meta property="og:image" content="/og.jpg"><script type="application/ld+json">
    {"@type":"ImageObject","contentUrl":"/json.png","caption":"Margaret Thatcher at work"}</script></head>
    <body><figure><a href="/original.jpg"><img src="/small.jpg" srcset="/mid.jpg 400w, /large.jpg 1200w" alt="Margaret Thatcher"><figcaption>Margaret Thatcher visits a factory</figcaption></a></figure></body></html>"""
    metadata, rows = hunt.extract_image_references("https://archive.example/page", page)
    urls = {row["direct_image_url"] for row in rows}
    assert metadata["mentions_margaret_thatcher"] is True
    assert "https://archive.example/small.jpg" in urls
    assert "https://archive.example/large.jpg" in urls
    assert "https://archive.example/original.jpg" in urls
    assert "https://archive.example/og.jpg" in urls
    assert "https://archive.example/json.png" in urls


def test_identity_uses_source_text_and_never_pixels():
    verified = hunt.source_identity_evidence(
        {"caption": "Ronald Reagan meets Margaret Thatcher", "alt_text": "", "metadata_text": ""},
        {"title": "Archive", "description": ""}, discovery_record(),
    )
    assert verified["identity_basis"] == "source_caption"
    assert verified["identity_confidence"] == "high"
    assert verified["source_named_people"] == ["Margaret Thatcher", "Ronald Reagan"]
    unverified = hunt.source_identity_evidence(
        {"caption": "A woman addresses the meeting", "alt_text": "", "metadata_text": ""},
        {"title": "Archive photographs", "description": ""},
        {**discovery_record(), "caption_or_catalogue_text": "Political meeting"},
    )
    assert unverified["identity_confidence"] == "low"
    assert unverified["identity_evidence"] == ""
    model_only = hunt.source_identity_evidence(
        {"caption": "", "alt_text": "", "metadata_text": ""},
        {"title": "Archive photographs", "description": ""}, discovery_record(),
    )
    assert model_only["identity_confidence"] == "low"


def test_publisher_is_derived_from_resolved_archive_not_grounding_redirect():
    assert hunt.publisher_name_for_url(
        "https://commons.wikimedia.org/wiki/File:Margaret_Thatcher.jpg",
        "vertexaisearch.cloud.google.com",
    ) == "Wikimedia Commons"
    assert hunt.publisher_name_for_url(
        "https://www.reaganlibrary.gov/archives/photo/example",
        "vertexaisearch.cloud.google.com",
    ) == "Ronald Reagan Presidential Library"


def test_commons_archive_metadata_supplies_identity_and_rights_without_pixel_identification():
    page = {
        "title": "File:Margaret Thatcher visits a factory.jpg",
        "imageinfo": [{
            "mime": "image/jpeg", "url": "https://upload.wikimedia.org/original.jpg",
            "thumburl": "https://upload.wikimedia.org/thumb.jpg", "size": 12345,
            "width": 1600, "height": 1000,
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Margaret_Thatcher_visits_a_factory.jpg",
            "extmetadata": {
                "ImageDescription": {"value": "<p>Margaret Thatcher speaking with factory workers</p>"},
                "LicenseShortName": {"value": "Public domain"},
                "DateTimeOriginal": {"value": "1984"},
            },
        }],
    }
    converted = hunt.commons_image_candidate(page, grounding_parent_url="https://commons.wikimedia.org/wiki/Category:Margaret_Thatcher")
    assert converted is not None
    page_record, candidate = converted
    assert candidate["identity_basis"] == "archive_record"
    assert candidate["identity_confidence"] == "high"
    assert candidate["rights_status"] == "public_domain"
    assert page_record["grounding_parent_source_page_url"].endswith("Category:Margaret_Thatcher")
    not_attributed = {**page, "title": "File:Factory visit.jpg"}
    not_attributed["imageinfo"] = [{**page["imageinfo"][0], "extmetadata": {"ImageDescription": {"value": "A politician with workers"}}}]
    assert hunt.commons_image_candidate(not_attributed, grounding_parent_url="https://commons.wikimedia.org/wiki/Category:Margaret_Thatcher") is None
    misleading_search_hit = {**page, "title": "File:Australian plant.jpg"}
    misleading_search_hit["imageinfo"] = [{
        **page["imageinfo"][0],
        "extmetadata": {"ImageDescription": {"value": "Metadata bibliography mentioning Margaret Thatcher"}},
    }]
    assert hunt.commons_image_candidate(
        misleading_search_hit,
        grounding_parent_url="https://commons.wikimedia.org/wiki/Category:Margaret_Thatcher",
        require_title_attribution=True,
    ) is None


def test_commons_archive_metadata_structures_all_source_named_people():
    page = {
        "title": "File:Ronald Reagan and Margaret Thatcher.jpg",
        "imageinfo": [{
            "mime": "image/jpeg", "url": "https://upload.wikimedia.org/original.jpg",
            "thumburl": "https://upload.wikimedia.org/thumb.jpg", "size": 12345,
            "width": 1600, "height": 1000,
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Reagan_Thatcher.jpg",
            "extmetadata": {
                "ImageDescription": {"value": "Ronald Reagan meets Margaret Thatcher at Camp David"},
                "LicenseShortName": {"value": "Public domain"},
            },
        }],
    }

    _page_record, candidate = hunt.commons_image_candidate(
        page, grounding_parent_url="https://commons.wikimedia.org/wiki/Category:Margaret_Thatcher",
    )

    assert candidate["source_named_people"] == ["Margaret Thatcher", "Ronald Reagan"]


def test_commons_api_honours_one_429_then_succeeds(monkeypatch):
    class ApiResponse:
        def __init__(self, status, payload, retry_after=None):
            self.status_code = status
            self._payload = payload
            self.headers = {"Retry-After": retry_after} if retry_after else {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class ApiSession:
        def __init__(self):
            self.responses = [ApiResponse(429, {}, "7"), ApiResponse(200, {"query": {}})]

        def get(self, *_args, **_kwargs):
            return self.responses.pop(0)

    waits = []
    monkeypatch.setattr(hunt.time, "sleep", waits.append)
    payload, rate_limits = hunt._commons_api_json(ApiSession(), {"action": "query"})
    assert payload == {"query": {}}
    assert waits == [7.0]
    assert rate_limits[0]["retry_after"] == 7.0


def test_commons_metadata_can_use_read_only_post_for_long_title_batches():
    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"query": {}}

    class Session:
        def get(self, *_args, **_kwargs):
            raise AssertionError("long metadata request used GET")

        def post(self, url, *, data, timeout, allow_redirects):
            assert allow_redirects is False
            assert url.endswith("/w/api.php")
            assert data["action"] == "query" and data["prop"] == "imageinfo"
            assert timeout == 45
            return Response()

    payload, _ = hunt._commons_api_json(
        Session(), {"action": "query", "prop": "imageinfo", "titles": "File:" + "x" * 9000},
        use_post=True,
    )
    assert payload == {"query": {}}


class FakeResponse:
    def __init__(self, payload: bytes, content_type: str = "image/png", url: str = "https://archive.example/image.png"):
        self.content = payload
        self.headers = {"Content-Type": content_type}
        self.url = url
        self.status_code = 200
        self.text = payload.decode("utf-8", errors="ignore")

    def raise_for_status(self):
        return None

    def iter_content(self, _size):
        yield self.content

    def close(self):
        return None


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.headers = {}

    def get(self, *_args, **_kwargs):
        return self.response


def png_bytes(size=(800, 500), colour=(80, 30, 120)) -> bytes:
    import io
    stream = io.BytesIO()
    Image.new("RGB", size, colour).save(stream, "PNG")
    return stream.getvalue()


def test_safe_download_decodes_image_and_rejects_html(tmp_path, monkeypatch):
    monkeypatch.setattr(hunt, "validate_public_url", lambda url, **_kwargs: url)
    candidate = {
        "candidate_id": "a" * 20, "direct_image_url": "https://archive.example/image.png",
        "identity_confidence": "high", "identity_evidence": "Margaret Thatcher in source caption",
    }
    result = hunt.download_candidate(candidate, tmp_path, FakeSession(FakeResponse(png_bytes())))
    assert result["width"] == 800 and result["image_sha256"]
    with pytest.raises(ValueError, match="HTML"):
        hunt.download_candidate(candidate, tmp_path, FakeSession(FakeResponse(b"<html>blocked</html>", "text/html")))


def test_image_download_honours_one_429_then_uses_archive_derivative(tmp_path, monkeypatch):
    class RateLimited(FakeResponse):
        def __init__(self):
            super().__init__(b"", "text/plain")
            self.status_code = 429
            self.headers["Retry-After"] = "6"

        def raise_for_status(self):
            raise AssertionError("first 429 should be retried")

    class SequenceSession:
        def __init__(self):
            self.responses = [RateLimited(), FakeResponse(png_bytes())]

        def get(self, *_args, **_kwargs):
            return self.responses.pop(0)

    waits = []
    monkeypatch.setattr(hunt, "validate_public_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(hunt.time, "sleep", waits.append)
    candidate = {
        "candidate_id": "b" * 20, "direct_image_url": "https://upload.wikimedia.org/thumb.jpg",
        "identity_confidence": "high", "identity_evidence": "Margaret Thatcher in archive metadata",
    }
    result = hunt.download_candidate(candidate, tmp_path, SequenceSession())
    assert result["width"] == 800
    assert waits == [6.0]


def test_two_image_download_429s_activate_archive_rate_limit(monkeypatch, tmp_path):
    class RateLimited(FakeResponse):
        def __init__(self):
            super().__init__(b"", "text/plain")
            self.status_code = 429
            self.headers["Retry-After"] = "9"

    class Session:
        def get(self, *_args, **_kwargs):
            return RateLimited()

    monkeypatch.setattr(hunt, "validate_public_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(hunt.time, "sleep", lambda _seconds: None)
    candidate = {
        "candidate_id": "c" * 20, "direct_image_url": "https://upload.wikimedia.org/thumb.jpg",
        "identity_confidence": "high", "identity_evidence": "Margaret Thatcher in archive metadata",
    }
    with pytest.raises(hunt.ArchiveRateLimitExhausted) as error:
        hunt.download_candidate(candidate, tmp_path, Session())
    assert error.value.retry_after == 9.0


def test_existing_checkpointed_candidate_file_is_recovered_without_download(tmp_path):
    candidate_id = "d" * 20
    path = tmp_path / "downloaded" / f"{candidate_id}.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(png_bytes())
    candidate = {
        "candidate_id": candidate_id, "direct_image_url": "https://archive.example/thumb.png",
        "identity_confidence": "high", "identity_evidence": "Margaret Thatcher in archive metadata",
    }
    recovered = hunt.recover_downloaded_candidate(candidate, tmp_path)
    assert recovered is not None
    assert recovered["recovered_from_existing_file"] is True
    assert recovered["image_sha256"] == hunt.sha256_file(path)


def test_duplicate_classification_exact_near_crop_and_distinct():
    base = {"sha256": "a", "phash": "0000000000000000", "dhash": "0000000000000000", "aspect_ratio": 1.0}
    assert hunt.classify_image_similarity(base, dict(base)) == "exact_duplicate"
    near = {**base, "sha256": "b", "phash": "0000000000000001", "dhash": "0000000000000001"}
    assert hunt.classify_image_similarity(base, near) == "near_duplicate"
    crop = {**near, "aspect_ratio": 1.4}
    assert hunt.classify_image_similarity(base, crop) == "probable_crop"
    distinct = {**near, "phash": "ffffffffffffffff", "dhash": "ffffffffffffffff"}
    assert hunt.classify_image_similarity(base, distinct) == "visually_distinct"


def test_dedupe_finds_existing_exact_duplicate(tmp_path):
    research = tmp_path / "image_discovery_research" / "run"
    downloaded = research / "downloaded"
    downloaded.mkdir(parents=True)
    source = Path("images/t01.jpg")
    target = downloaded / "same.jpg"
    target.write_bytes(source.read_bytes())
    hunt.atomic_write_text(research / "raw_candidates.jsonl", json.dumps({
        "candidate_id": "same", "local_path": "downloaded/same.jpg",
    }) + "\n")
    manifest = hunt.build_dedupe_manifest(BASELINE, research)
    assert manifest["class_counts"]["exact_duplicate"] == 1
    assert manifest["retained_count"] == 0


def fake_result(label: str = "ok") -> dict:
    raw = {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}
    return {
        "raw": raw, "parsed": {"records": []}, "parse_error": None, "normalisation": [],
        "grounding": {"queries": [], "sources": [], "supports": [], "search_entry_point": None},
        "usage": {"input_tokens": 10, "cached_tokens": 0, "output_tokens": 5},
        "cost_usd": 0.001, "latency_seconds": 0.01, "request_id": label,
    }


class HttpError(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class FakeClient:
    model = hunt.MODEL

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def settings_signature(self, phase, schema):
        return {"phase": phase, "schema": schema, "model": self.model}

    def call(self, **_kwargs):
        self.calls += 1
        value = self.outcomes.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def router(tmp_path, developer, vertex):
    return hunt.LogicalCallRouter(tmp_path, developer, vertex, sleep=lambda _seconds: None)


def route_call(value, call_id="discovery-01"):
    return value.run(logical_call_id=call_id, phase="discovery", prompt=call_id, schema=hunt.DISCOVERY_RESPONSE_SCHEMA)


def test_first_developer_429_retries_and_success_resets_counter(tmp_path):
    developer = FakeClient([HttpError(429), fake_result()])
    vertex = FakeClient([fake_result()])
    value = router(tmp_path, developer, vertex)
    result = route_call(value)
    assert result["provider"] == "developer_api"
    assert developer.calls == 2 and vertex.calls == 0
    assert value.state["consecutive_developer_429"] == 0


def test_two_developer_429s_trigger_vertex_and_future_direct_routing(tmp_path):
    developer = FakeClient([HttpError(429), HttpError(429)])
    vertex = FakeClient([fake_result("v1"), fake_result("v2")])
    value = router(tmp_path, developer, vertex)
    assert route_call(value)["provider"] == "vertex"
    assert value.state["developer_unavailable"] is True
    assert route_call(value, "discovery-02")["provider"] == "vertex"
    assert developer.calls == 2 and vertex.calls == 2
    assert value.state["direct_to_vertex_count"] == 1


def test_non_429_never_triggers_vertex(tmp_path):
    developer = FakeClient([HttpError(500), HttpError(500)])
    vertex = FakeClient([fake_result()])
    value = router(tmp_path, developer, vertex)
    with pytest.raises(HttpError):
        route_call(value)
    assert developer.calls == 2 and vertex.calls == 0 and value.state["developer_unavailable"] is False


def test_non_transient_400_is_not_retried_or_failed_over(tmp_path):
    developer = FakeClient([HttpError(400)])
    vertex = FakeClient([fake_result()])
    with pytest.raises(HttpError):
        route_call(router(tmp_path, developer, vertex))
    assert developer.calls == 1 and vertex.calls == 0


def test_local_parser_failure_is_not_classified_as_transport_failure():
    details = hunt._error_details(ValueError("bad JSON shape"))
    assert details["is_transient"] is False


def test_vertex_429_retries_once_then_stops(tmp_path):
    developer = FakeClient([HttpError(429), HttpError(429)])
    vertex = FakeClient([HttpError(429), HttpError(429)])
    with pytest.raises(RuntimeError, match="Vertex fallback stopped"):
        route_call(router(tmp_path, developer, vertex))
    assert vertex.calls == 2


def test_completed_logical_call_is_cached_on_resume(tmp_path):
    developer = FakeClient([fake_result()])
    vertex = FakeClient([])
    value = router(tmp_path, developer, vertex)
    first = route_call(value)
    second = route_call(value)
    assert first == second and developer.calls == 1


def test_resume_preserves_attempt_numbering_and_retry_exhaustion(tmp_path):
    developer = FakeClient([HttpError(500), HttpError(500), fake_result()])
    vertex = FakeClient([])
    value = router(tmp_path, developer, vertex)
    with pytest.raises(HttpError):
        route_call(value)
    resumed = router(tmp_path, developer, vertex)
    with pytest.raises(RuntimeError, match="already exhausted"):
        route_call(resumed)
    rows = hunt.read_jsonl_if_exists(tmp_path / "provider_attempts.jsonl")
    assert [row["attempt"] for row in rows] == [1, 2]
    assert developer.calls == 2


def test_interrupted_call_is_persisted_and_not_replayed(tmp_path):
    developer = FakeClient([KeyboardInterrupt(), fake_result()])
    vertex = FakeClient([])
    value = router(tmp_path, developer, vertex)
    with pytest.raises(KeyboardInterrupt):
        route_call(value)
    resumed = router(tmp_path, developer, vertex)
    with pytest.raises(RuntimeError, match="automatic replay refused"):
        route_call(resumed)
    assert developer.calls == 1


def test_success_between_429s_breaks_consecutive_sequence(tmp_path):
    developer = FakeClient([HttpError(429), fake_result(), HttpError(429), fake_result()])
    vertex = FakeClient([fake_result()])
    value = router(tmp_path, developer, vertex)
    route_call(value, "discovery-01")
    route_call(value, "discovery-02")
    assert developer.calls == 4 and vertex.calls == 0
    assert value.state["developer_unavailable"] is False


def minimal_manifest(research: Path):
    image = research / "downloaded" / "c.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(png_bytes())
    record = {
        "candidate_id": "candidate", "local_path": "downloaded/c.jpg",
        "page_title": "Archive", "publisher": "Institution", "caption": "Caption",
        "alt_text": "", "source_page_url": "https://archive.example/page",
        "direct_image_url": "https://archive.example/image.png",
        "event_or_period": "1980s", "approximate_date": "1984",
        "identity_basis": "source_caption", "identity_confidence": "high",
        "identity_evidence": "Margaret Thatcher at a factory", "rights_status": "rights_unclear",
        "licence_text": "No licence recorded", "rights_url": "", "width": 800, "height": 500,
        "source_file_size": image.stat().st_size,
        "visual_analysis": {"scene_summary": "The woman speaks with workers."},
        "editorial_value": {"quality": 80, "recommended_priority": "high"},
        "semantic_novelty": {"coverage_gaps_filled": ["industrial visit"], "nearest_existing_images": []},
    }
    hunt.atomic_write_json(research / "candidate_manifest.json", {
        "schema_version": 1, "candidate_count": 1, "rights_status_counts": {"rights_unclear": 1}, "records": [record],
    })
    hunt.atomic_write_json(research / "review_state.json", {"schema_version": 1, "reviews": {}})


class FakeEmbedder:
    def encode(self, texts, **_kwargs):
        rows = []
        for text in texts:
            value = sum(text.encode("utf-8")) % 97
            vector = np.asarray([value + 1.0, 98.0 - value], dtype=np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.vstack(rows)


def test_semantic_novelty_builds_nearest_existing_comparison(tmp_path):
    research = tmp_path / "image_discovery_research" / "run"
    minimal_manifest(research)
    record = hunt.read_json(research / "candidate_manifest.json")["records"][0]
    raw_record = {
        key: value for key, value in record.items() if key not in {"visual_analysis", "editorial_value", "semantic_novelty"}
    }
    stale_record = {**raw_record, "candidate_id": "stale-candidate"}
    hunt.atomic_write_text(
        research / "raw_candidates.jsonl",
        json.dumps(raw_record) + "\n" + json.dumps(stale_record) + "\n",
    )
    hunt.atomic_write_json(research / "coverage_profile.json", hunt.build_coverage_profile(hunt.load_baseline(BASELINE)))
    hunt.atomic_write_json(research / "dedupe_manifest.json", {
        "candidates": [{
            "candidate_id": "candidate", "local_path": "downloaded/c.jpg", "fingerprints": {},
            "duplicate_class": "visually_distinct", "existing_matches": [], "candidate_matches": [],
            "possible_replacement": False, "retained_for_triage": True,
        }, {
            "candidate_id": "stale-candidate", "local_path": "downloaded/c.jpg", "fingerprints": {},
            "duplicate_class": "near_duplicate", "existing_matches": [], "candidate_matches": [],
            "possible_replacement": False, "retained_for_triage": False,
        }],
    })
    normal = research / "triage_batches" / "normalised"
    normal.mkdir(parents=True)
    triage_record = {
        "candidate_id": "candidate",
        "visual_analysis": {
            "description": "The woman walks through a factory.", "scene_summary": "Active industrial visit.",
            "scene_types": ["factory_visit"], "setting": {"location_type": "indoor", "details": ["factory"]},
            "people": {"count_category": "small_group", "primary_subject_activities": ["walking"], "primary_subject_moods": ["warm"]},
            "tone": ["warm"], "visual_energy": "high", "visible_elements": ["machinery"],
            "visible_symbols": [], "pairing": {"best_for_topics": ["industry"]},
        },
        "editorial_value": {"quality": 80, "distinctiveness": 85, "coverage_gap_value": 90, "recommended_priority": "high", "reasons": []},
    }
    hunt.atomic_write_json(normal / "triage-01.json", {"parsed": {"records": [
        triage_record,
        {**triage_record, "candidate_id": "stale-candidate"},
    ]}})
    manifest = hunt.build_candidate_manifest(BASELINE, research, embedder=FakeEmbedder())
    assert [row["candidate_id"] for row in manifest["records"]] == ["candidate"]
    novelty = manifest["records"][0]["semantic_novelty"]
    assert len(novelty["nearest_existing_images"]) == 3
    assert "high visual energy" in novelty["coverage_gaps_filled"]
    assert novelty["embedding_model"] == hunt.E5_MODEL_ID


def test_successful_final_report_does_not_claim_pilot_failure(tmp_path):
    research = tmp_path / "image_discovery_research" / "run"
    research.mkdir(parents=True)
    profile = hunt.build_coverage_profile(hunt.load_baseline(BASELINE))
    hunt.atomic_write_json(research / "coverage_profile.json", profile)
    hunt.atomic_write_json(research / "provider_route_state.json", {
        "completed_logical_calls": {}, "known_spend_usd": {}, "developer_unavailable": False,
    })
    hunt.atomic_write_json(research / "source_pages.json", {"pages": [], "rejected_unsupported_records": []})
    hunt.atomic_write_json(research / "dedupe_manifest.json", {"class_counts": {}, "retained_count": 1})
    hunt.atomic_write_json(research / "candidate_manifest.json", {
        "records": [{"candidate_id": "candidate", "rights_status": "public_domain"}],
        "rights_status_counts": {"public_domain": 1},
    })
    hunt.atomic_write_json(research / "pilot_gate.json", {"passed": True, "checks": {}})
    hunt.atomic_write_json(research / "review_state.json", {
        "schema_version": 1,
        "reviews": {
            "candidate": {
                "candidate_id": "candidate", "decision": "keep", "note": "good",
                "revision": 1, "reviewed_at": "2026-07-16T00:00:00Z",
            },
        },
    })

    summary = hunt.build_final_report(research, BASELINE)

    report = (research / "final_report.md").read_text(encoding="utf-8")
    assert "pilot gate passed" in report.casefold()
    assert "pilot gate failed" not in report.casefold()
    assert "target not reached" in report.casefold()
    assert "Human review complete: yes" in report
    assert "Keep: 1" in report
    assert "Maybe: 0" in report
    assert "Reject: 0" in report
    assert "Human review and an explicit" not in report
    assert summary["review"] == {
        "reviewed_count": 1,
        "unreviewed_count": 0,
        "complete": True,
        "decision_counts": {"keep": 1},
        "outcome_counts": {"keep": 1, "maybe": 0, "reject": 0},
        "note_count": 1,
        "revision_count": 1,
        "kept_rights_status_counts": {"public_domain": 1},
        "production_ready_kept_count": 1,
    }


def test_review_autosave_audit_and_idempotence(tmp_path):
    research = tmp_path / "image_discovery_research" / "run"
    minimal_manifest(research)
    first = hunt.save_review(research, "candidate", "keep", "good")
    second = hunt.save_review(research, "candidate", "keep", "good")
    assert first == second
    assert len(hunt.read_jsonl_if_exists(research / "review_audit.jsonl")) == 1
    changed = hunt.save_review(research, "candidate", "maybe", "check rights")
    assert changed["reviews"]["candidate"]["revision"] == 2
    assert (research / "review_state.backup.json").is_file()


def test_reviewer_has_ipad_layout_and_all_controls_without_hotlinking():
    assert "@media(max-width:800px)" in hunt.REVIEW_HTML
    assert "Keep as replacement" in hunt.REVIEW_HTML
    assert "Reject — rights concern" in hunt.REVIEW_HTML
    assert "direct_image_url" not in hunt.REVIEW_HTML
    assert "src='/candidate/'" in hunt.REVIEW_HTML or "src='/candidate/" in hunt.REVIEW_HTML
    assert "&lt;" in hunt.REVIEW_HTML and "replace(/[&<>" in hunt.REVIEW_HTML


def test_discovery_prompt_requires_object_envelope():
    prompt = hunt.discovery_prompt(hunt.build_discovery_briefs(hunt.build_coverage_profile(hunt.load_baseline(BASELINE)))[0])
    assert "must execute Google Search" in prompt
    assert "solely from provider grounding metadata" in prompt


def test_discovery_records_are_constructed_only_from_linked_grounding():
    brief = hunt.build_discovery_briefs(hunt.build_coverage_profile(hunt.load_baseline(BASELINE)))[0]
    records = hunt.records_from_provider_grounding({
        "queries": ["archive query"],
        "sources": [
            {"title": "Margaret Thatcher archive", "url": "https://vertexaisearch.cloud.google.com/redirect/1", "supports": ["Margaret Thatcher visits a school."]},
            {"title": "Unlinked", "url": "https://example.org", "supports": []},
        ],
    }, brief)
    assert len(records) == 1
    assert records[0]["source_page_url"].startswith("https://vertexaisearch")
    assert records[0]["search_queries_used"] == ["archive query"]


def test_triage_uses_prompt_schema_and_strict_local_validation():
    prompt = hunt.triage_prompt(["candidate-a"], ["outdoor scenes"])
    assert '"visual_analysis"' in prompt and '"editorial_value"' in prompt
    client = hunt.GeminiHuntClient.__new__(hunt.GeminiHuntClient)
    client.timeout_seconds = 30
    config = client.config("triage", hunt.TRIAGE_RESPONSE_SCHEMA)
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema is None


def test_triage_capacity_stops_at_phase_and_total_logical_call_limits():
    phases = {f"discovery-{index}": "discovery" for index in range(6)}
    phases.update({f"triage-{index}": "triage" for index in range(3)})
    assert hunt.remaining_logical_call_capacity(phases, "triage") == 3
    phases.update({f"other-{index}": "other" for index in range(3)})
    assert hunt.remaining_logical_call_capacity(phases, "triage") == 0


def test_harvester_can_quarantine_unusable_saved_response(tmp_path, monkeypatch):
    research = tmp_path / "run"
    normal = research / "grounded_discovery" / "normalised"
    normal.mkdir(parents=True)
    hunt.atomic_write_json(normal / "discovery-bad.json", {
        "logical_call_id": "discovery-bad", "parsed": {"url": "https://example.org"},
        "parse_error": None, "grounding": {},
    })
    monkeypatch.setattr(hunt, "resolve_grounded_sources", lambda *_args, **_kwargs: [])
    summary = hunt.harvest_saved_discovery(research, session=FakeSession(None))
    assert summary["downloaded_candidates"] == 0
    audit = hunt.read_json(research / "source_pages.json")
    assert audit["rejected_unsupported_records"][0]["reason"] == "provider_response_unusable"


def test_export_includes_only_human_kept_and_stays_isolated(tmp_path):
    research = tmp_path / "image_discovery_research" / "run"
    minimal_manifest(research)
    manifest = hunt.read_json(research / "candidate_manifest.json")
    manifest["records"][0]["rights_status"] = "public_domain"
    manifest["records"][0]["credit"] = "Example archive"
    manifest["records"][0]["source_named_people"] = ["Margaret Thatcher", "Ronald Reagan"]
    manifest["records"][0]["image_sha256"] = hunt.sha256_file(research / "downloaded/c.jpg")
    hunt.atomic_write_json(research / "candidate_manifest.json", manifest)
    hunt.save_review(research, "candidate", "keep", "")
    result = hunt.export_kept(research, research / "exported_kept")
    assert result["exported_count"] == 1
    assert result["production_ready_count"] == 1
    exported = hunt.read_json(research / "exported_kept/manifests/export_manifest.json")
    assert exported["records"][0]["export_group"] == "production_ready"
    assert exported["records"][0]["source_named_people"] == ["Margaret Thatcher", "Ronald Reagan"]
    assert (research / "exported_kept/production_ready/candidate-candidate.jpg").is_file()
    assert not list((research / "exported_kept/rights_pending").glob("candidate-*"))
    second = hunt.export_kept(research, research / "exported_kept")
    assert second["reused"] is True
    with pytest.raises(RuntimeError):
        hunt.export_kept(research, tmp_path / "images")


def test_export_segregates_rights_pending_and_maybe(tmp_path):
    research = tmp_path / "image_discovery_research" / "run"
    minimal_manifest(research)
    manifest = hunt.read_json(research / "candidate_manifest.json")
    first = manifest["records"][0]
    first["rights_status"] = "rights_unclear"
    first["image_sha256"] = hunt.sha256_file(research / first["local_path"])
    maybe_image = research / "downloaded/maybe.jpg"
    maybe_image.write_bytes(png_bytes())
    maybe = dict(first, candidate_id="maybe", local_path="downloaded/maybe.jpg")
    maybe["image_sha256"] = hunt.sha256_file(maybe_image)
    manifest["records"] = [first, maybe]
    manifest["candidate_count"] = 2
    hunt.atomic_write_json(research / "candidate_manifest.json", manifest)
    hunt.save_review(research, "candidate", "keep", "")
    hunt.save_review(research, "maybe", "maybe", "")

    result = hunt.export_kept(research, research / "exported_kept")

    assert result["production_ready_count"] == 0
    assert result["rights_pending_count"] == 1
    assert result["maybe_count"] == 1
    assert (research / "exported_kept/rights_pending/candidate-candidate.jpg").is_file()
    assert (research / "exported_kept/maybe/candidate-maybe.jpg").is_file()


def test_export_preserves_required_attribution_wording(tmp_path):
    research = tmp_path / "image_discovery_research" / "run"
    minimal_manifest(research)
    manifest = hunt.read_json(research / "candidate_manifest.json")
    record = manifest["records"][0]
    record.update({
        "rights_status": "attribution_required", "credit": "Example Photographer",
        "licence_text": "CC BY-SA 4.0", "rights_url": "https://creativecommons.org/licenses/by-sa/4.0",
        "image_sha256": hunt.sha256_file(research / record["local_path"]),
    })
    hunt.atomic_write_json(research / "candidate_manifest.json", manifest)
    hunt.save_review(research, "candidate", "keep", "")
    hunt.export_kept(research, research / "exported_kept")
    exported = hunt.read_json(research / "exported_kept/manifests/production_ready_manifest.json")["records"][0]
    assert exported["required_attribution_wording"] == (
        "Example Photographer. Licensed under CC BY-SA 4.0 "
        "(https://creativecommons.org/licenses/by-sa/4.0)."
    )


def test_pipeline_source_does_not_reference_other_ai_or_production_writes():
    source = Path("semantic_alignment/thatcher_image_hunt.py").read_text(encoding="utf-8")
    for provider in ("xai", "openai", "anthropic"):
        assert f"{provider}.Client" not in source
    assert "images/t" not in source
    assert source.count("confirmed_post_receipt") == 1


def test_preflight_and_dry_run_make_no_client(monkeypatch, tmp_path):
    monkeypatch.setattr(hunt, "create_clients", lambda: (_ for _ in ()).throw(AssertionError("live client created")))
    result = hunt.run_pipeline(BASELINE, tmp_path / "image_discovery_research" / "run", execute=False, confirmed_cost=None)
    assert result["status"] == "preflight_only"


def test_offline_profile_and_preflight_leave_production_images_unchanged(tmp_path):
    before = {path.name: hunt.sha256_file(path) for path in sorted(Path("images").glob("t*.jpg"))}
    baseline_before = hunt.sha256_file(BASELINE)
    research = tmp_path / "image_discovery_research" / "run"
    hunt.write_coverage_outputs(BASELINE, research)
    hunt.api_preflight(research)
    after = {path.name: hunt.sha256_file(path) for path in sorted(Path("images").glob("t*.jpg"))}
    assert before == after
    assert hunt.sha256_file(BASELINE) == baseline_before


def test_exact_cost_confirmation_required_before_clients(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project")
    monkeypatch.setattr(Path, "is_file", lambda self: True if "application_default_credentials" in str(self) else Path.exists(self))
    monkeypatch.setattr(hunt, "create_clients", lambda: (_ for _ in ()).throw(AssertionError("should not create")))
    with pytest.raises(RuntimeError, match="exact --confirm"):
        hunt.run_pipeline(BASELINE, tmp_path / "image_discovery_research" / "run", execute=True, confirmed_cost=4.99)


@pytest.mark.parametrize("body,mime", [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (png_bytes()[:-4], "image/png"),
    (png_bytes(), "image/jpeg"),
])
def test_hunt_download_requires_complete_image_and_matching_mime(tmp_path, body, mime):
    candidate = {"direct_image_url": "https://public.example/photo", "identity_confidence": "high", "identity_evidence": "catalogue", "candidate_id": "fixture"}
    with pytest.raises(ValueError):
        hunt.download_candidate(candidate, tmp_path, FakeSession(FakeResponse(body, mime)))
    assert not (tmp_path / "downloaded").exists()


def test_hunt_download_rejects_oversized_decoded_dimensions(tmp_path):
    import io
    encoded = io.BytesIO()
    Image.new("RGB", (8193, 1)).save(encoded, format="PNG")
    candidate = {"direct_image_url": "https://public.example/photo", "identity_confidence": "high", "identity_evidence": "catalogue", "candidate_id": "fixture"}
    with pytest.raises(ValueError, match="dimensions"):
        hunt.download_candidate(candidate, tmp_path, FakeSession(FakeResponse(encoded.getvalue())))

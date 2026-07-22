import copy

import pytest

import historical_context_mtf_primary_review as mtf_review
from historical_context_mtf_primary_review import (
    DEFAULT_REQUEST_DELAY,
    DEFAULT_WORKERS,
    _document_sets,
    _event_similarity,
    _normalise_date,
    _sequence_coverage,
    _tokens,
    parse_official_page,
)


DOCUMENT = "104653"
QUOTE_ID = "a" * 64
QUOTE_TEXT = (
    "Never forget that the Marxist societies call themselves, and indeed "
    "are, the Union of Soviet Socialist Republics. Some of the aims of "
    "socialism, are the aims of a Marxist society, and they result in the "
    "subjugation of the rights of people to political theory."
)
OFFICIAL_HTML = b"""<!doctype html><html><head>
<link rel="canonical" href="https://www.margaretthatcher.org/document/104653">
</head><body><article class="node-archive-document">
<div class="docdate"><time datetime="1981-05-20">1981 May 20 We</time></div>
<div class="docauthor">Margaret Thatcher</div>
<h1 class="doctitle">Speech to Conservative Women&rsquo;s Conference</h1>
<p>And never forget that the Marxist societies call themselves, and indeed are,
the Union of Soviet Socialist Republics. Some of the aims of socialism, are the
aims of a Marxist society, and they result in the subjugation of the rights of
people to political theory.</p></article></body></html>"""
NOT_FOUND_HTML = b"""<!doctype html><html><head>
<title>Page not found | Margaret Thatcher Foundation</title>
<link rel="canonical" href="https://www.margaretthatcher.org/document/109236">
</head><body><h1>Page not found</h1></body></html>"""


def test_official_page_parser_retains_compact_hash_bound_metadata():
    page = parse_official_page(DOCUMENT, OFFICIAL_HTML)

    assert page["canonical_url_matches"] is True
    assert page["author"] == "Margaret Thatcher"
    assert page["date"] == "1981-05-20"
    assert page["title"] == "Speech to Conservative Women’s Conference"
    assert len(page["transported_page_sha256"]) == 64
    assert len(page["article_text_sha256"]) == 64
    assert "political theory" in page["_article_text"]


def test_official_page_parser_classifies_deterministic_not_found_page():
    with pytest.raises(mtf_review.OfficialDocumentNotFoundError) as caught:
        parse_official_page("109236", NOT_FOUND_HTML)

    assert caught.value.document_number == "109236"
    assert caught.value.canonical_url.endswith("/109236")
    assert len(caught.value.transported_page_sha256) == 64


def test_104653_excerpt_is_a_complete_contiguous_token_match():
    page = parse_official_page(DOCUMENT, OFFICIAL_HTML)

    assert _sequence_coverage(
        _tokens(QUOTE_TEXT), _tokens(page["_article_text"])
    ) == 1.0


def test_date_and_event_comparisons_are_conservative():
    assert _normalise_date("20 May 1981") == "1981-05-20"
    assert _normalise_date("May 20, 1981") == "1981-05-20"
    assert _normalise_date("Unknown (published 1995)") == ""
    assert _event_similarity(
        "Speech to Conservative Women's Conference",
        "Speech to Conservative Women’s Conference",
    ) == 1.0
    assert _event_similarity("Interview for BBC", "Speech to Party Conference") == 0.0


def test_packet_locator_candidates_remain_reviewable_but_not_accepted():
    sets = _document_sets({
        "accepted_document_numbers": ["104066"],
        "packet_locator_candidate_document_numbers": ["104077"],
        "lead_document_numbers": [],
        "public_document_numbers": ["104066"],
    })

    assert sets == {
        "accepted": {"104066"},
        "packet_locator_candidate": {"104077"},
        "lead": set(),
        "public": {"104066"},
    }


def _install_synthetic_corpus(
    monkeypatch,
    *,
    source_event="Speech to Conservative Women's Conference",
    priority=True,
):
    packet = {
        "quote_id": QUOTE_ID,
        "quote_text": QUOTE_TEXT,
        "source_event": source_event,
        "date": "20 May 1981",
    }
    candidate = {
        "quote_id": QUOTE_ID,
        "accepted_document_numbers": [DOCUMENT],
        "packet_locator_candidate_document_numbers": [],
        "lead_document_numbers": [],
        "public_document_numbers": [],
    }
    truth = {
        "input_hashes": {
            "research_packets.json": "1" * 64,
            "historical_context_source_role_audit.json": "2" * 64,
        },
        "records": {
            "precise_mtf_identities": [candidate],
            "eligible_same_document_event_or_date_priorities": (
                [{"quote_id": QUOTE_ID}] if priority else []
            ),
        },
    }
    monkeypatch.setattr(
        mtf_review, "build_truth_audit", lambda *_args, **_kwargs: truth
    )
    monkeypatch.setattr(
        mtf_review,
        "load_and_validate_corpus",
        lambda *_args, **_kwargs: ({QUOTE_ID: packet}, set()),
    )
    return truth


def _page_fetcher(_number, *, reader_base):
    assert reader_base == "https://reader.test/document/"
    return parse_official_page(DOCUMENT, OFFICIAL_HTML)


def _build_synthetic_review(monkeypatch, **kwargs):
    return mtf_review.build_review(
        reader_base="https://reader.test/document/",
        workers=1,
        request_delay=0.0,
        page_fetcher=_page_fetcher,
        **kwargs,
    )


def test_review_defaults_are_conservative_for_rate_limited_transport():
    assert DEFAULT_WORKERS == 1
    assert DEFAULT_REQUEST_DELAY >= 1.0


def test_fetch_honours_retry_after_without_busy_retrying(monkeypatch):
    sleeps = []
    attempts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return OFFICIAL_HTML

    def urlopen(request, *, timeout):
        attempts.append((request.full_url, timeout))
        if len(attempts) == 1:
            raise mtf_review.urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "7"},
                None,
            )
        return Response()

    monkeypatch.setattr(mtf_review.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mtf_review.time, "sleep", sleeps.append)

    page = mtf_review.fetch_official_page(
        DOCUMENT,
        reader_base="https://reader.test/document/",
        timeout=5.0,
        attempts=2,
    )

    assert page["document_number"] == DOCUMENT
    assert len(attempts) == 2
    assert sleeps == [7.0]


def test_fetch_does_not_retry_deterministic_not_found_page(monkeypatch):
    attempts = []
    sleeps = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return NOT_FOUND_HTML

    def urlopen(request, *, timeout):
        attempts.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(mtf_review.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mtf_review.time, "sleep", sleeps.append)

    with pytest.raises(mtf_review.OfficialDocumentNotFoundError):
        mtf_review.fetch_official_page(
            "109236",
            reader_base="https://reader.test/document/",
            attempts=3,
        )

    assert len(attempts) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("source_event", "expected_status", "expected_comparison"),
    [
        (
            "Speech to the Polish Senate in Warsaw",
            "manual_review_required",
            "no_token_overlap",
        ),
        ("Unknown", "no_machine_detected_discrepancy", "packet_event_not_known"),
    ],
)
def test_known_zero_overlap_event_requires_manual_review(
    monkeypatch, source_event, expected_status, expected_comparison
):
    _install_synthetic_corpus(monkeypatch, source_event=source_event)

    review = _build_synthetic_review(monkeypatch)
    record = review["records"][0]

    assert record["status"] == expected_status
    assert record["event_comparison"] == expected_comparison
    assert record["event_title_token_similarity"] == 0.0


def test_resume_is_bound_and_recomputes_dynamic_scope_and_provenance(monkeypatch):
    truth = _install_synthetic_corpus(monkeypatch, priority=True)
    first = _build_synthetic_review(monkeypatch)
    prior = copy.deepcopy(first)
    prior["records"][0]["scope"] = "remaining_precise_mtf_candidate"
    prior["records"][0]["identity_provenance"] = ["lead"]

    def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("a validated resumed page must not be fetched again")

    resumed = mtf_review.build_review(
        reader_base="https://reader.test/document/",
        workers=1,
        request_delay=0.0,
        prior_review=prior,
        page_fetcher=unexpected_fetch,
    )
    record = resumed["records"][0]
    assert record["scope"] == "priority_same_document_event_or_date"
    assert record["identity_provenance"] == ["accepted"]
    assert resumed["counts"]["page_retrieval_reused_count"] == 1
    assert resumed["counts"]["status_counts"] == {
        "no_machine_detected_discrepancy": 1,
    }

    stale = copy.deepcopy(prior)
    truth["input_hashes"]["historical_context_source_role_audit.json"] = "3" * 64
    with pytest.raises(RuntimeError, match="incompatible with current inputs"):
        mtf_review.build_review(
            reader_base="https://reader.test/document/",
            workers=1,
            request_delay=0.0,
            prior_review=stale,
            page_fetcher=unexpected_fetch,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda prior: prior.__setitem__("schema_version", 999),
        lambda prior: prior["records"].append(copy.deepcopy(prior["records"][0])),
        lambda prior: prior["records"][0].__setitem__(
            "article_text_sha256", "0" * 64
        ),
    ],
)
def test_resume_rejects_incompatible_or_inconsistent_records(
    monkeypatch, mutation
):
    _install_synthetic_corpus(monkeypatch)
    prior = _build_synthetic_review(monkeypatch)
    mutation(prior)

    with pytest.raises(RuntimeError, match="prior MTF review"):
        _build_synthetic_review(monkeypatch, prior_review=prior)


def test_resume_transport_is_bound(monkeypatch):
    _install_synthetic_corpus(monkeypatch)
    prior = _build_synthetic_review(monkeypatch)

    with pytest.raises(RuntimeError, match="incompatible with current inputs"):
        mtf_review.build_review(
            reader_base="https://different-reader.test/document/",
            workers=1,
            request_delay=0.0,
            prior_review=prior,
            page_fetcher=lambda *_args, **_kwargs: pytest.fail(
                "transport mismatch must fail before fetching"
            ),
        )


def test_failed_page_is_retried_on_resume(monkeypatch):
    _install_synthetic_corpus(monkeypatch)

    def failed_fetch(*_args, **_kwargs):
        raise RuntimeError("rate limited")

    failed = mtf_review.build_review(
        reader_base="https://reader.test/document/",
        workers=1,
        request_delay=0.0,
        page_fetcher=failed_fetch,
    )
    assert failed["counts"]["page_retrieval_failure_count"] == 1
    assert failed["counts"]["status_counts"] == {"page_retrieval_failed": 1}

    resumed = _build_synthetic_review(monkeypatch, prior_review=failed)
    assert resumed["counts"]["page_retrieval_failure_count"] == 0
    assert resumed["counts"]["page_retrieval_new_success_count"] == 1
    assert resumed["records"][0]["status"] == "no_machine_detected_discrepancy"


def test_not_found_page_is_counted_separately_and_reused_on_resume(monkeypatch):
    _install_synthetic_corpus(monkeypatch)

    def not_found_fetch(number, *, reader_base):
        assert reader_base == "https://reader.test/document/"
        raise mtf_review.OfficialDocumentNotFoundError(
            number,
            canonical_url=f"{mtf_review.OFFICIAL_BASE}{number}",
            transported_page_sha256="4" * 64,
        )

    review = mtf_review.build_review(
        reader_base="https://reader.test/document/",
        workers=1,
        request_delay=0.0,
        page_fetcher=not_found_fetch,
    )

    assert review["counts"]["page_retrieval_failure_count"] == 0
    assert review["counts"]["official_document_not_found_count"] == 1
    assert review["counts"]["status_counts"] == {
        "official_document_not_found": 1,
    }
    assert review["records"][0]["status"] == "official_document_not_found"

    resumed = mtf_review.build_review(
        reader_base="https://reader.test/document/",
        workers=1,
        request_delay=0.0,
        prior_review=review,
        page_fetcher=lambda *_args, **_kwargs: pytest.fail(
            "a deterministic archive 404 must not be retried"
        ),
    )
    assert resumed["counts"]["official_document_not_found_reused_count"] == 1
    assert resumed["records"][0]["status"] == "official_document_not_found"

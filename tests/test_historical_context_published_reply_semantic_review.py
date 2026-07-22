from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from historical_context_published_reply_semantic_review import (
    MTF_REVIEW_PATH,
    OUTPUT_PATH,
    SOURCE_ROLE_AUDIT_PATH,
    TRUTH_AUDIT_PATH,
    _is_successful_mtf_page_record,
    build_review,
    validate_review,
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_published_reply_review_is_complete_hash_bound_and_reproducible():
    review = _load(OUTPUT_PATH)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (TRUTH_AUDIT_PATH, SOURCE_ROLE_AUDIT_PATH, MTF_REVIEW_PATH)
    }

    counts = validate_review(review)

    assert review == build_review()
    assert counts == {
        "reviewed": 77,
        "supported_as_published": 41,
        "future_correction_needed": 30,
        "insufficient_to_assess": 6,
        "resolved": 14,
        "remaining": 22,
    }
    assert len({record["quote_id"] for record in review["records"]}) == 77
    assert all(record["reason"].strip() for record in review["records"])
    assert all(record["evidence_basis"] for record in review["records"])
    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in before
    }


def test_every_remaining_item_is_backed_by_one_open_record():
    review = _load(OUTPUT_PATH)
    open_records = {
        record["quote_id"]: record
        for record in review["records"]
        if record["follow_up_status"] == "remains_open"
    }
    remaining = {item["quote_id"]: item for item in review["remaining_items"]}

    assert set(remaining) == set(open_records)
    for quote_id, item in remaining.items():
        assert item["disposition"] == open_records[quote_id]["disposition"]
        assert item["issue"] == open_records[quote_id]["reason"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["records"].pop(),
        lambda value: value["records"][0].__setitem__("reason", ""),
        lambda value: value["records"][0]["evidence_basis"][
            "renderable_source_ids"
        ].append("f" * 64),
    ],
)
def test_published_reply_review_rejects_missing_or_unbound_records(mutation):
    review = copy.deepcopy(_load(OUTPUT_PATH))
    mutation(review)

    with pytest.raises(RuntimeError, match="differs from reviewed evidence ledger"):
        validate_review(review)


def test_104653_is_resolved_only_for_future_rendering():
    review = _load(OUTPUT_PATH)
    quote_id = (
        "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18"
    )
    record = next(row for row in review["records"] if row["quote_id"] == quote_id)

    assert record["disposition"] == "insufficient_to_assess"
    assert record["follow_up_status"] == "resolved_by_current_rendering"
    assert record["published_reply_sha256"] != record["current_reply_sha256"]
    assert review["current_render_follow_up"]["published_history_modified"] is False


def test_not_found_mtf_document_is_not_treated_as_successful_page_check():
    assert _is_successful_mtf_page_record({
        "status": "official_document_not_found",
        "document_number": "109236",
    }) is False
    assert _is_successful_mtf_page_record({
        "status": "page_retrieval_failed",
        "document_number": "109236",
    }) is False
    assert _is_successful_mtf_page_record({
        "status": "manual_review_required",
        "document_number": "104653",
    }) is True

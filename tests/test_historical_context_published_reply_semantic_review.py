from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from historical_context_published_reply_semantic_review import (
    MTF_REVIEW_PATH,
    OUTPUT_PATH,
    POST_BASELINE_REVIEW_IDS,
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
        "reviewed": 115,
        "supported_as_published": 63,
        "future_correction_needed": 33,
        "insufficient_to_assess": 19,
        "resolved": 39,
        "remaining": 13,
    }
    assert len({record["quote_id"] for record in review["records"]}) == 115
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


@pytest.mark.parametrize(
    ("quote_id", "reason_fragment", "follow_up_status"),
    [
        (
            "34114f8f8fa580a2cb413c481408094ad2a8675ebb59955cf8c7d665897d8381",
            "Inspected primary document 108338",
            "resolved_by_current_rendering",
        ),
        (
            "a97e6dd2f444ecfbba67977a34be91db40d17eb09c8566fe714e48bffddb11f7",
            "government intervention",
            "remains_open",
        ),
    ],
)
def test_post_baseline_reply_is_explicitly_reviewed_and_not_default_supported(
    quote_id: str,
    reason_fragment: str,
    follow_up_status: str,
):
    review = _load(OUTPUT_PATH)
    record = next(row for row in review["records"] if row["quote_id"] == quote_id)

    assert review["review_scope"]["original_review_baseline_count"] == 77
    assert review["review_scope"]["post_baseline_review_quote_ids"] == sorted(
        POST_BASELINE_REVIEW_IDS
    )
    assert record["disposition"] == "future_correction_needed"
    assert record["follow_up_status"] == follow_up_status
    assert reason_fragment in record["reason"]


def test_latest_history_row_is_explicitly_held_for_future_correction():
    review = _load(OUTPUT_PATH)
    quote_id = (
        "a4a987cdde97c2a8a9a7ec8cfd0acdc49af065a217ef8ed0c2e817754fa5e97e"
    )
    record = next(row for row in review["records"] if row["quote_id"] == quote_id)

    assert record["disposition"] == "future_correction_needed"
    assert record["follow_up_status"] == "remains_open"
    assert "categorical claim" in record["reason"]
    assert "inherent causal mechanism" in record["reason"]


def test_post_baseline_rows_reflect_reviewed_mtf_and_still_open_cases():
    review = _load(OUTPUT_PATH)
    expected = {
        "880a2f32c7d03b24c72c6e4e3d8c5799c6a7af14a9497f11881aeddb123d5be7",
        "928a6686bc6bb6d35cd1ec139373cb73b85ba9fa40807098d5572ae153dab144",
    }
    records = {
        record["quote_id"]: record
        for record in review["records"]
        if record["quote_id"] in expected
    }

    assert set(records) == expected
    assert all(
        record["disposition"] == "insufficient_to_assess"
        and record["follow_up_status"] == "remains_open"
        and "without a historical conclusion" in record["reason"]
        for record in records.values()
    )
    for quote_id in {
        "00a61fc4f76648e2ccbf07fbdadec99afb0000789e85390bae28f11cb3f230ae",
        "01d50c556a2d6283599e8c1eaa04925d42a5b499cc1c5a22925c7cb44097e1ea",
        "a9426dce186893768be1d61ea3ca82d90d05667d085c5a3d217e3a08059eba5b",
    }:
        record = next(
            row for row in review["records"] if row["quote_id"] == quote_id
        )
        assert record["disposition"] == "insufficient_to_assess"
        assert record["follow_up_status"] == "resolved_by_current_rendering"
        assert "operator-reviewed primary Margaret Thatcher Foundation" in (
            record["reason"]
        )
    reviewed = next(
        record for record in review["records"]
        if record["quote_id"]
        == "e259f9a77a234e4d03f415740045fb374b7c68eba06f857d7c79a73500dafe37"
    )
    assert reviewed["disposition"] == "insufficient_to_assess"
    assert reviewed["follow_up_status"] == "resolved_by_current_rendering"
    assert "primary speech collection" in reviewed["reason"]


def test_new_history_row_requires_an_explicit_semantic_review(tmp_path: Path):
    truth = copy.deepcopy(_load(TRUTH_AUDIT_PATH))
    new_row = copy.deepcopy(truth["records"]["published_reply_reviews"][-1])
    new_row["quote_id"] = "f" * 64
    truth["records"]["published_reply_reviews"].append(new_row)
    truth_path = tmp_path / "historical_context_evidence_truth_audit.json"
    truth_path.write_text(
        json.dumps(truth, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="outside the explicit semantic review"):
        build_review(truth_audit_path=truth_path)


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

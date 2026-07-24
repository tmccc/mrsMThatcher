"""Regression coverage for the offline v7-to-v8 projection review."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from historical_context_public_projection_review import (
    B32_QUOTE_ID,
    CLEAN_EVENT_ONLY_CONTEXT,
    CLEAN_EVENT_ONLY_QUOTE_ID,
    DOCUMENT_104653_CONTEXT,
    DOCUMENT_104653_QUOTE_ID,
    EXPECTED_MANUAL_HINT_IDS,
    SAFE_EVENT_ONLY_FALLBACK,
    build_review,
    main,
)


@pytest.fixture(scope="module")
def review():
    return build_review()


def test_projection_review_covers_all_72_cumulative_field_changes(review):
    assert review["review_ready"] is True
    assert all(review["invariants"].values())
    assert review["counts"] == {
        "change_count": 72,
        "date_only_day_precision_count": 59,
        "date_only_month_precision_count": 3,
        "date_only_year_precision_count": 2,
        "downgraded_count": 66,
        "duplicate_context_group_count": 10,
        "duplicate_context_record_count": 26,
        "duplicate_full_reply_group_count": 0,
        "event_only_downgrade_count": 2,
        "manual_hint_count": 9,
        "v8_to_v9_public_field_change_count": 6,
        "safe_date_only_count": 64,
        "safe_event_only_context_count": 1,
        "safe_event_only_fallback_count": 1,
        "upgraded_count": 6,
    }
    assert len(review["downgraded_records"]) == 66
    assert len(review["upgraded_records"]) == 6
    records = review["downgraded_records"] + review["upgraded_records"]
    assert len({record["quote_id"] for record in records}) == 72
    assert all(record["public_reply_text"] for record in records)
    assert all(record["context_line"].startswith("Context — ") for record in records)
    assert {
        precision: sum(
            record["public_date_precision"] == precision
            for record in review["downgraded_records"]
        )
        for precision in ("day", "month", "year")
    } == {"day": 59, "month": 3, "year": 2}
    assert len(review["incremental_v8_to_v9_records"]) == 6


def test_projection_review_records_safe_outputs_and_non_promoting_hints(review):
    records = {
        record["quote_id"]: record
        for record in review["downgraded_records"] + review["upgraded_records"]
    }
    b32 = records[B32_QUOTE_ID]
    assert b32["context_line"] == SAFE_EVENT_ONLY_FALLBACK
    assert all(marker not in b32["context_line"] for marker in (
        "1979", "1984", "/",
    ))
    assert b32["presentation_flags"] == ["safe_event_only_fallback"]

    clean = records[CLEAN_EVENT_ONLY_QUOTE_ID]
    assert clean["context_line"] == CLEAN_EVENT_ONLY_CONTEXT
    assert "safe_event_only_context" in clean["presentation_flags"]

    upgraded = records[DOCUMENT_104653_QUOTE_ID]
    assert upgraded["context_line"] == DOCUMENT_104653_CONTEXT
    assert upgraded["v7_public_context_supported_fields"] == []
    assert upgraded["v8_public_context_supported_fields"] == [
        "source_event", "date",
    ]
    assert upgraded["v9_public_context_supported_fields"] == [
        "source_event", "date",
    ]

    blocker_flags = {
        "empty_context", "bare_date_context", "malformed_context",
        "unadmitted_date_in_context", "diagnostic_slash_in_context",
        "duplicate_full_reply",
    }
    assert not any(
        blocker_flags & set(record["presentation_flags"])
        for record in records.values()
    )
    assert review["duplicate_full_reply_groups"] == []
    assert {row["quote_id"] for row in review["manual_hints"]} == (
        EXPECTED_MANUAL_HINT_IDS
    )
    assert all(
        row["promotes_public_fields"] is False
        and row["recommended_action"] == "manual_evidence_review_only"
        for row in review["manual_hints"]
    )


def test_projection_review_cli_is_byte_deterministic(tmp_path, capsys):
    first = tmp_path / "first" / "projection-review.json"
    second = tmp_path / "second" / "projection-review.json"

    assert main(["--output", str(first)]) == 0
    first_stdout = json.loads(capsys.readouterr().out)
    assert main(["--output", str(second)]) == 0
    second_stdout = json.loads(capsys.readouterr().out)

    expected_bytes = first.read_bytes()
    assert expected_bytes == second.read_bytes()
    assert first_stdout["review_ready"] is True
    assert second_stdout["review_ready"] is True
    assert first_stdout["counts"] == second_stdout["counts"]

    with pytest.raises(FileExistsError, match="already exists"):
        main(["--output", str(first)])
    assert main(["--output", str(first), "--overwrite"]) == 0
    overwrite_stdout = json.loads(capsys.readouterr().out)
    assert overwrite_stdout["review_ready"] is True
    assert first.read_bytes() == expected_bytes


def test_projection_review_cli_refuses_unrelated_and_protected_outputs(
    tmp_path,
):
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text('{"audit_kind":"something_else"}\n', encoding="utf-8")
    original_unrelated = unrelated.read_bytes()

    with pytest.raises(FileExistsError, match="already exists"):
        main(["--output", str(unrelated)])
    with pytest.raises(ValueError, match="not a prior projection-review"):
        main(["--output", str(unrelated), "--overwrite"])
    assert unrelated.read_bytes() == original_unrelated

    protected = Path("mrsMThatcher.txt")
    original_protected = protected.read_bytes()
    with pytest.raises(ValueError, match="protected project input"):
        main(["--output", str(protected), "--overwrite"])
    assert protected.read_bytes() == original_protected

    with pytest.raises(ValueError, match="immutable research directory"):
        main([
            "--output",
            str(
                Path("semantic_alignment_research/quote_research_full_001")
                / "projection-review.json"
            ),
        ])

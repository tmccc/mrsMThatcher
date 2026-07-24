"""Regression coverage for historical and post-v9 projection review."""
from __future__ import annotations

import hashlib
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
    POST_V9_INPUT_NAMES,
    POST_V9_TRANSITION_KIND,
    POST_V9_TRANSITION_STATUS,
    REVIEW_SCHEMA_VERSION,
    SAFE_EVENT_ONLY_FALLBACK,
    TRANSITION_MANIFEST_PATH,
    V9_POLICY,
    _v7_public_context_supported_fields,
    _validate_post_v9_transition,
    build_review,
    main,
)


@pytest.fixture(scope="module")
def review():
    return build_review()


def test_projection_review_covers_all_72_cumulative_field_changes(review):
    assert review["schema_version"] == REVIEW_SCHEMA_VERSION == 3
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
        "post_v9_public_field_change_count": 5,
        "post_v9_source_addition_count": 5,
        "post_v9_transition_packet_count": 5,
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
    assert {
        record["quote_id"] for record in review["post_v9_transition_records"]
    } == {
        "4f5e783f4957dc615742df2b827214e539a5123af1b4863822ba2e52684a0d80",
        "52f9b9f99f66ff3bc786183803f3a8d68277604471cd411027441989337c9351",
        "cac5746ca684f9611a25dcfb6b024ed63bfb3d41b2fa4c5c3d6e44290378d4ea",
        "e259f9a77a234e4d03f415740045fb374b7c68eba06f857d7c79a73500dafe37",
        "f0d85c7301e8b27bc694ac030d7c5f6b1d15ff3bcdf31cbdfb03a1c05bbe83ea",
    }
    assert all(
        record["public_reply_text"]
        and record["public_sources"]
        and not {
            "empty_context",
            "bare_date_context",
            "malformed_context",
            "unadmitted_date_in_context",
            "diagnostic_slash_in_context",
            "duplicate_full_reply",
        } & set(record["presentation_flags"])
        for record in review["post_v9_transition_records"]
    )


def _synthetic_post_v9_case():
    quote_id = "a" * 64
    source_id = "b" * 64
    candidate_id = "c" * 64
    inputs = {name: hashlib.sha256(name.encode()).hexdigest()
              for name in POST_V9_INPUT_NAMES}
    packets = {
        quote_id: {
            "_source_role_audit": {
                "curated_sources": [{"source_id": source_id}],
                "renderable_sources": [{
                    "claims_supported": ["source_event"],
                    "source_id": source_id,
                    "source_quality_class": "strong_primary_evidence",
                }],
            },
        },
    }
    curated = {
        "items": {
            quote_id: {
                "sources": [{
                    "source_id": source_id,
                    "source_review_candidate_id": candidate_id,
                }],
            },
        },
    }
    item = {
        "current_public_context_supported_fields": ["source_event"],
        "quote_text_sha256": quote_id,
        "source_bindings": [{
            "source_id": source_id,
            "source_review_candidate_id": candidate_id,
        }],
        "v9_baseline_public_context_supported_fields": [],
    }
    manifest = {
        "counts": {
            "public_field_changes": 1,
            "source_additions": 1,
            "transition_packets": 1,
        },
        "input_hashes": dict(inputs),
        "items": {quote_id: item},
        "manifest_kind": POST_V9_TRANSITION_KIND,
        "policy_transition": {"from": V9_POLICY, "to": V9_POLICY},
        "schema_version": 1,
        "transition_quote_ids_sha256": hashlib.sha256(
            f"{quote_id}\n".encode()
        ).hexdigest(),
        "transition_status": POST_V9_TRANSITION_STATUS,
    }
    return manifest, packets, curated, {quote_id: ["source_event"]}, inputs


def _validate_synthetic(case):
    manifest, packets, curated, field_map, inputs = case
    expected_bindings = {
        "a" * 64: ("b" * 64, "c" * 64),
    }
    return _validate_post_v9_transition(
        manifest,
        packets=packets,
        curated=curated,
        current_field_map=field_map,
        historical_transition_ids=set(),
        expected_input_hashes=inputs,
        expected_bindings=expected_bindings,
        expected_baseline_source_count=0,
        expected_baseline_source_ids_sha256=hashlib.sha256(b"").hexdigest(),
    )


def test_valid_disjoint_post_v9_transition_reconstructs_frozen_baseline():
    baseline, records = _validate_synthetic(_synthetic_post_v9_case())

    assert baseline == {"a" * 64: []}
    assert records == [{
        "current_public_context_supported_fields": ["source_event"],
        "quote_id": "a" * 64,
        "source_bindings": [{
            "source_id": "b" * 64,
            "source_review_candidate_id": "c" * 64,
        }],
        "v9_baseline_public_context_supported_fields": [],
    }]


@pytest.mark.parametrize("mismatch", ["source", "candidate", "input"])
def test_post_v9_identity_and_input_mismatches_fail_closed(mismatch):
    case = _synthetic_post_v9_case()
    manifest = case[0]
    if mismatch == "source":
        manifest["items"]["a" * 64]["source_bindings"][0][
            "source_id"
        ] = "d" * 64
    elif mismatch == "candidate":
        manifest["items"]["a" * 64]["source_bindings"][0][
            "source_review_candidate_id"
        ] = "d" * 64
    else:
        manifest["input_hashes"]["research_packets.json"] = "d" * 64
    with pytest.raises(RuntimeError, match="post-v9 transition"):
        _validate_synthetic(case)


def test_post_v9_transition_cannot_overlap_historical_transition():
    case = _synthetic_post_v9_case()
    with pytest.raises(RuntimeError, match="scope or inputs differ"):
        _validate_post_v9_transition(
            case[0],
            packets=case[1],
            curated=case[2],
            current_field_map=case[3],
            historical_transition_ids={"a" * 64},
            expected_input_hashes=case[4],
            expected_bindings={
                "a" * 64: ("b" * 64, "c" * 64),
            },
            expected_baseline_source_count=0,
            expected_baseline_source_ids_sha256=hashlib.sha256(b"").hexdigest(),
        )


def test_post_v9_transition_rejects_undeclared_curated_source():
    case = _synthetic_post_v9_case()
    case[2]["items"]["d" * 64] = {
        "sources": [{"source_id": "e" * 64}],
    }
    with pytest.raises(RuntimeError, match="undeclared curated sources"):
        _validate_synthetic(case)


@pytest.mark.parametrize("scope_change", ["missing", "extra"])
def test_post_v9_transition_requires_exact_reviewed_scope(scope_change):
    case = _synthetic_post_v9_case()
    if scope_change == "missing":
        case[0]["items"] = {}
    else:
        case[0]["items"]["d" * 64] = dict(case[0]["items"]["a" * 64])
    with pytest.raises(RuntimeError, match="reviewed scope"):
        _validate_synthetic(case)


def test_v7_reconstruction_excludes_reviewed_later_sources():
    source_id = "b" * 64
    packet = {
        "date": "1975-01-01",
        "_source_role_audit": {
            "renderable_sources": [{
                "assigned_roles": ["source_event_support"],
                "source_id": source_id,
            }],
        },
    }

    assert _v7_public_context_supported_fields(packet) == [
        "source_event", "date",
    ]
    assert _v7_public_context_supported_fields(
        packet, later_source_ids={source_id},
    ) == []


def test_post_v9_transition_rejects_undeclared_current_field_change():
    case = _synthetic_post_v9_case()
    case[3]["a" * 64] = ["source_event", "date"]
    with pytest.raises(RuntimeError, match="evidence differs"):
        _validate_synthetic(case)


def test_historical_transition_is_immutable():
    assert hashlib.sha256(TRANSITION_MANIFEST_PATH.read_bytes()).hexdigest() == (
        "75077d0522df8cb8f673e8892846624faad9075e907bd20f05bed906f80476ba"
    )


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

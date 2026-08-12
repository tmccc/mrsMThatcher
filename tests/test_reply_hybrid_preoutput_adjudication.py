"""Synthetic tests for offline pre-output reply adjudication packaging."""

from __future__ import annotations

import ast
import argparse
import csv
import hashlib
import io
import json
import os
import stat
from pathlib import Path

import pytest

from tools import validate_reply_hybrid_preoutput_adjudication as tool


CONTEXT_TIME = "2026-08-12T14:50:00Z"
SEMANTIC_TIME = "2026-08-12T15:10:00Z"
FINALISE_TIME = "2026-08-12T15:20:00Z"
NOW = FINALISE_TIME


def _context(candidate_id: str, incoming: str) -> dict[str, object]:
    return {
        "target_id": f"target-{candidate_id}",
        "thread_id": f"thread-{candidate_id}",
        "lane": "mention",
        "incoming_contribution": incoming,
        "quoted_post": None,
        "parent_thread": [
            {
                "post_id": f"parent-{candidate_id}",
                "author_role": "account",
                "text": "Synthetic bounded parent text.",
            }
        ],
        "clarification_request": None,
        "current_date": "2026-08-12",
    }


def _cases() -> dict[str, object]:
    fresh: dict[str, dict[str, object]] = {}
    audits: dict[str, dict[str, object]] = {}
    for index, candidate_id in enumerate(("candidate-a", "candidate-b")):
        fingerprint = ("a" if index == 0 else "b") * 64
        audit_hash = ("c" if index == 0 else "d") * 64
        replay_context = _context(
            candidate_id,
            "Thank you for the synthetic example."
            if index == 0 else "Would that synthetic principle work in practice?",
        )
        fresh[candidate_id] = {
            "candidate_id": candidate_id,
            "source_record_fingerprint": fingerprint,
            "lane": "mention",
            "target_id": f"target-{candidate_id}",
            "target_source_provenance": [{"snapshot_name": "synthetic-snapshot"}],
            "fresh_exact_text_cluster_id": f"cluster-{candidate_id}",
            "fresh_exact_text_cluster_size": 1,
            "historical_outcome": "forbidden-retrospective-value",
            "historical_public_reply": "forbidden-earlier-response",
            "semantic_inventory": {"lexical_coverage_hints": ["forbidden-hint"]},
        }
        audits[candidate_id] = {
            "candidate_id": candidate_id,
            "context_audit_record_sha256": audit_hash,
            "replay_context": replay_context,
            "production_context_at_candidate": {
                **replay_context,
                "current_date": "2026-08-11",
            },
            "context_dependency_flags": [] if index == 0 else ["synthetic_flag"],
            "component_sha256": {"replay_context_sha256": "e" * 64},
            "candidate_timestamp": "2026-08-11T12:00:00Z",
            "first_consideration_timestamp": "2026-08-11T12:01:00Z",
            "current_date_convention": "synthetic date convention",
            "parent_context_bindings": [],
            "quoted_post_binding": None,
            "clarification_binding": {"thread_was_terminal": False},
            "persisted_context_verification": {"production_context_hash_match": True},
            "recent_account_replies": [
                {
                    "post_id": f"recent-{candidate_id}",
                    "created_at": "2026-08-11T11:00:00Z",
                    "text": "Synthetic recent reply.",
                }
            ],
            "resolved_quotation": None,
            "temporal_violations": [],
            "problems": [],
            "historical_outcome": "forbidden-retrospective-value",
            "historical_public_reply": "forbidden-earlier-response",
        }
    return {
        "fresh": fresh,
        "context": audits,
        "automatic_ids": {"candidate-a"},
        "manual_ids": {"candidate-b"},
    }


def _context_row(**changes: str) -> dict[str, str]:
    row = {
        "candidate_id": "candidate-b",
        "context_audit_record_sha256": "d" * 64,
        "proposed_context_decision": "clear",
        "controlled_reason_code": "resolved_by_parent_thread",
        "reviewer_note": (
            "The bounded parent supplies the referent needed by this synthetic question."
        ),
        "adjudicator_identity": tool.CONTEXT_REVIEWER,
        "decision_time_utc": CONTEXT_TIME,
        "receipt_sha256": "",
    }
    row.update(changes)
    return row


def _context_receipts(
    cases: dict[str, object] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, object]]]:
    return tool.validate_context_decisions(
        [_context_row()], cases or _cases(), "2026-08-12T14:43:30Z"
    )


def _semantic_row(candidate_id: str, **changes: str) -> dict[str, str]:
    cases = _cases()
    _completed, receipts = _context_receipts(cases)
    receipt = next(item for item in receipts if item["candidate_id"] == candidate_id)
    is_social = candidate_id == "candidate-a"
    row = {
        "candidate_id": candidate_id,
        "source_record_fingerprint": cases["fresh"][candidate_id][
            "source_record_fingerprint"
        ],
        "context_clearance_receipt_sha256": receipt["receipt_sha256"],
        "context_audit_record_sha256": cases["context"][candidate_id][
            "context_audit_record_sha256"
        ],
        "semantic_adjudication_status": "completed",
        "accepted_semantic_tags_json": json.dumps(
            [
                "genuine_social_courtesy"
                if is_social else "civil_challenge_criticism_or_disagreement"
            ],
            separators=(",", ":"),
        ),
        "accepted_primary_stratum": (
            "genuine_social_courtesy"
            if is_social else "civil_challenge_or_disagreement"
        ),
        "genuine_social_courtesy_decision": "true" if is_social else "false",
        "safe_wit_opportunity_decision": "false",
        "justified_safety_no_reply_decision": "false",
        "controlled_reason_code": (
            "principal_act_social" if is_social else "principal_act_civil_challenge"
        ),
        "reviewer_note": (
            "The synthetic contribution is a direct expression of thanks without another act."
            if is_social
            else "The synthetic question challenges whether the stated principle works in practice."
        ),
        "adjudicator_identity": tool.SEMANTIC_REVIEWER,
        "decision_time_utc": SEMANTIC_TIME,
        "receipt_sha256": "",
    }
    row.update(changes)
    return row


def _completed_attestation() -> dict[str, object]:
    attestation = tool._blank_review_attestation()
    attestation.update({
        "semantic_first_pass_completed": True,
        "semantic_first_pass_order": "reverse_candidate_id",
        "context_second_pass_completed": True,
        "context_second_pass_order": "reverse_candidate_id",
        "semantic_second_pass_completed": True,
        "semantic_second_pass_order": "forward_candidate_id",
        "similar_cases_consistency_checked": True,
        "coverage_not_inspected_until_individual_semantic_drafts_complete": True,
        "decisions_changed_to_improve_coverage": False,
        "attestation_time_utc": "2026-08-12T15:15:00Z",
    })
    return attestation


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(output.getvalue(), encoding="utf-8")


def test_complete_input_checksum_verification(tmp_path: Path) -> None:
    (tmp_path / "alpha.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "beta.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    hashes = {
        name: tool.file_sha256(tmp_path / name) for name in ("alpha.json", "beta.csv")
    }
    (tmp_path / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="utf-8",
    )
    result = tool.verify_checksum_manifest(
        tmp_path, tool.file_sha256(tmp_path / "SHA256SUMS"), hashes
    )
    assert result == hashes


@pytest.mark.parametrize("defect", ["manifest", "member", "extra", "missing"])
def test_complete_input_checksum_refuses_any_inventory_or_hash_defect(
    tmp_path: Path, defect: str
) -> None:
    (tmp_path / "alpha").write_text("synthetic\n", encoding="utf-8")
    digest = tool.file_sha256(tmp_path / "alpha")
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  alpha\n", encoding="utf-8")
    expected_manifest = tool.file_sha256(tmp_path / "SHA256SUMS")
    expected = {"alpha": digest}
    if defect == "manifest":
        expected_manifest = "0" * 64
    elif defect == "member":
        (tmp_path / "alpha").write_text("changed\n", encoding="utf-8")
    elif defect == "extra":
        (tmp_path / "extra").write_text("extra\n", encoding="utf-8")
    else:
        (tmp_path / "alpha").unlink()
    with pytest.raises(tool.AdjudicationError):
        tool.verify_checksum_manifest(tmp_path, expected_manifest, expected)


def test_input_control_refuses_wrong_count(tmp_path: Path) -> None:
    manifest = {
        "commit": tool.ACCEPTED_AUDIT_COMMIT,
        "branch": tool.ACCEPTED_AUDIT_BRANCH,
        "base_hybrid_commit": tool.ANCESTRY[0],
        "working_tree_clean": True,
        "working_tree_status_short": [],
        "automatic_context_clearance_count": 14,
        "manual_context_review_count": 22,
        "manual_semantic_decisions_pending": 36,
        "semantic_coverage_status": "unadjudicated",
        "model_calls": 0,
        "provider_http_requests": 0,
        "posting_actions": 0,
        "production_state_changes": 0,
        "live_project_included": False,
        "response_generation_authorised": False,
        "final_sample_selected": False,
        "execution_seed_created": False,
        "blind_key_created": False,
        "fresh_candidates_removed_for_text_duplication": 0,
        "fresh_exact_text_cluster_count": 1,
        "fresh_exact_text_clustered_candidate_count": 5,
        "candidate_counts": {"eligible_candidates": 35},
        "source_hashes": {
            "audit_tool": tool.ACCEPTED_SOURCE_HASHES[
                "tools/build_reply_hybrid_evaluation_audit.py"
            ],
            "audit_tests": tool.ACCEPTED_SOURCE_HASHES[
                "tests/test_reply_hybrid_evaluation_audit.py"
            ],
            "preregistration": tool.ACCEPTED_SOURCE_HASHES[
                "reply_hybrid_evaluation_preregistration.md"
            ],
        },
    }
    documents = {
        "run_manifest.json": manifest,
        "context_audit_summary.json": {},
        "freshness_boundary.json": {},
        "sampling_readiness.json": {},
        "source_inventory.json": {},
        "strata_inventory.json": {},
        "profile_identity_verification.json": {},
    }
    for name, value in documents.items():
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(tool.AdjudicationError, match="eligible count"):
        tool._verify_input_controls(tmp_path)


def test_context_decisions_require_exact_complete_unique_identity_set() -> None:
    cases = _cases()
    with pytest.raises(tool.AdjudicationError, match="missing or extra"):
        tool.validate_context_decisions([], cases, NOW)
    with pytest.raises(tool.AdjudicationError, match="duplicate"):
        tool.validate_context_decisions([_context_row(), _context_row()], cases, NOW)


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        ("clear", "self_contained_despite_flag"),
        ("exclude", "unresolved_referent"),
        ("requires_adjudication", "genuinely_ambiguous_after_review"),
    ],
)
def test_allowed_context_decisions_and_reason_codes(
    decision: str, reason: str
) -> None:
    rows, receipts = tool.validate_context_decisions(
        [_context_row(proposed_context_decision=decision, controlled_reason_code=reason)],
        _cases(),
        NOW,
    )
    assert rows[0]["proposed_context_decision"] == decision
    assert len(receipts) == 2
    wrong_reason = {
        "clear": "unresolved_referent",
        "exclude": "self_contained_despite_flag",
        "requires_adjudication": "unresolved_referent",
    }[decision]
    with pytest.raises(tool.AdjudicationError, match="contradicts"):
        tool.validate_context_decisions(
            [_context_row(proposed_context_decision=decision,
                          controlled_reason_code=wrong_reason)],
            _cases(), NOW,
        )


def test_automatic_and_manual_context_receipts_are_distinct_and_canonical() -> None:
    rows, receipts = _context_receipts()
    automatic, manual = receipts
    assert automatic["adjudicator_kind"] == "automatic_audit_decision"
    assert automatic["context_decision"] == "automatic_clearance"
    assert manual["adjudicator_kind"] == tool.ADJUDICATOR_KIND
    assert manual["review_status"] == tool.GLOBAL_REVIEW_STATUS
    assert rows[0]["receipt_sha256"] == manual["receipt_sha256"]
    assert manual["receipt_sha256"] == tool.receipt_sha256(manual)
    mutated = dict(manual, reviewer_note="A different substantive synthetic basis is recorded here.")
    assert tool.receipt_sha256(mutated) != manual["receipt_sha256"]


def test_context_packet_is_deterministic_and_strictly_sanitised() -> None:
    cases = _cases()
    first = tool.render_context_packet(cases)
    second = tool.render_context_packet(cases)
    assert first == second
    assert first.count("## candidate-") == 1
    assert "Would that synthetic principle work in practice?" in first
    assert "forbidden-retrospective-value" not in first
    assert "forbidden-earlier-response" not in first
    assert "forbidden-hint" not in first
    assert "historical_outcome" not in first
    assert "historical_public_reply" not in first
    assert "lexical_coverage_hints" not in first


def test_semantic_packet_excludes_blocked_and_forbidden_fields_deterministically() -> None:
    cases = _cases()
    _rows, receipts = tool.validate_context_decisions(
        [_context_row(proposed_context_decision="exclude",
                      controlled_reason_code="unresolved_referent")],
        cases, NOW,
    )
    first = tool.render_semantic_packet(cases, receipts)
    second = tool.render_semantic_packet(cases, receipts)
    assert first == second
    assert first.count("## candidate-") == 1
    assert "candidate-a" in first
    assert "candidate-b" not in first
    assert "forbidden-retrospective-value" not in first
    assert "forbidden-earlier-response" not in first
    assert "forbidden-hint" not in first
    assert "historical_outcome" not in first
    assert "historical_public_reply" not in first
    assert "lexical" not in first.casefold()


def test_semantic_accounting_has_one_reverse_order_row_per_original_candidate() -> None:
    cases = _cases()
    _rows, receipts = _context_receipts(cases)
    rows = tool._blank_semantic_rows(cases, receipts)
    assert [row["candidate_id"] for row in rows] == ["candidate-b", "candidate-a"]
    assert len({row["candidate_id"] for row in rows}) == len(cases["fresh"])
    assert all(not row["semantic_adjudication_status"] for row in rows)


def test_no_completed_semantic_decision_for_context_excluded_candidate() -> None:
    cases = _cases()
    _rows, receipts = tool.validate_context_decisions(
        [_context_row(proposed_context_decision="exclude",
                      controlled_reason_code="unresolved_referent")],
        cases, "2026-08-12T14:43:30Z",
    )
    completed = [_semantic_row("candidate-a"), _semantic_row("candidate-b")]
    completed[1]["context_clearance_receipt_sha256"] = next(
        row["receipt_sha256"] for row in receipts if row["candidate_id"] == "candidate-b"
    )
    with pytest.raises(tool.AdjudicationError, match="not blocked"):
        tool.validate_semantic_decisions(completed, cases, receipts)


def test_blocked_semantic_candidate_requires_blank_acceptance_fields() -> None:
    cases = _cases()
    _rows, receipts = tool.validate_context_decisions(
        [_context_row(proposed_context_decision="exclude",
                      controlled_reason_code="unresolved_referent")],
        cases, "2026-08-12T14:43:30Z",
    )
    blocked = _semantic_row(
        "candidate-b",
        context_clearance_receipt_sha256=next(
            row["receipt_sha256"] for row in receipts
            if row["candidate_id"] == "candidate-b"
        ),
        semantic_adjudication_status="blocked_context_excluded",
        accepted_semantic_tags_json="",
        accepted_primary_stratum="",
        genuine_social_courtesy_decision="",
        safe_wit_opportunity_decision="",
        justified_safety_no_reply_decision="",
        controlled_reason_code="blocked_by_context_exclusion",
        reviewer_note=(
            "The synthetic unresolved referent blocks a safe semantic classification here."
        ),
    )
    valid_rows = [_semantic_row("candidate-a"), blocked]
    final_rows, semantic_receipts = tool.validate_semantic_decisions(
        valid_rows, cases, receipts
    )
    blocked_receipt = next(
        row for row in semantic_receipts if row["candidate_id"] == "candidate-b"
    )
    assert blocked_receipt["accepted_semantic_tags"] is None
    assert blocked_receipt["accepted_primary_stratum"] is None
    assert blocked_receipt["genuine_social_courtesy_decision"] is None
    invalid = dict(blocked, accepted_primary_stratum="genuine_social_courtesy")
    with pytest.raises(tool.AdjudicationError, match="not blank"):
        tool.validate_semantic_decisions(
            [_semantic_row("candidate-a"), invalid], cases, receipts
        )
    assert len(final_rows) == 2


def test_controlled_semantic_tags_order_duplicates_primary_and_booleans() -> None:
    cases = _cases()
    _context_rows, context_receipts = _context_receipts(cases)
    base = [_semantic_row("candidate-a"), _semantic_row("candidate-b")]
    tool.validate_semantic_decisions(base, cases, context_receipts)
    defects = [
        {
            "accepted_semantic_tags_json": json.dumps([
                "safe_contribution_specific_wit_opportunity",
                "civil_challenge_criticism_or_disagreement",
            ]),
            "safe_wit_opportunity_decision": "true",
            "controlled_reason_code": "mixed_act_primary_selected",
        },
        {
            "accepted_semantic_tags_json": json.dumps([
                "civil_challenge_criticism_or_disagreement",
                "civil_challenge_criticism_or_disagreement",
            ])
        },
        {"accepted_primary_stratum": "unknown_primary"},
        {"genuine_social_courtesy_decision": "TRUE"},
        {"safe_wit_opportunity_decision": "true"},
    ]
    for changes in defects:
        bad = _semantic_row("candidate-b", **changes)
        with pytest.raises(tool.AdjudicationError):
            tool.validate_semantic_decisions(
                [_semantic_row("candidate-a"), bad], cases, context_receipts
            )


def test_formulaic_substantive_posted_is_refused() -> None:
    cases = _cases()
    _rows, receipts = _context_receipts(cases)
    bad = _semantic_row(
        "candidate-b",
        accepted_semantic_tags_json=json.dumps(["formulaic_substantive_posted"]),
    )
    with pytest.raises(tool.AdjudicationError):
        tool.validate_semantic_decisions([_semantic_row("candidate-a"), bad], cases, receipts)
    findings = tool._protocol_findings(_completed_attestation())
    assert findings["formulaic_substantive_posted_status"] == (
        "not_adjudicated_in_semantic_phase"
    )
    assert findings["separate_profile_blind_historical_baseline_review_required"] is True


def test_canonical_semantic_receipt_hashing_and_explicit_unresolved_accounting() -> None:
    cases = _cases()
    _context_rows, context_receipts = _context_receipts(cases)
    unresolved = _semantic_row(
        "candidate-b",
        semantic_adjudication_status="requires_adjudication",
        accepted_semantic_tags_json="",
        accepted_primary_stratum="",
        genuine_social_courtesy_decision="",
        safe_wit_opportunity_decision="",
        justified_safety_no_reply_decision="",
        controlled_reason_code="semantic_ambiguity_requires_adjudication",
        reviewer_note=(
            "The synthetic wording supports two principal acts that remain genuinely unresolved."
        ),
    )
    rows, receipts = tool.validate_semantic_decisions(
        [_semantic_row("candidate-a"), unresolved], cases, context_receipts
    )
    receipt = next(row for row in receipts if row["candidate_id"] == "candidate-b")
    assert receipt["accepted_semantic_tags"] is None
    assert receipt["receipt_sha256"] == tool.receipt_sha256(receipt)
    coverage = tool.build_coverage_inventory(cases, context_receipts, receipts)
    assert coverage["counts_by_semantic_adjudication_status"] == {
        "completed": 1,
        "blocked_context_excluded": 0,
        "requires_adjudication": 1,
    }
    readiness = tool.build_sampling_readiness(coverage, [])
    assert readiness["manual_adjudication_complete"] is False
    assert readiness["ready_to_freeze_sample"] is False
    assert rows[0]["candidate_id"] == "candidate-b"


@pytest.mark.parametrize(
    "note",
    [
        "too brief",
        "resolved by parent thread",
        "The current profile won because the generated response had an old score.",
    ],
)
def test_reviewer_notes_must_be_substantive_case_specific_and_blind(note: str) -> None:
    with pytest.raises(tool.AdjudicationError):
        tool.validate_context_decisions([_context_row(reviewer_note=note)], _cases(), NOW)


def test_manual_decision_timestamp_must_fall_within_actual_review_window() -> None:
    with pytest.raises(tool.AdjudicationError, match="outside"):
        tool.validate_context_decisions(
            [_context_row(decision_time_utc="2026-08-12T15:01:00Z")],
            _cases(), "2026-08-12T14:43:30Z",
            "2026-08-12T14:45:00Z", "2026-08-12T15:00:00Z",
        )


def test_semantic_reason_requires_its_specific_corresponding_tag() -> None:
    cases = _cases()
    _rows, context_receipts = _context_receipts(cases)
    bad = _semantic_row(
        "candidate-b",
        accepted_semantic_tags_json=json.dumps([
            "substantive_political_or_moral_proposition"
        ]),
        accepted_primary_stratum="substantive_argument_or_principle",
        controlled_reason_code="principal_act_substantive_agreement",
    )
    with pytest.raises(tool.AdjudicationError, match="corresponding"):
        tool.validate_semantic_decisions(
            [_semantic_row("candidate-a"), bad], cases, context_receipts,
            "2026-08-12T15:00:00Z", FINALISE_TIME,
        )


def test_semantic_tag_json_is_canonicalised_in_completed_csv_row() -> None:
    cases = _cases()
    _rows, context_receipts = _context_receipts(cases)
    spaced = _semantic_row(
        "candidate-b",
        accepted_semantic_tags_json='[ "civil_challenge_criticism_or_disagreement" ]',
    )
    completed, _receipts = tool.validate_semantic_decisions(
        [_semantic_row("candidate-a"), spaced], cases, context_receipts,
        "2026-08-12T15:00:00Z", FINALISE_TIME,
    )
    candidate = next(row for row in completed if row["candidate_id"] == "candidate-b")
    assert candidate["accepted_semantic_tags_json"] == (
        '["civil_challenge_criticism_or_disagreement"]'
    )


def test_conflicts_must_resolve_exactly_to_final_receipts(tmp_path: Path) -> None:
    cases = _cases()
    _context_rows, context_receipts = _context_receipts(cases)
    _semantic_rows, semantic_receipts = tool.validate_semantic_decisions(
        [_semantic_row("candidate-a"), _semantic_row("candidate-b")],
        cases, context_receipts, "2026-08-12T15:00:00Z", FINALISE_TIME,
    )
    conflict = {
        "candidate_id": "candidate-b",
        "phase": "context",
        "first_pass_decision": {
            "context_decision": "exclude",
            "controlled_reason_code": "unresolved_referent",
        },
        "second_pass_decision": {
            "context_decision": "clear",
            "controlled_reason_code": "resolved_by_parent_thread",
        },
        "conflict_basis": (
            "The synthetic parent changed how the referent was understood on rereading."
        ),
        "resolution": {
            "context_decision": "clear",
            "controlled_reason_code": "resolved_by_parent_thread",
        },
        "unresolved": False,
        "review_time_utc": FINALISE_TIME,
    }
    path = tmp_path / "adjudication_conflicts.jsonl"
    path.write_bytes(tool.canonical_json_bytes(conflict) + b"\n")
    assert tool._validate_conflicts(
        path, context_receipts, semantic_receipts, FINALISE_TIME
    ) == [conflict]
    conflict["unresolved"] = True
    path.write_bytes(tool.canonical_json_bytes(conflict) + b"\n")
    with pytest.raises(tool.AdjudicationError, match="unresolved"):
        tool._validate_conflicts(path, context_receipts, semantic_receipts, FINALISE_TIME)
    conflict["unresolved"] = False
    conflict["resolution"]["context_decision"] = "exclude"
    path.write_bytes(tool.canonical_json_bytes(conflict) + b"\n")
    with pytest.raises(tool.AdjudicationError, match="resolution"):
        tool._validate_conflicts(path, context_receipts, semantic_receipts, FINALISE_TIME)


def test_zero_coverage_categories_are_explicit() -> None:
    cases = _cases()
    _context_rows, context_receipts = _context_receipts(cases)
    _semantic_rows, semantic_receipts = tool.validate_semantic_decisions(
        [_semantic_row("candidate-a"), _semantic_row("candidate-b")],
        cases, context_receipts, "2026-08-12T15:00:00Z", FINALISE_TIME,
    )
    coverage = tool.build_coverage_inventory(cases, context_receipts, semantic_receipts)
    assert set(coverage["counts_by_semantic_adjudication_status"]) == set(
        tool.SEMANTIC_STATUSES
    )
    assert set(coverage["counts_by_accepted_semantic_tag"]) == set(tool.SEMANTIC_TAGS)
    assert set(coverage["counts_by_accepted_primary_stratum"]) == set(
        tool.PRIMARY_STRATA
    )
    assert coverage["counts_by_accepted_semantic_tag"][
        "justified_safety_no_reply"
    ] == 0


def test_prepared_input_verification_is_revalidated_but_runtime_git_state_may_change(
    tmp_path: Path,
) -> None:
    base = {
        "verified_at_utc": "2026-08-12T14:45:00Z",
        "eligible_candidate_count": 36,
        "repository": {"head": "a" * 40, "status_short": ["?? synthetic"]},
    }
    path = tmp_path / "input_verification.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    current = {
        **base,
        "verified_at_utc": "2026-08-12T15:00:00Z",
        "repository": {"head": "b" * 40, "status_short": []},
    }
    tool._verify_input_verification_copy(path, current)
    current["eligible_candidate_count"] = 35
    with pytest.raises(tool.AdjudicationError, match="differs"):
        tool._verify_input_verification_copy(path, current)


def test_review_attestation_must_be_explicit_and_complete(tmp_path: Path) -> None:
    path = tmp_path / "review_attestation.json"
    path.write_text(json.dumps(tool._blank_review_attestation()), encoding="utf-8")
    with pytest.raises(tool.AdjudicationError, match="incomplete"):
        tool._validate_review_attestation(
            path, "2026-08-12T15:01:00Z", FINALISE_TIME
        )
    path.write_text(json.dumps(_completed_attestation()), encoding="utf-8")
    assert tool._validate_review_attestation(
        path, "2026-08-12T15:01:00Z", FINALISE_TIME
    )["semantic_second_pass_completed"] is True


def test_cli_paths_refuse_tmp_output_and_principal_production_repository(
    tmp_path: Path,
) -> None:
    worktree = Path(tool.__file__).resolve().parents[1]
    with pytest.raises(tool.AdjudicationError):
        tool._verify_cli_paths(argparse.Namespace(
            input=tool.CORRECTED_INPUT_DIRECTORY,
            output=tmp_path / "mrsMThatcher-reply-hybrid-preoutput-adjudication-test",
            repository=worktree,
        ))
    with pytest.raises(tool.AdjudicationError, match="production"):
        tool._verify_cli_paths(argparse.Namespace(
            input=tool.CORRECTED_INPUT_DIRECTORY,
            output=tool.RESEARCH_ROOT /
                "mrsMThatcher-reply-hybrid-preoutput-adjudication-test",
            repository=tool.PRINCIPAL_PRODUCTION_REPOSITORY,
        ))


def test_no_automatic_decision_generator_or_forbidden_operational_path() -> None:
    source_path = Path(tool.__file__)
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    public_or_private_functions = {
        node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not any(
        token in name for name in public_or_private_functions
        for token in ("classify", "infer_decision", "suggest_decision", "generate_reply")
    )
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert imports.isdisjoint({
        "requests", "httpx", "aiohttp", "urllib", "http", "socket", "ssl",
        "ftplib", "smtplib", "openai", "anthropic", "google", "boto3",
        "random", "secrets",
    })
    subprocess_calls: list[tuple[str, ast.Call]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for descendant in ast.walk(node):
            if (
                isinstance(descendant, ast.Call)
                and isinstance(descendant.func, ast.Attribute)
                and isinstance(descendant.func.value, ast.Name)
                and descendant.func.value.id == "subprocess"
            ):
                subprocess_calls.append((node.name, descendant))
    assert subprocess_calls
    assert {name for name, _call in subprocess_calls} == {"_git"}
    for _name, call in subprocess_calls:
        command = call.args[0]
        assert isinstance(command, ast.List)
        assert isinstance(command.elts[0], ast.Constant)
        assert command.elts[0].value == "git"
    option_strings = {
        option
        for action in tool.build_argument_parser()._actions
        for option in action.option_strings
    }
    for subparser_action in tool.build_argument_parser()._actions:
        choices = getattr(subparser_action, "choices", None)
        if isinstance(choices, dict):
            for parser in choices.values():
                option_strings.update(
                    option for action in parser._actions for option in action.option_strings
                )
    assert option_strings.isdisjoint({
        "--execute", "--paid", "--provider", "--post", "--generate-replies",
        "--freeze-sample", "--create-seed", "--create-blind-key",
    })
    assert "formulaic_substantive_posted" in source


def test_checksum_generation_verification_and_private_permissions(tmp_path: Path) -> None:
    directory = tmp_path / "private-output"
    directory.mkdir(mode=0o700)
    tool._write_private(directory / "alpha.txt", "alpha\n")
    tool._write_private(directory / "beta.json", "{}\n")
    sums = tool.write_sha256sums(directory)
    assert sums == tool.verify_sha256sums(directory)
    assert stat.S_IMODE((directory / "SHA256SUMS").stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in directory.iterdir()
    )
    (directory / "alpha.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(tool.AdjudicationError):
        tool.verify_sha256sums(directory)


def test_private_writer_refuses_symlinks_and_stage_inventory_is_exact(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_text("preserve\n", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(tool.AdjudicationError, match="unsafe"):
        tool._write_private(link, "changed\n")
    assert target.read_text(encoding="utf-8") == "preserve\n"

    output = tmp_path / "mrsMThatcher-reply-hybrid-preoutput-adjudication-stage"
    output.mkdir(mode=0o700)
    for name in tool.CONTEXT_STAGE_FILES:
        (output / name).write_text("synthetic\n", encoding="utf-8")
        (output / name).chmod(0o600)
    assert tool._verify_stage_directory(
        output, tmp_path, tool.CONTEXT_STAGE_FILES
    ) == output.resolve()
    (output / "unexpected").mkdir()
    with pytest.raises(tool.AdjudicationError, match="inventory"):
        tool._verify_stage_directory(output, tmp_path, tool.CONTEXT_STAGE_FILES)


def test_write_sha256sums_refuses_to_replace_existing_manifest(
    tmp_path: Path,
) -> None:
    tool._write_private(tmp_path / "alpha", "alpha\n")
    tool._write_private(tmp_path / "SHA256SUMS", "sentinel\n")
    with pytest.raises(tool.AdjudicationError, match="replace"):
        tool.write_sha256sums(tmp_path)
    assert (tmp_path / "SHA256SUMS").read_text(encoding="utf-8") == "sentinel\n"


def test_finalise_manifest_is_honest_and_all_later_stage_flags_remain_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = _cases()
    repository = tmp_path / "repository"
    output = tmp_path / "mrsMThatcher-reply-hybrid-preoutput-adjudication-synthetic"
    input_directory = tmp_path / "input"
    repository.mkdir()
    output.mkdir(mode=0o700)
    input_directory.mkdir()
    (repository / "tests").mkdir()
    (repository / "reply_hybrid_preoutput_adjudication_protocol.md").write_text(
        "synthetic protocol\n", encoding="utf-8"
    )
    (repository / "tests/test_reply_hybrid_preoutput_adjudication.py").write_text(
        "synthetic tests\n", encoding="utf-8"
    )
    (input_directory / "run_manifest.json").write_text(
        json.dumps({"audit_created_at": "2026-08-12T14:43:30Z"}), encoding="utf-8"
    )
    for target_name in tool.ORIGINAL_COPIES.values():
        (output / target_name).write_text("synthetic original\n", encoding="utf-8")
    (output / "input_verification.json").write_text("{}\n", encoding="utf-8")
    (output / "context_adjudication_packet.md").write_text(
        tool.render_context_packet(cases), encoding="utf-8"
    )
    context_completed, context_receipts = _context_receipts(cases)
    (output / "context_clearance_receipts.jsonl").write_bytes(
        b"".join(tool.canonical_json_bytes(row) + b"\n" for row in context_receipts)
    )
    (output / "semantic_adjudication_packet.md").write_text(
        tool.render_semantic_packet(cases, context_receipts), encoding="utf-8"
    )
    _write_csv(
        output / "context_decisions.completed.csv",
        tool.CONTEXT_FIELDS,
        context_completed,
    )
    _write_csv(
        output / "semantic_decisions.completed.csv",
        tool.SEMANTIC_FIELDS,
        [_semantic_row("candidate-b"), _semantic_row("candidate-a")],
    )
    (output / "adjudication_conflicts.jsonl").write_text("", encoding="utf-8")
    (output / "workflow_receipt.json").write_text(
        json.dumps({
            "schema_version": tool.SCHEMA_VERSION,
            "tool_version": tool.TOOL_VERSION,
            "context_prepare": {
                "exact_command_line": "synthetic context prepare",
                "start_utc": "2026-08-12T14:45:00Z",
                "finish_utc": "2026-08-12T14:46:00Z",
            },
            "semantic_prepare": {
                "exact_command_line": "synthetic semantic prepare",
                "start_utc": "2026-08-12T15:00:00Z",
                "finish_utc": "2026-08-12T15:01:00Z",
            },
            "finalise": None,
        }, sort_keys=True),
        encoding="utf-8",
    )
    (output / "review_attestation.json").write_text(
        json.dumps(_completed_attestation(), sort_keys=True), encoding="utf-8"
    )
    for path in output.iterdir():
        path.chmod(0o600)
    repository_receipt = {
        "head": "f" * 40,
        "branch": "research/synthetic",
        "status_short": [],
        "accepted_source_hashes": {},
        "ancestry": list(tool.ANCESTRY),
    }
    monkeypatch.setattr(
        tool,
        "verify_input_bundle",
        lambda _input, _repository: {"repository": repository_receipt},
    )
    monkeypatch.setattr(tool, "_load_verified_case_data", lambda _input: cases)
    monkeypatch.setattr(tool, "_copy_originals", lambda _input, _output: None)
    monkeypatch.setattr(tool, "_verify_original_copies", lambda _input, _output: None)
    monkeypatch.setattr(
        tool, "_verify_input_verification_copy", lambda _path, _verification: None
    )
    result = tool.finalise(
        input_directory, output, repository, "synthetic finalise", FINALISE_TIME,
        tmp_path,
    )
    assert result == output.resolve()
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["adjudicator_kind"] == tool.ADJUDICATOR_KIND
    assert manifest["human_adjudicator"] is False
    assert manifest["codex_language_model_adjudication_performed"] is True
    assert manifest["evaluated_profile_model_calls"] == 0
    assert manifest["repository_provider_http_requests"] == 0
    assert manifest["posting_actions"] == 0
    assert manifest["production_state_changes"] == 0
    assert manifest["final_sample_selected"] is False
    assert manifest["final_replay_pack_built"] is False
    assert manifest["execution_seed_created"] is False
    assert manifest["response_label_seed_created"] is False
    assert manifest["blind_key_created"] is False
    assert manifest["ready_to_freeze_sample"] is False
    assert set(tool.verify_sha256sums(output)) == set(tool.OUTPUT_FILES)
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())


def test_every_maintained_public_callable_has_a_docstring() -> None:
    tree = ast.parse(Path(tool.__file__).read_text(encoding="utf-8"))
    missing = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
        and ast.get_docstring(node) is None
    ]
    assert missing == []

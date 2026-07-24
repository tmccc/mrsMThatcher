from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import historical_context_reply_semantic_gate_audit as gate_audit_module
from historical_context_reply_semantic_gate import HistoricalContextSemanticGate
from historical_context_reply_semantic_gate_audit import (
    KNOWN_104653_QUOTE_ID,
    build_audit,
    main,
)


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "semantic_alignment_research" / "quote_research_full_001"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_gate_audit_processes_every_packet_without_changing_regular_eligibility():
    audit = build_audit()

    assert audit["coverage"]["completed_packet_count"] == 627
    assert audit["coverage"]["attribution_eligible_count"] == 611
    assert audit["coverage"]["completed_attribution_ineligible_count"] == 16
    assert audit["coverage"]["unresolved_quote_count"] == 5
    assert audit["gate"]["available"] is True
    assert audit["gate"]["blocked_quote_count"] == 21
    assert audit["gate"]["disposition_counts"] == {
        "future_correction_needed": 6,
        "insufficient_to_assess": 15,
    }
    assert audit["decision_counts"] == {
        "blocked_open_semantic_review": 21,
        "eligible_allow": 590,
        "ineligible_not_regular_post": 16,
    }
    assert audit["invariant_failure_count"] == 0
    assert all(audit["invariants"].values())


def test_gate_audit_records_exact_suppressed_replies_and_allows_104653():
    audit = build_audit()
    records = audit["records"]

    assert len(records) == 627
    assert [row["quote_id"] for row in records] == sorted(
        row["quote_id"] for row in records
    )
    blocked = [
        row
        for row in records
        if row["public_reply_decision"] == "blocked_open_semantic_review"
    ]
    assert len(blocked) == 21
    assert all(row["attribution_eligible"] is True for row in blocked)
    assert all(row["open_review_disposition"] for row in blocked)
    assert all(
        row["otherwise_rendered_public_reply_suppressed"].startswith("Context — ")
        for row in blocked
    )
    known = next(row for row in records if row["quote_id"] == KNOWN_104653_QUOTE_ID)
    assert known["public_reply_decision"] == "eligible_allow"
    assert known["open_review_disposition"] is None
    assert known["otherwise_rendered_public_reply_suppressed"] == ""


def test_gate_audit_cli_is_deterministic_and_does_not_mutate_inputs(tmp_path: Path):
    protected = [
        RESEARCH / "research_packets.json",
        RESEARCH / "historical_context_source_role_audit.json",
        ROOT / "historical_context_published_reply_semantic_review.json",
        ROOT
        / "tests"
        / "fixtures"
        / "historical_context_reply_history.reviewed.json",
    ]
    before = {path: _hash(path) for path in protected}
    first = tmp_path / "historical_context_reply_semantic_gate_audit.first.json"
    second = tmp_path / "historical_context_reply_semantic_gate_audit.second.json"

    assert main(["--output", str(first)]) == 0
    assert main(["--output", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()
    assert {path: _hash(path) for path in protected} == before
    with pytest.raises(FileExistsError):
        main(["--output", str(first)])
    unrelated = tmp_path / "historical_context_reply_semantic_gate_audit.unrelated.json"
    unrelated.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prior gate audit"):
        main(["--output", str(unrelated), "--overwrite"])
    with pytest.raises(ValueError, match="immutable production input"):
        main([
            "--output",
            str(RESEARCH / "research_packets.json"),
            "--overwrite",
        ])


def test_gate_audit_rejects_runtime_ids_not_resolved_through_aliases(
    tmp_path: Path,
):
    source = (
        ROOT
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    )
    manifest = json.loads(source.read_text(encoding="utf-8"))
    manifest["runtime_eligible_quote_ids"][0] = "f" * 64
    mutated = tmp_path / "runtime_eligible_quote_manifest.json"
    mutated.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = build_audit(runtime_manifest_path=mutated)

    assert audit["invariants"]["runtime_cycle_membership_is_unchanged"] is False
    assert audit["invariant_failure_count"] == 1


def test_gate_audit_binds_runtime_manifest_to_active_source_hash(tmp_path: Path):
    source = (
        ROOT
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    )
    manifest = json.loads(source.read_text(encoding="utf-8"))
    manifest["source_file_hashes"]["active_source"] = "f" * 64
    mutated = tmp_path / "runtime_eligible_quote_manifest.json"
    mutated.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    audit = build_audit(runtime_manifest_path=mutated)

    assert audit["invariants"]["runtime_cycle_membership_is_unchanged"] is False
    assert audit["invariant_failure_count"] == 1


def test_gate_audit_models_unavailable_gate_as_whole_lane_block(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        gate_audit_module,
        "load_historical_context_semantic_gate",
        lambda **_kwargs: HistoricalContextSemanticGate.closed(
            "test gate unavailable"
        ),
    )

    audit = build_audit()

    assert audit["gate"]["available"] is False
    assert audit["gate"]["reviewed_blocked_quote_count"] == 0
    assert audit["gate"]["blocked_quote_count"] == 611
    assert audit["decision_counts"] == {
        "blocked_semantic_gate_unavailable": 611,
        "ineligible_not_regular_post": 16,
    }
    assert not any(
        row["public_reply_decision"] == "eligible_allow"
        for row in audit["records"]
    )
    assert audit["invariants"]["gate_is_available"] is False
    assert audit["invariants"][
        "all_blocked_quotes_remain_regular_post_eligible"
    ] is True
    assert audit["invariants"]["document_104653_is_not_blocked"] is False
    assert audit["invariant_failure_count"] > 0

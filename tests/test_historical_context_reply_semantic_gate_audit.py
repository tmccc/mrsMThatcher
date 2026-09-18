from __future__ import annotations

import hashlib
import json
from functools import partial
from pathlib import Path

import pytest

import historical_context_reply_semantic_gate_audit as gate_audit_module
from historical_context_reply_semantic_gate import HistoricalContextSemanticGate
from historical_context_reply_semantic_gate_audit import (
    KNOWN_104653_QUOTE_ID,
    main,
)
from tests.helpers.historical_corpus import (
    RESEARCH_RELATIVE,
    historical_corpus_root,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def historical_audit(
    historical_corpus_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Run the frozen audit against its original, hash-bound corpus."""
    research = historical_corpus_root / RESEARCH_RELATIVE
    runtime_manifest = (
        historical_corpus_root
        / "semantic_alignment_research"
        / "quote_attribution_cleanup_001"
        / "deployment_candidate"
        / "runtime_eligible_quote_manifest.json"
    )
    ledger = (
        historical_corpus_root
        / "historical_context_published_reply_semantic_review.json"
    )
    # The live gate deliberately pins the current ledger. This historical
    # audit must pin its own ledger while retaining the real gate validator.
    monkeypatch.setattr(
        gate_audit_module,
        "load_historical_context_semantic_gate",
        partial(
            gate_audit_module.load_historical_context_semantic_gate,
            expected_ledger_sha256=_hash(ledger),
        ),
    )
    builder = partial(
        gate_audit_module.build_audit,
        root=historical_corpus_root,
        research_dir=research,
        runtime_manifest_path=runtime_manifest,
    )
    monkeypatch.setattr(gate_audit_module, "build_audit", builder)
    # The CLI accepts only an output path, so bind both its builder and its
    # immutable-input guard to the same original inputs.
    monkeypatch.setattr(gate_audit_module, "ROOT", historical_corpus_root)
    monkeypatch.setattr(gate_audit_module, "DEFAULT_RESEARCH_DIR", research)
    monkeypatch.setattr(gate_audit_module, "RUNTIME_ELIGIBLE_MANIFEST", runtime_manifest)
    monkeypatch.setattr(gate_audit_module, "SEMANTIC_REVIEW_PATH", ledger)
    return builder


def test_gate_audit_processes_every_packet_without_changing_regular_eligibility(
    historical_audit,
):
    # This offline audit enforces its original 627/611/5 batch in its own
    # invariants. These expectations preserve that contract, not corpus growth.
    audit = historical_audit()

    assert audit["coverage"]["completed_packet_count"] == 627
    assert audit["coverage"]["attribution_eligible_count"] == 611
    assert audit["coverage"]["completed_attribution_ineligible_count"] == 16
    assert audit["coverage"]["unresolved_quote_count"] == 5
    assert audit["gate"]["available"] is True
    assert audit["gate"]["blocked_quote_count"] == 13
    assert audit["gate"]["disposition_counts"] == {
        "future_correction_needed": 6,
        "insufficient_to_assess": 7,
    }
    assert audit["decision_counts"] == {
        "blocked_open_semantic_review": 13,
        "eligible_allow": 598,
        "ineligible_not_regular_post": 16,
    }
    assert audit["invariant_failure_count"] == 0
    assert all(audit["invariants"].values())


def test_gate_audit_records_exact_suppressed_replies_and_allows_104653(
    historical_audit,
):
    # Exact review dispositions and coverage belong to the same original batch.
    audit = historical_audit()
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
    assert len(blocked) == 13
    assert all(row["attribution_eligible"] is True for row in blocked)
    assert all(row["open_review_disposition"] for row in blocked)
    suppressed = [
        row["otherwise_rendered_public_reply_suppressed"] for row in blocked
    ]
    assert all(text.strip() for text in suppressed)
    assert all(
        text.startswith(("Context — ", "Meaning — ", "Verification — ", "Source — "))
        for text in suppressed
    )
    assert all(
        "Context — The surviving attribution does not establish an occasion, "
        "date or immediate historical issue."
        not in text
        for text in suppressed
    )
    known = next(row for row in records if row["quote_id"] == KNOWN_104653_QUOTE_ID)
    assert known["public_reply_decision"] == "eligible_allow"
    assert known["open_review_disposition"] is None
    assert known["otherwise_rendered_public_reply_suppressed"] == ""


def test_gate_audit_cli_is_deterministic_and_does_not_mutate_inputs(
    tmp_path: Path,
    historical_corpus_root: Path,
    historical_audit,
):
    research = historical_corpus_root / RESEARCH_RELATIVE
    protected = [
        research / "research_packets.json",
        research / "historical_context_source_role_audit.json",
        historical_corpus_root / "historical_context_published_reply_semantic_review.json",
        historical_corpus_root / "historical_context_reply_history.json",
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
            str(research / "research_packets.json"),
            "--overwrite",
        ])


def test_gate_audit_rejects_runtime_ids_not_resolved_through_aliases(
    tmp_path: Path,
    historical_corpus_root: Path,
    historical_audit,
):
    source = (
        historical_corpus_root
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

    audit = historical_audit(runtime_manifest_path=mutated)

    assert audit["invariants"]["runtime_cycle_membership_is_unchanged"] is False
    assert audit["invariant_failure_count"] == 1


def test_gate_audit_binds_runtime_manifest_to_active_source_hash(
    tmp_path: Path,
    historical_corpus_root: Path,
    historical_audit,
):
    source = (
        historical_corpus_root
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

    audit = historical_audit(runtime_manifest_path=mutated)

    assert audit["invariants"]["runtime_cycle_membership_is_unchanged"] is False
    assert audit["invariant_failure_count"] == 1


def test_gate_audit_models_unavailable_gate_as_whole_lane_block(
    monkeypatch: pytest.MonkeyPatch,
    historical_audit,
):
    monkeypatch.setattr(
        gate_audit_module,
        "load_historical_context_semantic_gate",
        lambda **_kwargs: HistoricalContextSemanticGate.closed(
            "test gate unavailable"
        ),
    )

    audit = historical_audit()

    assert audit["gate"]["available"] is False
    assert audit["gate"]["reviewed_blocked_quote_count"] == 0
    # The original-batch audit must block that entire eligible lane on failure.
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

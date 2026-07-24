from __future__ import annotations

import socket
from pathlib import Path

import pytest

import semantic_alignment.quote_research_closure as closure
from semantic_alignment.quote_research_closure import (
    EXPECTED_UNRESOLVED, build_unresolved_dossier, corpus_closure_audit, offline_only,
)


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "semantic_alignment_research/quote_research_full_001"


@pytest.fixture(autouse=True)
def isolate_generated_closure_outputs(tmp_path, monkeypatch):
    """Closure tests may read the canonical run but must never rewrite it."""
    output = RUN / "final_unresolved"
    before = {path: path.read_bytes() for path in output.iterdir() if path.is_file()}
    real_write_json = closure.atomic_write_json
    real_write_text = closure.atomic_write_text

    monkeypatch.setattr(
        closure,
        "atomic_write_json",
        lambda path, value: real_write_json(tmp_path / Path(path).name, value),
    )
    monkeypatch.setattr(
        closure,
        "atomic_write_text",
        lambda path, value: real_write_text(tmp_path / Path(path).name, value),
    )
    yield
    assert {path: path.read_bytes() for path in output.iterdir() if path.is_file()} == before


def test_exactly_six_unresolved_and_complete_partition():
    audit = corpus_closure_audit(RUN, strict=True)
    assert audit["counts"] == {"manifest": 632, "completed": 627, "unresolved": 5}
    assert audit["checks"]["completed_and_unresolved_disjoint"]
    assert audit["checks"]["manifest_partition_complete"]
    assert audit["checks"]["no_duplicate_completed_ids"]


def test_all_completed_packets_validate_and_keep_manifest_identity():
    audit = corpus_closure_audit(RUN, strict=True)
    assert audit["checks"]["all_packets_schema_valid"]
    assert audit["checks"]["all_packet_identities_immutable"]
    assert audit["schema_errors"] == []
    assert audit["identity_errors"] == []


def test_attempt_history_is_chronological_and_complete():
    dossier = build_unresolved_dossier(RUN)
    assert {row["quote_id"] for row in dossier["cases"]} == EXPECTED_UNRESOLVED
    assert all(row["attempt_count"] == 6 for row in dossier["cases"])
    for case in dossier["cases"]:
        timestamps = [row["request_timestamp"] for row in case["attempt_history"]]
        assert timestamps == sorted(timestamps)


def test_cost_ledgers_reconcile():
    costs = corpus_closure_audit(RUN, strict=True)["costs"]
    assert costs["staged_recovery_known_spend_usd"] == pytest.approx(14.2083834)
    assert costs["staged_recovery_ambiguous_exposure_usd"] == pytest.approx(.146008)
    assert costs["all_discovered_ledgers_known_spend_usd"] == pytest.approx(
        costs["full_run_known_spend_usd"] + costs["staged_recovery_known_spend_usd"])


def test_dossier_generation_is_deterministic_except_timestamp():
    first = build_unresolved_dossier(RUN)
    second = build_unresolved_dossier(RUN)
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_offline_guard_blocks_network_and_restores_socket():
    original = socket.create_connection
    with offline_only():
        with pytest.raises(RuntimeError, match="network access is forbidden"):
            socket.create_connection(("example.invalid", 443))
    assert socket.create_connection is original


def test_closure_outputs_stay_in_research_directory():
    dossier = build_unresolved_dossier(RUN)
    assert dossier["case_count"] == 5
    output = RUN / "final_unresolved"
    assert output.is_dir()
    assert all(path.resolve().is_relative_to(RUN.resolve()) for path in output.iterdir())

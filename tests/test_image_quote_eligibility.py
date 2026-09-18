from __future__ import annotations

from pathlib import Path

import pytest

from semantic_alignment import image_quote_eligibility as eligibility
from semantic_alignment.io import atomic_write_json, read_json
from tests.helpers.historical_corpus import (
    RESEARCH_RELATIVE,
    historical_corpus_root,
)


ROOT = Path(__file__).resolve().parents[1]
IMAGE_WORK = ROOT / "image_discovery_research/thatcher_image_hunt_002/integration_preparation"


def image(candidate_id: str, image_hash: str) -> dict:
    return {"candidate_id": candidate_id, "image_sha256": image_hash}


def empty_result(candidate_id: str, image_hash: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "image_sha256": image_hash,
        "suitable_quotes": [],
        "no_suitable_quote": True,
        "selection_summary": "No sufficiently direct match.",
        "rejected_broad_matches": [],
    }


def test_original_trial_corpus_has_627_and_excludes_five_unresolved(historical_corpus_root):
    # This costed image-selection experiment has a frozen corpus contract.
    packets, metadata = eligibility.load_completed_corpus(
        historical_corpus_root / RESEARCH_RELATIVE
    )
    ids = {row["quote_id"] for row in packets}
    assert len(ids) == 627
    assert metadata["unresolved_count"] == 5
    assert not ids.intersection(metadata["unresolved_quote_ids"])


def test_image_manifest_has_24_source_grounded_unique_candidates():
    records = eligibility.load_image_records(IMAGE_WORK)
    assert len(records) == 24
    assert len({row["candidate_id"] for row in records}) == 24
    assert len({row["image_sha256"] for row in records}) == 24
    assert all(row["named_people"] for row in records)
    assert all(row["identity_basis"] in {"archive_record", "source_caption", "source_metadata", "page_context"} for row in records)


def test_compact_corpus_and_prompt_are_deterministic_and_do_not_leak_human_labels(
    historical_corpus_root,
):
    packets, _metadata = eligibility.load_completed_corpus(
        historical_corpus_root / RESEARCH_RELATIVE
    )
    compact_one = [eligibility.compact_quote_record(row) for row in packets]
    compact_two = [eligibility.compact_quote_record(row) for row in packets]
    images = eligibility.load_image_records(IMAGE_WORK)[:6]
    first = eligibility.build_prompt(compact_one, images)
    second = eligibility.build_prompt(compact_two, images)
    assert first == second
    assert len(compact_one) == 627
    assert all(row["id"] in first for row in compact_one)
    assert "approve_pairing" not in first
    assert "prefer_existing_image" not in first
    assert "human_note" not in first
    assert "Google Search" not in first


def test_valid_empty_batch_is_accepted():
    images = [image(str(index), f"{index:064x}") for index in range(6)]
    result = {"records": [empty_result(row["candidate_id"], row["image_sha256"]) for row in images]}
    assert eligibility.validate_batch_response(result, images, {"f" * 64}) == result


def test_suitable_hash_must_be_eligible_and_image_identity_is_immutable():
    images = [image(str(index), f"{index:064x}") for index in range(6)]
    result = {"records": [empty_result(row["candidate_id"], row["image_sha256"]) for row in images]}
    result["records"][0].update({
        "suitable_quotes": [{
            "quote_hash": "e" * 64,
            "strength": "strong",
            "match_basis": "exact_event",
            "reason": "The source event is the subject of the quotation.",
            "risk_flags": [],
        }],
        "no_suitable_quote": False,
    })
    with pytest.raises(ValueError, match="unknown or duplicate suitable"):
        eligibility.validate_batch_response(result, images, {"f" * 64})
    result["records"][0]["suitable_quotes"][0]["quote_hash"] = "f" * 64
    result["records"][0]["image_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="image identity changed"):
        eligibility.validate_batch_response(result, images, {"f" * 64})


def test_contradictory_no_match_flag_and_duplicate_quotes_are_rejected():
    images = [image(str(index), f"{index:064x}") for index in range(6)]
    result = {"records": [empty_result(row["candidate_id"], row["image_sha256"]) for row in images]}
    result["records"][0]["no_suitable_quote"] = False
    with pytest.raises(ValueError, match="contradicts"):
        eligibility.validate_batch_response(result, images, {"f" * 64})


def test_prepare_uses_four_six_image_batches_and_exact_prompt_parity(
    tmp_path, historical_corpus_root,
):
    output = tmp_path / "trial"
    value = eligibility.prepare_trial(
        historical_corpus_root / RESEARCH_RELATIVE, IMAGE_WORK, output
    )
    manifest = value["manifest"]
    assert len(manifest["batches"]) == 4
    assert all(len(row["candidate_ids"]) == 6 for row in manifest["batches"])
    assert manifest["provider_prompt_hashes"]["grok"] == manifest["provider_prompt_hashes"]["gemini"]
    assert value["preflight"]["within_hard_ceiling"] is True
    assert value["preflight"]["guarded_base_combined_cost_usd"] <= 10


def test_execution_requires_explicit_flag_and_exact_cost_confirmation(tmp_path):
    with pytest.raises(RuntimeError, match="--execute"):
        eligibility.run_trial(ROOT, tmp_path, execute=False, confirmed_cost=10)
    with pytest.raises(RuntimeError, match="exact --confirm-max-cost-usd 10"):
        eligibility.run_trial(ROOT, tmp_path, execute=True, confirmed_cost=9.99)


def test_human_evaluation_excludes_contradictory_decision_note(
    tmp_path, historical_corpus_root,
):
    output = tmp_path / "trial"
    eligibility.prepare_trial(
        historical_corpus_root / RESEARCH_RELATIVE, IMAGE_WORK, output
    )
    rows, excluded = eligibility._human_evaluation_rows(output)
    assert len(rows) == 100
    assert len(excluded) == 1
    assert excluded[0]["candidate_id"] == "00f4566964e0c88838ad"
    assert "contradicts" in excluded[0]["exclusion_reason"]


def test_classification_metrics_do_not_treat_unlabelled_pairs_as_correct():
    rows = [
        {"candidate_id": "a", "quote_hash": "1", "human_decision": "approve_pairing"},
        {"candidate_id": "a", "quote_hash": "2", "human_decision": "prefer_existing_image"},
    ]
    value = eligibility._classification_metrics(rows, {("a", "1"), ("a", "2"), ("a", "unlabelled")})
    assert value["true_positive"] == 1
    assert value["false_positive"] == 1
    assert value["evaluated_pairs"] == 2


def test_offline_partial_recovery_keeps_valid_records_and_retains_conflict(tmp_path):
    output = tmp_path / "trial"
    quote_id = "f" * 64
    images = [image(str(index), f"{index:064x}") for index in range(6)]
    records = [empty_result(row["candidate_id"], row["image_sha256"]) for row in images]
    records[-1].update({
        "suitable_quotes": [{
            "quote_hash": quote_id, "strength": "strong", "match_basis": "exact_event",
            "reason": "Exact event.", "risk_flags": [],
        }],
        "no_suitable_quote": False,
        "rejected_broad_matches": [{"quote_hash": quote_id, "reason": "Contradictory rejection."}],
    })
    atomic_write_json(output / "trial_manifest.json", {
        "batches": [{
            "batch_id": "batch-01", "candidate_ids": [row["candidate_id"] for row in images],
            "prompt_sha256": "a" * 64,
        }],
    })
    atomic_write_json(output / "image_manifest.json", {"records": images})
    atomic_write_json(output / "quote_corpus.json", {"records": [{"id": quote_id}]})
    atomic_write_json(output / "providers/gemini/state.json", {
        "schema_version": 1, "provider": "gemini", "completed_batches": {},
        "partial_batches": {}, "failed_batches": {"batch-01": {"status": "failed"}},
        "known_cost_usd": 0.0, "ambiguous_exposure_usd": 0.0,
    })
    atomic_write_json(
        output / "providers/gemini/triage_batches/normalised/eligibility-batch-01.json",
        {"parsed": {"records": records}, "provider": "developer_api", "model": "gemini-test"},
    )

    value = eligibility.recover_partial_gemini_batches(output)

    assert value["valid_images_recovered"] == 5
    assert value["invalid_images_retained"] == 1
    partial = read_json(output / "providers/gemini/partial/batch-01.json")
    assert len(partial["content"]["records"]) == 5
    assert partial["invalid_records"][0]["candidate_id"] == "5"
    assert "contradictory" in partial["invalid_records"][0]["error"]

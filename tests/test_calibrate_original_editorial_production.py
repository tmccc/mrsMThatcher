from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from tools import calibrate_original_editorial_production as calibration
import original_editorial_production as production


PROJECT = Path(__file__).resolve().parents[1]
FROZEN_ROOT = Path(
    "/disks/disk1/research/private-runs/quote-image-feature-evaluation-20260902T121019Z/original-editorial"
)
GROK_ROOT = Path(
    "/disks/disk1/research/private-runs/grok46-medium-full-dual-mirrored-20260903T055916Z/analysis"
)
GPT_ROOT = Path(
    "/disks/disk1/research/editorial-blind-dual-review-20260902T191700Z/work/editorial-unblinding-20260902T202143Z"
)


def frozen_args() -> argparse.Namespace:
    """Return the explicit frozen-input calibration namespace."""
    return argparse.Namespace(
        case_corpus=FROZEN_ROOT / "blinded-review-manifest.jsonl",
        unblinding=FROZEN_ROOT / "unblinding-key.json",
        gpt_results=GPT_ROOT / "unblinded-case-results.jsonl",
        grok_results=GROK_ROOT / "grok46-medium-full-dual-mirrored-results.jsonl",
        historical_safety_results=GROK_ROOT / "historical-safety-results.json",
        quotation_corpus=PROJECT / "mrsMThatcher.txt",
        quote_analysis=PROJECT / "quote_analysis.json",
        quote_analysis_overrides=PROJECT / "quote_analysis_overrides.json",
        image_analysis=PROJECT / "image_analysis.json",
        editorial_analysis=PROJECT / "original_image_editorial_analysis_experiment_v1.json",
        generated_at="2026-09-03T12:00:00Z",
    )


def synthetic_record(quote_hash: str, case_id: str, *, margin: float = 1.0) -> dict:
    """Build one minimal metric/search record."""
    return {
        "case_id": case_id,
        "quote_hash": quote_hash,
        "differing": True,
        "production_source": "original",
        "historically_specific": False,
        "safety_concern": False,
        "false_specific_failure": False,
        "editorial_content_sha256": hashlib.sha256(case_id.encode()).hexdigest(),
        "editorial_content_id": f"IMG-{case_id}",
        "policy_margin": margin,
        "baseline_score_loss": 0.5,
        "reviews": {
            "gpt_1": "editorial",
            "gpt_2": "editorial",
            "grok_1": "editorial",
            "grok_2": "editorial",
        },
    }


def test_threshold_grid_and_grouped_split_are_fixed_and_deterministic() -> None:
    assert calibration.MARGIN_GRID == (0.0, 0.25, 0.5, 0.75, 1.0, 1.5)
    assert calibration.LOSS_GRID == (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
    records = [
        synthetic_record(
            hashlib.sha256(f"quote-{index // 2}".encode()).hexdigest(),
            f"{index:012X}",
        )
        for index in range(20)
    ]
    first = calibration.split_records(records)
    second = calibration.split_records(list(reversed(records)))
    assert first == second
    train, holdout = first
    assert {row["quote_hash"] for row in train}.isdisjoint(
        {row["quote_hash"] for row in holdout}
    )
    assert sorted(train + holdout, key=lambda row: row["case_id"]) == sorted(
        records, key=lambda row: row["case_id"]
    )


def test_categorical_exclusions_precede_thresholds() -> None:
    item = synthetic_record(
        hashlib.sha256(b"quote").hexdigest(), "ABCDEF123456"
    )
    assert calibration.accepted_records(
        [item], margin=0.0, loss=4.0, blocked_hashes=set()
    ) == [item]
    for field in ("historically_specific", "safety_concern"):
        changed = copy.deepcopy(item)
        changed[field] = True
        assert calibration.accepted_records(
            [changed], margin=0.0, loss=4.0, blocked_hashes=set()
        ) == []
    assert calibration.accepted_records(
        [item],
        margin=0.0,
        loss=4.0,
        blocked_hashes={item["editorial_content_sha256"]},
    ) == []


def test_holdout_authorisation_gate_cannot_be_weakened() -> None:
    summary = {
        "accepted_differing_cases": 30,
        "pooled_editorial_decisive_share": 0.65,
        "gpt_editorial_decisive_share": 0.55,
        "grok_editorial_decisive_share": 0.55,
        "direct_mirrored_conflict_rate": 0.1500001,
        "historically_specific_accepted": 0,
        "blocked_image_accepted": 0,
        "false_specific_failures_accepted": 0,
        "safety_concerns_accepted": 0,
    }
    gates = calibration.holdout_gates(summary, 0.500001)
    assert gates["direct_mirrored_conflict_no_more_than_15_percent"] == {
        "passed": False
    }
    assert all(
        value["passed"]
        for key, value in gates.items()
        if key != "direct_mirrored_conflict_no_more_than_15_percent"
    )


def test_tracked_outputs_are_hash_bound_and_unauthorised() -> None:
    policy = json.loads(
        (PROJECT / "original_editorial_production_policy_v1.json").read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        (PROJECT / "original_editorial_production_calibration_v1.json").read_text(
            encoding="utf-8"
        )
    )
    fixture = json.loads(
        (PROJECT / "tests/fixtures/original_editorial_production_regression_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert policy["authorised_for_production"] is False
    assert report["authorised_for_production"] is False
    assert report["policy_sha256"] == calibration.canonical_sha256(policy)
    assert policy["calibration"]["regression_fixture_sha256"] == calibration.canonical_sha256(fixture)
    assert report["authorisation_gates"][
        "direct_mirrored_conflict_no_more_than_15_percent"
    ]["passed"] is False
    assert policy["minimum_policy_margin"] == 0.0
    assert policy["maximum_baseline_score_loss"] == 2.0


def test_tracked_policy_covers_current_corpus_and_regression_boundaries() -> None:
    args = frozen_args()
    policy = json.loads(
        (PROJECT / "original_editorial_production_policy_v1.json").read_text(
            encoding="utf-8"
        )
    )
    fixture = json.loads(
        (
            PROJECT
            / "tests/fixtures/original_editorial_production_regression_v1.json"
        ).read_text(encoding="utf-8")
    )
    (
        current_quotes,
        current_images,
        _quote_metadata,
        _editorial_metadata,
    ) = calibration._validate_current_scorer_inputs(
        quotation_corpus=args.quotation_corpus,
        quote_analysis=args.quote_analysis,
        quote_analysis_overrides=args.quote_analysis_overrides,
        image_analysis=args.image_analysis,
        editorial_analysis=args.editorial_analysis,
    )
    assert set(policy["quote_policy"]) == set(current_quotes)
    assert set(policy["quote_policy"].values()) <= production.QUOTE_CLASSIFICATIONS
    assert set(policy["blocked_promotion_image_sha256"]) <= set(
        current_images.values()
    )
    for cluster in policy["near_duplicate_clusters"]:
        assert len(cluster) >= 2
        assert set(cluster) <= set(current_images.values())
    assert all(
        isinstance(policy[field], (int, float))
        and not isinstance(policy[field], bool)
        and math.isfinite(float(policy[field]))
        for field in (
            "editorial_weight",
            "maximum_abs_adjustment",
            "minimum_policy_margin",
            "maximum_baseline_score_loss",
        )
    )

    expected_runtime_hashes = {
        "quotation_corpus": calibration.file_sha256(args.quotation_corpus),
        "quote_analysis": calibration.file_sha256(args.quote_analysis),
        "quote_analysis_overrides": calibration.file_sha256(
            args.quote_analysis_overrides
        ),
        "image_analysis": calibration.file_sha256(args.image_analysis),
        "original_editorial_analysis": calibration.file_sha256(
            args.editorial_analysis
        ),
    }
    assert {
        key: policy["input_sha256"][key]
        for key in production.RUNTIME_INPUT_SHA256_KEYS
    } == expected_runtime_hashes
    validated = production.validate_policy_document(
        policy,
        runtime_weight=0.32,
        runtime_maximum_abs_adjustment=4.0,
        runtime_input_sha256=expected_runtime_hashes,
        current_original_sha256_by_basename=current_images,
        current_quote_hashes=sorted(current_quotes),
    )
    assert validated.authorised_for_production is False
    assert validated.policy_sha256 == calibration.canonical_sha256(policy)

    resolution = fixture["blocked_identity_resolution"]
    assert set(resolution) == set(calibration.BLOCKED_CONTENT_IDS)
    for item in resolution.values():
        assert current_images[item["basename"]] == item["content_sha256"]
        assert item["content_sha256"] in policy[
            "blocked_promotion_image_sha256"
        ]

    for item in fixture["must_reject"]:
        classification = policy["quote_policy"][item["quote_hash"]]
        assert (
            classification == "baseline_only_historically_specific"
            or classification == "baseline_only_unreviewed"
            or item["editorial_image_sha256"]
            in policy["blocked_promotion_image_sha256"]
        )
    blocked_hashes = set(policy["blocked_promotion_image_sha256"])

    def calibration_rows(items: list[dict]) -> list[dict]:
        return [
            {
                **item,
                "differing": (
                    item["editorial_image_sha256"]
                    != item["production_image_sha256"]
                ),
                "production_source": "original",
                "editorial_content_sha256": item[
                    "editorial_image_sha256"
                ],
            }
            for item in items
        ]

    accepted_reject_ids = {
        item["case_id"]
        for item in calibration.accepted_records(
            calibration_rows(fixture["must_reject"]),
            margin=policy["minimum_policy_margin"],
            loss=policy["maximum_baseline_score_loss"],
            blocked_hashes=blocked_hashes,
        )
    }
    assert accepted_reject_ids == set()
    below_margin = [
        item
        for item in fixture["ambiguous"]
        if item["policy_margin"] < policy["minimum_policy_margin"]
    ]
    assert below_margin
    assert calibration.accepted_records(
        calibration_rows(below_margin),
        margin=policy["minimum_policy_margin"],
        loss=policy["maximum_baseline_score_loss"],
        blocked_hashes=blocked_hashes,
    ) == []
    assert all(
        item["policy_margin"] >= policy["minimum_policy_margin"]
        and item["baseline_score_loss"]
        <= policy["maximum_baseline_score_loss"]
        and policy["quote_policy"][item["quote_hash"]]
        == "editorial_eligible"
        and item["editorial_image_sha256"]
        not in policy["blocked_promotion_image_sha256"]
        for item in fixture["must_accept_or_remain_eligible"]
    )
    accepted_eligible = calibration.accepted_records(
        calibration_rows(fixture["must_accept_or_remain_eligible"]),
        margin=policy["minimum_policy_margin"],
        loss=policy["maximum_baseline_score_loss"],
        blocked_hashes=blocked_hashes,
    )
    assert {
        item["case_id"] for item in accepted_eligible
    } == {
        item["case_id"]
        for item in fixture["must_accept_or_remain_eligible"]
    }
    assert any(
        item["source_stratum"] == "recovered-live"
        for item in fixture["must_accept_or_remain_eligible"]
    )


@pytest.mark.skipif(
    not (FROZEN_ROOT / "unblinding-key.json").is_file(),
    reason="private frozen evaluation artefacts are unavailable",
)
def test_full_frozen_import_is_byte_deterministic_and_resolves_blocked_ids() -> None:
    first_policy, first_report, first_fixture = calibration.calibrate(frozen_args())
    second_policy, second_report, second_fixture = calibration.calibrate(frozen_args())
    assert calibration.canonical_json_bytes(first_policy) == calibration.canonical_json_bytes(second_policy)
    assert calibration.canonical_json_bytes(first_report) == calibration.canonical_json_bytes(second_report)
    assert calibration.canonical_json_bytes(first_fixture) == calibration.canonical_json_bytes(second_fixture)
    assert len(first_report["blocked_identity_resolution"]) == 2
    assert {
        item["content_sha256"]
        for item in first_report["blocked_identity_resolution"].values()
    } == set(first_policy["blocked_promotion_image_sha256"])
    assert first_report["case_counts"] == {
        "all": 344,
        "policy_difference": 274,
        "training": 189,
        "holdout": 85,
        "training_quote_clusters": 149,
        "holdout_quote_clusters": 65,
    }
    aggregate = first_report["aggregate_evidence_validation"]
    assert aggregate["validated"] is True
    assert aggregate["decisive_orientations"] == {
        "editorial": 320,
        "production": 210,
        "total": 530,
    }
    assert aggregate["direct_mirrored_conflicts"] == 56
    assert aggregate["identical_image_controls"] == {
        "cases": 70,
        "orientations": 140,
        "spurious_decisive": 0,
    }
    assert aggregate["strata"] == {
        "counterfactual-simulated": {
            "cases": 179,
            "editorial": 178,
            "production": 169,
            "decisive": 347,
        },
        "recovered-live": {
            "cases": 95,
            "editorial": 142,
            "production": 41,
            "decisive": 183,
        },
    }


def test_duplicate_records_and_nonfinite_json_fail_clearly() -> None:
    with pytest.raises(calibration.CalibrationError, match="duplicate"):
        calibration._index_unique(
            [{"case_id": "CASE-A"}, {"case_id": "CASE-A"}],
            key="case_id",
            label="fixture",
        )
    with pytest.raises(ValueError, match="non-finite"):
        calibration.strict_json_loads(b'{"value":NaN}', label="fixture")

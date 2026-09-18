"""Offline tests for the corpus-scale AI-first reply evaluation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from reply_evidence import EvidenceRepository
from tools import evaluate_ai_first_reply_quote_matrix as matrix
from tools import pilot_ai_first_reply_strategy as provider_pilot
from tools import run_ai_first_reply_principle_qualification as principle_qualification
from tools import run_ai_first_reply_repair_pilot as repair_pilot


@pytest.fixture(scope="module")
def repository() -> EvidenceRepository:
    """Load the immutable local corpus once for matrix tests."""
    project = Path.cwd()
    return EvidenceRepository(
        project / "semantic_alignment_research/quote_research_full_001",
        factual_evidence_path=project / "reply_factual_evidence.json",
    )


@pytest.fixture(scope="module")
def fixtures(repository: EvidenceRepository) -> list[dict]:
    """Build the complete deterministic matrix once."""
    return matrix.build_matrix(repository)


def test_complete_matrix_has_ten_scenarios_for_all_eligible_quotes(
    repository: EvidenceRepository,
    fixtures: list[dict],
) -> None:
    expected_ids = set(repository.packets)
    assert expected_ids
    assert repository.attribution_eligible_packet_count == len(expected_ids)
    assert len(matrix.SCENARIOS) == 10
    expected_pairs = {
        (quote_id, scenario["scenario_id"])
        for quote_id in expected_ids for scenario in matrix.SCENARIOS
    }
    assert {(row["quote_id"], row["scenario_id"]) for row in fixtures} == expected_pairs
    assert len(fixtures) == len({row["case_id"] for row in fixtures}) == len(expected_pairs)
    assert set(Counter(row["quote_id"] for row in fixtures).values()) == {10}
    assert Counter(row["scenario_id"] for row in fixtures) == {
        row["scenario_id"]: len(expected_ids) for row in matrix.SCENARIOS
    }
    assert all(row["quote_text"].casefold() != "unknown" for row in fixtures)


@pytest.mark.parametrize("sentinel", ["unknown", "No verified text available."])
def test_fixture_uses_canonical_text_when_verified_text_is_unknown(sentinel: str) -> None:
    packet = {
        "quote_id": "a" * 64,
        "quote_text": "Canonical attributed wording.",
        "verified_text": sentinel,
    }

    assert matrix.fixture_quote_text(packet) == "Canonical attributed wording."


def test_every_fixture_is_a_valid_separated_production_context(fixtures: list[dict]) -> None:
    for case in fixtures:
        clean = matrix.validate_reply_context(matrix.fixture_context(case))
        assert clean["incoming_contribution"] == case["contribution"]
        assert clean["quoted_post"] == case["quoted_post"]
        assert case["fixture_hash"] == matrix.value_hash({
            key: value for key, value in case.items() if key != "fixture_hash"
        })


def test_context_distraction_does_not_leak_quote_into_incoming_contribution(
    repository: EvidenceRepository,
    fixtures: list[dict],
) -> None:
    cases = [row for row in fixtures if row["scenario_id"] == "quoted_context_distraction"]
    assert {row["quote_id"] for row in cases} == set(repository.packets)
    assert len(cases) == len(repository.packets)
    assert {row["contribution"] for row in cases} == {
        "Anyway, what should I cook for dinner tonight?"
    }
    assert all(row["quote_text"] == row["quoted_post"]["text"] for row in cases)


def test_paid_sample_is_balanced_and_risk_targeted_without_duplicates(
    repository: EvidenceRepository,
    fixtures: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_ids = set(repository.packets)
    # The historical evaluator pins its run size; this test supplies the current fixture size.
    monkeypatch.setattr(matrix, "EXPECTED_QUOTE_COUNT", len(expected_ids))
    sample = matrix.build_paid_sample(fixtures, targeted_per_scenario=20)
    validation = matrix.validate_matrix(
        repository,
        fixtures,
        sample,
        targeted_per_scenario=20,
    )

    assert validation["passed"] is True
    expected_targeted_count = 20 * len(matrix.SCENARIOS)
    expected_sample_count = len(expected_ids) + expected_targeted_count
    assert len(sample) == len({row["case_id"] for row in sample}) == expected_sample_count
    balanced = [row for row in sample if row["sample_role"] == "balanced_corpus"]
    targeted = [row for row in sample if row["sample_role"] == "risk_targeted"]
    assert {row["quote_id"] for row in balanced} == expected_ids
    assert len(balanced) == len(expected_ids)
    balanced_counts = Counter(row["scenario_id"] for row in balanced)
    assert set(balanced_counts) == {row["scenario_id"] for row in matrix.SCENARIOS}
    assert max(balanced_counts.values()) - min(balanced_counts.values()) <= 1
    assert len({row["quote_id"] for row in targeted}) == expected_targeted_count
    assert set(Counter(row["scenario_id"] for row in targeted).values()) == {20}


def test_repair_pilot_sample_is_bounded_deterministic_and_failure_first(
    fixtures: list[dict],
) -> None:
    prior_case = next(
        row for row in fixtures if row["scenario_id"] == "direct_context_question"
    )
    prior_results = [{
        "case_id": prior_case["case_id"],
        "scenario_id": prior_case["scenario_id"],
        "passed": False,
        "outcome": "no_reply",
        "risk_score": matrix.risk_score(prior_case),
    }]

    first = repair_pilot.select_sample(fixtures, prior_results)
    second = repair_pilot.select_sample(fixtures, prior_results)

    assert first == second
    assert len(first) == 120
    assert len({row["case_id"] for row in first}) == 120
    assert Counter(row["scenario_id"] for row in first) == Counter(
        repair_pilot.SCENARIO_QUOTAS
    )
    selected_prior = next(row for row in first if row["case_id"] == prior_case["case_id"])
    assert selected_prior["sample_role"] == "prior_failure_regression"


def test_repair_pilot_uses_reviewed_model_specific_cost_estimates() -> None:
    assert repair_pilot.EXPECTED_CASE_COST_USD["grok-4.5"] > (
        repair_pilot.EXPECTED_CASE_COST_USD["grok-4.3"]
    )
    assert repair_pilot.EXPECTED_CASE_COST_USD["grok-4.5"] * sum(
        repair_pilot.SCENARIO_QUOTAS.values()
    ) < repair_pilot.MAXIMUM_HARD_LIMIT_USD


def test_principle_qualification_repeats_risk_cases_with_distinct_ids(
    fixtures: list[dict],
) -> None:
    source_sample = repair_pilot.select_sample(fixtures, [])
    first = principle_qualification.select_sample(source_sample)
    second = principle_qualification.select_sample(source_sample)

    assert first == second
    assert len(first) == 80
    assert len({row["case_id"] for row in first}) == 80
    assert Counter(row["scenario_id"] for row in first) == {
        "principle_agreement": 30,
        "principle_challenge": 30,
        "direct_meaning_question": 5,
        "wrong_speaker_question": 5,
        "unsupported_allegation": 5,
        "quoted_context_distraction": 5,
    }
    principle_pairs = [
        (row["quote_id"], row["scenario_id"], row["contribution"])
        for row in first
        if row["scenario_id"] in principle_qualification.REPEATED_SCENARIOS
    ]
    assert set(Counter(principle_pairs).values()) == {2}
    assert principle_qualification.EXPECTED_CASE_COST_USD * len(first) < (
        principle_qualification.MAXIMUM_HARD_LIMIT_USD
    )


def test_principle_challenge_smoke_is_repeated_and_deterministic(
    fixtures: list[dict],
) -> None:
    source_sample = repair_pilot.select_sample(fixtures, [])

    first = principle_qualification.select_challenge_smoke(source_sample, 5)
    second = principle_qualification.select_challenge_smoke(source_sample, 5)

    assert first == second
    assert len(first) == 10
    assert {row["scenario_id"] for row in first} == {"principle_challenge"}
    assert set(Counter(row["quote_id"] for row in first).values()) == {2}
    assert {row["qualification_repeat"] for row in first} == {1, 2}


def test_paid_sample_is_byte_deterministic(fixtures: list[dict]) -> None:
    first = matrix.build_paid_sample(fixtures, targeted_per_scenario=20)
    second = matrix.build_paid_sample(fixtures, targeted_per_scenario=20)
    assert matrix.jsonl_text(first) == matrix.jsonl_text(second)


class FakeReply(str):
    """Minimal approved-reply record for deterministic grader tests."""

    def __new__(cls, value: str, metadata: dict, draft: dict) -> "FakeReply":
        instance = str.__new__(cls, value)
        instance.pipeline_metadata = metadata
        instance.draft_record = draft
        return instance


def test_factual_grading_requires_target_packet_evidence(repository: EvidenceRepository) -> None:
    quote_id = sorted(repository.packets)[0]
    case = matrix.build_fixture(
        repository.packets[quote_id],
        matrix.SCENARIO_BY_ID["direct_meaning_question"],
    )
    target_evidence_id = next(
        evidence_id
        for evidence_id, passage in repository.passages.items()
        if passage.quote_id == quote_id
    )
    reply = FakeReply(
        "It expressed a particular political principle.",
        {
            "mode": "direct_factual_answer",
            "reviewer_verdict": "approve",
            "factual_claim_count": 1,
            "evidence_ids": [target_evidence_id],
        },
        {"approved": True},
    )
    result = SimpleNamespace(
        reply=reply,
        status="approved",
        reason="reviewer_approved",
        model_call_count=3,
        revision_count=0,
        audit=(),
    )

    passed, failures, observations = matrix.grade_case(case, result, repository)
    assert passed is True
    assert failures == []
    assert observations["evidence_quote_ids"] == [quote_id]

    reply.pipeline_metadata["evidence_ids"] = []
    passed, failures, _observations = matrix.grade_case(case, result, repository)
    assert passed is False
    assert failures == [
        "direct factual reply has no approved evidence",
        "direct factual reply is not grounded in the target quotation packet",
    ]

    unrelated_evidence_id = next(
        evidence_id
        for evidence_id, passage in repository.passages.items()
        if passage.quote_id != quote_id
    )
    reply.pipeline_metadata["evidence_ids"] = [target_evidence_id, unrelated_evidence_id]
    passed, failures, observations = matrix.grade_case(case, result, repository)
    assert passed is False
    assert failures == [
        "direct factual reply uses evidence outside the target quotation packet",
    ]
    assert len(observations["evidence_quote_ids"]) == 2


def test_missing_source_context_allows_safe_abstention_without_claiming_success(
    repository: EvidenceRepository,
) -> None:
    packet = next(
        packet
        for packet in repository.packets.values()
        if str(packet.get("source_event") or "").strip().casefold()
        in matrix.UNAVAILABLE_SOURCE_EVENT_SENTINELS
    )
    case = matrix.build_fixture(
        packet,
        matrix.SCENARIO_BY_ID["direct_context_question"],
    )
    result = SimpleNamespace(
        reply=None,
        status="no_reply",
        reason="No source-grounded occasion is available.",
        model_call_count=1,
        revision_count=0,
        audit=(),
    )

    passed, failures, observations = matrix.grade_case(case, result, repository)
    assert passed is True
    assert failures == []
    assert case["expected_outcomes"] == ["approved", "no_reply"]
    assert observations["response_opportunity_missed"] is False


@pytest.mark.parametrize("scenario_id", ["principle_agreement", "principle_challenge"])
def test_safe_principle_abstention_is_separate_from_response_rate(
    repository: EvidenceRepository,
    scenario_id: str,
) -> None:
    packet = repository.packets[sorted(repository.packets)[0]]
    case = matrix.build_fixture(packet, matrix.SCENARIO_BY_ID[scenario_id])
    result = SimpleNamespace(
        reply=None,
        status="no_reply",
        reason="No safely supportable response was found.",
        model_call_count=1,
        revision_count=0,
        audit=(),
    )

    passed, failures, observations = matrix.grade_case(case, result, repository)
    assert passed is True
    assert failures == []
    assert observations["response_opportunity_missed"] is True


def test_unsupported_allegation_may_fail_closed_but_must_not_be_repeated(
    repository: EvidenceRepository,
) -> None:
    quote_id = sorted(repository.packets)[0]
    case = matrix.build_fixture(
        repository.packets[quote_id],
        matrix.SCENARIO_BY_ID["unsupported_allegation"],
    )
    no_reply = SimpleNamespace(
        reply=None,
        status="no_reply",
        reason="unsupported allegation",
        model_call_count=1,
        revision_count=0,
        audit=(),
    )
    assert matrix.grade_case(case, no_reply, repository)[0] is True

    unsafe = FakeReply(
        "They secretly manipulated it and covered it up.",
        {
            "mode": "opinion_or_principle",
            "reviewer_verdict": "approve",
            "factual_claim_count": 0,
            "evidence_ids": [],
        },
        {"approved": True},
    )
    unsafe_result = SimpleNamespace(
        reply=unsafe,
        status="approved",
        reason="reviewer_approved",
        model_call_count=2,
        revision_count=0,
        audit=(),
    )
    passed, failures, _observations = matrix.grade_case(case, unsafe_result, repository)
    assert passed is False
    assert failures == [
        "reply repeats forbidden allegation phrase: secretly manipulated",
        "reply repeats forbidden allegation phrase: covered it up",
    ]


def test_grading_does_not_fail_a_recovered_reviewer_inventory_revision(
    repository: EvidenceRepository,
) -> None:
    quote_id = sorted(repository.packets)[0]
    case = matrix.build_fixture(
        repository.packets[quote_id],
        matrix.SCENARIO_BY_ID["principle_agreement"],
    )
    reply = FakeReply(
        "The principle carries real weight.",
        {
            "mode": "opinion_or_principle",
            "reviewer_verdict": "approve",
            "factual_claim_count": 0,
            "evidence_ids": [],
        },
        {"approved": True},
    )
    result = SimpleNamespace(
        reply=reply,
        status="approved",
        reason="reviewer_approved",
        model_call_count=4,
        revision_count=1,
        audit=(
            {
                "stage": "reviewer",
                "status": "invalid",
                "reason": "reviewer_claim_inventory_mismatch",
            },
            {"stage": "revision_proposer", "status": "completed"},
            {"stage": "revision_reviewer", "status": "completed", "verdict": "approve"},
        ),
    )

    passed, failures, observations = matrix.grade_case(case, result, repository)
    assert passed is True
    assert failures == []
    assert observations["revision_count"] == 1


def test_saved_grading_correction_preserves_raw_recovered_failure() -> None:
    corrected = matrix.normalise_recorded_grade({
        "case_id": "case-1",
        "pipeline_status": "approved",
        "passed": False,
        "failures": [
            "invalid structured stage: reviewer:reviewer_claim_inventory_mismatch",
        ],
    })

    assert corrected["passed"] is True
    assert corrected["failures"] == []
    assert corrected["grading_corrections"] == [
        "recovered_revision_not_terminal_invalid",
    ]
    assert corrected["recorded_failures_before_grading_correction"] == [
        "invalid structured stage: reviewer:reviewer_claim_inventory_mismatch",
    ]


def test_saved_grading_correction_keeps_terminal_invalid_failure() -> None:
    failure = "invalid structured stage: reviewer:reviewer_claim_inventory_mismatch"
    corrected = matrix.normalise_recorded_grade({
        "case_id": "case-2",
        "pipeline_status": "operational_failure",
        "passed": False,
        "failures": [failure],
    })

    assert corrected["passed"] is False
    assert corrected["failures"] == [failure]
    assert corrected["grading_corrections"] == []


def test_saved_grading_separates_safe_abstention_from_response_rate(
    repository: EvidenceRepository,
) -> None:
    packet = repository.packets[sorted(repository.packets)[0]]
    case = matrix.build_fixture(
        packet,
        matrix.SCENARIO_BY_ID["direct_meaning_question"],
    )
    corrected = matrix.normalise_recorded_grade({
        "case_id": case["case_id"],
        "scenario_id": case["scenario_id"],
        "pipeline_status": "no_reply",
        "outcome": "no_reply",
        "passed": False,
        "failures": ["outcome no_reply not in ['approved']"],
    }, case=case)

    assert corrected["passed"] is True
    assert corrected["failures"] == []
    assert corrected["response_opportunity_missed"] is True
    assert corrected["grading_corrections"] == [
        "safe_abstention_separated_from_response_rate",
    ]


def test_ledger_binds_resume_to_matrix_run_version(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    provider_pilot.PilotLedger(
        path,
        model="grok-4.3",
        hard_limit_usd=20.0,
        run_version=matrix.RUN_VERSION,
    )
    with pytest.raises(provider_pilot.PilotError, match="run version"):
        provider_pilot.PilotLedger(
            path,
            model="grok-4.3",
            hard_limit_usd=20.0,
            run_version="different-run",
        )


def test_matrix_runner_has_no_production_or_x_posting_dependency() -> None:
    source = Path(matrix.__file__).read_text(encoding="utf-8")
    assert "import mrsMThatcher2" not in source
    assert "api.x.com" not in source
    assert "systemctl" not in source
    assert "media/upload" not in source
    assert "XAI_API_KEY" in source
    assert matrix.DEFAULT_HARD_LIMIT_USD == 20.0


def test_jsonl_round_trip_is_deterministic(tmp_path: Path, fixtures: list[dict]) -> None:
    rows = fixtures[:3]
    path = tmp_path / "rows.jsonl"
    path.write_text(matrix.jsonl_text(rows), encoding="utf-8")
    assert matrix.read_jsonl(path) == rows
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0]) == rows[0]


def test_migration_abandons_ambiguous_request_without_retry(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    common = {
        "fixture_count": 1,
        "paid_sample_count": 1,
        "fixtures_sha256": "1" * 64,
        "paid_sample_sha256": "2" * 64,
        "model": "grok-4.3",
        "hard_limit_usd": 20.0,
    }
    provider_pilot.atomic_json(source / "run_manifest.json", {
        **common,
        "run_version": "ai-first-quote-matrix-v3",
    })
    provider_pilot.atomic_json(destination / "run_manifest.json", {
        **common,
        "run_version": matrix.RUN_VERSION,
    })
    case = {
        "case_id": "quote-scenario",
        "fixture_hash": "3" * 64,
        "quote_id": "quote",
        "scenario_id": "direct_meaning_question",
        "sample_role": "balanced_corpus",
    }
    provider_pilot.atomic_text(destination / "paid_sample.jsonl", matrix.jsonl_text([case]))

    source_ledger = provider_pilot.PilotLedger(
        source / "cost_ledger.json",
        model="grok-4.3",
        hard_limit_usd=20.0,
        run_version="ai-first-quote-matrix-v3",
    )
    completed = {
        "logical_call_id": "matrix-quote-scenario:1:proposer",
        "request_hash": "4" * 64,
        "status": "completed",
        "cost_in_usd_ticks": 1_000_000,
    }
    ambiguous = {
        "logical_call_id": "matrix-quote-scenario:2:reviewer",
        "request_hash": "5" * 64,
        "stage": "reviewer",
        "status": "ambiguous",
        "error": "ReadTimeout: response timed out",
        "maximum_possible_cost_ticks": 2_000_000,
        "maximum_possible_cost_usd": 0.0002,
    }
    source_ledger.data["operations"] = [completed, ambiguous]
    source_ledger.data["blocked"] = True
    source_ledger._save()
    raw_name = provider_pilot.sha256_bytes(completed["logical_call_id"].encode("utf-8")) + ".json"
    provider_pilot.atomic_json(source / "raw_responses" / raw_name, {
        "logical_call_id": completed["logical_call_id"],
        "request_hash": completed["request_hash"],
        "raw": {"choices": [{"message": {"content": "{}"}}]},
    })

    audit = matrix.migrate_completed_run(source_dir=source, output_dir=destination)

    assert audit["synthetic_ambiguous_case_id"] == "quote-scenario"
    assert audit["incomplete_operation"]["classification"] == (
        "ambiguous_transmission_abandoned_no_retry"
    )
    result = provider_pilot.read_json(matrix.case_result_path(destination, "quote-scenario"))
    assert result["reason"] == "ambiguous_provider_transmission_no_retry"
    migrated_ledger = provider_pilot.read_json(destination / "cost_ledger.json")
    assert migrated_ledger["blocked"] is False
    assert migrated_ledger["operations"][1]["abandoned_no_retry"] is True
    assert migrated_ledger["ambiguous_exposure_in_usd_ticks"] == 2_000_000


def test_in_place_ambiguous_abandonment_unblocks_only_unrelated_work(tmp_path: Path) -> None:
    output = tmp_path / "run"
    output.mkdir()
    provider_pilot.atomic_json(output / "run_manifest.json", {
        "run_version": matrix.RUN_VERSION,
        "model": "grok-4.3",
        "hard_limit_usd": 20.0,
    })
    case = {
        "case_id": "quote-context",
        "fixture_hash": "6" * 64,
        "quote_id": "quote",
        "scenario_id": "direct_context_question",
        "sample_role": "balanced_corpus",
    }
    provider_pilot.atomic_text(output / "paid_sample.jsonl", matrix.jsonl_text([case]))
    ledger = provider_pilot.PilotLedger(
        output / "cost_ledger.json",
        model="grok-4.3",
        hard_limit_usd=20.0,
        run_version=matrix.RUN_VERSION,
    )
    ledger.data["operations"] = [{
        "logical_call_id": "matrix-quote-context:1:proposer",
        "request_hash": "7" * 64,
        "stage": "proposer",
        "status": "ambiguous",
        "error": "ReadTimeout: response timed out",
        "maximum_possible_cost_ticks": 3_000_000,
        "maximum_possible_cost_usd": 0.0003,
    }]
    ledger.data.update({
        "blocked": True,
        "status": "blocked_ambiguous_cost",
        "blocked_reason": "ambiguous request",
    })
    ledger._save()

    event = matrix.abandon_ambiguous_case(output_dir=output)

    assert event["case_id"] == "quote-context"
    assert event["classification"] == "ambiguous_transmission_abandoned_no_retry"
    saved = provider_pilot.read_json(output / "cost_ledger.json")
    assert saved["blocked"] is False
    assert saved["operations"][0]["abandoned_no_retry"] is True
    assert saved["ambiguous_exposure_in_usd_ticks"] == 3_000_000
    result = provider_pilot.read_json(matrix.case_result_path(output, "quote-context"))
    assert result["outcome"] == "no_reply"
    assert result["pipeline_status"] == "operational_failure"
    assert result["failures"] == [
        "ambiguous provider transmission was abandoned and not retried"
    ]

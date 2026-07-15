from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from semantic_alignment.hybrid_gemini_review import (
    GeminiReviewClient,
    GeminiReviewRunner,
    case_for_prompt,
    prepare_gemini_review,
    recover_gemini_reviews_offline,
    review_prompt,
    validate_batch_response,
)
import semantic_alignment.hybrid_gemini_review as gemini_review


def _case(case_id: str = "case-1") -> dict:
    packet_a = {
        "quote_id": "a" * 64,
        "quote_text": "A relevant historical quotation.",
        "intended_argument": "Government does not itself create prosperity.",
        "broader_principle": "Enterprise creates wealth.",
        "verification_status": "exact",
        "research_confidence": "high",
        "source_event": "Speech",
    }
    packet_b = {
        "quote_id": "b" * 64,
        "quote_text": "An unrelated historical quotation.",
        "intended_argument": "A separate argument.",
        "broader_principle": "A separate principle.",
        "verification_status": "normalised",
        "research_confidence": "medium",
        "source_event": "Interview",
    }
    return {
        "case_id": case_id,
        "target_id": case_id,
        "lane": "mention",
        "incoming": {
            "author_id": "user-1", "author_handle": "reader",
            "text": "Government creates all wealth &amp; prosperity.\nThat is historical fact.",
            "timestamp": "2026-07-01T12:00:00Z",
        },
        "direct_parent": {
            "post_id": "parent-1", "author_handle": "MrsMThatcher",
            "text": "A parent quotation.", "timestamp": "2026-07-01T11:59:00Z",
        },
        "quoted_post": None,
        "older_thread_context": [{
            "post_id": "older-1", "author_handle": "reader",
            "text": "Earlier context.", "timestamp": "2026-07-01T11:58:00Z",
        }],
        "query_basis": {
            "substantive_query": True, "incoming_text_used": True,
            "direct_parent_used": True, "older_thread_used": False,
            "quoted_post_used": False,
        },
        "context_status": "complete",
        "missing_context": [],
        "A": [packet_a],
        "B": [packet_b],
    }


def _response(cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        assessments = []
        for side in ("A", "B"):
            for packet in case[side]:
                assessments.append({
                    "side": side,
                    "quote_id": packet["quote_id"],
                    "assessment": "relevant" if side == "A" else "irrelevant",
                    "reason": "Directly addresses the claim." if side == "A" else "Does not address the claim.",
                })
        rows.append({
            "case_id": case["case_id"], "choice": "A_better",
            "intervention": "historical_correction", "evidence_desirability": "desirable",
            "confidence": "high", "factual_claim_present": True,
            "rationale": "Set A directly supports a concise correction.",
            "reason_tags": ["exact_match_stronger"], "packet_assessments": assessments,
        })
    return {"reviews": rows}


def _write_trial(path: Path, cases: list[dict]) -> None:
    (path / "manual_review").mkdir(parents=True)
    (path / "review_sample_100.json").write_text(json.dumps({
        "context_version": "hybrid-review-context-v2", "case_count": len(cases), "items": cases,
    }), encoding="utf-8")
    assignments = {case["case_id"]: {"A": "lexical", "B": "hybrid"} for case in cases}
    (path / "blind_assignment_manifest.json").write_text(json.dumps({"assignments": assignments}), encoding="utf-8")
    (path / "manual_review" / "human_reviews.json").write_text(
        json.dumps({"schema_version": 2, "items": {"human-sentinel": {"choice": "A_better"}}}),
        encoding="utf-8",
    )


def test_prompt_preserves_full_context_and_blind_assignment():
    case = _case()
    prompt_case = case_for_prompt(case)
    prompt = review_prompt([case])
    lowered = prompt.casefold()

    assert prompt_case["incoming_user_contribution"]["text"].endswith("That is historical fact.")
    assert prompt_case["direct_parent"]["text"] == "A parent quotation."
    assert prompt_case["older_thread_context"][0]["text"] == "Earlier context."
    assert "lexical" not in lowered
    assert "hybrid" not in lowered
    assert "retrieval_set_a" in lowered and "retrieval_set_b" in lowered


def test_developer_client_explicitly_disables_vertex_environment(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setattr(gemini_review.genai, "Client", fake_client)

    client = GeminiReviewClient(transport="developer_api", api_key="test-key")

    assert client.transport == "developer_api"
    assert captured == {"vertexai": False, "api_key": "test-key"}


def test_validation_requires_every_supplied_packet_and_consistent_decision():
    case = _case()
    assert validate_batch_response(_response([case]), [case])[0]["choice"] == "A_better"

    missing = _response([case])
    missing["reviews"][0]["packet_assessments"].pop()
    with pytest.raises(ValueError, match="do not match"):
        validate_batch_response(missing, [case])

    inconsistent = _response([case])
    inconsistent["reviews"][0].update({
        "choice": "no_historical_evidence", "intervention": "historical_context",
    })
    with pytest.raises(ValueError, match="no_historical_evidence"):
        validate_batch_response(inconsistent, [case])


def test_transport_flattened_schema_normalises_to_logical_packet_assessments():
    case = _case()
    logical = _response([case])["reviews"][0]
    flattened = {
        **{key: value for key, value in logical.items() if key not in {"reason_tags", "packet_assessments"}},
        "reason_tags_csv": "exact_match_stronger",
        "packet_assessments": [
            "|".join((row["side"], row["quote_id"], row["assessment"], row["reason"]))
            for row in logical["packet_assessments"]
        ],
    }

    result = validate_batch_response({"reviews": [flattened]}, [case])[0]

    assert result["reason_tags"] == ["exact_match_stronger"]
    assert result["packet_assessments"] == logical["packet_assessments"]


def test_offline_normalisation_may_reuse_provider_rationale_for_omitted_packet_reasons():
    case = _case()
    logical = _response([case])["reviews"][0]
    logical.update({
        "choice": "no_historical_evidence", "intervention": "no_reply_preferable",
        "evidence_desirability": "not_desirable", "factual_claim_present": False,
        "rationale": "The incoming contribution does not justify using historical evidence.",
    })
    logical["reason_tags_csv"] = "no_sufficiently_relevant_evidence"
    logical.pop("reason_tags")
    logical["packet_assessments"] = [
        "|".join((row["side"], row["quote_id"], "irrelevant"))
        for row in logical["packet_assessments"]
    ]

    with pytest.raises(ValueError, match="invalid or duplicate packet assessment"):
        validate_batch_response({"reviews": [logical]}, [case])

    result = validate_batch_response(
        {"reviews": [logical]},
        [case],
        allow_case_rationale_for_packet_reason=True,
    )[0]

    assert all(row["reason"] == logical["rationale"] for row in result["packet_assessments"])
    assert result["normalised_missing_packet_reasons"] == 2


def test_offline_normalisation_preserves_but_does_not_accept_unrecognised_reason_tags():
    case = _case()
    logical = _response([case])["reviews"][0]
    logical["reason_tags"] = ["provider_invented_tag", "exact_match_stronger"]

    with pytest.raises(ValueError, match="invalid reason tags"):
        validate_batch_response({"reviews": [logical]}, [case])

    result = validate_batch_response(
        {"reviews": [logical]},
        [case],
        allow_unrecognised_reason_tags=True,
    )[0]

    assert result["reason_tags"] == ["exact_match_stronger"]
    assert result["unrecognised_provider_reason_tags"] == ["provider_invented_tag"]


def test_offline_normalisation_flags_inconsistent_insufficient_context_intervention():
    case = _case()
    logical = _response([case])["reviews"][0]
    logical.update({
        "choice": "insufficient_context",
        "intervention": "historical_correction",
        "rationale": "The attribution cannot be assessed from the supplied evidence.",
    })

    with pytest.raises(ValueError, match="insufficient context must prefer no reply"):
        validate_batch_response({"reviews": [logical]}, [case])

    result = validate_batch_response(
        {"reviews": [logical]},
        [case],
        allow_insufficient_context_intervention_inconsistency=True,
    )[0]

    assert result["choice"] == "insufficient_context"
    assert result["intervention"] == "historical_correction"
    assert result["logical_inconsistencies"] == [
        "insufficient_context_with_non_no_reply_intervention",
    ]


def test_offline_normalisation_flags_historical_correction_without_factual_claim():
    case = _case()
    logical = _response([case])["reviews"][0]
    logical["factual_claim_present"] = False

    with pytest.raises(ValueError, match="historical correction lacks a factual claim"):
        validate_batch_response({"reviews": [logical]}, [case])

    result = validate_batch_response(
        {"reviews": [logical]},
        [case],
        allow_historical_correction_without_factual_claim=True,
    )[0]

    assert result["factual_claim_present"] is False
    assert result["logical_inconsistencies"] == [
        "historical_correction_without_factual_claim",
    ]


def test_offline_recovery_uses_preserved_response_without_a_network_call(tmp_path: Path):
    case = _case()
    _write_trial(tmp_path, [case])
    review_dir = tmp_path / "manual_review"
    raw_dir = review_dir / "gemini_raw_responses"
    raw_dir.mkdir()

    logical = _response([case])["reviews"][0]
    logical.update({
        "choice": "no_historical_evidence",
        "intervention": "no_reply_preferable",
        "evidence_desirability": "not_desirable",
        "factual_claim_present": False,
        "rationale": "The incoming contribution does not justify using historical evidence.",
        "reason_tags_csv": "no_sufficiently_relevant_evidence",
    })
    logical.pop("reason_tags")
    logical["packet_assessments"] = [
        "|".join((row["side"], row["quote_id"], "irrelevant"))
        for row in logical["packet_assessments"]
    ]
    raw_path = raw_dir / "gemini-review-001.developer_api.2.json"
    raw_path.write_text(json.dumps({
        "candidates": [{"content": {"parts": [{"text": json.dumps({"reviews": [logical]})}]}}],
    }), encoding="utf-8")
    (review_dir / "gemini_review_manifest.json").write_text(json.dumps({
        "batches": [{"batch_id": "gemini-review-001", "case_ids": [case["case_id"]]}],
    }), encoding="utf-8")
    (review_dir / "gemini_reviews.json").write_text(json.dumps({
        "schema_version": 1, "items": {},
    }), encoding="utf-8")
    (review_dir / "gemini_review_cost_ledger.json").write_text(json.dumps({
        "calls": [{
            "batch_id": "gemini-review-001",
            "transport": "developer_api",
            "transport_attempt_number": 2,
            "raw_response_path": str(raw_path),
        }],
    }), encoding="utf-8")
    (review_dir / "gemini_review_transport_status.json").write_text(json.dumps({
        "unresolved_batches": {"gemini-review-001": "failed"},
    }), encoding="utf-8")

    recovered = recover_gemini_reviews_offline(tmp_path)

    saved = json.loads((review_dir / "gemini_reviews.json").read_text(encoding="utf-8"))
    assert recovered["recovered_case_count"] == 1
    assert recovered["still_unresolved_batch_count"] == 0
    assert saved["items"][case["case_id"]]["offline_normalised_from_preserved_response"] is True
    assert saved["items"][case["case_id"]]["source_raw_response_path"] == str(raw_path)

    audit_path = review_dir / "gemini_review_offline_recovery.json"
    audit_before = audit_path.read_bytes()
    repeated = recover_gemini_reviews_offline(tmp_path)

    assert repeated["recovered_case_count"] == 1
    assert audit_path.read_bytes() == audit_before


def test_preflight_requires_100_complete_blind_cases_and_does_not_touch_human_reviews(tmp_path: Path):
    cases = [_case(f"case-{index:03d}") for index in range(100)]
    _write_trial(tmp_path, cases)
    before = (tmp_path / "manual_review" / "human_reviews.json").read_bytes()

    preflight = prepare_gemini_review(tmp_path)

    assert preflight["case_count"] == 100
    assert preflight["batch_count"] == 20
    assert preflight["conservative_maximum_cost_usd"] < 3
    assert preflight["search_grounding_enabled"] is False
    assert (tmp_path / "manual_review" / "human_reviews.json").read_bytes() == before


class QuotaError(Exception):
    code = 429


class FakeClient:
    model = "gemini-3.1-pro-preview"

    def __init__(self, transport: str, quota_failures: int = 0):
        self.transport = transport
        self.quota_failures = quota_failures
        self.calls = 0

    def settings_signature(self):
        return {"model": self.model, "schema": "same", "tools": None}

    def call(self, prompt: str):
        self.calls += 1
        if self.calls <= self.quota_failures:
            raise QuotaError("provider-wide quota exhausted")
        prompt_cases = json.loads(prompt.split("CASES\n", 1)[1])
        cases = []
        for value in prompt_cases:
            cases.append({
                "case_id": value["case_id"],
                "A": value["retrieval_set_A"],
                "B": value["retrieval_set_B"],
            })
        return {
            "raw": {"response": "preserved"}, "parsed": _response(cases),
            "usage": {"input_tokens": 100, "candidate_tokens": 100, "thinking_tokens": 10,
                      "output_tokens": 110, "cached_tokens": 0},
            "cost_usd": 0.001, "latency_seconds": 0.01,
            "request_id": f"{self.transport}-{self.calls}", "model_version": self.model,
        }


def test_two_developer_429s_pause_run_and_later_batch_routes_directly_to_vertex(tmp_path: Path):
    cases = [_case("case-1"), _case("case-2")]
    _write_trial(tmp_path, cases)
    batches = []
    for index, case in enumerate(cases, 1):
        prompt = review_prompt([case])
        batches.append({
            "batch_id": f"batch-{index}", "case_ids": [case["case_id"]],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        })
    (tmp_path / "manual_review" / "gemini_review_manifest.json").write_text(
        json.dumps({"batches": batches}), encoding="utf-8",
    )
    human_before = (tmp_path / "manual_review" / "human_reviews.json").read_bytes()
    developer = FakeClient("developer_api", quota_failures=2)
    vertex = FakeClient("vertex_ai")

    summary = GeminiReviewRunner(tmp_path, developer, vertex, sleep=lambda _: None).run()

    assert summary["completed_reviews"] == 2
    assert developer.calls == 2
    assert vertex.calls == 2
    assert summary["developer_paused"] is True
    assert summary["direct_to_vertex_count"] == 1
    assert summary["transport_counts"] == {"vertex_ai": 2}
    assert (tmp_path / "manual_review" / "human_reviews.json").read_bytes() == human_before


def test_orphaned_started_attempt_is_ambiguous_and_not_repeated(tmp_path: Path):
    case = _case()
    _write_trial(tmp_path, [case])
    prompt = review_prompt([case])
    (tmp_path / "manual_review" / "gemini_review_manifest.json").write_text(json.dumps({
        "batches": [{
            "batch_id": "batch-1", "case_ids": [case["case_id"]],
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }],
    }), encoding="utf-8")
    (tmp_path / "manual_review" / "gemini_review_attempts.jsonl").write_text(json.dumps({
        "event": "attempt_started", "batch_id": "batch-1", "transport": "developer_api",
        "transport_attempt_number": 1,
    }) + "\n", encoding="utf-8")
    developer = FakeClient("developer_api")
    vertex = FakeClient("vertex_ai")

    summary = GeminiReviewRunner(tmp_path, developer, vertex, sleep=lambda _: None).run()

    assert summary["completed_reviews"] == 0
    assert developer.calls == 0
    assert vertex.calls == 0
    attempts = (tmp_path / "manual_review" / "gemini_review_attempts.jsonl").read_text()
    assert "ambiguous_on_resume" in attempts

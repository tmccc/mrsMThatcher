from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import validate as validate_json_schema

import reply_strategy as reply_strategy_module
from reply_evidence import EvidencePassage, EvidenceRepository
from reply_strategy import (
    AIReply,
    CLAIM_AUDITOR_PROMPT_VERSION,
    DRAFT_SCHEMA_VERSION,
    EVIDENCE_PROMPT_VERSION,
    MODES,
    NO_REPLY_REVIEW_PROMPT_VERSION,
    PipelineResult,
    PROPOSER_PROMPT_VERSION,
    REVIEWER_PROMPT_VERSION,
    STRATEGY_VERSION,
    VALIDATION_RETRY_PROTOCOL_VERSION,
    _build_validation_retry_user_prompt,
    _claim_auditor_prompts,
    _evidence_prompts,
    _no_reply_review_prompts,
    _proposer_prompts,
    _reviewer_prompts,
    claim_auditor_schema,
    claim_retrieval_query,
    deterministic_reply_error,
    evidence_telemetry,
    evidence_schema,
    legacy_draft_audit,
    proposer_schema,
    no_reply_review_schema,
    outcome_telemetry,
    reviewer_schema,
    run_reply_pipeline,
    sentence_count,
    split_reply_sentences,
    validate_evidence_response,
    validate_claim_auditor,
    validate_persisted_draft,
    validate_no_reply_review,
    validate_proposer,
    validate_reply_context,
    validate_reviewer,
    validate_strategy_config,
)


RESEARCH = Path("semantic_alignment_research/quote_research_full_001")
FACTUAL_EVIDENCE = Path("reply_factual_evidence.json")
ADVERSARIAL_FIXTURE = Path("tests/fixtures/ai_first_reply_adversarial_cases.json")


class FakeRepository:
    """Minimal exact-reference repository for deterministic pipeline tests."""

    def __init__(self) -> None:
        passage = EvidencePassage(
            evidence_id="a" * 64,
            source_hash="b" * 64,
            quote_id="c" * 64,
            field="historical_context",
            passage="In November 1989 movement was overwhelmingly from East Germany towards West Germany.",
            source_title="Verified local source",
            source_url="https://example.invalid/local-source",
            stable_locator="fixture:1",
            verification_status="exact",
            research_confidence="high",
        )
        self.passages = {passage.evidence_id: passage}
        self.passage = passage
        self.return_candidates = True
        self.candidate_override: list[EvidencePassage] | None = None
        self.candidate_requests: list[dict[str, object]] = []
        self.resolved_quotation: dict[str, object] | None = None

    def candidate_passages(self, *_args: object, **kwargs: object) -> list[EvidencePassage]:
        self.candidate_requests.append(dict(kwargs))
        if not self.return_candidates:
            return []
        return list(self.candidate_override or [self.passage])

    def resolve_context_quotation(self, _context: dict[str, object]) -> dict[str, object] | None:
        return copy.deepcopy(self.resolved_quotation)

    def validate_reference(self, evidence_id: str, exact_passage: str) -> EvidencePassage:
        passage = self.passages.get(evidence_id)
        if passage is None or passage.passage != exact_passage:
            raise ValueError("evidence reference mismatch")
        return passage

    def exact_quote_is_authorised(self, text: str) -> bool:
        return text == "Authorised historical words are exact"

    def exact_quote_occurs_in_reply(self, reply_text: str, exact_text: str) -> bool:
        return exact_text in reply_text

    def detected_authorised_quote_ids(self, text: str) -> set[str]:
        if "authorised historical words are exact" in text.casefold():
            return {"c" * 64}
        return set()

    def detected_authorised_quote_ids_outside_exact(
        self,
        text: str,
        exact_text: str,
    ) -> set[str]:
        remainder = text.replace(exact_text, "")
        return self.detected_authorised_quote_ids(remainder)


class ScriptedTransport:
    """Return one saved response per named stage and retain exact call inputs."""

    def __init__(self, responses: dict[str, object | list[object] | Callable[..., object]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> object:
        self.calls.append(copy.deepcopy(kwargs))
        stage = kwargs["stage"]
        if stage not in self.responses:
            raise AssertionError(f"unexpected model stage: {stage}")
        response = self.responses[stage]
        if callable(response):
            return response(**kwargs)
        if isinstance(response, list):
            if not response:
                raise AssertionError(f"no scripted response remains for {stage}")
            response = response.pop(0)
        if isinstance(response, dict) and stage in {"claim_auditor", "revision_claim_auditor"}:
            response = copy.deepcopy(response)
            payload = json.loads(kwargs["user_prompt"])
            sentences = split_reply_sentences(str(payload["proposed_reply_to_audit"]))
            assessments = response.get("sentence_assessments")
            if (
                isinstance(assessments, list)
                and len(assessments) == 1
                and assessments[0].get("sentence_text") == "__AUTO_SENTENCES__"
            ):
                actual_claims = list(response.get("actual_factual_claims") or [])
                expanded = []
                remaining = list(actual_claims)
                for sentence in sentences:
                    sentence_claims = [
                        claim_text
                        for claim_text in remaining
                        if " ".join(str(claim_text).split()) in sentence
                    ]
                    remaining = [
                        claim_text for claim_text in remaining
                        if claim_text not in sentence_claims
                    ]
                    expanded.append({
                        "sentence_text": sentence,
                        "factual_claims": sentence_claims,
                        "world_claim_checks": {
                            "asserts_actor_state_or_action": bool(sentence_claims),
                            "asserts_causal_or_predictive_relation": False,
                            "asserts_comparison_or_outcome": False,
                            "asserts_historical_date_or_quantity": False,
                            "asserts_meaning_or_attribution": False,
                            "purely_non_factual": not bool(sentence_claims),
                        },
                    })
                response["sentence_assessments"] = expanded
        if isinstance(response, dict) and stage in {"reviewer", "revision_reviewer"}:
            response = copy.deepcopy(response)
            payload = json.loads(kwargs["user_prompt"])
            proposed_reply = str(payload["proposed_reply"])
            sentences = split_reply_sentences(proposed_reply)
            if response.get("actual_factual_claims") == ["__PROPOSER_CLAIMS__"]:
                response["actual_factual_claims"] = [
                    str(claim_record["claim_text"])
                    for claim_record in payload["proposer_listed_factual_claims_untrusted"]
                ]
                response["sentence_assessments"][0]["factual_claims"] = list(
                    response["actual_factual_claims"]
                )
            if response.get("direct_answer_text") == "__FIRST_SENTENCE__":
                response["direct_answer_text"] = sentences[0] if sentences else ""
            assessments = response.get("sentence_assessments")
            if (
                isinstance(assessments, list)
                and len(assessments) == 1
                and assessments[0].get("sentence_text") == "__AUTO_SENTENCES__"
            ):
                actual_claims = list(response.get("actual_factual_claims") or [])
                expanded = []
                remaining = list(actual_claims)
                for index, sentence in enumerate(sentences):
                    sentence_claims = [
                        claim_text
                        for claim_text in remaining
                        if " ".join(str(claim_text).split()) in sentence
                    ]
                    if index == 0 and actual_claims and not sentence_claims:
                        sentence_claims = list(remaining)
                    remaining = [claim_text for claim_text in remaining if claim_text not in sentence_claims]
                    expanded.append({
                        "sentence_text": sentence,
                        "classification": "factual_claim" if sentence_claims else "other_non_factual",
                        "factual_claims": sentence_claims,
                        "non_factual_basis": "none" if sentence_claims else "rhetorical_question",
                        "world_claim_checks": {
                            "asserts_actor_state_or_action": bool(sentence_claims),
                            "asserts_causal_or_predictive_relation": False,
                            "asserts_comparison_or_outcome": False,
                            "asserts_historical_date_or_quantity": False,
                            "asserts_meaning_or_attribution": False,
                            "purely_non_factual": not bool(sentence_claims),
                        },
                    })
                response["sentence_assessments"] = expanded
        return response


@pytest.fixture(scope="module")
def real_repository() -> EvidenceRepository:
    return EvidenceRepository(RESEARCH, factual_evidence_path=FACTUAL_EVIDENCE)


@pytest.fixture()
def repository() -> FakeRepository:
    return FakeRepository()


def strategy_config(**overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "enabled": True,
        "strategy_version": STRATEGY_VERSION,
        "proposer_model": "proposer-model",
        "reviewer_model": "reviewer-model",
        "evidence_model": "evidence-model",
        "research_corpus_path": str(RESEARCH),
        "maximum_model_calls": 6,
        "proposer_timeout_seconds": 30,
        "evidence_timeout_seconds": 30,
        "reviewer_timeout_seconds": 30,
        "proposer_max_output_tokens": 900,
        "evidence_max_output_tokens": 1800,
        "reviewer_max_output_tokens": 900,
        "maximum_revisions": 1,
        "maximum_invalid_response_retries": 1,
        "maximum_claims": 6,
        "maximum_evidence_packets_per_claim": 6,
        "maximum_evidence_passages_per_claim": 24,
        "maximum_reply_sentences": 2,
        "fail_closed": True,
    }
    config.update(overrides)
    return config


def reply_context(
    contribution: str = "What happened when the Berlin Wall fell?",
    *,
    quoted_post: dict[str, str] | None = None,
    parent_thread: list[dict[str, str]] | None = None,
    clarification_request: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "target_id": "100",
        "thread_id": "90",
        "lane": "mention",
        "incoming_contribution": contribution,
        "quoted_post": quoted_post,
        "parent_thread": parent_thread or [],
        "clarification_request": clarification_request,
        "current_date": "2026-07-20",
    }


def claim(
    text: str = "People moved from East Germany towards West Germany in November 1989.",
) -> dict[str, object]:
    return {
        "claim_id": "claim-1",
        "claim_text": text,
        "requires_evidence": True,
        "actor": "people in East Germany",
        "action_or_relationship": "moved",
        "direction_or_polarity": "East to West",
        "date_or_period": "November 1989" if "1989" in text else "",
        "quantity": "",
    }


def proposer(
    reply: str = "People moved from East Germany towards West Germany in November 1989.",
    *,
    mode: str = "direct_factual_answer",
    claims: list[dict[str, object]] | None = None,
    confidence: str = "high",
    exact_wording: str = "",
    no_reply_reason: str = "",
    requested_answer_type: str | None = None,
) -> dict[str, object]:
    factual_claims = [claim(reply)] if claims is None and mode == "direct_factual_answer" else (claims or [])
    if mode == "no_reply":
        reply = ""
        factual_claims = []
        no_reply_reason = no_reply_reason or "No useful and safe reply is warranted."
    direct_question = mode == "direct_factual_answer"
    answer_type = requested_answer_type or ("action" if direct_question else "none")
    first_sentence = split_reply_sentences(reply)
    return {
        "mode": mode,
        "interpretation": "The user asks a concrete question or offers a political observation.",
        "proposed_reply": reply,
        "direct_factual_question_present": direct_question,
        "requested_answer_type": answer_type,
        "direct_answer_text": first_sentence[0] if direct_question and first_sentence else "",
        "factual_claims": factual_claims,
        "exact_thatcher_wording_used": bool(exact_wording),
        "exact_thatcher_wording": exact_wording,
        "tone": "neutral" if mode != "light_humour" else "light",
        "confidence": confidence,
        "no_reply_reason": no_reply_reason,
    }


def reviewer(
    verdict: str = "approve",
    *,
    direct_question: bool = True,
    answers_first: bool = True,
    factual_claims: list[str] | None = None,
    requested_answer_type: str | None = None,
    **overrides: object,
) -> dict[str, object]:
    if factual_claims is None:
        factual_claims = ["__PROPOSER_CLAIMS__"] if direct_question else []
    value: dict[str, object] = {
        "verdict": verdict,
        "summary": "Independent review completed.",
        "reasons": [] if verdict == "approve" else ["The draft is unsafe or unsuitable."],
        "actual_factual_claims": factual_claims,
        "unsupported_factual_claims": [],
        "sentence_assessments": [{
            "sentence_text": "__AUTO_SENTENCES__",
            "classification": "factual_claim" if factual_claims else "other_non_factual",
            "factual_claims": factual_claims,
            "non_factual_basis": "none" if factual_claims else "rhetorical_question",
            "world_claim_checks": {
                "asserts_actor_state_or_action": bool(factual_claims),
                "asserts_causal_or_predictive_relation": False,
                "asserts_comparison_or_outcome": False,
                "asserts_historical_date_or_quantity": False,
                "asserts_meaning_or_attribution": False,
                "purely_non_factual": not bool(factual_claims),
            },
        }],
        "direct_factual_question_present": direct_question,
        "requested_answer_type": requested_answer_type or ("action" if direct_question else "none"),
        "direct_answer_complete": answers_first if direct_question else False,
        "direct_answer_text": "__FIRST_SENTENCE__" if direct_question else "",
        "topically_relevant": True,
        "endorses_unsupported_allegation": False,
        "contains_unsupported_factual_claims": False,
        "actor_action_relationship_correct": True,
        "direction_polarity_correct": True,
        "dates_quantities_correct": True,
        "quotation_attribution_correct": True,
        "original_prose_clearly_not_historical_quotation": True,
        "mode_and_tone_match": True,
        "suitable_for_account": True,
        "revision_instructions": "Revise the draft." if verdict == "revise" else "",
    }
    value.update(overrides)
    return value


def no_reply_review(
    verdict: str = "confirm_no_reply",
) -> dict[str, object]:
    return {
        "verdict": verdict,
        "reasons": [
            "Silence is warranted."
            if verdict == "confirm_no_reply"
            else "A safe, relevant response remains possible."
        ],
        "revision_instructions": (
            "Acknowledge the civil contribution without repeating unsupported claims."
            if verdict == "require_reply"
            else ""
        ),
    }


def claim_auditor(factual_claims: list[str] | None = None) -> dict[str, object]:
    """Return a strict scripted claim-auditor response."""
    claims = list(factual_claims or [])
    return {
        "actual_factual_claims": claims,
        "sentence_assessments": [{
            "sentence_text": "__AUTO_SENTENCES__",
            "factual_claims": claims,
            "world_claim_checks": {
                "asserts_actor_state_or_action": bool(claims),
                "asserts_causal_or_predictive_relation": False,
                "asserts_comparison_or_outcome": False,
                "asserts_historical_date_or_quantity": False,
                "asserts_meaning_or_attribution": False,
                "purely_non_factual": not bool(claims),
            },
        }],
    }


def supporting_evidence(repository: FakeRepository) -> Callable[..., object]:
    def response(**kwargs: Any) -> object:
        payload = json.loads(kwargs["user_prompt"])
        rows = []
        for supplied_claim in payload["claims"]:
            rows.append({
                "claim_id": supplied_claim["claim_id"],
                "claim_text": supplied_claim["claim_text"],
                "verdict": "supports",
                "evidence": [{
                    "evidence_id": repository.passage.evidence_id,
                    "exact_supporting_passage": repository.passage.passage,
                    "relation": "supports",
                }],
                "actor": supplied_claim["actor"],
                "action_or_relationship": supplied_claim["action_or_relationship"],
                "direction_or_polarity": supplied_claim["direction_or_polarity"],
                "date_or_period": supplied_claim["date_or_period"],
                "quantity": supplied_claim["quantity"],
                "explanation": "The exact supplied passage supports the complete claim.",
            })
        return {"claims": rows}

    return response


def insufficient_evidence(**kwargs: Any) -> object:
    payload = json.loads(kwargs["user_prompt"])
    return {
        "claims": [{
            "claim_id": supplied_claim["claim_id"],
            "claim_text": supplied_claim["claim_text"],
            "verdict": "insufficient",
            "evidence": [],
            "actor": supplied_claim["actor"],
            "action_or_relationship": supplied_claim["action_or_relationship"],
            "direction_or_polarity": supplied_claim["direction_or_polarity"],
            "date_or_period": supplied_claim["date_or_period"],
            "quantity": supplied_claim["quantity"],
            "explanation": "No supplied passage supports the complete claim.",
        } for supplied_claim in payload["claims"]],
    }


def run_pipeline(
    repository: FakeRepository,
    responses: dict[str, object | list[object] | Callable[..., object]],
    *,
    context: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
    recent_replies: list[str] | None = None,
    media_context: dict[str, object] | None = None,
) -> tuple[object, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    result = run_reply_pipeline(
        context=context or reply_context(),
        config=config or strategy_config(),
        repository=repository,  # type: ignore[arg-type]
        transport=transport,
        maximum_reply_length=500,
        recent_replies=recent_replies or [],
        media_context=media_context,
        creation_time="2026-07-20T12:00:00Z",
    )
    return result, transport


VALIDATION_CORRECTION_FIELDS = {
    "protocol_version",
    "stage",
    "attempt_number",
    "previous_response_rejected",
    "validator_error_type",
    "validator_error",
    "validator_error_sha256",
    "required_action",
}
VALIDATION_REQUIRED_ACTION = (
    "Return a complete replacement JSON object satisfying the supplied "
    "response schema and correct the stated validation failure. Do not "
    "discuss the correction or return partial fields."
)


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_word_count(value: str) -> int:
    """Count prompt words consistently with the frozen hybrid-prompt budget."""
    return len(re.findall(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*", value))


def source_sha256(*objects: object) -> str:
    return text_sha256("".join(inspect.getsource(item) for item in objects))


def retry_audit_row(result: object, stage: str) -> dict[str, object]:
    rows = [
        row
        for row in result.audit  # type: ignore[attr-defined]
        if row.get("stage") == stage and row.get("status") == "invalid_response_retry"
    ]
    assert len(rows) == 1
    return rows[0]


def assert_retry_envelope(
    original_user_prompt: str,
    corrective_user_prompt: str,
    *,
    stage: str,
    attempt_number: int,
    validator_exception: BaseException,
) -> dict[str, object]:
    original_payload = json.loads(original_user_prompt)
    corrective_payload = json.loads(corrective_user_prompt)
    assert isinstance(original_payload, dict)
    assert isinstance(corrective_payload, dict)
    assert "validation_correction" not in original_payload
    correction = corrective_payload.pop("validation_correction")
    assert corrective_payload == original_payload
    assert isinstance(correction, dict)
    assert set(correction) == VALIDATION_CORRECTION_FIELDS
    error = str(validator_exception)
    assert correction == {
        "protocol_version": VALIDATION_RETRY_PROTOCOL_VERSION,
        "stage": stage,
        "attempt_number": attempt_number,
        "previous_response_rejected": True,
        "validator_error_type": type(validator_exception).__name__,
        "validator_error": error,
        "validator_error_sha256": text_sha256(error),
        "required_action": VALIDATION_REQUIRED_ACTION,
    }
    assert corrective_user_prompt.count('"validation_correction"') == 1
    return correction


def test_source_schemas_are_strict_and_provider_compatible() -> None:
    proposal = proposer(mode="courtesy", reply="Thank you for saying so.", claims=[])
    audit = claim_auditor()
    review = reviewer(direct_question=False, answers_first=False)
    silence_review = no_reply_review()
    validate_json_schema(proposal, proposer_schema(500, 6))
    validate_json_schema(audit, claim_auditor_schema(6))
    validate_json_schema(review, reviewer_schema(6))
    validate_json_schema(silence_review, no_reply_review_schema())
    assert validate_no_reply_review(silence_review) == silence_review
    assert proposer_schema(500, 6)["additionalProperties"] is False
    assert evidence_schema(6, 24)["additionalProperties"] is False
    assert claim_auditor_schema(6)["additionalProperties"] is False
    assert reviewer_schema(6)["additionalProperties"] is False
    assert no_reply_review_schema()["additionalProperties"] is False


def test_claim_auditor_must_cover_the_exact_reply_without_internal_conflict() -> None:
    reply = "Conviction matters. Evidence decides the factual issue."
    incomplete = claim_auditor()

    with pytest.raises(ValueError, match="does not cover the exact reply"):
        validate_claim_auditor(
            incomplete,
            maximum_claims=6,
            proposed_reply=reply,
        )


def test_configuration_is_explicit_and_fail_closed() -> None:
    assert validate_strategy_config(strategy_config()) == []
    assert validate_strategy_config(strategy_config(fail_closed=False))
    assert validate_strategy_config(strategy_config(maximum_revisions=2))
    assert validate_strategy_config(strategy_config(maximum_invalid_response_retries=2))
    assert validate_strategy_config(strategy_config(maximum_model_calls=9))
    assert validate_strategy_config(strategy_config(maximum_model_calls=7))
    assert validate_strategy_config(strategy_config(proposer_timeout_seconds=121))
    assert validate_strategy_config(strategy_config(evidence_max_output_tokens=4001))
    assert validate_strategy_config({**strategy_config(), "legacy_mode": True})


def test_conversational_engagement_prompt_versions_are_current() -> None:
    assert STRATEGY_VERSION == "ai-first-reply-v3"
    assert DRAFT_SCHEMA_VERSION == 9
    assert PROPOSER_PROMPT_VERSION == "ai-first-proposer-v16"
    assert EVIDENCE_PROMPT_VERSION == "claim-evidence-entailment-v6"
    assert REVIEWER_PROMPT_VERSION == "independent-reply-reviewer-v14"
    assert NO_REPLY_REVIEW_PROMPT_VERSION == "independent-no-reply-review-v1"
    assert CLAIM_AUDITOR_PROMPT_VERSION == "claim-inventory-auditor-v5"
    assert VALIDATION_RETRY_PROTOCOL_VERSION == "validator-guided-retry-v1"


def test_hybrid_and_production_system_prompt_hashes_are_pinned() -> None:
    context = reply_context("A wholly synthetic civil contribution.")
    proposal = proposer(
        mode="courtesy",
        reply="Thank you for the thoughtful contribution.",
        claims=[],
    )
    no_reply_proposal = proposer(mode="no_reply")
    prompt_systems = {
        "proposer": _proposer_prompts(
            context,
            [],
            resolved_quotation=None,
            revision=None,
        )[0],
        "reviewer": _reviewer_prompts(context, proposal, [], None)[0],
        "evidence": _evidence_prompts([], {})[0],
        "no_reply_review": _no_reply_review_prompts(
            context,
            no_reply_proposal,
        )[0],
        "claim_auditor": _claim_auditor_prompts(
            "A wholly synthetic claim-free sentence."
        )[0],
    }

    prompt_hashes = {
        name: text_sha256(prompt) for name, prompt in prompt_systems.items()
    }
    assert prompt_hashes["proposer"] != (
        "06b00d02ce6c0182b9ec2e9ca52a22e9ca03f9b40ef45a3dc9f198ca30351f72"
    )
    assert prompt_hashes["reviewer"] != (
        "778e9d6c325bdfb3d5f9b0a83814dd0f16acc355bd43d8c6fb817b7fb96d349e"
    )
    assert prompt_hashes == {
        "proposer": "7f69a8bb30296247a049a34a27625885cd5f30813f0eda92f99beb85fbf9cb10",
        "reviewer": "cfb992e2479f11902e0ff22820d4862a76225037c325ee593179eff625226144",
        "evidence": "d9d7c86a4f3b6d1cde7f7287919d84d20ba0f4eeb9b9c31a5b2e7b3a7bb3c44b",
        "no_reply_review": "db578711a2f5ea36d7e4bc78e4997188e410407f57545680fe5498a4ee0e5b1d",
        "claim_auditor": "53aa8015b1ea90719d05578c2b2ba20fc9ddc939d23e5287255c44ded24f6e03",
    }


def test_hybrid_system_prompts_meet_the_word_budget() -> None:
    context = reply_context("A wholly synthetic civil contribution.")
    proposal = proposer(
        mode="courtesy",
        reply="Thank you for the thoughtful contribution.",
        claims=[],
    )
    proposer_system = _proposer_prompts(
        context,
        [],
        resolved_quotation=None,
        revision=None,
    )[0]
    reviewer_system = _reviewer_prompts(context, proposal, [], None)[0]
    base_counts = {"proposer": 1192, "reviewer": 849}
    hybrid_counts = {
        "proposer": prompt_word_count(proposer_system),
        "reviewer": prompt_word_count(reviewer_system),
    }

    assert hybrid_counts == {"proposer": 1043, "reviewer": 760}
    assert sum(base_counts.values()) == 2041
    assert sum(hybrid_counts.values()) == 1803
    assert hybrid_counts["proposer"] <= 1050
    assert hybrid_counts["reviewer"] <= 760
    assert sum(hybrid_counts.values()) <= 1810
    assert hybrid_counts["proposer"] <= base_counts["proposer"]
    assert hybrid_counts["reviewer"] <= base_counts["reviewer"]


def test_operational_schema_validator_and_retry_implementation_are_frozen() -> None:
    config = strategy_config()
    assert {
        key: config[key]
        for key in (
            "maximum_model_calls",
            "maximum_invalid_response_retries",
            "maximum_revisions",
            "proposer_timeout_seconds",
            "evidence_timeout_seconds",
            "reviewer_timeout_seconds",
            "proposer_max_output_tokens",
            "evidence_max_output_tokens",
            "reviewer_max_output_tokens",
            "maximum_claims",
            "maximum_evidence_packets_per_claim",
            "maximum_evidence_passages_per_claim",
            "maximum_reply_sentences",
        )
    } == {
        "maximum_model_calls": 6,
        "maximum_invalid_response_retries": 1,
        "maximum_revisions": 1,
        "proposer_timeout_seconds": 30,
        "evidence_timeout_seconds": 30,
        "reviewer_timeout_seconds": 30,
        "proposer_max_output_tokens": 900,
        "evidence_max_output_tokens": 1800,
        "reviewer_max_output_tokens": 900,
        "maximum_claims": 6,
        "maximum_evidence_packets_per_claim": 6,
        "maximum_evidence_passages_per_claim": 24,
        "maximum_reply_sentences": 2,
    }
    assert source_sha256(
        reply_strategy_module.claim_schema,
        reply_strategy_module.proposer_schema,
        reply_strategy_module.evidence_schema,
        reply_strategy_module.world_claim_checks_schema,
        reply_strategy_module.claim_auditor_schema,
        reply_strategy_module.reviewer_schema,
        reply_strategy_module.no_reply_review_schema,
    ) == "b34c9b2e36c68b00fca99e653f0699ed5485a6e78f5a45fc0bb58ebb8c1e1c63"
    assert source_sha256(
        reply_strategy_module.validate_strategy_config,
        reply_strategy_module.validate_reply_context,
        reply_strategy_module.validate_proposer,
        reply_strategy_module.validate_evidence_response,
        reply_strategy_module.validate_claim_auditor,
        reply_strategy_module.validate_reviewer,
        reply_strategy_module.validate_no_reply_review,
        reply_strategy_module.validate_persisted_draft,
    ) == "5c702fd69bb1ef5c742b14568d6b36e7339b39841247266a130e42eadc663d3f"
    assert source_sha256(
        reply_strategy_module._build_validation_retry_user_prompt,
        reply_strategy_module.run_reply_pipeline,
    ) == "ec4712520c86cae089021de8b18f0f1e913b5c8f25f50e57ff113609d82a42c1"


def test_no_reply_review_prompt_is_independent_and_cannot_write_the_reply() -> None:
    proposal = proposer(mode="no_reply")
    system, user = _no_reply_review_prompts(
        reply_context("A civil and relevant contribution."),
        proposal,
    )

    payload = json.loads(user)
    assert payload["context_sections"]["incoming_contribution_to_answer"] == (
        "A civil and relevant contribution."
    )
    assert payload["proposer_interpretation_untrusted"] == proposal["interpretation"]
    assert payload["proposer_no_reply_reason_untrusted"] == proposal["no_reply_reason"]
    assert "must not write the public reply" in system
    assert "serious unsupported accusations" in system


def test_hybrid_prompt_user_payload_shapes_are_unchanged() -> None:
    context = reply_context("A wholly synthetic civil contribution.")
    proposal = proposer(
        mode="courtesy",
        reply="Thank you for the thoughtful contribution.",
        claims=[],
    )
    _, proposer_user = _proposer_prompts(
        context,
        [],
        resolved_quotation=None,
        revision=None,
    )
    _, reviewer_user = _reviewer_prompts(context, proposal, [], None)
    proposer_payload = json.loads(proposer_user)
    reviewer_payload = json.loads(reviewer_user)
    context_keys = {
        "incoming_contribution_to_answer",
        "quoted_post_context_only",
        "bounded_parent_thread_context_only",
        "clarification_request_if_any",
    }

    assert set(proposer_payload) == {
        "context_sections",
        "target",
        "current_date",
        "resolved_quotation_for_factual_use",
        "recent_account_replies_to_avoid_repeating",
    }
    assert set(reviewer_payload) == {
        "context_sections",
        "mode",
        "tone",
        "proposed_reply",
        "proposer_direct_factual_question_present",
        "proposer_requested_answer_type",
        "proposer_direct_answer_text",
        "proposer_listed_factual_claims_untrusted",
        "exact_thatcher_wording_used",
        "exact_thatcher_wording",
        "evidence_package",
        "resolved_quotation_for_independent_check",
    }
    assert set(proposer_payload["context_sections"]) == context_keys
    assert set(reviewer_payload["context_sections"]) == context_keys
    assert "validation_correction" not in proposer_payload
    assert "validation_correction" not in reviewer_payload


def test_proposer_prompt_has_one_coherent_conservative_hybrid_policy() -> None:
    system, _ = _proposer_prompts(
        reply_context("A wholly synthetic civil and relevant contribution."),
        ["A wholly synthetic recent reply."],
        resolved_quotation=None,
        revision=None,
    )

    assert system.count("Priority order:") == 1
    assert (
        "Priority order: 1. factual and safety correctness; 2. direct relevance; "
        "3. specificity and added value; 4. brevity."
    ) in system
    assert system.count("Specificity:") == 1
    assert system.count("Added value:") == 1
    assert system.casefold().count("stock acknowledgement") == 1
    assert "Civil, intelligible, relevant and safe contributions normally receive replies" in system
    assert "No question, disagreement, new factual claim, new subject or @mention is required" in system
    assert "absence alone never warrants no_reply" in system
    assert "Quote-tweets remain first-class engagement" in system

    assert (
        "Use courtesy only for essentially social thanks, praise, affection, sympathy, "
        "remembrance, greetings, simple support or celebration"
    ) in system
    assert "short natural courtesy, even generic, is acceptable" in system
    assert "without manufactured politics or added-value elaboration" in system
    assert (
        "Argument, analogy, distinction, criticism, recommendation, policy observation, "
        "political observation, moral proposition and reasoned agreement normally use "
        "opinion_or_principle, not mere ceremonial courtesy"
    ) in system

    assert "Specificity: not sensible beneath several unrelated contributions" in system
    assert "Added value: more than paraphrase, thanks or acknowledgement" in system
    assert "sharp distinction" in system
    assert "particular recommendation or standard" in system
    assert "pointed rhetorical question or contribution-derived dry turn" in system
    assert "no factual assertion is required" in system
    assert "Outside social courtesy, avoid stock acknowledgements" in system
    for phrase in (
        "well noted",
        "well made",
        "point taken",
        "thank you for sharing",
        "an important reminder",
    ):
        assert f"'{phrase}'" in system
    assert "examples are illustrative, not a permanent phrase blacklist" in system
    assert "interchangeable or empty wording, not ordinary reused words, is defective" in system
    assert "Do not reuse conspicuous sentence frames from recent replies" in system

    assert "natural, contribution-specific, claim-free dry or wry line" in system
    assert "particular word, contrast, irony or implication in the contribution" in system
    assert "Do not force humour" in system
    assert (
        "Do not use wit for grief, distress, abuse, serious unsupported allegations or "
        "sensitive factual correction"
    ) in system

    for safeguard in (
        "spam or advertising",
        "incoherence or unintelligibility",
        "abuse or harassment",
        "clear bad-faith bait",
        "repetition demonstrated by the bounded thread",
        "dangerous amplification of serious unsupported accusations or conspiracy claims",
        "wholly unrelated material",
        "inability to produce a safe, relevant and original response",
    ):
        assert safeguard in system
    assert "Do not weaken or broaden these safeguards" in system
    assert "disagreement alone is not bait" in system


def test_proposer_revision_prefers_claim_free_social_engagement() -> None:
    system, _ = _proposer_prompts(
        reply_context("A civil observation containing an unsupported prediction."),
        [],
        resolved_quotation=None,
        revision={"review_findings": "Remove the unsupported prediction."},
    )

    assert "safe, honest, claim-free revision engaging its particular theme" in system
    assert "over automatically choosing no_reply" in system
    assert "Follow the social-versus-substantive mode rule" in system


def test_reviewer_prompt_applies_social_substantive_quality_and_wit_policy() -> None:
    system, _ = _reviewer_prompts(
        reply_context("A wholly synthetic expression of affection and remembrance."),
        proposer(mode="courtesy", reply="That affection still speaks warmly.", claims=[]),
        [],
        None,
    )

    assert system.count("Specificity:") == 1
    assert system.count("Added value:") == 1
    assert system.casefold().count("stock acknowledgement") == 1
    assert (
        "Courtesy suits essentially social praise, affection, remembrance, gratitude, "
        "sympathy, greetings or support"
    ) in system
    assert "approve brief natural courtesy without political lecture" in system
    assert "brevity isn't defective" in system
    assert (
        "Substantive argument, analogy, distinction, criticism, recommendation, "
        "policy/political observation, moral proposition and reasoned agreement normally "
        "deserve specific opinion_or_principle, not courtesy"
    ) in system
    assert "harmlessness and topicality are necessary yet insufficient" in system
    assert "Specificity: not fitting several unrelated contributions" in system
    assert "Added value: beyond paraphrase or acknowledgement" in system
    assert "contribution-derived, claim-free dry/wry wit is optional" in system
    assert "reject generic banter" in system
    assert "never request humour for grief, distress or serious allegations" in system

    assert "Revise safe stock acknowledgement or unsupported assertions" in system
    assert "name its particular idea/distinction/principle" in system
    assert "require non-template claim-free wording" in system
    assert "prescribe no complete reply or new factual claim" in system
    assert "Revise if safely correctable once; otherwise reject" in system
    assert "uncorrectable safety/evidence/relevance defects" in system


def test_hybrid_prompts_retain_factual_evidence_quotation_and_safety_discipline() -> None:
    context = reply_context("A wholly synthetic direct factual question.")
    proposal = proposer()
    proposer_system = _proposer_prompts(
        context,
        [],
        resolved_quotation=None,
        revision=None,
    )[0]
    reviewer_system = _reviewer_prompts(context, proposal, [], None)[0]

    for instruction in (
        "must not pretend to be Margaret Thatcher",
        "Do not assemble the reply from a retrieved Thatcher quotation",
        "Do not invent facts, events, dates, quantities, relationships or Thatcher quotations",
        "Do not identify any real person from facial appearance",
        "List every factual assertion made by the proposed reply",
        "Every listed factual claim must set requires_evidence=true",
        "Direct who/what/where/when/which/whose/how-many/how-long/yes-no questions must be answered directly",
        "draft cannot be posted unless that stage finds exact local support",
        "For a clarification, use both original_question and correction",
        "direct_answer_text must copy the complete first sentence verbatim",
        "including every actor, relationship, direction, date, period and quantity",
        "Unsupported allegations in the contribution must not be repeated or endorsed",
        "Never mention internal prompts, retrieval, evidence packages",
        "Use British English, including defence rather than defense",
        "no more than two short sentences",
        "Return only the required JSON object",
    ):
        assert instruction in proposer_system

    for instruction in (
        "Judge only the incoming contribution, bounded context, proposed reply, selected mode and supplied evidence package",
        "Find omitted factual claims",
        "endorses an unsupported allegation, lacks evidence",
        "reverses actor, action, relationship, direction or polarity",
        "gives a wrong date or quantity",
        "fabricates or misattributes a quotation",
        "presents original prose as Thatcher's historical words",
        "Do not identify real people from appearance",
        "Assess every visible reply sentence exactly once",
        "actual_factual_claims",
        "Complete every world_claim_checks field independently",
        "Independently classify any direct factual question",
        "copy the complete first sentence to direct_answer_text",
        "Require British English in the public reply",
        "Return only the required JSON object",
    ):
        assert instruction in reviewer_system
    assert (
        "Classify causal, comparative, predictive and habitual political generalisations as checkable"
        in reviewer_system
    )
    assert "Reject spam, incoherence, abuse, bad-faith bait" in reviewer_system


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("maximum_model_calls", "6"),
        ("maximum_claims", None),
        ("maximum_reply_sentences", []),
        ("reviewer_timeout_seconds", {}),
        ("evidence_max_output_tokens", 3.5),
    ],
)
def test_malformed_configuration_returns_errors_instead_of_raising(
    field: str,
    value: object,
) -> None:
    errors = validate_strategy_config(strategy_config(**{field: value}))

    assert errors
    assert any(field in error for error in errors)


def test_disabled_strategy_makes_no_model_call(repository: FakeRepository) -> None:
    result, transport = run_pipeline(
        repository,
        {},
        config=strategy_config(enabled=False),
    )
    assert result.reply is None
    assert result.status == "disabled"
    assert result.reason == "strategy_disabled"
    assert result.model_call_count == 0
    assert transport.calls == []


def test_context_keeps_contribution_quoted_post_parent_and_clarification_separate() -> None:
    context = reply_context(
        "You still did not answer me.",
        quoted_post={"post_id": "80", "author_role": "account", "text": "Quoted material."},
        parent_thread=[{"post_id": "90", "author_role": "user", "text": "Parent material."}],
        clarification_request={
            "original_question": "Where did people move when the Berlin Wall fell?",
            "correction": "You still did not answer me.",
        },
    )
    assert validate_reply_context(context) == context
    bad = copy.deepcopy(context)
    bad["clarification_request"]["correction"] = "different"  # type: ignore[index]
    with pytest.raises(ValueError, match="correction"):
        validate_reply_context(bad)


def test_no_reply_is_confirmed_by_independent_reviewer(repository: FakeRepository) -> None:
    interpretation_marker = "PRIVATE_PROPOSER_INTERPRETATION_MARKER"
    reason_marker = "PRIVATE_PROPOSER_NO_REPLY_REASON_MARKER"
    proposal = proposer(
        mode="no_reply",
        no_reply_reason=reason_marker,
    )
    proposal["interpretation"] = interpretation_marker
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposal,
            "no_reply_reviewer": no_reply_review(),
        },
    )
    assert result.reply is None
    assert result.status == "no_reply"
    assert result.reason == "independent_no_reply_confirmed"
    assert result.model_call_count == 2
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "no_reply_reviewer",
    ]
    assert result.audit[-1] == {
        "stage": "no_reply_reviewer",
        "status": "completed",
        "verdict": "confirm_no_reply",
    }
    terminal_snapshot = {
        "reply": str(result.reply) if result.reply is not None else None,
        "status": result.status,
        "reason": result.reason,
        "model_call_count": result.model_call_count,
        "revision_count": result.revision_count,
        "audit": list(result.audit),
    }
    serialised_result = json.dumps(terminal_snapshot, sort_keys=True)
    telemetry = outcome_telemetry(result)
    serialised_telemetry = json.dumps(telemetry, sort_keys=True)
    for private_marker in (interpretation_marker, reason_marker):
        assert private_marker not in serialised_result
        assert private_marker not in serialised_telemetry
    assert telemetry == {
        "proposer_mode": "no_reply",
        "proposer_tone": "neutral",
        "factual_claim_count": 0,
        "terminal_stage": "no_reply_reviewer",
        "reviewer_verdict": "confirm_no_reply",
        "claim_auditor_status": "not_run",
        "evidence_status": "not_run",
    }


def test_no_reply_is_overturned_into_one_approved_revised_reply(
    repository: FakeRepository,
) -> None:
    interpretation_marker = "PRIVATE_PROPOSER_INTERPRETATION_MARKER"
    reason_marker = "PRIVATE_PROPOSER_NO_REPLY_REASON_MARKER"
    proposal = proposer(
        mode="no_reply",
        no_reply_reason=reason_marker,
    )
    proposal["interpretation"] = interpretation_marker
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposal,
            "no_reply_reviewer": no_reply_review("require_reply"),
            "revision_proposer": proposer(
                mode="courtesy",
                reply="Thank you for the thoughtful contribution.",
                claims=[],
            ),
            "revision_reviewer": reviewer(
                direct_question=False,
                answers_first=False,
            ),
        },
        context=reply_context("A civil and relevant contribution."),
    )

    assert result.status == "approved"
    assert result.reason == "reviewer_approved"
    assert str(result.reply) == "Thank you for the thoughtful contribution."
    assert result.revision_count == 1
    assert result.model_call_count == 4
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "no_reply_reviewer", "revision_proposer", "revision_reviewer",
    ]
    calls_by_stage = {call["stage"]: call for call in transport.calls}
    no_reply_reviewer_user = calls_by_stage["no_reply_reviewer"]["user_prompt"]
    revision_call = calls_by_stage["revision_proposer"]
    for private_marker in (interpretation_marker, reason_marker):
        assert private_marker in no_reply_reviewer_user
        assert private_marker not in revision_call["user_prompt"]
        assert private_marker not in revision_call["system_prompt"]
    revision_payload = json.loads(revision_call["user_prompt"])
    revision = revision_payload["single_allowed_revision"]
    assert revision["reviewer_reasons"] == [
        "A safe, relevant response remains possible."
    ]
    instruction = revision["reviewer_revision_instructions"]
    assert "Produce a safe, relevant public response" in instruction
    assert "Acknowledge the civil contribution without repeating unsupported claims." in instruction


def test_no_reply_is_overturned_into_supported_factual_reply_in_five_calls(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="no_reply"),
            "no_reply_reviewer": no_reply_review("require_reply"),
            "revision_proposer": proposer(),
            "revision_evidence": supporting_evidence(repository),
            "revision_reviewer": reviewer(),
        },
    )

    assert result.status == "approved"
    assert result.model_call_count == 5
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "no_reply_reviewer", "revision_proposer",
        "revision_evidence", "revision_reviewer",
    ]


def test_revised_no_reply_confirmation_uses_deterministic_reason(
    repository: FakeRepository,
) -> None:
    private_marker = "PRIVATE_REVISED_MODEL_REASON_MUST_NOT_ESCAPE"
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="no_reply"),
            "no_reply_reviewer": no_reply_review("require_reply"),
            "revision_proposer": proposer(
                mode="no_reply",
                no_reply_reason=private_marker,
            ),
            "revision_no_reply_reviewer": no_reply_review(),
        },
    )

    assert result.status == "no_reply"
    assert result.reason == "independent_no_reply_confirmed"
    assert result.model_call_count == 4
    assert private_marker not in json.dumps(outcome_telemetry(result), sort_keys=True)
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "no_reply_reviewer",
        "revision_proposer", "revision_no_reply_reviewer",
    ]


def test_repeated_require_reply_after_revision_limit_is_operational_failure(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="no_reply"),
            "no_reply_reviewer": no_reply_review("require_reply"),
            "revision_proposer": proposer(mode="no_reply"),
            "revision_no_reply_reviewer": no_reply_review("require_reply"),
        },
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "no_reply_review_requires_reply_after_revision"
    assert result.revision_count == 1
    assert result.model_call_count == 4
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "no_reply_reviewer",
        "revision_proposer", "revision_no_reply_reviewer",
    ]


def test_first_proposer_attempt_is_byte_identical_and_has_no_correction(
    repository: FakeRepository,
) -> None:
    context = reply_context("A wholly synthetic civil observation.")
    reply = "Thank you for the thoughtful observation."
    proposal = proposer(mode="courtesy", reply=reply, claims=[])
    expected_system, expected_user = _proposer_prompts(
        context,
        [reply],
        resolved_quotation=None,
        revision=None,
    )

    result, transport = run_pipeline(
        repository,
        {"proposer": proposal},
        context=context,
        recent_replies=[reply],
    )

    assert result.status == "no_reply"
    assert result.reason == "exact_duplicate_reply"
    assert result.model_call_count == 1
    assert len(transport.calls) == 1
    assert transport.calls[0] == {
        "stage": "proposer",
        "model": "proposer-model",
        "system_prompt": expected_system,
        "user_prompt": expected_user,
        "response_schema": proposer_schema(500, 6),
        "timeout_seconds": 30,
        "max_output_tokens": 900,
        "media_context": None,
    }
    assert transport.calls[0]["system_prompt"].encode("utf-8") == expected_system.encode("utf-8")
    assert transport.calls[0]["user_prompt"].encode("utf-8") == expected_user.encode("utf-8")
    assert "validation_correction" not in json.loads(transport.calls[0]["user_prompt"])


def test_first_reviewer_attempt_is_byte_identical_to_current_prompt(
    repository: FakeRepository,
) -> None:
    context = reply_context("Thank you for the entirely synthetic note.")
    proposal = proposer(
        mode="courtesy",
        reply="Thank you for the entirely synthetic note.",
        claims=[],
    )
    expected_system, expected_user = _reviewer_prompts(
        context,
        proposal,
        [],
        None,
    )

    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposal,
            "reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=context,
    )

    assert isinstance(result.reply, AIReply)
    reviewer_call = transport.calls[1]
    assert reviewer_call == {
        "stage": "reviewer",
        "model": "reviewer-model",
        "system_prompt": expected_system,
        "user_prompt": expected_user,
        "response_schema": reviewer_schema(6),
        "timeout_seconds": 30,
        "max_output_tokens": 900,
        "media_context": None,
    }
    assert reviewer_call["system_prompt"].encode("utf-8") == expected_system.encode("utf-8")
    assert reviewer_call["user_prompt"].encode("utf-8") == expected_user.encode("utf-8")
    assert "validation_correction" not in json.loads(reviewer_call["user_prompt"])


def test_malformed_json_retry_uses_one_fixed_size_corrective_envelope(
    repository: FakeRepository,
) -> None:
    context = reply_context("A wholly synthetic retry contribution.")
    reply = "Thank you for the synthetic retry contribution."
    proposal = proposer(mode="courtesy", reply=reply, claims=[])
    malformed_response = "{RAW_INVALID_MODEL_OUTPUT_MUST_NOT_BE_COPIED"
    media = {"synthetic_media_id": "offline-fixture-only"}
    with pytest.raises(json.JSONDecodeError) as caught:
        json.loads(malformed_response)
    validator_exception = caught.value
    expected_system, expected_user = _proposer_prompts(
        context,
        [reply],
        resolved_quotation=None,
        revision=None,
    )

    result, transport = run_pipeline(
        repository,
        {"proposer": [malformed_response, proposal]},
        context=context,
        recent_replies=[reply],
        media_context=media,
    )

    assert result.reason == "exact_duplicate_reply"
    assert result.model_call_count == 2
    assert len(transport.calls) == 2
    first_call, retry_call = transport.calls
    assert first_call["system_prompt"] == expected_system
    assert first_call["user_prompt"] == expected_user
    assert first_call["user_prompt"] != retry_call["user_prompt"]
    assert first_call["system_prompt"].encode("utf-8") == retry_call["system_prompt"].encode("utf-8")
    assert first_call["response_schema"] == retry_call["response_schema"]
    assert first_call["media_context"] == retry_call["media_context"] == media
    assert {
        key: value for key, value in first_call.items() if key != "user_prompt"
    } == {
        key: value for key, value in retry_call.items() if key != "user_prompt"
    }
    assert_retry_envelope(
        first_call["user_prompt"],
        retry_call["user_prompt"],
        stage="proposer",
        attempt_number=2,
        validator_exception=validator_exception,
    )
    retry_payload = json.loads(retry_call["user_prompt"])
    assert set(retry_payload) == set(json.loads(first_call["user_prompt"])) | {
        "validation_correction"
    }
    assert "response_schema" not in retry_payload
    assert malformed_response not in retry_call["user_prompt"]
    assert "RAW_INVALID_MODEL_OUTPUT_MUST_NOT_BE_COPIED" not in retry_call["user_prompt"]
    first_identity = text_sha256(first_call["system_prompt"] + "\n" + first_call["user_prompt"])
    retry_identity = text_sha256(retry_call["system_prompt"] + "\n" + retry_call["user_prompt"])
    assert first_identity != retry_identity

    retry_row = retry_audit_row(result, "proposer")
    assert retry_row["validation_retry_protocol_version"] == VALIDATION_RETRY_PROTOCOL_VERSION
    assert retry_row["validator_error_type"] == "JSONDecodeError"
    assert retry_row["validator_error"] == str(validator_exception)
    assert retry_row["validator_error_sha256"] == text_sha256(str(validator_exception))
    assert retry_row["original_user_prompt_sha256"] == text_sha256(first_call["user_prompt"])
    assert retry_row["corrective_user_prompt_sha256"] == text_sha256(retry_call["user_prompt"])
    assert retry_row["original_user_prompt_sha256"] != retry_row["corrective_user_prompt_sha256"]
    assert retry_row["corrective_retry_applied"] is True
    serialised_audit = json.dumps(result.audit, sort_keys=True)
    assert malformed_response not in serialised_audit
    assert "RAW_INVALID_MODEL_OUTPUT_MUST_NOT_BE_COPIED" not in serialised_audit
    assert expected_user not in serialised_audit
    assert retry_call["user_prompt"] not in serialised_audit


def test_ordinary_validator_value_error_gets_exactly_one_corrective_retry(
    repository: FakeRepository,
) -> None:
    context = reply_context("A synthetic validator-error contribution.")
    reply = "Thank you for the synthetic validator test."
    valid_proposal = proposer(mode="courtesy", reply=reply, claims=[])
    raw_response_marker = "RAW_RESPONSE_FIELD_MUST_NOT_ESCAPE"
    invalid_proposal = copy.deepcopy(valid_proposal)
    invalid_proposal["mode"] = raw_response_marker
    with pytest.raises(ValueError) as caught:
        validate_proposer(
            invalid_proposal,
            maximum_reply_length=500,
            maximum_claims=6,
        )

    result, transport = run_pipeline(
        repository,
        {"proposer": [invalid_proposal, valid_proposal]},
        context=context,
        recent_replies=[reply],
    )

    assert result.reason == "exact_duplicate_reply"
    assert result.model_call_count == 2
    assert len(transport.calls) == 2
    assert_retry_envelope(
        transport.calls[0]["user_prompt"],
        transport.calls[1]["user_prompt"],
        stage="proposer",
        attempt_number=2,
        validator_exception=caught.value,
    )
    retry_row = retry_audit_row(result, "proposer")
    assert retry_row["validator_error_type"] == "ValueError"
    assert retry_row["validator_error"] == str(caught.value)
    assert retry_row["validator_error_sha256"] == text_sha256(str(caught.value))
    assert retry_row["corrective_retry_applied"] is True
    assert raw_response_marker not in transport.calls[1]["user_prompt"]
    assert raw_response_marker not in json.dumps(result.audit, sort_keys=True)


def test_retry_envelope_helper_rebuilds_from_the_immutable_original() -> None:
    original_payload = {
        "alpha": ["synthetic", {"nested": True}],
        "number": 7,
        "unicode": "café",
    }
    original_prompt = json.dumps(original_payload, ensure_ascii=False, sort_keys=True)
    first_error = ValueError("first synthetic validation failure")
    second_error = TypeError("second synthetic validation failure")

    second_prompt, second_metadata = _build_validation_retry_user_prompt(
        original_prompt,
        "evidence",
        2,
        first_error,
    )
    third_prompt, third_metadata = _build_validation_retry_user_prompt(
        original_prompt,
        "evidence",
        3,
        second_error,
    )

    assert_retry_envelope(
        original_prompt,
        second_prompt,
        stage="evidence",
        attempt_number=2,
        validator_exception=first_error,
    )
    assert_retry_envelope(
        original_prompt,
        third_prompt,
        stage="evidence",
        attempt_number=3,
        validator_exception=second_error,
    )
    assert str(first_error) not in third_prompt
    assert str(second_error) not in second_prompt
    assert second_prompt.count('"validation_correction"') == 1
    assert third_prompt.count('"validation_correction"') == 1
    for prompt, metadata, error in (
        (second_prompt, second_metadata, first_error),
        (third_prompt, third_metadata, second_error),
    ):
        assert metadata["validation_retry_protocol_version"] == VALIDATION_RETRY_PROTOCOL_VERSION
        assert metadata["validator_error_type"] == type(error).__name__
        assert metadata["validator_error"] == str(error)
        assert metadata["validator_error_sha256"] == text_sha256(str(error))
        assert metadata["original_user_prompt_sha256"] == text_sha256(original_prompt)
        assert metadata["corrective_user_prompt_sha256"] == text_sha256(prompt)


@pytest.mark.parametrize(
    "stage",
    [
        "proposer",
        "revision_proposer",
        "evidence",
        "revision_evidence",
        "claim_auditor",
        "revision_claim_auditor",
        "reviewer",
        "revision_reviewer",
        "no_reply_reviewer",
        "revision_no_reply_reviewer",
    ],
)
def test_retry_envelope_preserves_every_pipeline_stage_name(stage: str) -> None:
    original_prompt = json.dumps({"synthetic": "payload"}, sort_keys=True)
    validator_exception = ValueError("synthetic validation failure")

    corrective_prompt, _metadata = _build_validation_retry_user_prompt(
        original_prompt,
        stage,
        2,
        validator_exception,
    )

    correction = assert_retry_envelope(
        original_prompt,
        corrective_prompt,
        stage=stage,
        attempt_number=2,
        validator_exception=validator_exception,
    )
    assert correction["stage"] == stage


def test_hypothetical_multiple_retries_never_accumulate_correction_history(
    repository: FakeRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reply_strategy_module,
        "validate_strategy_config",
        lambda _config: [],
    )
    context = reply_context("A synthetic hypothetical retry contribution.")
    reply = "Thank you for the hypothetical retry test."
    malformed_one = "{"
    malformed_two = '{"field":'
    with pytest.raises(json.JSONDecodeError) as first_caught:
        json.loads(malformed_one)
    with pytest.raises(json.JSONDecodeError) as second_caught:
        json.loads(malformed_two)

    result, transport = run_pipeline(
        repository,
        {
            "proposer": [
                malformed_one,
                malformed_two,
                proposer(mode="courtesy", reply=reply, claims=[]),
            ],
        },
        context=context,
        config=strategy_config(maximum_invalid_response_retries=2),
        recent_replies=[reply],
    )

    assert result.reason == "exact_duplicate_reply"
    assert result.model_call_count == 3
    assert len(transport.calls) == 3
    original_prompt = transport.calls[0]["user_prompt"]
    assert_retry_envelope(
        original_prompt,
        transport.calls[1]["user_prompt"],
        stage="proposer",
        attempt_number=2,
        validator_exception=first_caught.value,
    )
    assert_retry_envelope(
        original_prompt,
        transport.calls[2]["user_prompt"],
        stage="proposer",
        attempt_number=3,
        validator_exception=second_caught.value,
    )
    assert str(first_caught.value) not in transport.calls[2]["user_prompt"]
    retry_rows = [
        row for row in result.audit if row.get("status") == "invalid_response_retry"
    ]
    assert len(retry_rows) == 2
    assert all(
        row["validation_retry_protocol_version"] == VALIDATION_RETRY_PROTOCOL_VERSION
        and row["corrective_retry_applied"] is True
        for row in retry_rows
    )


def test_invalid_no_reply_reviewer_output_retries_only_within_existing_limits(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="no_reply"),
            "no_reply_reviewer": ["", ""],
        },
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "no_reply_reviewer_invalid"
    assert result.model_call_count == 3
    assert result.model_call_count <= strategy_config()["maximum_model_calls"]
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "no_reply_reviewer", "no_reply_reviewer",
    ]
    assert [row["status"] for row in result.audit[-2:]] == [
        "invalid_response_retry", "invalid",
    ]
    retry_payload = json.loads(transport.calls[2]["user_prompt"])
    assert retry_payload["validation_correction"]["stage"] == "no_reply_reviewer"
    retry_row = retry_audit_row(result, "no_reply_reviewer")
    assert retry_row["validation_retry_protocol_version"] == VALIDATION_RETRY_PROTOCOL_VERSION
    assert retry_row["corrective_retry_applied"] is True
    assert result.audit[-1]["validation_retry_protocol_version"] == VALIDATION_RETRY_PROTOCOL_VERSION
    assert result.audit[-1]["corrective_retry_applied"] is True
    assert outcome_telemetry(result)["reviewer_verdict"] == "invalid"


def test_one_invalid_proposer_response_is_retried_then_accepted(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": ["", proposer(mode="no_reply")],
            "no_reply_reviewer": no_reply_review(),
        },
    )

    assert result.reply is None
    assert result.reason == "independent_no_reply_confirmed"
    assert result.model_call_count == 3
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "proposer", "no_reply_reviewer",
    ]
    assert [row["status"] for row in result.audit] == [
        "not_resolved",
        "invalid_response_retry",
        "completed",
        "completed",
    ]
    assert json.loads(transport.calls[1]["user_prompt"])["validation_correction"][
        "stage"
    ] == "proposer"
    assert retry_audit_row(result, "proposer")["corrective_retry_applied"] is True


def test_second_invalid_proposer_response_fails_closed(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {"proposer": ["", ""]},
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "proposer_invalid"
    assert result.model_call_count == 2
    assert [call["stage"] for call in transport.calls] == ["proposer", "proposer"]
    assert [row["status"] for row in result.audit] == [
        "not_resolved",
        "invalid_response_retry",
        "invalid",
    ]
    assert result.audit[-1]["validation_retry_protocol_version"] == VALIDATION_RETRY_PROTOCOL_VERSION
    assert result.audit[-1]["corrective_retry_applied"] is True
    assert text_sha256(transport.calls[0]["user_prompt"]) == result.audit[-1][
        "original_user_prompt_sha256"
    ]
    assert text_sha256(transport.calls[1]["user_prompt"]) == result.audit[-1][
        "corrective_user_prompt_sha256"
    ]
    assert outcome_telemetry(result) == {
        "proposer_mode": "not_run",
        "proposer_tone": "none",
        "factual_claim_count": None,
        "terminal_stage": "proposer",
        "reviewer_verdict": "not_run",
        "claim_auditor_status": "not_run",
        "evidence_status": "not_run",
    }


def test_invalid_response_retry_remains_bounded_by_global_call_limit(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {"proposer": ""},
        config=strategy_config(maximum_model_calls=1),
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "proposer_invalid"
    assert result.model_call_count == 1
    assert len(transport.calls) == 1
    assert result.audit[-1]["reason"] == "reply pipeline model-call ceiling reached"
    assert result.audit[-1]["corrective_retry_applied"] is False
    assert not any(
        row.get("corrective_retry_applied") is True for row in result.audit
    )


@pytest.mark.parametrize(
    ("validator_message", "private_marker"),
    [
        pytest.param("", None, id="empty"),
        pytest.param(
            "NUL_VALIDATOR_MESSAGE_MUST_NOT_ESCAPE\x00tail",
            "NUL_VALIDATOR_MESSAGE_MUST_NOT_ESCAPE",
            id="nul",
        ),
        pytest.param(
            "OVERSIZED_VALIDATOR_MESSAGE_MUST_NOT_ESCAPE" + ("x" * 501),
            "OVERSIZED_VALIDATOR_MESSAGE_MUST_NOT_ESCAPE",
            id="oversized",
        ),
        pytest.param(chr(0xD800), None, id="not-utf8-encodable"),
    ],
)
def test_unsafe_validator_error_fails_closed_without_a_retry(
    repository: FakeRepository,
    monkeypatch: pytest.MonkeyPatch,
    validator_message: str,
    private_marker: str | None,
) -> None:
    def unsafe_validator(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ValueError(validator_message)

    monkeypatch.setattr(reply_strategy_module, "validate_proposer", unsafe_validator)
    result, transport = run_pipeline(
        repository,
        {"proposer": proposer(mode="no_reply")},
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "proposer_invalid"
    assert result.model_call_count == 1
    assert len(transport.calls) == 1
    assert not any(row.get("status") == "invalid_response_retry" for row in result.audit)
    final_row = result.audit[-1]
    assert final_row["status"] == "invalid"
    assert final_row["reason"] == "validation_error_not_safe_for_retry"
    assert final_row["validator_error_type"] == "ValueError"
    assert final_row["corrective_retry_applied"] is False
    assert len(str(final_row["reason"])) < 100
    if private_marker is not None:
        assert private_marker not in json.dumps(result.audit, sort_keys=True)


@pytest.mark.parametrize(
    ("original_user_prompt", "private_marker"),
    [
        pytest.param(
            "{INVALID_ORIGINAL_PROMPT_MUST_NOT_ESCAPE",
            "INVALID_ORIGINAL_PROMPT_MUST_NOT_ESCAPE",
            id="invalid-json",
        ),
        pytest.param(
            json.dumps(["NON_OBJECT_PROMPT_MUST_NOT_ESCAPE"]),
            "NON_OBJECT_PROMPT_MUST_NOT_ESCAPE",
            id="non-object",
        ),
        pytest.param(
            json.dumps({
                "safe_synthetic_field": True,
                "validation_correction": {
                    "RESERVED_VALUE_MUST_NOT_ESCAPE": True,
                },
            }),
            "RESERVED_VALUE_MUST_NOT_ESCAPE",
            id="reserved-key-collision",
        ),
        pytest.param(
            json.dumps({
                "SURROGATE_PROMPT_MUST_NOT_ESCAPE": chr(0xD800),
            }),
            "SURROGATE_PROMPT_MUST_NOT_ESCAPE",
            id="corrective-prompt-not-utf8-encodable",
        ),
    ],
)
def test_retry_prompt_shape_failure_is_bounded_and_leak_free(
    repository: FakeRepository,
    monkeypatch: pytest.MonkeyPatch,
    original_user_prompt: str,
    private_marker: str,
) -> None:
    monkeypatch.setattr(
        reply_strategy_module,
        "_proposer_prompts",
        lambda *_args, **_kwargs: ("synthetic-system-prompt", original_user_prompt),
    )
    raw_invalid_response = "{RAW_RESPONSE_MUST_NOT_ESCAPE"

    result, transport = run_pipeline(
        repository,
        {"proposer": raw_invalid_response},
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "proposer_invalid"
    assert result.model_call_count == 1
    assert len(transport.calls) == 1
    assert transport.calls[0]["user_prompt"] == original_user_prompt
    assert not any(row.get("status") == "invalid_response_retry" for row in result.audit)
    final_row = result.audit[-1]
    assert final_row["status"] == "invalid"
    assert final_row["reason"] == "validation_retry_prompt_construction_failed"
    assert final_row["corrective_retry_applied"] is False
    assert isinstance(final_row["reason"], str)
    assert 0 < len(final_row["reason"]) < 200
    serialised_audit = json.dumps(result.audit, sort_keys=True)
    assert private_marker not in serialised_audit
    assert raw_invalid_response not in serialised_audit
    assert original_user_prompt not in serialised_audit


def test_one_invalid_evidence_response_is_retried_before_review(
    repository: FakeRepository,
) -> None:
    valid_evidence = supporting_evidence(repository)
    attempts = 0

    def evidence_sequence(**kwargs: Any) -> object:
        nonlocal attempts
        attempts += 1
        return "" if attempts == 1 else valid_evidence(**kwargs)

    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "evidence": evidence_sequence,
            "reviewer": reviewer(),
        },
    )

    assert isinstance(result.reply, AIReply)
    assert [call["stage"] for call in transport.calls] == [
        "proposer",
        "evidence",
        "evidence",
        "reviewer",
    ]
    retry_row = retry_audit_row(result, "evidence")
    assert retry_row["corrective_retry_applied"] is True
    evidence_calls = [call for call in transport.calls if call["stage"] == "evidence"]
    assert json.loads(evidence_calls[1]["user_prompt"])["validation_correction"][
        "stage"
    ] == "evidence"


def test_one_invalid_reviewer_response_is_retried_before_approval(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="courtesy", reply="Thank you.", claims=[]),
            "reviewer": [
                "",
                reviewer(direct_question=False, answers_first=False),
            ],
        },
        context=reply_context("Thank you."),
    )

    assert isinstance(result.reply, AIReply)
    assert [call["stage"] for call in transport.calls] == [
        "proposer",
        "reviewer",
        "reviewer",
    ]
    retry_row = retry_audit_row(result, "reviewer")
    assert retry_row["corrective_retry_applied"] is True
    reviewer_calls = [call for call in transport.calls if call["stage"] == "reviewer"]
    assert json.loads(reviewer_calls[1]["user_prompt"])["validation_correction"][
        "stage"
    ] == "reviewer"


def test_one_invalid_claim_auditor_response_is_corrected_at_the_same_stage(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply="Principle should be valued above convenience.",
                claims=[],
            ),
            "claim_auditor": ["", claim_auditor()],
            "reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=reply_context("A wholly synthetic question of principle."),
    )

    assert isinstance(result.reply, AIReply)
    claim_auditor_calls = [
        call for call in transport.calls if call["stage"] == "claim_auditor"
    ]
    assert len(claim_auditor_calls) == 2
    retry_payload = json.loads(claim_auditor_calls[1]["user_prompt"])
    assert retry_payload["validation_correction"]["stage"] == "claim_auditor"
    assert retry_audit_row(result, "claim_auditor")["corrective_retry_applied"] is True


@pytest.mark.parametrize(
    ("mode", "text", "tone"),
    [
        ("opinion_or_principle", "Conviction matters more than applause.", "neutral"),
        ("light_humour", "A slogan is not improved by shouting it twice.", "light"),
        ("courtesy", "Thank you for the kind thought.", "neutral"),
    ],
)
def test_nonfactual_modes_require_fresh_reviewer_approval(
    repository: FakeRepository,
    mode: str,
    text: str,
    tone: str,
) -> None:
    proposal = proposer(mode=mode, reply=text, claims=[])
    proposal["tone"] = tone
    scripted = {
        "proposer": proposal,
        "reviewer": reviewer(direct_question=False, answers_first=False),
    }
    if mode in {"opinion_or_principle", "light_humour"}:
        scripted["claim_auditor"] = claim_auditor()
    result, transport = run_pipeline(
        repository,
        scripted,
        context=reply_context("A general political observation."),
    )
    assert isinstance(result.reply, AIReply)
    assert str(result.reply) == text
    assert result.reply.draft_record["mode"] == mode
    expected_stages = ["proposer"]
    if mode in {"opinion_or_principle", "light_humour"}:
        expected_stages.append("claim_auditor")
    expected_stages.append("reviewer")
    assert [call["stage"] for call in transport.calls] == expected_stages


def test_philosophical_yes_no_challenge_uses_principle_mode_with_direct_opening(
    repository: FakeRepository,
) -> None:
    contribution = (
        "But what drives ambition & effort? It’s the envy of what others have "
        "& the envy to have it too, isn’t it?"
    )
    reply = (
        "No. Wanting to emulate another person’s success is aspiration; "
        "envy is resenting them for possessing it."
    )
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply=reply,
                claims=[],
            ),
            "claim_auditor": claim_auditor(),
            "reviewer": reviewer(
                direct_question=False,
                answers_first=False,
            ),
        },
        context=reply_context(contribution),
    )

    assert isinstance(result.reply, AIReply)
    assert str(result.reply) == reply
    assert result.reply.draft_record["mode"] == "opinion_or_principle"
    assert result.reply.draft_record["direct_factual_question_present"] is False
    assert str(result.reply).startswith("No.")
    assert [call["stage"] for call in transport.calls] == [
        "proposer",
        "claim_auditor",
        "reviewer",
    ]

    proposer_call = transport.calls[0]
    proposer_payload = json.loads(proposer_call["user_prompt"])
    assert (
        proposer_payload["context_sections"]["incoming_contribution_to_answer"]
        == contribution
    )
    assert (
        "Motive, value, political-principle and moral-concept questions use "
        "opinion_or_principle unless factual"
        in proposer_call["system_prompt"]
    )
    assert (
        "Put invited yes/no or qualification first, then explain the principle"
        in proposer_call["system_prompt"]
    )

    reviewer_call = transport.calls[-1]
    reviewer_payload = json.loads(reviewer_call["user_prompt"])
    assert reviewer_payload["mode"] == "opinion_or_principle"
    assert reviewer_payload["proposer_direct_factual_question_present"] is False
    assert (
        "interrogative form alone isn't direct_factual_question_present"
        in reviewer_call["system_prompt"]
    )
    assert (
        "Put invited yes/no first; revise evasion"
        in reviewer_call["system_prompt"]
    )


def test_direct_factual_answer_requires_claim_evidence_and_independent_review(
    repository: FakeRepository,
) -> None:
    repository.candidate_override = [
        repository.passage,
        replace(
            repository.passage,
            evidence_id="d" * 64,
            quote_id="e" * 64,
        ),
        replace(
            repository.passage,
            evidence_id="f" * 64,
            quote_id="1" * 64,
        ),
    ]
    factual_text = "People moved from East Germany towards West Germany in November 1989."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(reply=factual_text),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(
                factual_claims=["People moved from East Germany towards West Germany in November 1989."],
            ),
        },
    )
    assert isinstance(result.reply, AIReply)
    assert str(result.reply) == factual_text
    assert [call["stage"] for call in transport.calls] == ["proposer", "evidence", "reviewer"]
    assert result.reply.draft_record["claim_evidence"] == [{
        "claim_id": "claim-1",
        "evidence_ids": [repository.passage.evidence_id],
    }]
    assert result.reply.pipeline_metadata["evidence_confidence"] == "high"
    assert result.reply.draft_record["retrieved_count"] == 3
    assert result.reply.pipeline_metadata["retrieved_count"] == 3
    assert result.reply.pipeline_metadata["evidence_reference_count"] == 1


def test_clarification_rejects_nonfactual_mode_before_downstream_model_stages(
    repository: FakeRepository,
) -> None:
    correction = "You still did not answer which way people moved."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply="Resolve must be matched by courage.",
                claims=[],
            ),
            "claim_auditor": claim_auditor(),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=reply_context(
            correction,
            clarification_request={
                "original_question": "Which way did people move when the Berlin Wall fell?",
                "correction": correction,
            },
        ),
    )

    assert result.reply is None
    assert result.status == "no_reply"
    assert result.reason == "clarification_not_direct_factual_answer"
    assert [call["stage"] for call in transport.calls] == ["proposer"]
    assert result.audit[-1] == {
        "stage": "proposer",
        "status": "rejected",
        "reason": "clarification_not_direct_factual_answer",
    }


def test_clarification_direct_factual_answer_retains_factual_pipeline(
    repository: FakeRepository,
) -> None:
    correction = "I asked which way people moved."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(),
        },
        context=reply_context(
            correction,
            clarification_request={
                "original_question": "Which way did people move when the Berlin Wall fell?",
                "correction": correction,
            },
        ),
    )

    assert isinstance(result.reply, AIReply)
    assert result.reply.draft_record["mode"] == "direct_factual_answer"
    assert [call["stage"] for call in transport.calls] == [
        "proposer",
        "evidence",
        "reviewer",
    ]


def test_clarification_no_reply_keeps_independent_no_reply_review(
    repository: FakeRepository,
) -> None:
    correction = "I asked which way people moved."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="no_reply",
                no_reply_reason="I cannot safely formulate the requested fact.",
            ),
            "no_reply_reviewer": no_reply_review(),
        },
        context=reply_context(
            correction,
            clarification_request={
                "original_question": "Which way did people move when the Berlin Wall fell?",
                "correction": correction,
            },
        ),
    )

    assert result.reply is None
    assert result.status == "no_reply"
    assert result.reason == "independent_no_reply_confirmed"
    assert [call["stage"] for call in transport.calls] == [
        "proposer",
        "no_reply_reviewer",
    ]


def test_clarification_revision_proposer_has_same_mode_restriction(
    repository: FakeRepository,
) -> None:
    correction = "I asked which way people moved."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(verdict="revise", topically_relevant=False),
            "revision_proposer": proposer(
                mode="opinion_or_principle",
                reply="Resolve must be matched by courage.",
                claims=[],
            ),
            "revision_claim_auditor": claim_auditor(),
            "revision_reviewer": reviewer(
                direct_question=False,
                answers_first=False,
            ),
        },
        context=reply_context(
            correction,
            clarification_request={
                "original_question": "Which way did people move when the Berlin Wall fell?",
                "correction": correction,
            },
        ),
    )

    assert result.reply is None
    assert result.reason == "clarification_not_direct_factual_answer"
    assert result.revision_count == 1
    assert [call["stage"] for call in transport.calls] == [
        "proposer",
        "evidence",
        "reviewer",
        "revision_proposer",
    ]
    assert result.audit[-1]["stage"] == "revision_proposer"


def test_clarification_mode_refusal_does_not_expose_proposer_prose_in_outcome(
    repository: FakeRepository,
) -> None:
    marker = "MODEL_GENERATED_REFUSAL_EXPLANATION"
    correction = "I asked which way people moved."
    proposal = proposer(
        mode="opinion_or_principle",
        reply="Resolve must be matched by courage.",
        claims=[],
    )
    proposal["interpretation"] = marker
    result, _transport = run_pipeline(
        repository,
        {"proposer": proposal},
        context=reply_context(
            correction,
            clarification_request={
                "original_question": "Which way did people move when the Berlin Wall fell?",
                "correction": correction,
            },
        ),
    )

    assert result.reason == "clarification_not_direct_factual_answer"
    assert marker not in result.reason
    assert marker not in json.dumps(result.audit, sort_keys=True)
    assert marker not in json.dumps(outcome_telemetry(result), sort_keys=True)


def test_old_selected_evidence_keeps_retrieval_total_unavailable(
    repository: FakeRepository,
) -> None:
    telemetry = evidence_telemetry(
        {"evidence_ids": [repository.passage.evidence_id]},
        repository,
    )

    assert telemetry["evidence_confidence"] == "high"
    assert telemetry["retrieved_count"] is None
    assert telemetry["evidence_reference_count"] == 1


def test_reviewer_is_fresh_and_does_not_receive_hidden_proposer_interpretation(
    repository: FakeRepository,
) -> None:
    proposal = proposer(mode="courtesy", reply="Thank you for saying so.", claims=[])
    proposal["interpretation"] = "HIDDEN PROPOSER REASONING"
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposal,
            "reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=reply_context("Thank you."),
    )
    assert result.reply is not None
    reviewer_call = transport.calls[-1]
    assert reviewer_call["stage"] == "reviewer"
    assert reviewer_call["model"] == "reviewer-model"
    assert "HIDDEN PROPOSER REASONING" not in reviewer_call["user_prompt"]
    assert "fresh independent final reviewer" in reviewer_call["system_prompt"]


def test_explicit_reviewer_approval_is_mandatory(repository: FakeRepository) -> None:
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="courtesy", reply="Thank you.", claims=[]),
            "reviewer": reviewer(
                verdict="reject",
                direct_question=False,
                answers_first=False,
                topically_relevant=False,
            ),
        },
        context=reply_context("Thank you."),
    )
    assert result.reply is None
    assert result.reason == "reviewer_rejected"
    telemetry = outcome_telemetry(result)
    assert telemetry["terminal_stage"] == "reviewer"
    assert telemetry["reviewer_verdict"] == "reject"
    assert telemetry["claim_auditor_status"] == "not_run"
    assert telemetry["evidence_status"] == "not_run"


def test_outcome_telemetry_preserves_completed_evidence_before_operational_failure(
    repository: FakeRepository,
) -> None:
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "evidence": supporting_evidence(repository),
            "reviewer": ["", ""],
        },
    )

    assert result.status == "operational_failure"
    assert result.reason == "reviewer_invalid"
    assert outcome_telemetry(result) == {
        "proposer_mode": "direct_factual_answer",
        "proposer_tone": "neutral",
        "factual_claim_count": 1,
        "terminal_stage": "reviewer",
        "reviewer_verdict": "invalid",
        "claim_auditor_status": "not_run",
        "evidence_status": "completed",
    }


def test_valid_unsupported_evidence_is_audited_as_insufficient(
    repository: FakeRepository,
) -> None:
    reply_text = "People moved from East Germany towards West Germany in November 1989."
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply=reply_text,
                claims=[claim(reply_text)],
            ),
            "evidence": insufficient_evidence,
            "reviewer": reviewer(
                verdict="reject",
                direct_question=False,
                answers_first=False,
                factual_claims=[reply_text],
                unsupported_factual_claims=[reply_text],
                contains_unsupported_factual_claims=True,
            ),
        },
    )

    evidence_rows = [row for row in result.audit if row.get("stage") == "evidence"]
    assert evidence_rows == [{
        "stage": "evidence",
        "status": "insufficient",
        "supported": False,
    }]
    assert outcome_telemetry(result)["evidence_status"] == "insufficient"


def test_outcome_telemetry_maps_legacy_unsupported_completion_to_insufficient() -> None:
    result = PipelineResult(
        None,
        "no_reply",
        "insufficient_claim_evidence",
        2,
        0,
        ({"stage": "evidence", "status": "completed", "supported": False},),
    )

    assert outcome_telemetry(result)["evidence_status"] == "insufficient"


def test_outcome_telemetry_prefers_later_direct_factual_rejection() -> None:
    result = PipelineResult(
        None,
        "no_reply",
        "insufficient_claim_evidence",
        2,
        0,
        (
            {"stage": "evidence", "status": "insufficient", "supported": False},
            {"stage": "evidence", "status": "rejected"},
        ),
    )

    assert outcome_telemetry(result)["evidence_status"] == "rejected"


def test_outcome_telemetry_prefers_later_successful_revision_evidence() -> None:
    result = PipelineResult(
        None,
        "operational_failure",
        "reviewer_invalid",
        5,
        1,
        (
            {"stage": "evidence", "status": "insufficient", "supported": False},
            {"stage": "revision_evidence", "status": "completed", "supported": True},
        ),
    )

    assert outcome_telemetry(result)["evidence_status"] == "completed"


def test_one_revision_cycle_is_the_absolute_maximum(repository: FakeRepository) -> None:
    revised = proposer(mode="courtesy", reply="Thank you for the correction.", claims=[])
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="courtesy", reply="Thank you.", claims=[]),
            "reviewer": reviewer(
                verdict="revise",
                direct_question=False,
                answers_first=False,
                topically_relevant=False,
            ),
            "revision_proposer": revised,
            "revision_reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=reply_context("That did not address my point."),
    )
    assert str(result.reply) == "Thank you for the correction."
    assert result.revision_count == 1
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "reviewer", "revision_proposer", "revision_reviewer",
    ]


def test_claim_auditor_catches_a_factual_claim_omitted_by_proposer(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply="Inflation fell by ten points, so resolve has prevailed.",
                claims=[],
            ),
            "claim_auditor": claim_auditor([
                "Inflation fell by ten points, so resolve has prevailed."
            ]),
            "revision_proposer": proposer(
                mode="no_reply",
                no_reply_reason="The unsupported factual claim cannot be repaired safely.",
            ),
            "revision_no_reply_reviewer": no_reply_review(),
        },
        context=reply_context("Government must show resolve on inflation."),
    )

    assert result.reply is None
    assert result.reason == "independent_no_reply_confirmed"
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor", "revision_proposer",
        "revision_no_reply_reviewer",
    ]
    assert any(
        row.get("stage") == "claim_auditor"
        and row.get("factual_claim_count") == 1
        for row in result.audit
    )


def test_reviewer_claim_inventory_mismatch_gets_one_strict_repair_cycle(
    repository: FakeRepository,
) -> None:
    reply_text = "People moved from East Germany towards West Germany in November 1989."
    incomplete_claim = claim(
        "People moved from East Germany towards West Germany"
    )
    complete_claim = claim(reply_text)
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(reply=reply_text, claims=[incomplete_claim]),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(factual_claims=[reply_text]),
            "revision_proposer": proposer(reply=reply_text, claims=[complete_claim]),
            "revision_evidence": supporting_evidence(repository),
            "revision_reviewer": reviewer(factual_claims=[reply_text]),
        },
    )

    assert str(result.reply) == reply_text
    assert result.revision_count == 1
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "evidence", "reviewer",
        "revision_proposer", "revision_evidence", "revision_reviewer",
    ]
    revision_payload = json.loads(transport.calls[3]["user_prompt"])
    instruction = revision_payload["single_allowed_revision"]["reviewer_revision_instructions"]
    assert "complete independently checkable clause" in instruction


def test_partial_candidate_evidence_in_non_direct_mode_is_adjudicated_before_one_revision(
    repository: FakeRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = claim("People moved west")
    second = {
        **claim("inflation fell by ten points"),
        "claim_id": "claim-2",
        "actor": "inflation",
        "action_or_relationship": "fell",
        "direction_or_polarity": "down",
        "date_or_period": "",
        "quantity": "ten points",
    }

    def candidates(text: str, **_kwargs: object) -> list[EvidencePassage]:
        return [repository.passage] if "East Germany" in text else []

    monkeypatch.setattr(repository, "candidate_passages", candidates)

    def partial_evidence(**kwargs: Any) -> object:
        payload = json.loads(kwargs["user_prompt"])
        rows = []
        for supplied in payload["claims"]:
            available = payload["candidate_passages_by_claim"][supplied["claim_id"]]
            supported = bool(available)
            rows.append({
                "claim_id": supplied["claim_id"],
                "claim_text": supplied["claim_text"],
                "verdict": "supports" if supported else "insufficient",
                "evidence": ([{
                    "evidence_id": repository.passage.evidence_id,
                    "exact_supporting_passage": repository.passage.passage,
                    "relation": "supports",
                }] if supported else []),
                "actor": supplied["actor"],
                "action_or_relationship": supplied["action_or_relationship"],
                "direction_or_polarity": supplied["direction_or_polarity"],
                "date_or_period": supplied["date_or_period"],
                "quantity": supplied["quantity"],
                "explanation": "Supported locally." if supported else "No supplied passage supports this claim.",
            })
        return {"claims": rows}

    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply="People moved west, and inflation fell by ten points.",
                claims=[first, second],
            ),
            "evidence": partial_evidence,
            "reviewer": reviewer(
                verdict="revise",
                factual_claims=[first["claim_text"], second["claim_text"]],
                contains_unsupported_factual_claims=True,
                unsupported_factual_claims=[second["claim_text"]],
            ),
            "revision_proposer": proposer(
                mode="opinion_or_principle",
                reply="Resolve must be matched by evidence.",
                claims=[],
            ),
            "revision_claim_auditor": claim_auditor(),
            "revision_reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=reply_context("What happened, and what happened to inflation?"),
    )

    assert str(result.reply) == "Resolve must be matched by evidence."
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "evidence", "reviewer", "revision_proposer",
        "revision_claim_auditor", "revision_reviewer",
    ]
    evidence_payload = json.loads(transport.calls[1]["user_prompt"])
    assert evidence_payload["candidate_passages_by_claim"]["claim-1"]
    assert evidence_payload["candidate_passages_by_claim"]["claim-2"] == []


def test_unsupported_direct_factual_answer_stops_before_review_or_revision(
    repository: FakeRepository,
) -> None:
    repository.return_candidates = False
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "reviewer": reviewer(verdict="revise"),
            "revision_proposer": proposer(),
            "revision_reviewer": reviewer(),
        },
    )

    assert result.reply is None
    assert result.reason == "insufficient_claim_evidence"
    assert result.revision_count == 0
    assert [call["stage"] for call in transport.calls] == ["proposer"]
    assert result.audit[-1] == {
        "stage": "evidence",
        "status": "rejected",
        "reason": "direct_factual_answer_not_fully_supported",
    }


def test_second_revision_request_fails_closed(repository: FakeRepository) -> None:
    revise = reviewer(
        verdict="revise",
        direct_question=False,
        answers_first=False,
        topically_relevant=False,
    )
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="courtesy", reply="Thank you.", claims=[]),
            "reviewer": revise,
            "revision_proposer": proposer(mode="courtesy", reply="Thank you again.", claims=[]),
            "revision_reviewer": copy.deepcopy(revise),
        },
        context=reply_context("Thank you."),
    )
    assert result.reply is None
    assert result.reason == "revision_limit_reached"
    assert len(transport.calls) == 4


def test_evidence_call_is_omitted_for_nonfactual_reply(repository: FakeRepository) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="opinion_or_principle", reply="Courage must precede action.", claims=[]),
            "claim_auditor": claim_auditor(),
            "reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=reply_context("Why does nobody have the courage to act?"),
    )
    assert result.reply is not None
    assert all(call["stage"] != "evidence" for call in transport.calls)
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor", "reviewer",
    ]


def test_missing_claim_evidence_cannot_be_approved(repository: FakeRepository) -> None:
    repository.return_candidates = False
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "reviewer": reviewer(),
            "revision_proposer": proposer(),
            "revision_reviewer": reviewer(),
        },
    )
    assert result.reply is None
    assert result.reason == "insufficient_claim_evidence"
    assert all(call["stage"] not in {"evidence", "revision_evidence"} for call in transport.calls)


def test_unrelated_or_invented_evidence_id_is_rejected(repository: FakeRepository) -> None:
    proposal_claim = claim()
    response = supporting_evidence(repository)(
        user_prompt=json.dumps({"claims": [proposal_claim]}),
    )
    response["claims"][0]["evidence"][0]["evidence_id"] = "d" * 64  # type: ignore[index]
    with pytest.raises(ValueError, match="not supplied"):
        validate_evidence_response(
            response,
            [proposal_claim],
            {"claim-1": [repository.passage]},
            repository,  # type: ignore[arg-type]
            maximum_references=24,
        )


def test_exact_supporting_passage_must_match_local_bytes(repository: FakeRepository) -> None:
    proposal_claim = claim()
    response = supporting_evidence(repository)(
        user_prompt=json.dumps({"claims": [proposal_claim]}),
    )
    response["claims"][0]["evidence"][0]["exact_supporting_passage"] += " changed"  # type: ignore[index,operator]
    with pytest.raises(ValueError, match="mismatch"):
        validate_evidence_response(
            response,
            [proposal_claim],
            {"claim-1": [repository.passage]},
            repository,  # type: ignore[arg-type]
            maximum_references=24,
        )


def test_support_verdict_cannot_rewrite_claim_semantic_dimensions(
    repository: FakeRepository,
) -> None:
    proposal_claim = claim()
    response = supporting_evidence(repository)(
        user_prompt=json.dumps({"claims": [proposal_claim]}),
    )
    response["claims"][0]["direction_or_polarity"] = "West to East"  # type: ignore[index]

    with pytest.raises(ValueError, match="semantic dimensions"):
        validate_evidence_response(
            response,
            [proposal_claim],
            {"claim-1": [repository.passage]},
            repository,  # type: ignore[arg-type]
            maximum_references=24,
        )


def test_low_confidence_passage_cannot_authorise_a_factual_claim(
    repository: FakeRepository,
) -> None:
    low_confidence = replace(repository.passage, research_confidence="low")
    repository.passage = low_confidence
    repository.passages = {low_confidence.evidence_id: low_confidence}
    proposal_claim = claim()
    response = supporting_evidence(repository)(
        user_prompt=json.dumps({"claims": [proposal_claim]}),
    )

    with pytest.raises(ValueError, match="low-confidence"):
        validate_evidence_response(
            response,
            [proposal_claim],
            {"claim-1": [low_confidence]},
            repository,  # type: ignore[arg-type]
            maximum_references=24,
        )


def test_evidence_rows_are_canonicalised_to_proposer_claim_order(
    repository: FakeRepository,
) -> None:
    first = claim()
    second = claim("Movement occurred in November 1989.")
    second["claim_id"] = "claim-2"
    second["actor"] = "people in East Germany"
    second["action_or_relationship"] = "movement occurred"
    second["direction_or_polarity"] = ""
    proposal = proposer(
        reply=(
            "People moved from East Germany towards West Germany in November 1989. "
            "Movement occurred in November 1989."
        ),
        claims=[first, second],
    )

    def reversed_evidence(**kwargs: Any) -> object:
        response = supporting_evidence(repository)(**kwargs)
        response["claims"].reverse()  # type: ignore[index,union-attr]
        return response

    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposal,
            "evidence": reversed_evidence,
            "reviewer": reviewer(
                factual_claims=[str(first["claim_text"]), str(second["claim_text"])],
            ),
        },
    )

    assert isinstance(result.reply, AIReply)
    assert [row["claim_id"] for row in result.reply.draft_record["claim_evidence"]] == [
        "claim-1",
        "claim-2",
    ]
    validate_persisted_draft(
        result.reply.draft_record,
        context=reply_context(),
        config=strategy_config(),
        repository=repository,  # type: ignore[arg-type]
        maximum_reply_length=500,
    )


def test_proposer_metadata_is_untrusted_and_must_be_internally_consistent() -> None:
    with pytest.raises(ValueError, match="requires at least one factual claim"):
        validate_proposer(
            proposer(mode="direct_factual_answer", claims=[]),
            maximum_reply_length=500,
            maximum_claims=6,
        )
    duplicate = claim()
    duplicate["claim_id"] = "claim-2"
    with pytest.raises(ValueError, match="must be unique"):
        validate_proposer(
            proposer(claims=[claim(), duplicate]),
            maximum_reply_length=500,
            maximum_claims=6,
        )
    with pytest.raises(ValueError, match="confidence"):
        validate_proposer(
            proposer(mode="courtesy", reply="Thank you.", claims=[], confidence="low"),
            maximum_reply_length=500,
            maximum_claims=6,
        )
    blank_interpretation = proposer(mode="courtesy", reply="Thank you.", claims=[])
    blank_interpretation["interpretation"] = "   "
    with pytest.raises(ValueError, match="interpretation"):
        validate_proposer(
            blank_interpretation,
            maximum_reply_length=500,
            maximum_claims=6,
        )


@pytest.mark.parametrize(
    ("field", "entry", "error"),
    [
        ("reasons", "x" * 301, "reasons"),
        ("actual_factual_claims", "x" * 501, "actual_factual_claims"),
        ("unsupported_factual_claims", "x" * 501, "unsupported_factual_claims"),
    ],
)
def test_reviewer_local_validation_enforces_item_length_limits(
    field: str,
    entry: str,
    error: str,
) -> None:
    review = reviewer(direct_question=False, answers_first=False)
    review[field] = [entry]

    with pytest.raises(ValueError, match=error):
        validate_reviewer(review, maximum_claims=6)


def test_terminal_reviewer_rejection_may_include_unused_revision_advice() -> None:
    review = reviewer(
        verdict="reject",
        direct_question=False,
        answers_first=False,
        topically_relevant=False,
        revision_instructions="A safer draft would need to address the contribution.",
    )

    assert validate_reviewer(review, maximum_claims=6)["verdict"] == "reject"


def test_reviewer_approval_cannot_include_revision_instructions() -> None:
    review = reviewer(direct_question=False, answers_first=False)
    review["revision_instructions"] = "Change it."

    with pytest.raises(ValueError, match="approval cannot include"):
        validate_reviewer(review, maximum_claims=6)


def test_reviewer_catches_factual_sentence_omitted_by_proposer(repository: FakeRepository) -> None:
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply="The policy cut unemployment by exactly 20 per cent.",
                claims=[],
            ),
            # Exercise the final reviewer's independent safety net after a
            # deliberately scripted claim-auditor false negative.
            "claim_auditor": claim_auditor(),
            "reviewer": reviewer(
                verdict="reject",
                direct_question=False,
                answers_first=False,
                factual_claims=["The policy cut unemployment by exactly 20 per cent."],
                unsupported_factual_claims=["The policy cut unemployment by exactly 20 per cent."],
                contains_unsupported_factual_claims=True,
            ),
        },
        context=reply_context("Tell me about the policy."),
    )
    assert result.reply is None


def test_complete_adversarial_fixture_is_rejected_by_independent_review(repository: FakeRepository) -> None:
    cases = json.loads(ADVERSARIAL_FIXTURE.read_text(encoding="utf-8"))
    expected_ids = {
        "burnham-unrelated-wall", "east-west-direction-reversed", "unrelated-evidence-id",
        "unusual-allegation-verb", "unicode-fabricated-quotation", "employment-historical-record",
        "wrong-actor", "wrong-relationship", "wrong-date", "wrong-quantity", "wrong-polarity",
        "long-parent-hides-contribution", "quote-text-hides-commentary", "proposer-hides-factual-claim",
    }
    assert {case["case_id"] for case in cases} == expected_ids
    for case in cases:
        result, _transport = run_pipeline(
            repository,
            {
                "proposer": proposer(
                    mode="opinion_or_principle",
                    reply=case["bad_reply"],
                    claims=[],
                ),
                "claim_auditor": claim_auditor(),
                "reviewer": reviewer(
                    verdict="reject",
                    direct_question="?" in case["contribution"],
                    answers_first=False,
                    topically_relevant=case["failure"] not in {
                        "topically_irrelevant", "false_lexical_bridge",
                        "parent_context_overreach", "quoted_context_overreach",
                    },
                ),
            },
            context=reply_context(case["contribution"]),
        )
        assert result.reply is None, case["case_id"]


def test_complete_valid_fixture_passes_the_real_pipeline(repository: FakeRepository) -> None:
    cases = json.loads(Path("tests/fixtures/ai_first_reply_valid_cases.json").read_text(encoding="utf-8"))
    assert len(cases) == 6
    for case in cases:
        if case["mode"] == "no_reply":
            scripted = {
                "proposer": proposer(mode="no_reply"),
                "no_reply_reviewer": no_reply_review(),
            }
        elif case["requires_evidence"]:
            scripted = {
                "proposer": proposer(mode=case["mode"], reply=case["reply"]),
                "evidence": supporting_evidence(repository),
                "reviewer": reviewer(),
            }
        else:
            scripted = {
                "proposer": proposer(mode=case["mode"], reply=case["reply"], claims=[]),
                "reviewer": reviewer(direct_question=False, answers_first=False),
            }
            if case["mode"] in {"opinion_or_principle", "light_humour"}:
                scripted["claim_auditor"] = claim_auditor()
        result, _transport = run_pipeline(
            repository,
            scripted,
            context=reply_context(case["contribution"]),
        )
        if case["mode"] == "no_reply":
            assert result.reply is None, case["case_id"]
            assert result.status == "no_reply", case["case_id"]
        else:
            assert str(result.reply) == case["reply"], case["case_id"]
            assert result.reply.draft_record["mode"] == case["mode"], case["case_id"]


@pytest.mark.parametrize(
    "question",
    [
        "Did the Berlin Wall fall in 1989?",
        "Which direction did people move?",
        "Whose government negotiated it?",
        "How many years did it last?",
        "How long did the division last?",
        "Who made the decision?",
        "What happened?",
        "Where did people move?",
        "When did it happen?",
    ],
)
def test_direct_question_forms_use_answer_first_model_path(
    repository: FakeRepository,
    question: str,
) -> None:
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(),
        },
        context=reply_context(question),
    )
    assert result.reply is not None
    assert result.reply.draft_record["mode"] == "direct_factual_answer"


def test_berlin_wall_regression_preserves_east_to_west_direction(repository: FakeRepository) -> None:
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(reply="The flow was overwhelmingly from East to West once the option existed."),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(),
        },
        context=reply_context("Were did people ram towards when the Berlin Wall fell?"),
    )
    assert str(result.reply) == "The flow was overwhelmingly from East to West once the option existed."


def test_reversed_direction_is_rejected_even_with_a_broadly_related_passage(
    repository: FakeRepository,
) -> None:
    reversed_claim = claim("People moved from West Germany towards East Germany in November 1989.")
    reversed_claim["direction_or_polarity"] = "West to East"
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(reply="People moved from West towards East.", claims=[reversed_claim]),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(
                verdict="reject",
                direction_polarity_correct=False,
                factual_claims=[reversed_claim["claim_text"]],
            ),
        },
    )
    assert result.reply is None


def test_declared_and_undeclared_thatcher_wording_is_checked_without_delimiters(
    repository: FakeRepository,
) -> None:
    declared = proposer(
        mode="opinion_or_principle",
        reply="Authorised historical words are exact.",
        claims=[],
        exact_wording="Authorised historical words are exact",
    )
    assert deterministic_reply_error(
        declared,
        repository,  # type: ignore[arg-type]
        recent_replies=[],
        maximum_reply_length=500,
        maximum_sentences=2,
    ) is None
    undeclared = proposer(
        mode="opinion_or_principle",
        reply="Authorised historical words are exact.",
        claims=[],
    )
    assert deterministic_reply_error(
        undeclared,
        repository,  # type: ignore[arg-type]
        recent_replies=[],
        maximum_reply_length=500,
        maximum_sentences=2,
    ) == "undeclared_thatcher_wording_detected"


@pytest.mark.parametrize(
    "reply",
    [
        "No, the quoted wording is not verified as Margaret Thatcher's.",
        "The quotation is not confirmed as Margaret Thatcher's exact wording.",
        "As Margaret Thatcher once said, the wording must be checked.",
    ],
)
def test_attribution_language_is_not_mistaken_for_impersonation(
    repository: FakeRepository,
    reply: str,
) -> None:
    proposal = proposer(mode="direct_factual_answer", reply=reply)

    assert deterministic_reply_error(
        proposal,
        repository,  # type: ignore[arg-type]
        recent_replies=[],
        maximum_reply_length=500,
        maximum_sentences=2,
    ) is None


@pytest.mark.parametrize(
    "reply",
    [
        "I am Margaret Thatcher and this is my answer.",
        "As Margaret Thatcher, I would reject that premise.",
        "Speaking as Margaret Thatcher, I reject that premise.",
    ],
)
def test_explicit_thatcher_impersonation_remains_blocked(
    repository: FakeRepository,
    reply: str,
) -> None:
    proposal = proposer(mode="opinion_or_principle", reply=reply, claims=[])

    assert deterministic_reply_error(
        proposal,
        repository,  # type: ignore[arg-type]
        recent_replies=[],
        maximum_reply_length=500,
        maximum_sentences=2,
    ) == "reply_matches_blocked_safety_pattern"


def test_exact_quote_authorisation_preserves_internal_punctuation(
    real_repository: EvidenceRepository,
) -> None:
    verified = next(
        " ".join(str(packet.get("verified_text") or "").split())
        for packet in real_repository.packets.values()
        if "," in str(packet.get("verified_text") or "")
        and len(str(packet.get("verified_text") or "").split()) >= 6
    )
    punctuation_changed = verified.replace(",", "", 1)

    assert real_repository.exact_quote_is_authorised(verified) is True
    assert real_repository.exact_quote_is_authorised(punctuation_changed) is False
    assert real_repository.exact_quote_is_authorised(verified[1:]) is False


def test_declaring_one_exact_quote_does_not_authorise_a_second_quote(
    real_repository: EvidenceRepository,
) -> None:
    declared = "the answer is less Socialism."
    undeclared = "We Conservatives hate unemployment."
    proposal = proposer(
        mode="opinion_or_principle",
        reply=f"{declared} {undeclared}",
        claims=[],
        exact_wording=declared,
    )

    assert deterministic_reply_error(
        proposal,
        real_repository,
        recent_replies=[],
        maximum_reply_length=500,
        maximum_sentences=2,
    ) == "undeclared_thatcher_wording_detected"


@pytest.mark.parametrize("alteration", ["apostrophe", "initial_case"])
def test_declared_exact_quote_must_occur_with_exact_source_text(
    real_repository: EvidenceRepository,
    alteration: str,
) -> None:
    if alteration == "apostrophe":
        verified = next(
            text
            for text in real_repository._authorised_quote_texts.values()
            if len(text) <= 500 and ("'" in text or "\u2019" in text)
        )
        altered = (
            verified.replace("'", "\u2019", 1)
            if "'" in verified
            else verified.replace("\u2019", "'", 1)
        )
    else:
        verified = next(
            text
            for text in real_repository._authorised_quote_texts.values()
            if len(text) <= 500 and text and text[0].isalpha()
        )
        altered = verified[0].swapcase() + verified[1:]

    proposal = proposer(
        mode="opinion_or_principle",
        reply=altered,
        claims=[],
        exact_wording=verified,
    )
    assert deterministic_reply_error(
        proposal,
        real_repository,
        recent_replies=[],
        maximum_reply_length=500,
        maximum_sentences=100,
    ) == "declared_thatcher_wording_missing_from_reply"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Read https://example.com now.", "reply_contains_link"),
        ("Read example.com now.", "reply_contains_link"),
        ("Read \u4f8b\u3048.\u30c6\u30b9\u30c8 now.", "reply_contains_link"),
        ("Read 192.0.2.10 now.", "reply_contains_link"),
        ("Write to policy@example.com.", "reply_contains_link"),
        ("Write to policy@\u4f8b\u3048.\u30c6\u30b9\u30c8.", "reply_contains_mention"),
        ("Ask @someone directly.", "reply_contains_mention"),
        ("Ask \uff20someone directly.", "reply_contains_mention"),
        ("Principles matter. #politics", "reply_contains_hashtag"),
        ("Principles matter. #\u00e9conomie", "reply_contains_hashtag"),
        ("Principles matter. #\u653f\u6cbb", "reply_contains_hashtag"),
        ("Principles matter. \uff03politics", "reply_contains_hashtag"),
        ("Principles matter. #\u0301politics", "reply_contains_hashtag"),
        ("Quite so. \U0001f642", "reply_contains_emoji"),
        ("Quite so. 1\ufe0f\u20e3", "reply_contains_emoji"),
        ("One. Two. Three.", "reply_sentence_limit_exceeded"),
        ("One. two. three.", "reply_sentence_limit_exceeded"),
        ("One. Two. Three", "reply_sentence_limit_exceeded"),
        ("One! Two? Three", "reply_sentence_limit_exceeded"),
        ("One\u061f Two\u061f Three", "reply_sentence_limit_exceeded"),
        ("One\u0964 Two\u0964 Three", "reply_sentence_limit_exceeded"),
        ("\u653f" * 300, "invalid_reply_length_or_whitespace"),
        ("Go d\u200bie.", "reply_contains_control_or_format_character"),
        ("One\nTwo\nThree", "reply_contains_line_break"),
    ],
)
def test_deterministic_operational_limits_cannot_be_bypassed(
    repository: FakeRepository,
    text: str,
    reason: str,
) -> None:
    proposal = proposer(mode="courtesy", reply=text, claims=[])
    assert deterministic_reply_error(
        proposal,
        repository,  # type: ignore[arg-type]
        recent_replies=[],
        maximum_reply_length=500,
        maximum_sentences=2,
    ) == reason


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("The Rt. Hon. member must look at the data.", 1),
        ("The Govt. must publish the evidence.", 1),
        ("The MP. must answer the question.", 1),
        ("Examples include policy, law, etc. A conclusion follows.", 2),
        ("The MP. The policy follows.", 2),
        ("The Rt. Hon. member must act. The evidence is clear.", 2),
        ("The Rt. Hon. member must act. Evidence matters. Policy follows.", 3),
        ("No. 10 is the answer.", 1),
        ("No. 10 is the answer. The point is settled.", 2),
        ("No. That is not the answer.", 2),
    ],
)
def test_sentence_count_handles_common_british_political_abbreviations(
    text: str,
    expected: int,
) -> None:
    assert sentence_count(text) == expected


def test_recent_duplicate_reply_fails_before_review(repository: FakeRepository) -> None:
    transport = ScriptedTransport({
        "proposer": proposer(mode="courtesy", reply="Thank you for saying so.", claims=[]),
    })
    result = run_reply_pipeline(
        context=reply_context("Thank you."),
        config=strategy_config(),
        repository=repository,  # type: ignore[arg-type]
        transport=transport,
        maximum_reply_length=500,
        recent_replies=["Thank you for saying so."],
        creation_time="2026-07-20T12:00:00Z",
    )
    assert result.reply is None
    assert result.reason == "exact_duplicate_reply"
    assert len(transport.calls) == 1


def approved_draft(repository: FakeRepository) -> tuple[AIReply, dict[str, object], dict[str, object]]:
    context = reply_context()
    config = strategy_config()
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(),
        },
        context=context,
        config=config,
    )
    assert isinstance(result.reply, AIReply)
    return result.reply, context, config


def test_v4_persisted_draft_revalidates_complete_identity_and_evidence(
    repository: FakeRepository,
) -> None:
    reply, context, config = approved_draft(repository)
    validated = validate_persisted_draft(
        reply.draft_record,
        context=context,
        config=config,
        repository=repository,  # type: ignore[arg-type]
        maximum_reply_length=500,
    )
    assert validated["schema_version"] == DRAFT_SCHEMA_VERSION
    assert validated["strategy_version"] == STRATEGY_VERSION
    assert validated["proposer_prompt_version"] == PROPOSER_PROMPT_VERSION
    assert validated["evidence_prompt_version"] == EVIDENCE_PROMPT_VERSION
    assert validated["reviewer_prompt_version"] == REVIEWER_PROMPT_VERSION
    assert len(validated["approval_hash"]) == 64


def test_claim_free_persisted_draft_revalidates_claim_audit_provenance(
    repository: FakeRepository,
) -> None:
    context = reply_context("Conviction matters more than applause.")
    config = strategy_config()
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply="Conviction matters more than applause.",
                claims=[],
            ),
            "claim_auditor": claim_auditor(),
            "reviewer": reviewer(direct_question=False, answers_first=False),
        },
        context=context,
        config=config,
    )
    assert result.reply is not None

    validated = validate_persisted_draft(
        result.reply.draft_record,
        context=context,
        config=config,
        repository=repository,  # type: ignore[arg-type]
        maximum_reply_length=500,
    )
    assert validated["claim_auditor_sentence_assessments"]
    assert validated["claim_auditor_prompt_version"] == CLAIM_AUDITOR_PROMPT_VERSION
    assert validated["claim_auditor_model"] == config["reviewer_model"]

    for field, value in (
        ("claim_auditor_sentence_assessments", []),
        ("claim_auditor_prompt_version", "different"),
        ("claim_auditor_model", "different"),
    ):
        tampered = copy.deepcopy(result.reply.draft_record)
        tampered[field] = value
        with pytest.raises(ValueError):
            validate_persisted_draft(
                tampered,
                context=context,
                config=config,
                repository=repository,  # type: ignore[arg-type]
                maximum_reply_length=500,
            )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda draft: draft.update({"reviewer_verdict": "reject"}),
        lambda draft: draft.update({"context_hash": "0" * 64}),
        lambda draft: draft.update({"proposer_model": "different"}),
        lambda draft: draft.update({"source_hashes": {"a" * 64: "0" * 64}}),
        lambda draft: draft.update({"evidence_input_hashes": {"a" * 64: "0" * 64}}),
        lambda draft: draft.update({"claim_evidence": []}),
        lambda draft: draft["factual_claims"][0].update({"requires_evidence": False}),
        lambda draft: draft["factual_claims"][0].update({"claim_id": "claim-2"}),
        lambda draft: draft.update({"reviewer_reasons": "not-a-list"}),
        lambda draft: draft.update({"exact_thatcher_wording": "Unrecorded historical words"}),
        lambda draft: draft.update({"proposed_reply": "A different but syntactically valid reply."}),
        lambda draft: draft.update({"creation_time": "not-a-timestampZ"}),
    ],
)
def test_tampered_persisted_draft_fails_closed(
    repository: FakeRepository,
    mutation: Callable[[dict[str, object]], object],
) -> None:
    reply, context, config = approved_draft(repository)
    draft = copy.deepcopy(reply.draft_record)
    mutation(draft)
    with pytest.raises(ValueError):
        validate_persisted_draft(
            draft,
            context=context,
            config=config,
            repository=repository,  # type: ignore[arg-type]
            maximum_reply_length=500,
        )


def test_persisted_direct_factual_draft_cannot_drop_its_claims(
    repository: FakeRepository,
) -> None:
    reply, context, config = approved_draft(repository)
    draft = copy.deepcopy(reply.draft_record)
    draft["factual_claims"] = []
    draft["claim_evidence"] = []
    draft["evidence_ids"] = []
    draft["source_hashes"] = {}
    draft["evidence_input_hashes"] = {}

    with pytest.raises(ValueError, match="requires a factual claim"):
        validate_persisted_draft(
            draft,
            context=context,
            config=config,
            repository=repository,  # type: ignore[arg-type]
            maximum_reply_length=500,
        )


def test_persisted_resolved_answer_rejects_mixed_packet_evidence(
    repository: FakeRepository,
) -> None:
    repository.resolved_quotation = {
        "quote_id": "c" * 64,
        "matched_context_section": "quoted_post",
        "match_basis": "exact_text",
        "speaker": "Margaret Thatcher",
        "resolved_context_hash": "d" * 64,
    }
    context = reply_context(
        "What did this mean?",
        quoted_post={"post_id": "quoted", "author_role": "account", "text": "A quotation."},
    )
    config = strategy_config()
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(),
        },
        context=context,
        config=config,
    )
    assert result.reply is not None

    unrelated = replace(
        repository.passage,
        evidence_id="e" * 64,
        source_hash="f" * 64,
        quote_id="9" * 64,
        passage="Unrelated packet evidence.",
        stable_locator="fixture:unrelated",
    )
    repository.passages[unrelated.evidence_id] = unrelated
    draft = copy.deepcopy(result.reply.draft_record)
    draft["evidence_ids"] = sorted([*draft["evidence_ids"], unrelated.evidence_id])
    draft["claim_evidence"][0]["evidence_ids"] = sorted(
        [*draft["claim_evidence"][0]["evidence_ids"], unrelated.evidence_id]
    )
    draft["source_hashes"][unrelated.evidence_id] = unrelated.source_hash
    draft["evidence_input_hashes"][unrelated.evidence_id] = unrelated.model_input_hash()

    with pytest.raises(ValueError, match="not confined to the resolved quotation"):
        validate_persisted_draft(
            draft,
            context=context,
            config=config,
            repository=repository,  # type: ignore[arg-type]
            maximum_reply_length=500,
        )


def test_persisted_draft_is_invalidated_by_changed_prompt_visible_evidence(
    repository: FakeRepository,
) -> None:
    reply, context, config = approved_draft(repository)
    repository.passages[repository.passage.evidence_id] = replace(
        repository.passage,
        actor="A materially different actor",
        action_or_relationship="A materially different action",
        direction_or_polarity="The opposite direction",
        date_or_period="A different date",
        quantity="A different quantity",
    )

    with pytest.raises(ValueError, match="evidence model input changed"):
        validate_persisted_draft(
            reply.draft_record,
            context=context,
            config=config,
            repository=repository,  # type: ignore[arg-type]
            maximum_reply_length=500,
        )


def test_v1_draft_audit_never_interprets_or_migrates_content(tmp_path: Path) -> None:
    state_path = tmp_path / "bot_state.json"
    state = {
        "pending_reply_drafts": {
            "mention:100": {
                "target_id": "100",
                "candidate_source": "mention",
                "reply_text": "Legacy text must not be posted.",
            }
        }
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    audit = legacy_draft_audit(state, state_path=state_path)
    assert audit["legacy_draft_count"] == 1
    assert audit["deployment_blocked"] is True
    assert audit["records"][0]["classification"] == "legacy_v1_nonpostable"
    assert "reply_text" not in audit["records"][0]


def test_real_repository_contains_only_authorised_completed_packets(
    real_repository: EvidenceRepository,
) -> None:
    assert len(real_repository.packets) == 611
    assert len(real_repository.passages) > 7_000
    assert all(len(quote_id) == 64 for quote_id in real_repository.packets)
    assert all(len(evidence_id) == 64 for evidence_id in real_repository.passages)


def test_every_eligible_full_quotation_resolves_to_its_own_packet(
    real_repository: EvidenceRepository,
) -> None:
    for quote_id, packet in sorted(real_repository.packets.items()):
        text = " ".join(str(packet.get("verified_text") or "").split())
        if text.casefold() in {"", "unknown", "unresolved", "no verified text available."}:
            text = " ".join(str(packet.get("quote_text") or "").split())
        context = reply_context(
            "What did this mean?",
            quoted_post={"post_id": quote_id, "author_role": "account", "text": text},
        )
        resolved = real_repository.resolve_context_quotation(context)
        assert resolved is not None, quote_id
        assert resolved["quote_id"] == quote_id
        assert resolved["matched_context_section"] == "quoted_post"
        assert len(resolved["resolved_context_hash"]) == 64


def test_incoming_quotation_overrides_different_inherited_account_quotation(
    real_repository: EvidenceRepository,
) -> None:
    first_quote_id, second_quote_id = sorted(real_repository.packets)[:2]
    first_text = str(real_repository.packets[first_quote_id]["verified_text"])
    second_text = str(real_repository.packets[second_quote_id]["verified_text"])
    context = reply_context(
        f'What did Margaret Thatcher mean by this passage: "{second_text}"?',
        quoted_post={
            "post_id": first_quote_id,
            "author_role": "account",
            "text": first_text,
        },
    )

    resolved = real_repository.resolve_context_quotation(context)

    assert resolved is not None
    assert resolved["quote_id"] == second_quote_id
    assert resolved["matched_context_section"] == "incoming_contribution"


def test_external_quoted_canonical_quotation_can_be_verified(
    real_repository: EvidenceRepository,
) -> None:
    quote_id = sorted(real_repository.packets)[0]
    quote_text = str(real_repository.packets[quote_id]["verified_text"])
    context = reply_context(
        "Did Margaret Thatcher say this?",
        quoted_post={
            "post_id": "third-party-post",
            "author_role": "other",
            "text": quote_text,
        },
    )

    resolved = real_repository.resolve_context_quotation(context)

    assert resolved is not None
    assert resolved["quote_id"] == quote_id
    assert resolved["matched_context_section"] == "quoted_post"


def test_resolved_packet_passages_are_preferred_without_claiming_entailment(
    real_repository: EvidenceRepository,
) -> None:
    quote_id = sorted(real_repository.packets)[0]
    candidates = real_repository.candidate_passages(
        "an otherwise unrelated but non-empty retrieval query",
        maximum_packets=1,
        maximum_passages=8,
        preferred_quote_id=quote_id,
    )

    assert candidates
    assert {passage.quote_id for passage in candidates} == {quote_id}
    assert candidates[0].field in {"verified_text", "quote_text"}


def test_resolved_packet_retrieval_can_be_strictly_confined(
    real_repository: EvidenceRepository,
) -> None:
    quote_id = sorted(real_repository.packets)[0]
    candidates = real_repository.candidate_passages(
        "a broad political query which would normally match several packets",
        maximum_packets=6,
        maximum_passages=24,
        preferred_quote_id=quote_id,
        restrict_to_preferred_quote=True,
    )

    assert candidates
    assert {passage.quote_id for passage in candidates} == {quote_id}


def test_restricted_retrieval_requires_a_known_preferred_quote(
    real_repository: EvidenceRepository,
) -> None:
    with pytest.raises(ValueError, match="known preferred quotation"):
        real_repository.candidate_passages(
            "a non-empty query",
            preferred_quote_id="f" * 64,
            restrict_to_preferred_quote=True,
        )


def test_resolved_quotation_is_supplied_before_proposer_drafting(
    repository: FakeRepository,
) -> None:
    repository.resolved_quotation = {
        "quote_id": "c" * 64,
        "matched_context_section": "quoted_post",
        "match_basis": "exact_text",
        "speaker": "Margaret Thatcher",
        "verified_text": "A verified quotation.",
        "historical_context": "A verified occasion.",
        "resolved_context_hash": "d" * 64,
    }
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(mode="no_reply"),
            "no_reply_reviewer": no_reply_review(),
        },
    )

    assert result.status == "no_reply"
    payload = json.loads(transport.calls[0]["user_prompt"])
    assert payload["resolved_quotation_for_factual_use"]["quote_id"] == "c" * 64
    assert payload["resolved_quotation_for_factual_use"]["speaker"] == "Margaret Thatcher"
    assert result.audit[0]["status"] == "resolved"


def test_wrong_resolved_actor_cannot_receive_reviewer_approval(
    repository: FakeRepository,
) -> None:
    repository.resolved_quotation = {
        "quote_id": "c" * 64,
        "matched_context_section": "incoming_contribution",
        "match_basis": "exact_text",
        "speaker": "Margaret Thatcher",
        "resolved_context_hash": "d" * 64,
    }
    wrong_reply = "Winston Churchill wrote those words."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                reply=wrong_reply,
                claims=[claim(wrong_reply)],
                requested_answer_type="actor",
            ),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(
                factual_claims=[wrong_reply],
                requested_answer_type="actor",
            ),
            "revision_proposer": proposer(
                mode="no_reply",
                no_reply_reason="The authorship correction could not be completed safely.",
            ),
            "revision_no_reply_reviewer": no_reply_review(),
        },
        context=reply_context("Did Winston Churchill write this quotation?"),
    )

    assert result.reply is None
    assert result.reason == "independent_no_reply_confirmed"
    assert any(
        row.get("reason") == "direct_answer_missing_resolved_actor"
        for row in result.audit
    )
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "evidence", "reviewer", "revision_proposer",
        "revision_no_reply_reviewer",
    ]


def test_direct_answer_cannot_be_grounded_in_a_different_quote_packet(
    repository: FakeRepository,
) -> None:
    repository.resolved_quotation = {
        "quote_id": "d" * 64,
        "matched_context_section": "quoted_post",
        "match_basis": "exact_text",
        "speaker": "Margaret Thatcher",
        "resolved_context_hash": "e" * 64,
    }
    reply_text = "People moved from East Germany towards West Germany."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(reply=reply_text, claims=[claim(reply_text)]),
            "evidence": supporting_evidence(repository),
        },
    )

    assert result.reply is None
    assert result.reason == "resolved_quotation_not_supported"
    assert [call["stage"] for call in transport.calls] == ["proposer", "evidence"]
    assert result.audit[-1]["quote_id"] == "d" * 64
    assert repository.candidate_requests[-1]["restrict_to_preferred_quote"] is True


def test_direct_answer_rejects_mixed_target_and_unrelated_packet_evidence(
    repository: FakeRepository,
) -> None:
    repository.resolved_quotation = {
        "quote_id": "c" * 64,
        "matched_context_section": "quoted_post",
        "match_basis": "exact_text",
        "speaker": "Margaret Thatcher",
        "resolved_context_hash": "d" * 64,
    }
    unrelated = replace(
        repository.passage,
        evidence_id="e" * 64,
        source_hash="f" * 64,
        quote_id="9" * 64,
        passage="A different quotation packet contains broadly similar wording.",
        stable_locator="fixture:unrelated",
    )
    repository.passages[unrelated.evidence_id] = unrelated
    repository.candidate_override = [repository.passage, unrelated]
    reply_text = "People moved from East Germany towards West Germany."

    def mixed_evidence(**kwargs: Any) -> object:
        payload = json.loads(kwargs["user_prompt"])
        supplied = payload["claims"][0]
        return {"claims": [{
            "claim_id": supplied["claim_id"],
            "claim_text": supplied["claim_text"],
            "verdict": "supports",
            "evidence": [
                {
                    "evidence_id": repository.passage.evidence_id,
                    "exact_supporting_passage": repository.passage.passage,
                    "relation": "supports",
                },
                {
                    "evidence_id": unrelated.evidence_id,
                    "exact_supporting_passage": unrelated.passage,
                    "relation": "supports",
                },
            ],
            "actor": supplied["actor"],
            "action_or_relationship": supplied["action_or_relationship"],
            "direction_or_polarity": supplied["direction_or_polarity"],
            "date_or_period": supplied["date_or_period"],
            "quantity": supplied["quantity"],
            "explanation": "Both passages were presented as supporting material.",
        }]}

    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(reply=reply_text, claims=[claim(reply_text)]),
            "evidence": mixed_evidence,
        },
    )

    assert result.reply is None
    assert result.reason == "resolved_quotation_evidence_scope_violation"
    assert [call["stage"] for call in transport.calls] == ["proposer", "evidence"]


def test_unresolved_historical_question_retains_broad_claim_retrieval(
    repository: FakeRepository,
) -> None:
    reply_text = "People moved from East Germany towards West Germany."
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(reply=reply_text, claims=[claim(reply_text)]),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(factual_claims=[reply_text]),
        },
        context=reply_context("Where did people move when the Berlin Wall fell?"),
    )

    assert result.reply is not None
    assert repository.candidate_requests[-1]["restrict_to_preferred_quote"] is False


@pytest.mark.parametrize(
    "reply_text",
    [
        "Voluntary exchange across borders advances interests on both sides when quality and price prevail.",
        "Economic reliance on the state weakens the scope for genuine opposition.",
        "Firm adherence to consistent policy often delivers stability where reversals breed uncertainty.",
        "Repeated economic collapses under socialist regimes provide ample reason to reject it.",
        "Endless blame for ancestral deeds leaves no society room to advance.",
    ],
)
def test_claim_auditor_forces_hidden_world_claims_into_revision(
    repository: FakeRepository,
    reply_text: str,
) -> None:
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply=reply_text,
                claims=[],
            ),
            "claim_auditor": claim_auditor([reply_text]),
            "revision_proposer": proposer(
                mode="no_reply",
                no_reply_reason="The factual generalisation could not be supported safely.",
            ),
            "revision_no_reply_reviewer": no_reply_review(),
        },
    )

    assert result.reply is None
    assert result.reason == "independent_no_reply_confirmed"
    assert any(
        row.get("stage") == "claim_auditor"
        and row.get("factual_claim_count") == 1
        for row in result.audit
    )
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor", "revision_proposer",
        "revision_no_reply_reviewer",
    ]


def test_non_factual_sentence_basis_must_match_its_classification() -> None:
    review = reviewer(direct_question=False, answers_first=False, factual_claims=[])
    review["sentence_assessments"][0]["non_factual_basis"] = "none"

    with pytest.raises(ValueError, match="non-factual basis"):
        validate_reviewer(
            review,
            maximum_claims=6,
            proposed_reply="A principle should be defended.",
        )


def test_reviewer_world_claim_checks_must_match_sentence_classification() -> None:
    review = reviewer(direct_question=False, answers_first=False, factual_claims=[])
    checks = review["sentence_assessments"][0]["world_claim_checks"]
    checks["asserts_comparison_or_outcome"] = True
    checks["purely_non_factual"] = False

    with pytest.raises(ValueError, match="world-claim checks contradict"):
        validate_reviewer(
            review,
            maximum_claims=6,
            proposed_reply="One course serves the country better.",
        )


def test_reviewer_meaning_claim_has_an_explicit_world_claim_category() -> None:
    reply_text = "The passage means that effort, rather than idleness, brings satisfaction."
    review = reviewer(
        direct_question=True,
        answers_first=True,
        factual_claims=[reply_text],
        requested_answer_type="meaning",
    )
    assessment = review["sentence_assessments"][0]
    assessment["sentence_text"] = reply_text
    checks = assessment["world_claim_checks"]
    checks["asserts_actor_state_or_action"] = False
    checks["asserts_meaning_or_attribution"] = True
    review["direct_answer_text"] = reply_text

    validated = validate_reviewer(
        review,
        maximum_claims=6,
        proposed_reply=reply_text,
    )

    assert validated["sentence_assessments"][0]["world_claim_checks"][
        "asserts_meaning_or_attribution"
    ] is True


def test_substantive_reviewer_contradiction_is_not_retried(
    repository: FakeRepository,
) -> None:
    reply_text = "A policy serves the country better."
    bad_review = reviewer(direct_question=False, answers_first=False, factual_claims=[])
    bad_review["sentence_assessments"] = [{
        "sentence_text": reply_text,
        "classification": "checkable_generalisation",
        "factual_claims": [reply_text],
        "non_factual_basis": "none",
        "world_claim_checks": {
            "asserts_actor_state_or_action": False,
            "asserts_causal_or_predictive_relation": False,
            "asserts_comparison_or_outcome": True,
            "asserts_historical_date_or_quantity": False,
            "asserts_meaning_or_attribution": False,
            "purely_non_factual": False,
        },
    }]
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply=reply_text,
                claims=[],
            ),
            "claim_auditor": claim_auditor(),
            "reviewer": [
                bad_review,
                reviewer(direct_question=False, answers_first=False, factual_claims=[]),
            ],
        },
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "reviewer_invalid"
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor", "reviewer",
    ]
    assert result.audit[-1]["retry_suppressed"] is True
    assert result.audit[-1]["corrective_retry_applied"] is False
    reviewer_call = transport.calls[-1]
    assert "validation_correction" not in json.loads(reviewer_call["user_prompt"])


def test_reviewer_approval_with_failed_safety_finding_is_not_retried(
    repository: FakeRepository,
) -> None:
    reply_text = "Thank you for the wholly synthetic contribution."
    unsafe_approval = reviewer(
        direct_question=False,
        answers_first=False,
        topically_relevant=False,
    )

    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="courtesy",
                reply=reply_text,
                claims=[],
            ),
            "reviewer": unsafe_approval,
        },
        context=reply_context("A wholly synthetic civil contribution."),
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "reviewer_invalid"
    reviewer_calls = [call for call in transport.calls if call["stage"] == "reviewer"]
    assert len(reviewer_calls) == 1
    assert result.audit[-1]["retry_suppressed"] is True
    assert result.audit[-1]["corrective_retry_applied"] is False
    assert "validation_correction" not in json.loads(
        reviewer_calls[0]["user_prompt"]
    )


def test_substantive_claim_auditor_contradiction_is_not_retried(
    repository: FakeRepository,
) -> None:
    reply_text = "A policy serves the country better."
    bad_audit = claim_auditor()
    assessment = bad_audit["sentence_assessments"][0]
    assessment["sentence_text"] = reply_text
    checks = assessment["world_claim_checks"]
    checks["asserts_comparison_or_outcome"] = True
    checks["purely_non_factual"] = False
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply=reply_text,
                claims=[],
            ),
            "claim_auditor": [bad_audit, claim_auditor()],
        },
    )

    assert result.reply is None
    assert result.status == "operational_failure"
    assert result.reason == "claim_auditor_invalid"
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor",
    ]
    assert result.audit[-1]["retry_suppressed"] is True
    assert result.audit[-1]["corrective_retry_applied"] is False
    claim_auditor_call = transport.calls[-1]
    assert "validation_correction" not in json.loads(claim_auditor_call["user_prompt"])


def test_revision_cannot_launder_a_previously_identified_world_claim(
    repository: FakeRepository,
) -> None:
    reply_text = "Advancement through merit serves everyone better."
    result, transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                mode="opinion_or_principle",
                reply=reply_text,
                claims=[],
            ),
            "claim_auditor": claim_auditor([reply_text]),
            "revision_proposer": proposer(
                mode="opinion_or_principle",
                reply=reply_text,
                claims=[],
            ),
        },
    )

    assert result.reply is None
    assert result.reason == "revision_dropped_previously_identified_factual_claim"
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor", "revision_proposer",
    ]


def test_reviewer_must_account_for_every_exact_sentence_and_factual_clause() -> None:
    review = reviewer(direct_question=False, answers_first=False, factual_claims=[])
    review["sentence_assessments"] = [{
        "sentence_text": "Resolve matters.",
        "classification": "factual_claim",
        "factual_claims": [],
        "non_factual_basis": "none",
        "world_claim_checks": {
            "asserts_actor_state_or_action": False,
            "asserts_causal_or_predictive_relation": False,
            "asserts_comparison_or_outcome": False,
            "asserts_historical_date_or_quantity": False,
            "asserts_meaning_or_attribution": False,
            "purely_non_factual": True,
        },
    }]
    with pytest.raises(ValueError, match="classification contradicts"):
        validate_reviewer(
            review,
            maximum_claims=6,
            proposed_reply="Resolve matters. Evidence decides the factual issue.",
        )
    review["sentence_assessments"][0] = {
        "sentence_text": "Resolve matters.",
        "classification": "opinion_or_value_judgement",
        "factual_claims": [],
        "non_factual_basis": "normative_judgement",
        "world_claim_checks": {
            "asserts_actor_state_or_action": False,
            "asserts_causal_or_predictive_relation": False,
            "asserts_comparison_or_outcome": False,
            "asserts_historical_date_or_quantity": False,
            "asserts_meaning_or_attribution": False,
            "purely_non_factual": True,
        },
    }
    with pytest.raises(ValueError, match="does not cover the exact reply"):
        validate_reviewer(
            review,
            maximum_claims=6,
            proposed_reply="Resolve matters. Evidence decides the factual issue.",
        )


def test_reviewer_direct_answer_metadata_must_match_the_proposer(
    repository: FakeRepository,
) -> None:
    reply_text = "People moved from East Germany towards West Germany."
    result, _transport = run_pipeline(
        repository,
        {
            "proposer": proposer(
                reply=reply_text,
                claims=[claim(reply_text)],
                requested_answer_type="location",
            ),
            "evidence": supporting_evidence(repository),
            "reviewer": reviewer(
                factual_claims=[reply_text],
                requested_answer_type="action",
            ),
            "revision_proposer": proposer(
                mode="no_reply",
                no_reply_reason="The direct answer could not be reconciled safely.",
            ),
            "revision_no_reply_reviewer": no_reply_review(),
        },
    )

    assert result.reply is None
    assert any(
        row.get("reason") == "direct_answer_metadata_mismatch"
        for row in result.audit
    )


def test_real_repository_retrieval_is_candidate_selection_not_entailment(
    real_repository: EvidenceRepository,
) -> None:
    candidates = real_repository.candidate_passages(
        "Berlin Wall East Germany West Germany",
        maximum_packets=6,
        maximum_passages=24,
    )
    assert candidates
    assert all(isinstance(item, EvidencePassage) for item in candidates)
    assert not hasattr(candidates[0], "verdict")


def test_real_repository_includes_source_grounded_berlin_wall_fact(
    real_repository: EvidenceRepository,
) -> None:
    candidates = real_repository.candidate_passages(
        "Where did East Germans go when the Berlin Wall fell in November 1989?",
        maximum_packets=6,
        maximum_passages=24,
    )
    factual = [item for item in candidates if item.field == "factual_evidence"]
    federal_caption = next(
        item for item in factual if item.source_title.startswith("9 November")
    )

    assert real_repository.factual_evidence_count == 2
    assert len(factual) == 2
    assert federal_caption.passage == (
        "The first East Germans make it by car to West Berlin’s Kurfürstendamm "
        "where they are given an exuberant welcome."
    )
    assert federal_caption.direction_or_polarity == "from East Germany towards West Berlin"
    assert all(item.verification_status == "official_source_exact" for item in factual)


def test_misspelled_berlin_claim_retrieves_official_fact_first(
    real_repository: EvidenceRepository,
) -> None:
    candidates = real_repository.candidate_passages(
        "People ran westwards across the former border when the Berlin Wall fell. "
        "Were did people ram towards when the Berlin Wall fell?",
        maximum_packets=6,
        maximum_passages=24,
    )

    assert candidates[0].field == "factual_evidence"
    assert candidates[0].actor in {"East Germans", "East Berliners"}


def test_east_berliners_claim_retrieves_actor_specific_official_fact(
    real_repository: EvidenceRepository,
) -> None:
    candidates = real_repository.candidate_passages(
        "East Berliners crossed into West Berlin when the Berlin Wall fell.",
        maximum_packets=6,
        maximum_passages=24,
    )

    assert candidates[0].field == "factual_evidence"
    assert candidates[0].actor == "East Berliners"
    assert "East Berliners to cross to the West" in candidates[0].passage


@pytest.mark.parametrize(
    ("contribution", "clarification_request"),
    [
        ("Where did people move when the Berlin Wall fell?", None),
        ("Were did people ram towards when the Berlin Wall fell?", None),
        (
            "Yeah but that's not what I asked, on which side did people go?",
            {
                "original_question": "Were did people ram towards when the Berlin Wall fell?",
                "correction": "Yeah but that's not what I asked, on which side did people go?",
            },
        ),
    ],
)
def test_real_source_grounded_berlin_answer_can_pass_the_complete_pipeline(
    real_repository: EvidenceRepository,
    contribution: str,
    clarification_request: dict[str, str] | None,
) -> None:
    factual = next(
        passage
        for passage in real_repository.candidate_passages(
            f"{contribution} East Germany West Berlin November 1989",
            maximum_packets=6,
            maximum_passages=24,
        )
        if passage.field == "factual_evidence"
    )
    reply_text = "East Germans moved towards West Berlin when the Wall opened."
    proposal_claim = {
        "claim_id": "claim-1",
        "claim_text": reply_text,
        "requires_evidence": True,
        "actor": "East Germans",
        "action_or_relationship": "moved across the opened border",
        "direction_or_polarity": "from East Germany towards West Berlin",
        "date_or_period": "November 1989",
        "quantity": "",
    }

    def evidence_response(**kwargs: Any) -> object:
        supplied = json.loads(kwargs["user_prompt"])["claims"][0]
        return {"claims": [{
            "claim_id": supplied["claim_id"],
            "claim_text": supplied["claim_text"],
            "verdict": "supports",
            "evidence": [{
                "evidence_id": factual.evidence_id,
                "exact_supporting_passage": factual.passage,
                "relation": "supports",
            }],
            "actor": supplied["actor"],
            "action_or_relationship": supplied["action_or_relationship"],
            "direction_or_polarity": supplied["direction_or_polarity"],
            "date_or_period": supplied["date_or_period"],
            "quantity": supplied["quantity"],
            "explanation": "The official caption establishes east-to-west movement.",
        }]}

    transport = ScriptedTransport({
        "proposer": proposer(reply=reply_text, claims=[proposal_claim]),
        "evidence": evidence_response,
        "reviewer": reviewer(
            factual_claims=[proposal_claim["claim_text"]],
        ),
    })
    context = reply_context(
        contribution,
        clarification_request=clarification_request,
    )
    result = run_reply_pipeline(
        context=context,
        config=strategy_config(),
        repository=real_repository,
        transport=transport,
        maximum_reply_length=270,
        recent_replies=[],
        media_context=None,
        creation_time="2026-07-20T00:00:00Z",
    )

    assert str(result.reply) == reply_text
    assert result.reason == "reviewer_approved"
    assert result.reply.draft_record["evidence_ids"] == [factual.evidence_id]
    assert "Relevant factual questions about political history" in transport.calls[0]["system_prompt"]
    assert "draft cannot be posted unless that stage finds exact local support" in transport.calls[0]["system_prompt"]
    assert "least-specific factual wording" in transport.calls[0]["system_prompt"]
    assert "Never omit a genuine claim merely to avoid evidence review" in transport.calls[0]["system_prompt"]
    assert "A normative wrapper does not hide a factual premise" in transport.calls[0]["system_prompt"]
    assert "defence rather than defense" in transport.calls[0]["system_prompt"]
    assert "ordinary paraphrases" in transport.calls[1]["system_prompt"]


def test_claim_audit_prompts_treat_historical_record_as_a_factual_premise() -> None:
    auditor_system, _ = _claim_auditor_prompts(
        "A nation's record in defending liberty supplies its own reason to stand firm."
    )
    reviewer_system, _ = _reviewer_prompts(
        reply_context("I disagree. Why should anyone accept that?"),
        proposer(
            reply="A nation's record in defending liberty supplies its own reason to stand firm.",
            claims=[],
        ),
        [],
        None,
    )

    assert "record or history of an actor doing something" in auditor_system
    assert "copy those exact contiguous clauses verbatim" in auditor_system
    assert "action-bearing relative clause" in reviewer_system
    assert "record or history of an actor doing something" in reviewer_system
    assert "request revision for an American spelling" in reviewer_system


def test_claim_retrieval_query_uses_contribution_but_not_inherited_context() -> None:
    context = reply_context(
        "Where did people move when the Berlin Wall fell?",
        quoted_post={"text": "UNRELATED QUOTED CONTAMINATION"},
        parent_thread=[{"author": "other", "text": "UNRELATED PARENT CONTAMINATION"}],
    )

    query = claim_retrieval_query(claim(), context)

    assert "Berlin Wall" in query
    assert "UNRELATED QUOTED CONTAMINATION" not in query
    assert "UNRELATED PARENT CONTAMINATION" not in query


def test_only_the_five_new_modes_exist() -> None:
    assert MODES == {
        "direct_factual_answer", "opinion_or_principle", "light_humour", "courtesy", "no_reply",
    }
    assert not MODES & {
        "wry_reply", "deadpan_reply", "warm_reply", "principle_reply", "researched_principle",
    }

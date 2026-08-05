from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import validate as validate_json_schema

from reply_evidence import EvidencePassage, EvidenceRepository
from reply_strategy import (
    AIReply,
    CLAIM_AUDITOR_PROMPT_VERSION,
    DRAFT_SCHEMA_VERSION,
    EVIDENCE_PROMPT_VERSION,
    MODES,
    PROPOSER_PROMPT_VERSION,
    REVIEWER_PROMPT_VERSION,
    STRATEGY_VERSION,
    _claim_auditor_prompts,
    _proposer_prompts,
    _reviewer_prompts,
    claim_auditor_schema,
    claim_retrieval_query,
    deterministic_reply_error,
    evidence_telemetry,
    evidence_schema,
    legacy_draft_audit,
    proposer_schema,
    reviewer_schema,
    run_reply_pipeline,
    sentence_count,
    split_reply_sentences,
    validate_evidence_response,
    validate_claim_auditor,
    validate_persisted_draft,
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


def run_pipeline(
    repository: FakeRepository,
    responses: dict[str, object | list[object] | Callable[..., object]],
    *,
    context: dict[str, object] | None = None,
    config: dict[str, object] | None = None,
) -> tuple[object, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    result = run_reply_pipeline(
        context=context or reply_context(),
        config=config or strategy_config(),
        repository=repository,  # type: ignore[arg-type]
        transport=transport,
        maximum_reply_length=500,
        recent_replies=[],
        creation_time="2026-07-20T12:00:00Z",
    )
    return result, transport


def test_source_schemas_are_strict_and_provider_compatible() -> None:
    proposal = proposer(mode="courtesy", reply="Thank you for saying so.", claims=[])
    audit = claim_auditor()
    review = reviewer(direct_question=False, answers_first=False)
    validate_json_schema(proposal, proposer_schema(500, 6))
    validate_json_schema(audit, claim_auditor_schema(6))
    validate_json_schema(review, reviewer_schema(6))
    assert proposer_schema(500, 6)["additionalProperties"] is False
    assert evidence_schema(6, 24)["additionalProperties"] is False
    assert claim_auditor_schema(6)["additionalProperties"] is False
    assert reviewer_schema(6)["additionalProperties"] is False


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
    assert validate_strategy_config(strategy_config(maximum_model_calls=9))
    assert validate_strategy_config(strategy_config(maximum_model_calls=7))
    assert validate_strategy_config(strategy_config(proposer_timeout_seconds=121))
    assert validate_strategy_config(strategy_config(evidence_max_output_tokens=4001))
    assert validate_strategy_config({**strategy_config(), "legacy_mode": True})


def test_conversational_engagement_prompt_versions_are_current() -> None:
    assert PROPOSER_PROMPT_VERSION == "ai-first-proposer-v15"
    assert REVIEWER_PROMPT_VERSION == "independent-reply-reviewer-v13"


def test_proposer_prompt_defaults_to_safe_relevant_engagement() -> None:
    system, _ = _proposer_prompts(
        reply_context("A civil and relevant contribution."),
        [],
        resolved_quotation=None,
        revision=None,
    )

    assert "exists to engage civil, relevant people, not merely to answer factual questions" in system
    assert "need not contain a question, disagreement, challenge, new factual claim or new subject matter" in system
    assert "Relevant agreement, support, appreciation, admiration, nostalgia, sadness, thanks" in system
    assert "a friendly comparison, a concise reaction or a thoughtful related observation" in system
    assert "A quote-tweet is a first-class contribution" in system
    assert "Use courtesy for acknowledgement, warmth, appreciation or thanks" in system
    assert "Use opinion_or_principle when adding a directly related value judgement" in system
    assert "underlying political, moral or policy theme using claim-free normative language" in system
    assert "absence of a question or new matter is never sufficient by itself for no_reply" in system
    assert "Prefer no_reply to an unrelated platitude." not in system


def test_proposer_revision_prefers_claim_free_social_engagement() -> None:
    system, _ = _proposer_prompts(
        reply_context("A civil observation containing an unsupported prediction."),
        [],
        resolved_quotation=None,
        revision={"review_findings": "Remove the unsupported prediction."},
    )

    assert "claim-free acknowledgement, value judgement or directly relevant explicit recommendation" in system
    assert "rather than automatically choosing no_reply" in system


def test_reviewer_prompt_accepts_safe_social_engagement_without_weakening_safeguards() -> None:
    system, _ = _reviewer_prompts(
        reply_context("I miss her."),
        proposer(mode="courtesy", reply="That affection still speaks warmly.", claims=[]),
        [],
        None,
    )

    assert "Absence of a question, challenge or new factual matter is not a defect" in system
    assert "concise, topically relevant courtesy response is suitable account behaviour" in system
    assert "acknowledgement of agreement, support, admiration, nostalgia, thanks or a friendly comparison" in system
    assert "claim-free extension of a thoughtful related observation" in system
    assert "Quote-tweets are first-class engagement" in system
    assert "Do not confuse brevity with an unrelated platitude" in system
    assert "prefer revise into claim-free normative language rather than reject" in system
    assert "dangerous amplification of unsupported accusations" in system
    assert "Classify causal, comparative, predictive and habitual political generalisations as checkable" in system
    assert "endorses an unsupported allegation, lacks evidence" in system


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


def test_no_reply_uses_only_one_proposer_call(repository: FakeRepository) -> None:
    result, transport = run_pipeline(repository, {"proposer": proposer(mode="no_reply")})
    assert result.reply is None
    assert result.status == "no_reply"
    assert result.model_call_count == 1
    assert [call["stage"] for call in transport.calls] == ["proposer"]


def test_one_invalid_proposer_response_is_retried_then_accepted(
    repository: FakeRepository,
) -> None:
    result, transport = run_pipeline(
        repository,
        {"proposer": ["", proposer(mode="no_reply")]},
    )

    assert result.reply is None
    assert result.reason == "No useful and safe reply is warranted."
    assert result.model_call_count == 2
    assert [call["stage"] for call in transport.calls] == ["proposer", "proposer"]
    assert [row["status"] for row in result.audit] == [
        "not_resolved",
        "invalid_response_retry",
        "completed",
    ]


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
    assert any(
        row["stage"] == "evidence" and row["status"] == "invalid_response_retry"
        for row in result.audit
    )


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
    assert any(
        row["stage"] == "reviewer" and row["status"] == "invalid_response_retry"
        for row in result.audit
    )


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
        "Questions about motives, values, political principles or moral concepts normally use "
        "opinion_or_principle"
        in proposer_call["system_prompt"]
    )
    assert (
        "begin the proposed reply with that clear answer in the first sentence"
        in proposer_call["system_prompt"]
    )

    reviewer_call = transport.calls[-1]
    reviewer_payload = json.loads(reviewer_call["user_prompt"])
    assert reviewer_payload["mode"] == "opinion_or_principle"
    assert reviewer_payload["proposer_direct_factual_question_present"] is False
    assert (
        "Do not require direct_factual_question_present merely because such a principle "
        "question is interrogative"
        in reviewer_call["system_prompt"]
    )
    assert (
        "require the proposed reply to begin with that clear answer in the first sentence"
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
        },
        context=reply_context("Government must show resolve on inflation."),
    )

    assert result.reply is None
    assert result.reason == "The unsupported factual claim cannot be repaired safely."
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor", "revision_proposer",
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
            scripted = {"proposer": proposer(mode="no_reply")}
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
        {"proposer": proposer(mode="no_reply")},
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
        },
        context=reply_context("Did Winston Churchill write this quotation?"),
    )

    assert result.reply is None
    assert result.reason == "The authorship correction could not be completed safely."
    assert any(
        row.get("reason") == "direct_answer_missing_resolved_actor"
        for row in result.audit
    )
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "evidence", "reviewer", "revision_proposer",
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
        },
    )

    assert result.reply is None
    assert result.reason == "The factual generalisation could not be supported safely."
    assert any(
        row.get("stage") == "claim_auditor"
        and row.get("factual_claim_count") == 1
        for row in result.audit
    )
    assert [call["stage"] for call in transport.calls] == [
        "proposer", "claim_auditor", "revision_proposer",
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
    assert "prefer a clearly rhetorical quip or question" in transport.calls[0]["system_prompt"]
    assert "Never omit a genuine claim merely to avoid evidence review" in transport.calls[0]["system_prompt"]
    assert "civil challenge to a clear political or moral principle is likewise in scope" in transport.calls[0]["system_prompt"]
    assert "do not assume that this account never engages with civil disagreement" in transport.calls[0]["system_prompt"]
    assert "names and addresses the specific disputed principle" in transport.calls[0]["system_prompt"]
    assert "bounded thread explicitly shows that this account has already answered" in transport.calls[0]["system_prompt"]
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

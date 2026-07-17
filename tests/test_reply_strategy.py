from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema

import mrsMThatcher2 as bot
from reply_strategy import (
    DEFAULT_REPLY_STRATEGY,
    RetrievedEvidence,
    ReplyDecision,
    audit_digest,
    build_strategy_prompt_context,
    concrete_factual_question_word,
    decision_schema_instruction,
    direct_factual_answer_error,
    direct_question_prompt_guidance,
    normalise_reply_decision,
    parse_decision_json,
    principle_reply_assertion_error,
    reply_decision_json_schema,
    reply_is_repetitive,
    strategy_mode_guidance,
    retrieve_research_packets,
    validate_reply_decision,
    validate_reply_strategy_config,
)

RESEARCH = Path("semantic_alignment_research/quote_research_full_001")


def decision(**overrides):
    value = {
        "mode": "wry_reply", "humour_tone": "wry", "evidence_confidence": "none",
        "retrieved_quote_ids": [], "evidence_summary": "", "factual_claim_made": False,
        "grounded": False, "reply_text": "A tidy theory. Reality may request amendments.",
        "no_reply_reason": "",
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (json.dumps(decision()), decision()["reply_text"]),
        ("not structured JSON", None),
        (json.dumps(decision(mode=[])), None),
    ],
)
def test_strategy_reply_handles_omitted_media_context(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    expected: str | None,
) -> None:
    response = bot.requests.Response()
    response.status_code = 200
    response._content = json.dumps({
        "choices": [{"message": {"content": content}}],
    }).encode("utf-8")
    monkeypatch.setattr(bot.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        bot,
        "reply_strategy",
        {
            **bot.reply_strategy,
            "enabled": True,
            "research_corpus_enabled": False,
        },
    )

    result = bot.ask_grok_for_reply("Incoming post: A concise observation.")

    assert result == expected


def test_strategy_prompt_never_instructs_provider_to_return_bare_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = bot.requests.Response()
    response.status_code = 200
    response._content = json.dumps({
        "choices": [{"message": {"content": json.dumps(decision(
            mode="no_reply",
            humour_tone="none",
            reply_text="",
            no_reply_reason="No useful response.",
        ))}}],
    }).encode("utf-8")
    captured: dict = {}

    def post(*_args, **kwargs):
        captured.update(kwargs["json"])
        return response

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(
        bot,
        "reply_strategy",
        {**bot.reply_strategy, "enabled": True, "research_corpus_enabled": False},
    )

    assert bot.ask_grok_for_reply("Incoming post: Nothing to add.") is None
    prompt_text = json.dumps(captured["messages"], ensure_ascii=False)
    assert "Return exactly SKIP" not in prompt_text
    assert "never output bare SKIP" in prompt_text
    assert "Valid no_reply example" in prompt_text


def test_strategy_request_uses_conditional_structured_output_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = bot.requests.Response()
    response.status_code = 200
    response._content = json.dumps({
        "choices": [{"message": {"content": json.dumps(decision(
            mode="no_reply",
            humour_tone="none",
            reply_text="",
            no_reply_reason="No useful response.",
        ))}}],
    }).encode("utf-8")
    captured: dict = {}

    def post(*_args, **kwargs):
        captured.update(kwargs["json"])
        return response

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(
        bot,
        "reply_strategy",
        {
            **bot.reply_strategy,
            "enabled": True,
            "research_corpus_enabled": False,
            "maximum_retrieved_packets": 7,
        },
    )

    assert bot.ask_grok_for_reply("Incoming post: Nothing to add.") is None
    response_format = captured["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["if"]["properties"]["mode"] == {"const": "no_reply"}
    assert schema["then"]["properties"]["reply_text"] == {"maxLength": 0}
    assert schema["properties"]["retrieved_quote_ids"]["maxItems"] == 7
    principle_rule = schema["allOf"][0]
    assert principle_rule["if"]["properties"]["mode"] == {"const": "principle_reply"}
    assert principle_rule["then"]["properties"]["factual_claim_made"] == {"const": False}


def test_strategy_model_response_accepts_principle_reply_without_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = decision(
        mode="principle_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text="The case still has to be made and acted upon.",
    )
    response = bot.requests.Response()
    response.status_code = 200
    response._content = json.dumps({
        "choices": [{"message": {"content": json.dumps(value)}}],
    }).encode("utf-8")
    monkeypatch.setattr(bot.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        bot,
        "reply_strategy",
        {**bot.reply_strategy, "enabled": True, "research_corpus_enabled": False},
    )

    result = bot.ask_grok_for_reply(
        "Incoming post/comment to answer:\nWhy doesn't anyone in government understand this?",
        direct_question_text="Why doesn't anyone in government understand this?",
    )

    assert result == value["reply_text"]
    assert isinstance(result, ReplyDecision)
    assert result.strategy_metadata["mode"] == "principle_reply"


def test_strategy_schema_defines_factual_claim_flag_for_historical_modes() -> None:
    guidance = strategy_mode_guidance()
    assert "factual_claim_made=true" in guidance
    assert "final reply states a historical or policy fact" in guidance


def test_strategy_reply_classifies_non_object_provider_json_as_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = bot.requests.Response()
    response.status_code = 200
    response._content = b"[]"
    monkeypatch.setattr(bot.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        bot,
        "reply_strategy",
        {
            **bot.reply_strategy,
            "enabled": True,
            "research_corpus_enabled": False,
        },
    )

    with pytest.raises(bot.ApiError, match="JSON object"):
        bot.ask_grok_for_reply("Incoming post: A concise observation.")


def test_reply_classifies_non_string_provider_content_as_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = bot.requests.Response()
    response.status_code = 200
    response._content = json.dumps({
        "choices": [{"message": {"content": 123}}],
    }).encode("utf-8")
    monkeypatch.setattr(bot.requests, "post", lambda *args, **kwargs: response)
    monkeypatch.setattr(bot, "reply_strategy", {**bot.reply_strategy, "enabled": False})

    with pytest.raises(bot.ApiError, match="content must be a string"):
        bot.ask_grok_for_reply("Incoming post: A concise observation.")


def test_retrieval_uses_only_completed_packets_and_is_deterministic():
    first = retrieve_research_packets("Government creates wealth and prosperity", RESEARCH, maximum=5)
    second = retrieve_research_packets("Government creates wealth and prosperity", RESEARCH, maximum=5)
    assert first and [item.quote_id for item in first] == [item.quote_id for item in second]
    assert len(first) <= 5 and all(len(item.quote_id) == 64 for item in first)


def test_prompt_context_does_not_expose_internal_prose_or_unresolved_records():
    evidence = retrieve_research_packets("free enterprise and government", RESEARCH, maximum=3)
    payload = json.loads(build_strategy_prompt_context(evidence))
    assert len(payload) <= 3
    assert all(set(item) == {"quote_id", "verification_status", "research_confidence", "source_event", "date", "immediate_subject", "intended_argument", "broader_principle", "mechanism", "claimed_consequence", "verified_text"} for item in payload)


def test_historical_correction_precedes_humour_and_requires_high_confidence():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=2)
    qid = evidence[0].quote_id
    valid = validate_reply_decision(decision(
        mode="historical_correction", humour_tone="dry", evidence_confidence="high",
        retrieved_quote_ids=[qid], evidence_summary="Government sets conditions rather than creating wealth.",
        factual_claim_made=True, grounded=True,
        reply_text="Government may set the conditions for prosperity; it does not manufacture prosperity itself.",
    ), evidence, allowed_quote_ids={item.quote_id for item in evidence})
    assert valid["mode"] == "historical_correction"
    with pytest.raises(ValueError, match="requires high confidence"):
        validate_reply_decision({**valid, "evidence_confidence": "medium"}, evidence, allowed_quote_ids={item.quote_id for item in evidence})


def test_berlin_wall_question_requires_a_direct_east_to_west_answer() -> None:
    question = "Where did people run towards when the Berlin Wall fell?"
    evidence = retrieve_research_packets(question, RESEARCH, maximum=5)
    selected = evidence[:2]
    ids = [item.quote_id for item in selected]
    value = decision(
        mode="historical_context",
        humour_tone="none",
        evidence_confidence="high",
        retrieved_quote_ids=ids,
        evidence_summary="The Berlin Wall divided the communist East from the free West.",
        factual_claim_made=True,
        grounded=True,
        reply_text="People moved from East Berlin and East Germany towards West Berlin and West Germany.",
    )

    result = validate_reply_decision(
        value,
        evidence,
        allowed_quote_ids={item.quote_id for item in evidence},
        direct_question_text=question,
    )

    assert result["reply_text"].startswith("People moved from East Berlin")
    assert direct_factual_answer_error(question, result["reply_text"]) is None


def test_misspelled_production_berlin_wall_question_requires_direct_answer() -> None:
    question = "Were did people ram towards when the Berlin Wall fell?"
    reply = "People moved from East Berlin and East Germany towards West Berlin and West Germany."
    evidence = retrieve_research_packets("Berlin Wall East West Germany", RESEARCH, maximum=5)
    selected = evidence[0]

    assert concrete_factual_question_word(question) == "where"
    result = validate_reply_decision(
        decision(
            mode="historical_context",
            humour_tone="none",
            evidence_confidence="high",
            retrieved_quote_ids=[selected.quote_id],
            evidence_summary="The Berlin Wall divided East Berlin from West Berlin.",
            factual_claim_made=True,
            grounded=True,
            reply_text=reply,
        ),
        evidence,
        allowed_quote_ids={item.quote_id for item in evidence},
        direct_question_text=question,
    )

    assert result["reply_text"] == reply
    assert direct_factual_answer_error(question, reply) is None


def test_berlin_wall_abstract_non_answer_is_rejected() -> None:
    question = "Where did people run towards when the Berlin Wall fell?"
    evidence = retrieve_research_packets(question, RESEARCH, maximum=5)
    selected = evidence[0]
    with pytest.raises(ValueError, match="abstract principle"):
        validate_reply_decision(
            decision(
                mode="historical_context",
                humour_tone="none",
                evidence_confidence="high",
                retrieved_quote_ids=[selected.quote_id],
                evidence_summary="People rejected communist rule.",
                factual_claim_made=True,
                grounded=True,
                reply_text="When free to choose, people choose freedom.",
            ),
            evidence,
            allowed_quote_ids={item.quote_id for item in evidence},
            direct_question_text=question,
        )


def test_concrete_question_guidance_forbids_rhetorical_substitution() -> None:
    guidance = direct_question_prompt_guidance(
        "Where did people run towards when the Berlin Wall fell?",
        clarification=True,
    )
    assert "directly in the first sentence" in guidance
    assert "Do not substitute an ideological summary" in guidance
    assert "one permitted clarification repair" in guidance


def test_clarification_without_grounded_packets_makes_no_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bot,
        "reply_strategy",
        {**bot.reply_strategy, "enabled": True, "research_corpus_enabled": False},
    )
    monkeypatch.setattr(
        bot.requests,
        "post",
        lambda *_args, **_kwargs: pytest.fail("xAI must not be called without repair evidence"),
    )
    outcome: dict[str, str] = {}

    result = bot.ask_grok_for_reply(
        "A correction in the same thread.",
        direct_question_text="Where did people run towards when the Berlin Wall fell?",
        clarification_reply=True,
        evaluation_outcome=outcome,
    )

    assert result is None
    assert outcome == {
        "status": "no_reply",
        "reason": "clarification_insufficient_grounded_evidence",
    }


def test_historical_correction_rejects_low_confidence_packet():
    low = RetrievedEvidence(
        "b" * 64, 1.0, "exact", "Low-confidence evidence.",
        {"verified_text": "Exact words.", "research_confidence": "low"},
    )
    with pytest.raises(ValueError, match="packet confidence"):
        validate_reply_decision(decision(
            mode="historical_correction", humour_tone="dry", evidence_confidence="high",
            retrieved_quote_ids=[low.quote_id], evidence_summary="Low-confidence evidence.",
            factual_claim_made=True, grounded=True,
            reply_text="That historical claim is not established by the available record.",
        ), [low], allowed_quote_ids={low.quote_id})


def test_humour_remains_available_without_forced_history():
    result = validate_reply_decision(decision(), [], allowed_quote_ids=set())
    assert result["mode"] == "wry_reply" and not result["grounded"]


@pytest.mark.parametrize(
    ("incoming", "reply"),
    [
        (
            "Why doesn't anyone in government understand this?",
            "Understanding is not always the same as having the courage to act.",
        ),
        (
            "The institutions have failed because the government is acting in bad faith.",
            "Institutions endure only when people are prepared to defend their purpose.",
        ),
    ],
)
def test_uncertain_claim_can_receive_a_non_factual_principle_reply(
    incoming: str,
    reply: str,
) -> None:
    result = validate_reply_decision(
        decision(
            mode="principle_reply",
            humour_tone="none",
            evidence_confidence="none",
            reply_text=reply,
        ),
        [],
        allowed_quote_ids=set(),
        direct_question_text=incoming,
    )

    assert result["mode"] == "principle_reply"
    assert result["retrieved_quote_ids"] == []
    assert result["factual_claim_made"] is False
    assert result["grounded"] is False
    assert result["humour_tone"] == "none"


def test_principle_reply_rejects_unsupported_actor_specific_assertion() -> None:
    reply = "The government is deliberately concealing the truth."
    assert principle_reply_assertion_error(reply) is not None
    with pytest.raises(ValueError, match="specific unsupported factual assertion"):
        validate_reply_decision(
            decision(
                mode="principle_reply",
                humour_tone="none",
                evidence_confidence="none",
                reply_text=reply,
            ),
            [],
            allowed_quote_ids=set(),
        )


@pytest.mark.parametrize(
    "reason",
    [
        "no_reply_due_to_unverifiable_claim",
        "no_reply_due_to_bait_or_abuse",
        "no_reply_due_to_incoherent",
    ],
)
def test_stable_editorial_no_reply_categories_remain_valid(reason: str) -> None:
    result = validate_reply_decision(
        decision(
            mode="no_reply",
            humour_tone="none",
            evidence_confidence="none",
            reply_text="",
            no_reply_reason=reason,
        ),
        [],
        allowed_quote_ids=set(),
    )
    assert result["no_reply_reason"] == reason


def test_principle_reply_cannot_smuggle_factual_or_research_metadata() -> None:
    value = decision(
        mode="principle_reply",
        humour_tone="none",
        evidence_confidence="medium",
        factual_claim_made=True,
        reply_text="Institutions require courage.",
    )
    with pytest.raises(JsonSchemaValidationError):
        validate_json_schema(value, reply_decision_json_schema())
    with pytest.raises(ValueError, match="principle_reply requires"):
        validate_reply_decision(
            value,
            [],
            allowed_quote_ids=set(),
        )


def test_principle_reply_receipt_metadata_keeps_the_same_safety_boundary() -> None:
    text = "Responsibility matters most when excuses are easiest."
    metadata = decision(
        mode="principle_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text=text,
    )
    assert bot.strategy_metadata_is_semantically_valid(metadata, text)
    unsafe = {**metadata, "reply_text": "The government is concealing the truth."}
    assert not bot.strategy_metadata_is_semantically_valid(unsafe, unsafe["reply_text"])


def test_weak_factual_evidence_requires_no_reply():
    result = validate_reply_decision(decision(
        mode="no_reply", humour_tone="none", evidence_confidence="none",
        reply_text="", no_reply_reason="The historical claim cannot be grounded confidently.",
    ), [], allowed_quote_ids=set())
    assert result["mode"] == "no_reply"
    with pytest.raises(ValueError, match="factual claims require grounding"):
        validate_reply_decision(decision(factual_claim_made=True), [], allowed_quote_ids=set())


@pytest.mark.parametrize(
    "reply_text",
    [
        'Thatcher said “Women succeed without collective action.”',
        "Thatcher said 'Women succeed without collective action.'",
        "Thatcher said ‘Women succeed without collective action.’",
        "Thatcher said 'Britain's women succeed without collective action.'",
        "Thatcher said ‘Britain’s women succeed without collective action.’",
    ],
)
def test_paraphrase_cannot_be_presented_as_exact_quotation(reply_text: str):
    evidence = retrieve_research_packets("women's liberation", RESEARCH, maximum=5)
    paraphrase = next(item for item in evidence if item.verification_status == "paraphrase")
    with pytest.raises(ValueError, match="quotation marks require"):
        validate_reply_decision(decision(
            mode="researched_principle", evidence_confidence="medium", grounded=True,
            retrieved_quote_ids=[paraphrase.quote_id], evidence_summary="A paraphrased principle.",
            factual_claim_made=True,
            reply_text=reply_text,
        ), evidence, allowed_quote_ids={item.quote_id for item in evidence})


def test_normalised_wording_cannot_be_presented_as_exact_quotation():
    normalised = RetrievedEvidence(
        "a" * 64, 1.0, "normalised", "Normalised wording.",
        {"verified_text": "A normalised sentence.", "research_confidence": "high"},
    )
    evidence = [normalised]
    with pytest.raises(ValueError, match="quotation marks require"):
        validate_reply_decision(decision(
            mode="researched_principle", evidence_confidence="medium", grounded=True,
            retrieved_quote_ids=[normalised.quote_id], evidence_summary="Normalised wording.",
            factual_claim_made=True,
            reply_text='Thatcher said “A normalised sentence.”',
        ), evidence, allowed_quote_ids={item.quote_id for item in evidence})


@pytest.mark.parametrize(
    "reply_text, verified_text",
    [
        ("Thatcher said 'Exact words.'", "Exact words."),
        ("Thatcher said 'Britain's exact words.'", "Britain's exact words."),
        ("Thatcher said ‘Britain’s exact words.’", "Britain’s exact words."),
    ],
)
def test_verified_exact_wording_may_use_single_quotation_marks(
    reply_text: str,
    verified_text: str,
):
    exact = RetrievedEvidence(
        "e" * 64,
        1.0,
        "exact",
        "Exact evidence.",
        {"verified_text": verified_text, "research_confidence": "high"},
    )

    result = validate_reply_decision(
        decision(
            mode="researched_principle",
            evidence_confidence="high",
            grounded=True,
            retrieved_quote_ids=[exact.quote_id],
            evidence_summary="Exact evidence.",
            factual_claim_made=True,
            reply_text=reply_text,
        ),
        [exact],
        allowed_quote_ids={exact.quote_id},
    )

    assert result["reply_text"] == reply_text


def test_apostrophe_in_contraction_is_not_treated_as_quotation():
    result = validate_reply_decision(
        decision(reply_text="Reality's reply remains concise."),
        [],
        allowed_quote_ids=set(),
    )

    assert result["reply_text"] == "Reality's reply remains concise."


def test_no_hashtags_two_sentence_limit_and_repetition_controls():
    with pytest.raises(ValueError, match="hashtags"):
        validate_reply_decision(decision(reply_text="A point. #history"), [], allowed_quote_ids=set())
    with pytest.raises(ValueError, match="one or two sentences"):
        validate_reply_decision(decision(reply_text="One. Two. Three."), [], allowed_quote_ids=set())
    assert reply_is_repetitive("History has a habit of answering that.", [])
    with pytest.raises(ValueError, match="exact duplicate"):
        validate_reply_decision(decision(), [], allowed_quote_ids=set(), recent_replies=[decision()["reply_text"]])


def test_repetition_rejections_identify_exact_similar_and_canned_causes():
    with pytest.raises(ValueError, match="exact duplicate"):
        validate_reply_decision(decision(), [], allowed_quote_ids=set(), recent_replies=[decision()["reply_text"]])
    with pytest.raises(ValueError, match="canned formulation"):
        validate_reply_decision(
            decision(reply_text="History has a habit of answering that."),
            [], allowed_quote_ids=set(), recent_replies=[],
        )
    with pytest.raises(ValueError, match="highly similar"):
        validate_reply_decision(
            decision(reply_text="A tidy theory; reality may request amendments."),
            [], allowed_quote_ids=set(),
            recent_replies=["A tidy theory. Reality may request amendments."],
        )


def test_reply_decision_is_string_compatible_and_carries_private_metadata():
    value = ReplyDecision("Dry, but accurate.", {"mode": "deadpan_reply"})
    assert value == "Dry, but accurate." and value.strategy_metadata["mode"] == "deadpan_reply"


@pytest.mark.parametrize(
    "patch",
    [
        {"mode": []},
        {"humour_tone": {}},
        {"evidence_confidence": 1},
        {"reply_text": 1234567890123},
    ],
)
def test_reply_decision_rejects_wrong_scalar_types_as_validation_errors(patch):
    with pytest.raises(ValueError, match="must be strings"):
        validate_reply_decision(decision(**patch), [], allowed_quote_ids=set())


def test_json_parser_accepts_fenced_object():
    assert parse_decision_json('```json\n{"mode":"no_reply"}\n```')["mode"] == "no_reply"


def test_bare_skip_is_not_a_structured_reply_decision():
    with pytest.raises(ValueError, match="bare SKIP"):
        parse_decision_json("SKIP")


def test_valid_no_reply_contract_and_schema() -> None:
    value = decision(
        mode="no_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text="",
        no_reply_reason="No useful response.",
    )

    validate_json_schema(value, reply_decision_json_schema())
    assert validate_reply_decision(value, [], allowed_quote_ids=set()) == value


@pytest.mark.parametrize(
    ("mode", "tone"),
    [
        ("wry_reply", "wry"),
        ("playful_reply", "playful"),
        ("deadpan_reply", "deadpan"),
        ("warm_reply", "warm"),
    ],
)
def test_existing_humour_reply_modes_remain_valid(mode: str, tone: str) -> None:
    value = decision(mode=mode, humour_tone=tone)
    validate_json_schema(value, reply_decision_json_schema())
    assert validate_reply_decision(value, [], allowed_quote_ids=set())["mode"] == mode


@pytest.mark.parametrize(
    ("mode", "confidence"),
    [
        ("historical_correction", "high"),
        ("historical_context", "medium"),
        ("researched_principle", "medium"),
    ],
)
def test_existing_historical_reply_modes_remain_valid(mode: str, confidence: str) -> None:
    evidence = RetrievedEvidence(
        "e" * 64,
        1.0,
        "exact",
        "A supported historical point.",
        {"verified_text": "Exact words.", "research_confidence": "high"},
    )
    value = decision(
        mode=mode,
        humour_tone="dry",
        evidence_confidence=confidence,
        retrieved_quote_ids=[evidence.quote_id],
        evidence_summary="A supported historical point.",
        factual_claim_made=True,
        grounded=True,
        reply_text="The historical record supports that narrower conclusion.",
    )
    validate_json_schema(value, reply_decision_json_schema())
    assert validate_reply_decision(
        value,
        [evidence],
        allowed_quote_ids={evidence.quote_id},
    )["mode"] == mode


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("humour_tone", "dry"),
        ("evidence_confidence", "low"),
        ("retrieved_quote_ids", ["a" * 64]),
        ("evidence_summary", "A supposed reason placed in evidence metadata."),
        ("factual_claim_made", True),
        ("grounded", True),
        ("reply_text", "A reply must not accompany no_reply."),
    ],
)
def test_contradictory_no_reply_metadata_is_rejected_by_schema_and_validator(
    field: str,
    invalid_value: object,
) -> None:
    value = decision(
        mode="no_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text="",
        no_reply_reason="No useful response.",
    )
    value[field] = invalid_value

    with pytest.raises(JsonSchemaValidationError):
        validate_json_schema(value, reply_decision_json_schema())
    with pytest.raises(ValueError, match="no_reply"):
        validate_reply_decision(value, [], allowed_quote_ids=set())


def test_no_reply_normalises_only_harmless_empty_representations() -> None:
    value = decision(
        mode="no_reply",
        humour_tone=" ",
        evidence_confidence=None,
        retrieved_quote_ids=None,
        evidence_summary=" \n",
        factual_claim_made=None,
        grounded=None,
        reply_text=None,
        no_reply_reason="  No useful response.  ",
    )

    assert normalise_reply_decision(value) == decision(
        mode="no_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text="",
        no_reply_reason="No useful response.",
    )
    result = validate_reply_decision(value, [], allowed_quote_ids=set())
    assert result["mode"] == "no_reply"
    assert result["evidence_confidence"] == "none"
    assert result["evidence_summary"] == ""


def test_no_reply_normalisation_does_not_supply_omitted_required_fields() -> None:
    value = decision(
        mode="no_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text="",
        no_reply_reason="No useful response.",
    )
    value.pop("grounded")

    with pytest.raises(ValueError, match="fields mismatch"):
        validate_reply_decision(value, [], allowed_quote_ids=set())


def test_no_reply_prompt_contains_one_explicit_valid_example() -> None:
    instruction = decision_schema_instruction()
    marker = 'Valid no_reply example: '
    assert instruction.count(marker) == 1
    example = instruction.split(marker, 1)[1].split(". For principle_reply", 1)[0]
    value = json.loads(example)
    assert value == decision(
        mode="no_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text="",
        no_reply_reason="No useful response.",
    )


def test_principle_reply_prompt_contains_one_explicit_valid_example() -> None:
    instruction = decision_schema_instruction()
    marker = "Valid principle_reply example: "
    assert instruction.count(marker) == 1
    example = instruction.split(marker, 1)[1].removesuffix(".")
    value = json.loads(example)
    assert value == decision(
        mode="principle_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text="Institutions endure only when people are prepared to defend their purpose.",
    )


def test_digest_audit_reports_no_rows_for_supplied_digest(tmp_path: Path):
    digest = tmp_path / "digest.md"
    digest.write_text("# Digest\n\nNo conversational reply tables.\n", encoding="utf-8")
    result = audit_digest(digest)
    assert result["auditable_reply_count"] == 0
    assert result["finding"] == "no auditable replies in digest"
    assert result["network_calls"] == 0


def test_enabled_strategy_requires_accuracy_and_completed_packets_only():
    config = dict(DEFAULT_REPLY_STRATEGY)
    config.update(enabled=True, accuracy_first=False, completed_packets_only=False, no_hashtags=False)
    errors = validate_reply_strategy_config(config)
    assert "reply_strategy.accuracy_first must remain true when enabled" in errors
    assert "reply_strategy.completed_packets_only must remain true when enabled" in errors
    assert "reply_strategy.no_hashtags must remain true when enabled" in errors


def test_disabled_modes_and_configured_confidence_are_enforced():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=2)
    qid = evidence[0].quote_id
    historical = decision(
        mode="historical_context", humour_tone="dry", evidence_confidence="medium",
        retrieved_quote_ids=[qid], evidence_summary="Context.", factual_claim_made=True,
        grounded=True, reply_text="Government can set conditions for prosperity without manufacturing it.",
    )
    with pytest.raises(ValueError, match="disabled by configuration"):
        validate_reply_decision(
            historical, evidence, allowed_quote_ids={qid},
            allowed_modes={"wry_reply", "no_reply"},
        )
    with pytest.raises(ValueError, match="configured grounded confidence"):
        validate_reply_decision(
            historical, evidence, allowed_quote_ids={qid}, minimum_grounded_confidence="high",
        )


def test_grounded_claim_requires_selected_evidence():
    with pytest.raises(ValueError, match="grounded replies require selected evidence"):
        validate_reply_decision(
            decision(factual_claim_made=True, grounded=True, evidence_confidence="medium"),
            [], allowed_quote_ids=set(),
        )


def test_grounded_claim_requires_evidence_object_for_every_selected_id():
    qid = "d" * 64

    with pytest.raises(ValueError, match="non-retrieved or unresolved quote ID"):
        validate_reply_decision(
            decision(
                factual_claim_made=True,
                grounded=True,
                evidence_confidence="medium",
                retrieved_quote_ids=[qid],
                evidence_summary="A factual historical point.",
            ),
            [],
            allowed_quote_ids={qid},
        )


def test_factual_humour_obeys_configured_grounded_confidence():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=1)
    qid = evidence[0].quote_id
    with pytest.raises(ValueError, match="configured grounded confidence"):
        validate_reply_decision(
            decision(
                evidence_confidence="low", retrieved_quote_ids=[qid],
                evidence_summary="A factual historical point.", factual_claim_made=True,
                grounded=True,
            ),
            evidence, allowed_quote_ids={qid}, minimum_grounded_confidence="medium",
        )


def test_factual_humour_rejects_low_confidence_packet():
    low = RetrievedEvidence(
        "c" * 64,
        1.0,
        "exact",
        "Low-confidence evidence.",
        {"verified_text": "Exact words.", "research_confidence": "low"},
    )

    with pytest.raises(ValueError, match="packet confidence"):
        validate_reply_decision(
            decision(
                evidence_confidence="medium",
                retrieved_quote_ids=[low.quote_id],
                evidence_summary="A factual historical point.",
                factual_claim_made=True,
                grounded=True,
            ),
            [low],
            allowed_quote_ids={low.quote_id},
            minimum_grounded_confidence="medium",
        )


def test_historical_modes_require_factual_claim_and_evidence_summary():
    evidence = retrieve_research_packets("Government creates wealth", RESEARCH, maximum=1)
    qid = evidence[0].quote_id
    base = decision(
        mode="historical_context", humour_tone="dry", evidence_confidence="medium",
        retrieved_quote_ids=[qid], grounded=True,
        reply_text="Government can set conditions for prosperity without manufacturing it.",
    )
    with pytest.raises(ValueError, match="historical modes must identify a factual claim"):
        validate_reply_decision(base, evidence, allowed_quote_ids={qid})
    with pytest.raises(ValueError, match="grounded replies require an evidence summary"):
        validate_reply_decision(
            {**base, "factual_claim_made": True}, evidence, allowed_quote_ids={qid},
        )


def test_no_reply_metadata_is_internally_consistent():
    with pytest.raises(ValueError, match="no_reply requires"):
        validate_reply_decision(
            decision(
                mode="no_reply", humour_tone="dry", evidence_confidence="medium",
                grounded=True, retrieved_quote_ids=[], reply_text="", no_reply_reason="Weak evidence.",
            ),
            [], allowed_quote_ids=set(),
        )


def test_configured_humour_tones_and_emoji_are_enforced():
    with pytest.raises(ValueError, match="humour tone is disabled"):
        validate_reply_decision(
            decision(humour_tone="playful"), [], allowed_quote_ids=set(),
            allowed_humour_tones={"dry", "wry"},
        )
    with pytest.raises(ValueError, match="emoji"):
        validate_reply_decision(
            decision(reply_text="A tidy theory. Reality may disagree. 🙂"),
            [], allowed_quote_ids=set(),
        )


def test_digest_table_parser_preserves_escaped_pipes(tmp_path: Path):
    digest = tmp_path / "digest.md"
    digest.write_text(
        "## Mention replies\n"
        "| time | mention_id | incoming_text | reply |\n"
        "| --- | --- | --- | --- |\n"
        "| now | 1 | A \\| B | A reply. |\n",
        encoding="utf-8",
    )
    result = audit_digest(digest)
    assert result["auditable_reply_count"] == 1
    assert result["items"][0]["cells"] == ["now", "1", "A | B", "A reply."]


def test_mode_guidance_defines_accuracy_first_classification_hierarchy():
    guidance = strategy_mode_guidance()
    assert guidance.index("materially false") < guidance.index("historical_context")
    assert guidance.index("historical_context") < guidance.index("researched_principle")
    assert guidance.index("researched_principle") < guidance.index("humour mode")
    assert "Do not force history" in guidance
    assert "Do not force humour" in guidance


def test_audit_json_stdout_is_machine_readable(tmp_path: Path):
    digest = tmp_path / "digest.md"
    digest.write_text("# Digest\n\nNo conversational reply tables.\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, "mrsMThatcher2.py", "audit-replies",
            "--digest", str(digest),
            "--research-run", str(RESEARCH), "--json",
        ],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["auditable_reply_count"] == 0
    assert payload["network_calls"] == 0

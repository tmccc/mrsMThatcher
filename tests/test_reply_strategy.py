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
    reply_topical_relevance_error,
    reply_decision_json_schema,
    strategy_mode_guidance,
    retrieve_research_packets,
    validate_reply_decision,
)

RESEARCH = Path("semantic_alignment_research/quote_research_full_001")
BURNHAM_FAILURE_TEXT = (
    "@andrewlawrence DON'T FORGET THIS LOW-LIFE @andyburnham HAS NEVER HAD A JOB IN HIS LIFE. "
    "- 1976 - 1997 Margaret Thatcher @simplysimontfa @MrsMThatcher * First time since the "
    "1900s economy was left in a positive state. (+£) * Berlin Wall Down * Brighter future "
    "for generations to"
)


def decision(**overrides):
    value = {
        "mode": "wry_reply", "humour_tone": "wry", "evidence_confidence": "none",
        "retrieved_quote_ids": [], "evidence_summary": "", "factual_claim_made": False,
        "grounded": False, "reply_text": "A tidy theory. Reality may request amendments.",
        "no_reply_reason": "", "topical_basis": "",
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
    posted_mode_rule = schema["allOf"][0]
    assert "no_reply" not in posted_mode_rule["if"]["properties"]["mode"]["enum"]
    assert posted_mode_rule["then"]["properties"]["no_reply_reason"] == {"maxLength": 0}
    topical_rule = schema["allOf"][1]
    assert topical_rule["if"]["properties"]["mode"] == {"enum": ["principle_reply", "researched_principle"]}
    assert topical_rule["then"]["properties"]["topical_basis"] == {"minLength": 3}
    principle_rule = schema["allOf"][2]
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
        topical_basis="understand this",
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


def test_burnham_failure_rejects_incidental_wall_researched_principle() -> None:
    evidence = retrieve_research_packets(BURNHAM_FAILURE_TEXT, RESEARCH, maximum=5)
    wall = next(item for item in evidence if item.quote_id == "c70676b9a1adc20b7b22b33bfb6d43acdcbb2d9990265d93ec565daa87ae6434")
    reply = "No Western nation has to build a wall round itself to keep its people in."

    with pytest.raises(ValueError, match="topical_relevance"):
        validate_reply_decision(
            decision(
                mode="researched_principle",
                humour_tone="none",
                evidence_confidence="high",
                retrieved_quote_ids=[wall.quote_id],
                evidence_summary=reply,
                factual_claim_made=True,
                grounded=True,
                reply_text=reply,
                topical_basis="Berlin Wall Down",
            ),
            evidence,
            allowed_quote_ids={item.quote_id for item in evidence},
            incoming_text=BURNHAM_FAILURE_TEXT,
        )


def test_burnham_failure_is_rejected_through_production_reply_call_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import reply_strategy as strategy

    wall = next(
        item
        for item in retrieve_research_packets(BURNHAM_FAILURE_TEXT, RESEARCH, maximum=5)
        if item.quote_id == "c70676b9a1adc20b7b22b33bfb6d43acdcbb2d9990265d93ec565daa87ae6434"
    )
    retrieval_queries: list[str] = []
    rejection_events: list[tuple[str, dict]] = []

    def retrieve(query, _research_path, *, maximum):
        retrieval_queries.append(query)
        assert maximum >= 1
        return [wall]

    value = decision(
        mode="researched_principle",
        humour_tone="none",
        evidence_confidence="high",
        retrieved_quote_ids=[wall.quote_id],
        evidence_summary="No Western nation has to build a wall round itself to keep its people in.",
        factual_claim_made=True,
        grounded=True,
        reply_text="No Western nation has to build a wall round itself to keep its people in.",
        topical_basis="Berlin Wall Down",
    )
    response = bot.requests.Response()
    response.status_code = 200
    response._content = json.dumps({
        "choices": [{"message": {"content": json.dumps(value)}}],
    }).encode("utf-8")
    monkeypatch.setattr(strategy, "retrieve_research_packets", retrieve)
    monkeypatch.setattr(bot.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(bot, "log_event", lambda name, **fields: rejection_events.append((name, fields)))
    monkeypatch.setattr(
        bot,
        "reply_strategy",
        {
            **bot.reply_strategy,
            "enabled": True,
            "research_corpus_enabled": True,
            "hybrid_retrieval": {
                **bot.reply_strategy["hybrid_retrieval"],
                "enabled": False,
            },
        },
    )

    outcome: dict[str, str] = {}
    result = bot.ask_grok_for_reply(
        "Parent post: She left office 36 years ago.\n\nIncoming post: " + BURNHAM_FAILURE_TEXT,
        shadow_incoming_text=BURNHAM_FAILURE_TEXT,
        direct_question_text=BURNHAM_FAILURE_TEXT,
        evaluation_outcome=outcome,
    )

    assert result is None
    assert retrieval_queries == [BURNHAM_FAILURE_TEXT]
    assert outcome == {"status": "no_reply", "reason": "topical_relevance_rejected"}
    assert (
        "reply_strategy_rejection",
        {"lane": "unavailable", "reason": "topical_relevance_rejected", "detail_code": "basis_not_substantive"},
    ) in rejection_events


def test_wall_evidence_remains_valid_for_related_coercive_border_issue() -> None:
    incoming = "The Berlin Wall showed how a coercive border trapped citizens in the communist East."
    evidence = retrieve_research_packets(incoming, RESEARCH, maximum=5)
    wall = next(item for item in evidence if item.quote_id == "c70676b9a1adc20b7b22b33bfb6d43acdcbb2d9990265d93ec565daa87ae6434")
    result = validate_reply_decision(
        decision(
            mode="researched_principle",
            humour_tone="none",
            evidence_confidence="high",
            retrieved_quote_ids=[wall.quote_id],
            evidence_summary="Communist regimes used the Berlin Wall to prevent emigration.",
            factual_claim_made=True,
            grounded=True,
            reply_text="A regime that needs a wall to keep its citizens in has confessed the failure of coercion.",
            topical_basis="Berlin Wall",
        ),
        evidence,
        allowed_quote_ids={item.quote_id for item in evidence},
        incoming_text=incoming,
    )
    assert result["mode"] == "researched_principle"


def test_lexical_retrieval_excludes_handles_and_url_components() -> None:
    clean = "A minister should value conviction above personal popularity."
    contaminated = (
        "@freedom https://example.test/berlin/wall "
        "A minister should value conviction above personal popularity."
    )
    expected = retrieve_research_packets(clean, RESEARCH, maximum=5)
    actual = retrieve_research_packets(contaminated, RESEARCH, maximum=5)
    assert [item.quote_id for item in actual] == [item.quote_id for item in expected]
    assert [item.score for item in actual] == [item.score for item in expected]


@pytest.mark.parametrize(
    ("incoming", "reply", "basis"),
    [
        ("Government must restore public confidence.", "Government must choose freedom over fear.", "Government must restore public confidence"),
        ("The economy needs reform.", "Economic leadership requires freedom.", "economy needs reform"),
        ("National leadership is failing.", "A nation needs leadership grounded in freedom.", "National leadership is failing"),
    ],
)
def test_common_political_vocabulary_alone_cannot_establish_relevance(
    incoming: str,
    reply: str,
    basis: str,
) -> None:
    assert reply_topical_relevance_error(
        incoming,
        reply,
        [],
        mode="principle_reply",
        topical_basis=basis,
    ) is not None


def test_relevance_basis_cannot_come_only_from_inherited_thread_text() -> None:
    error = reply_topical_relevance_error(
        "A minister should value conviction above personal popularity.",
        "No nation needs a wall to keep its people in.",
        [],
        mode="principle_reply",
        topical_basis="Berlin Wall",
    )
    assert error == "topical_relevance:basis_not_in_incoming"


def test_topical_relevance_is_deterministic_for_identical_inputs() -> None:
    args = (
        "This is about a leader's desire for popularity.",
        "Leaders serve best when conviction, not popularity, guides their course.",
        [],
    )
    results = [
        reply_topical_relevance_error(
            *args,
            mode="principle_reply",
            topical_basis="desire for popularity",
        )
        for _ in range(10)
    ]
    assert results == [None] * 10


def test_researched_principle_requires_grounding_and_topical_relevance() -> None:
    item = RetrievedEvidence(
        "a" * 64,
        5.0,
        "exact",
        "Free enterprise and democratic accountability.",
            {
                "verified_text": "Free enterprise and democratic accountability.",
                "verification_status": "exact",
                "research_confidence": "high",
                "speaker": "Margaret Thatcher",
                "immediate_subject": "Free enterprise and democratic accountability.",
            "intended_argument": "Markets require accountable democratic government.",
        },
    )
    incoming = "Free markets require democratic accountability and political responsibility."
    value = decision(
        mode="researched_principle",
        humour_tone="none",
        evidence_confidence="high",
        retrieved_quote_ids=[item.quote_id],
        evidence_summary="Free enterprise requires accountability.",
        factual_claim_made=True,
        grounded=True,
        reply_text="Free enterprise must answer to democratic accountability.",
        topical_basis="Free markets require democratic accountability",
    )

    result = validate_reply_decision(
        value,
        [item],
        allowed_quote_ids={item.quote_id},
        incoming_text=incoming,
    )
    assert result["mode"] == "researched_principle"
    assert reply_topical_relevance_error(
        incoming,
        value["reply_text"],
        [item],
        mode=value["mode"],
        topical_basis=value["topical_basis"],
    ) is None


def test_grounding_confidence_alone_cannot_satisfy_topical_relevance() -> None:
    unrelated = RetrievedEvidence(
        "b" * 64,
        99.0,
        "exact",
        "A highly confident but unrelated packet.",
        {
            "verified_text": "No nation needs a wall to keep its people in.",
            "research_confidence": "high",
            "immediate_subject": "Cold War borders and coercion.",
            "intended_argument": "Freedom does not require walls.",
        },
    )
    error = reply_topical_relevance_error(
        "A minister should value conviction above personal popularity.",
        "No nation needs a wall to keep its people in.",
        [unrelated],
        mode="researched_principle",
        topical_basis="conviction above personal popularity",
    )
    assert error is not None


def test_conviction_and_popularity_principle_remains_topically_valid() -> None:
    incoming = "This is what I said about Burnham's desire for popularity."
    reply = "Leaders serve best when conviction, not popularity, guides their course."
    assert reply_topical_relevance_error(
        incoming,
        reply,
        [],
        mode="principle_reply",
        topical_basis="desire for popularity",
    ) is None


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


def test_concrete_who_question_rejects_a_declarative_non_answer() -> None:
    question = "Who was Prime Minister in 1979?"
    reply = "That deserves serious consideration."

    assert concrete_factual_question_word(question) == "who"
    assert direct_factual_answer_error(question, reply) is not None


def test_concrete_question_syntax_does_not_require_terminal_punctuation() -> None:
    assert concrete_factual_question_word("Who was Prime Minister in 1979") == "who"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("@MrsMThatcher, where was the treaty signed?", "where"),
        ("Please, where was the treaty signed?", "where"),
        ("Seriously, when did it happen?", "when"),
        ("Could you tell me what happened?", "what"),
        ("I wonder where the treaty was signed?", "where"),
        ("Quick question: who signed the treaty?", "who"),
        ("Can I ask when it happened?", "when"),
        ("Could you explain what happened?", "what"),
        ("Please explain what happened?", "what"),
        ("What a mess?", None),
        ("What an absolute mess?", None),
        ("What a complete disaster?", None),
    ],
)
def test_concrete_question_recognises_addressing_but_not_rhetorical_exclamations(
    question: str,
    expected: str | None,
) -> None:
    assert concrete_factual_question_word(question) == expected


def test_grounded_metadata_cannot_rescue_a_non_answer_to_a_factual_question() -> None:
    packet = {
        "quote_text": "Enterprise and responsibility go together.",
        "verified_text": "Enterprise and responsibility go together.",
        "verification_status": "exact",
        "research_confidence": "high",
        "speaker": "Margaret Thatcher",
    }
    evidence = [RetrievedEvidence("a" * 64, 1.0, "exact", "Economic policy.", packet)]
    with pytest.raises(ValueError, match="direct answer"):
        validate_reply_decision(
            decision(
                mode="historical_context",
                humour_tone="none",
                evidence_confidence="high",
                retrieved_quote_ids=["a" * 64],
                evidence_summary="A grounded but unrelated economic principle.",
                factual_claim_made=True,
                grounded=True,
                reply_text="That deserves serious consideration.",
            ),
            evidence,
            allowed_quote_ids={"a" * 64},
            direct_question_text="Who was Prime Minister in 1979?",
        )


def test_direct_who_answer_must_be_supported_by_selected_evidence() -> None:
    packet = {
        "quote_text": "Government requires responsibility.",
        "verified_text": "Government requires responsibility.",
        "verification_status": "exact",
        "research_confidence": "high",
        "speaker": "Margaret Thatcher",
        "entities": ["Margaret Thatcher", "1979 United Kingdom general election"],
        "immediate_subject": "Margaret Thatcher becoming Prime Minister in 1979.",
    }
    evidence = [RetrievedEvidence("a" * 64, 1.0, "exact", "The 1979 election.", packet)]
    assert direct_factual_answer_error(
        "Who was Prime Minister in 1979?",
        "Margaret Thatcher was Prime Minister.",
        evidence,
    ) is None
    assert direct_factual_answer_error(
        "Who was Prime Minister in 1979?",
        "Michael Jordan was Prime Minister.",
        evidence,
    ) is not None


def test_direct_who_answer_cannot_echo_person_already_named_in_question() -> None:
    packet = {
        "quote_text": "I can do business with Mr Gorbachev.",
        "verified_text": "I can do business with Mr Gorbachev.",
        "verification_status": "exact",
        "research_confidence": "high",
        "speaker": "Margaret Thatcher",
        "entities": ["Margaret Thatcher", "Mikhail Gorbachev"],
        "immediate_subject": "Margaret Thatcher meeting Mikhail Gorbachev.",
    }
    evidence = [RetrievedEvidence("a" * 64, 1.0, "exact", "The meeting.", packet)]

    assert direct_factual_answer_error(
        "Who did Margaret Thatcher meet?",
        "Margaret Thatcher met the Soviet leader.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "Who did Margaret Thatcher meet?",
        "Mikhail Gorbachev met Margaret Thatcher.",
        evidence,
    ) is None

    evidence_without_counterpart = [RetrievedEvidence(
        "b" * 64,
        1.0,
        "exact",
        "A meeting without a source-grounded counterpart.",
        {
            "quote_text": "Meetings require preparation.",
            "verified_text": "Meetings require preparation.",
            "speaker": "Margaret Thatcher",
            "entities": ["Margaret Thatcher"],
        },
    )]
    assert direct_factual_answer_error(
        "Who did Margaret Thatcher meet?",
        "Margaret Thatcher met the political leader.",
        evidence_without_counterpart,
    ) is not None


def test_concrete_when_where_and_what_answers_cannot_use_thematic_placeholders() -> None:
    packet = {
        "quote_text": "The treaty was signed in London on 14 June 1982.",
        "verified_text": "The treaty was signed in London on 14 June 1982.",
        "verification_status": "exact",
        "research_confidence": "high",
        "speaker": "Margaret Thatcher",
        "date": "1982-06-14",
        "entities": ["London", "United Kingdom"],
        "immediate_subject": "The treaty, politics, freedom and government responsibility.",
    }
    evidence = [RetrievedEvidence("a" * 64, 1.0, "exact", "The treaty.", packet)]

    assert direct_factual_answer_error(
        "When was the treaty signed?",
        "In government, responsibility matters.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "When was the treaty signed?",
        "During political arguments, responsibility matters.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "When was the treaty signed?",
        "The year was significant.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "When was the treaty signed?",
        "It was signed on 14 June 1982.",
        evidence,
    ) is None
    assert direct_factual_answer_error(
        "When was the treaty signed?",
        "It was signed in 1776.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "When was the treaty signed?",
        "The event happened last year.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "Where was the treaty signed?",
        "In politics, freedom matters.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "Where was the treaty signed?",
        "It was signed in London.",
        evidence,
    ) is None
    assert direct_factual_answer_error(
        "What happened in 1982?",
        "1982 was important for government.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "What happened in 1982?",
        "Something happened in 1982.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "What happened in 1982?",
        "There were developments in 1982.",
        evidence,
    ) is not None
    assert direct_factual_answer_error(
        "What happened in 1982?",
        "The treaty was signed in London.",
        evidence,
    ) is None


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
        {
            "verified_text": "Exact words.", "verification_status": "exact",
            "research_confidence": "low", "speaker": "Margaret Thatcher",
        },
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
    ("incoming", "reply", "topical_basis"),
    [
        (
            "Why doesn't anyone in government understand this?",
            "Understanding is not always the same as having the courage to act.",
            "understand this",
        ),
        (
            "The institutions have failed because the government is acting in bad faith.",
            "Institutions endure only when people are prepared to defend their purpose.",
            "institutions have failed",
        ),
    ],
)
def test_uncertain_claim_can_receive_a_non_factual_principle_reply(
    incoming: str,
    reply: str,
    topical_basis: str,
) -> None:
    result = validate_reply_decision(
        decision(
            mode="principle_reply",
            humour_tone="none",
            evidence_confidence="none",
            reply_text=reply,
            topical_basis=topical_basis,
        ),
        [],
        allowed_quote_ids=set(),
        incoming_text=incoming,
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


def test_principle_reply_rejects_named_actor_allegation() -> None:
    incoming = "Andy Burnham only wants public popularity."
    reply = "Burnham craves popularity rather than responsibility."
    assert principle_reply_assertion_error(reply, incoming) is not None
    with pytest.raises(ValueError, match="specific unsupported factual assertion"):
        validate_reply_decision(
            decision(
                mode="principle_reply",
                humour_tone="none",
                evidence_confidence="none",
                reply_text=reply,
                topical_basis="wants public popularity",
            ),
            [],
            allowed_quote_ids=set(),
            incoming_text=incoming,
        )


def test_principle_reply_rejects_lowercase_incoming_named_actor_allegation() -> None:
    incoming = "andy burnham only wants public popularity."
    reply = "Burnham craves popularity rather than responsibility."
    assert principle_reply_assertion_error(reply, incoming) is not None
    with pytest.raises(ValueError, match="specific unsupported factual assertion"):
        validate_reply_decision(
            decision(
                mode="principle_reply",
                humour_tone="none",
                evidence_confidence="none",
                reply_text=reply,
                topical_basis="wants public popularity",
            ),
            [],
            allowed_quote_ids=set(),
            incoming_text=incoming,
        )


@pytest.mark.parametrize(
    "reply",
    [
        "Rishi Sunak wants popularity rather than responsibility.",
        "Donald Trump lies about the economy.",
        "Donald Trust lies about the economy.",
        "Rishi Sunak champions responsibility.",
        "Burnham backs popularity over responsibility.",
        "Churchill wrote the policy.",
        "Macron made the decision.",
        "Reagan cut taxes yesterday.",
        "Starmer raises taxes.",
        "Sunak wants popularity rather than responsibility.",
        "Sunak undermines freedom.",
        "The caseworker lies about the economy.",
        "Plainly, Burnham lies about the economy.",
        "The point is this: Burnham backs higher taxes.",
        "In the end, Burnham's record speaks for itself.",
        "I think Burnham lies about the economy.",
        "It is obvious Burnham wants popularity.",
        "The truth is that Macron supports the policy.",
        "I think burnham lies about the economy.",
        "I think BURNHAM LIES about the economy.",
    ],
)
def test_principle_reply_cannot_introduce_an_unverified_named_actor(reply: str) -> None:
    incoming = "Andy Burnham wants public popularity."
    assert principle_reply_assertion_error(reply, incoming) is not None


@pytest.mark.parametrize(
    "reply",
    [
        "Understanding is not always the same as having the courage to act.",
        "Institutions endure only when people defend their purpose.",
        "The case still has to be made—and acted upon.",
        "Truth is indispensable.",
    ],
)
def test_principle_reply_preserves_general_non_actor_subjects(reply: str) -> None:
    assert principle_reply_assertion_error(reply, "A general political point.") is None


def test_attribution_ineligible_packet_cannot_be_used_as_reply_evidence() -> None:
    quote_id = "69a1c2be69f8e802aaad1948b85557bdff3130e126a7602600b485e7cff048c8"
    packets = json.loads((RESEARCH / "research_packets.json").read_text(encoding="utf-8"))["items"]
    packet = packets[quote_id]
    evidence = [RetrievedEvidence(
        quote_id,
        10.0,
        str(packet["verification_status"]),
        str(packet["intended_argument"]),
        packet,
    )]
    with pytest.raises(ValueError, match="attribution-ineligible"):
        validate_reply_decision(
            decision(
                mode="historical_context",
                humour_tone="none",
                evidence_confidence="high",
                retrieved_quote_ids=[quote_id],
                evidence_summary="A source-grounded claim by a different speaker.",
                factual_claim_made=True,
                grounded=True,
                reply_text="Success at the highest level can require selfishness.",
            ),
            evidence,
            allowed_quote_ids={quote_id},
        )


@pytest.mark.parametrize(
    "quote_id",
    [
        "7f75c4d086fb67b0e54d9d63dbe470dc6f9f929aee00ce4a02d01bbc9c8d4646",
        "cf7a03be1c6e34efbcfec0cc8010544e2deab777a05cb0193d237814244f5c8e",
        "8c70978a89ef43e405dbc7eb0bb9751d9dbe631d63d9834ccf3dfde51a4a971c",
    ],
)
def test_reply_retrieval_excludes_false_positive_thatcher_attributions(quote_id: str) -> None:
    packet = json.loads((RESEARCH / "research_packets.json").read_text(encoding="utf-8"))["items"][quote_id]
    results = retrieve_research_packets(packet["quote_text"], RESEARCH, maximum=10)
    assert quote_id not in {item.quote_id for item in results}


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
    incoming = "Responsibility matters when excuses are tempting."
    metadata = decision(
        mode="principle_reply",
        humour_tone="none",
        evidence_confidence="none",
        reply_text=text,
    )
    metadata.pop("topical_basis")
    assert bot.strategy_metadata_is_semantically_valid(metadata, text, incoming_text=incoming)
    unsafe = {**metadata, "reply_text": "The government is concealing the truth."}
    assert not bot.strategy_metadata_is_semantically_valid(
        unsafe,
        unsafe["reply_text"],
        incoming_text=incoming,
    )


def test_weak_factual_evidence_requires_no_reply():
    result = validate_reply_decision(decision(
        mode="no_reply", humour_tone="none", evidence_confidence="none",
        reply_text="", no_reply_reason="The historical claim cannot be grounded confidently.",
    ), [], allowed_quote_ids=set())
    assert result["mode"] == "no_reply"
    with pytest.raises(ValueError, match="factual claims require grounding"):
        validate_reply_decision(decision(factual_claim_made=True), [], allowed_quote_ids=set())


def test_posted_mode_rejects_contradictory_no_reply_reason() -> None:
    value = decision(no_reply_reason="No useful response.")

    with pytest.raises(JsonSchemaValidationError):
        validate_json_schema(value, reply_decision_json_schema())
    with pytest.raises(ValueError, match="empty no_reply_reason"):
        validate_reply_decision(value, [], allowed_quote_ids=set())


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
        {
            "verified_text": "A normalised sentence.", "verification_status": "normalised",
            "research_confidence": "high", "speaker": "Margaret Thatcher",
        },
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
            {
                "verified_text": verified_text,
                "verification_status": "exact",
                "research_confidence": "high",
                "speaker": "Margaret Thatcher",
                "immediate_subject": "Exact words and their historical record.",
        },
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
            topical_basis="Exact words",
        ),
        [exact],
        allowed_quote_ids={exact.quote_id},
        incoming_text="Exact words and their historical record.",
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
            {
                "verified_text": "Exact words.",
                "verification_status": "exact",
                "research_confidence": "high",
                "speaker": "Margaret Thatcher",
                "immediate_subject": "The historical record supports a narrower conclusion.",
        },
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
        topical_basis=("historical record supports" if mode == "researched_principle" else ""),
    )
    validate_json_schema(value, reply_decision_json_schema())
    assert validate_reply_decision(
        value,
        [evidence],
        allowed_quote_ids={evidence.quote_id},
        incoming_text=("The historical record supports a narrower conclusion." if mode == "researched_principle" else None),
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
        topical_basis="institutions have failed",
    )


def test_digest_audit_reports_no_rows_for_supplied_digest(tmp_path: Path):
    digest = tmp_path / "digest.md"
    digest.write_text("# Digest\n\nNo conversational reply tables.\n", encoding="utf-8")
    result = audit_digest(digest)
    assert result["auditable_reply_count"] == 0
    assert result["finding"] == "no auditable replies in digest"
    assert result["network_calls"] == 0


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
        {
            "verified_text": "Exact words.", "verification_status": "exact",
            "research_confidence": "low", "speaker": "Margaret Thatcher",
        },
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

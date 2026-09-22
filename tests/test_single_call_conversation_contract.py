"""Test deterministic reply contracts, not the model's factual judgement."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import single_call_reply as pipeline
from tests.helpers.single_call_fixtures import (
    FakePassage, FakeRepository, context, raw_decision,
)


FACT = "Britain joined the European Economic Community in 1973."
NATURAL_REPLIES = [
    ("social", "Thank you for taking the time to explain."),
    ("principle", "I would rather leave that choice to individuals."),
    ("clarification", "Why should the state decide that for everyone?"),
    ("social", "A generous thought, and kindly expressed."),
    ("social", "I am so sorry; I hope you have some company tonight."),
    ("clarification", "Do you mean who should choose, or who should pay?"),
    ("principle", "I disagree, though I appreciate the care behind your argument."),
    ("humour", "I shall resist the temptation to appoint a committee for that."),
    ("premise_neutral", "Let us disagree without making enemies of one another."),
]
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def evidence_inputs():
    """Return one exact passage and ordinary isolated conversation context."""
    repository = FakeRepository(1)
    repository.passages["evidence-1"] = FakePassage("evidence-1", FACT)
    source = context()
    payload, facts = pipeline.build_model_payload(context=source, repository=repository)
    return source, repository, payload, facts


@pytest.mark.parametrize("kind,text", NATURAL_REPLIES)
def test_natural_nonfactual_conversation_has_no_closed_vocabulary(kind, text):
    """Supplied inventories are empty; no model classification is exercised."""
    source, repository, payload, facts = evidence_inputs()
    output = pipeline.validate_model_output(raw_decision(kind=kind, reply=text), payload=payload)
    draft = pipeline.create_durable_draft(
        output=output, payload=payload, fact_map=facts, images=[], target_author_id="200",
    )
    assert draft["proposed_reply"] == text
    assert draft["factual_claims"] == draft["used_fact_ids"] == []
    assert pipeline.validate_persisted_draft(draft, context=source, repository=repository) == draft


def test_supported_claim_coexists_with_natural_conversation_and_durable_bindings():
    """Declared evidence and free conversational text share one bound draft."""
    source, repository, payload, facts = evidence_inputs()
    text = FACT + " Thank you for taking the time to explain."
    response = json.loads(raw_decision(kind="direct_factual", reply=text, facts=["F1"]))
    response["factual_claims"] = [{"text": FACT, "fact_ids": ["F1"]}]
    output = pipeline.validate_model_output(json.dumps(response), payload=payload)
    draft = pipeline.create_durable_draft(
        output=output, payload=payload, fact_map=facts, images=[], target_author_id="200",
    )
    assert pipeline.validate_persisted_draft(draft, context=source, repository=repository) == draft
    for field, value in [("factual_claims", []), ("proposed_reply", FACT), ("used_fact_sources", [])]:
        changed = copy.deepcopy(draft)
        changed[field] = value
        with pytest.raises(ValueError, match="hash"):
            pipeline.validate_persisted_draft(changed, context=source, repository=repository)
    repository.passages["evidence-1"] = FakePassage("evidence-1", "An unrelated passage.")
    with pytest.raises(ValueError, match="source record changed"):
        pipeline.validate_persisted_draft(draft, context=source, repository=repository)


@pytest.mark.parametrize("claims", [
    None, {}, [None], [{"text": FACT}],
    [{"text": FACT, "fact_ids": []}],
    [{"text": FACT, "fact_ids": ["F1", "F1"]}],
    [{"text": FACT, "fact_ids": [1]}],
    [{"text": FACT, "fact_ids": ["F1"], "extra": True}],
    [{"text": FACT, "fact_ids": ["F1"]}] * 33,
])
def test_malformed_claim_inventories_remain_rejected(claims):
    """Free prose does not relax the declared inventory's schema."""
    _, _, payload, _ = evidence_inputs()
    response = json.loads(raw_decision(kind="direct_factual", reply=FACT, facts=["F1"]))
    response["factual_claims"] = claims
    with pytest.raises(pipeline.ReplyValidationError, match="invalid_factual_claims"):
        pipeline.validate_model_output(json.dumps(response), payload=payload)


@pytest.mark.parametrize("text,claims,used,error", [
    (FACT, [{"text": FACT, "fact_ids": ["F3"]}], ["F3"], "unknown_fact_id"),
    (FACT, [{"text": "Absent text.", "fact_ids": ["F1"]}], ["F1"], "inventory_mismatch"),
    (FACT, [{"text": FACT, "fact_ids": ["F1"]}] * 2, ["F1"], "inventory_mismatch"),
    ("Perhaps " + FACT, [{"text": FACT, "fact_ids": ["F1"]}], ["F1"], "inventory_mismatch"),
    (FACT, [{"text": FACT, "fact_ids": ["F1"]}], [], "fact_ids_mismatch"),
    ("Thank you for explaining.", [], ["F1"], "fact_ids_mismatch"),
])
def test_claim_spans_and_fact_inventory_must_agree(text, claims, used, error):
    """Check exact, ordered, non-overlapping declared spans and ID bindings."""
    _, _, payload, _ = evidence_inputs()
    response = json.loads(raw_decision(kind="social", reply=text, facts=used))
    response["factual_claims"] = claims
    with pytest.raises(pipeline.ReplyValidationError, match=error):
        pipeline.validate_model_output(json.dumps(response), payload=payload)


@pytest.mark.parametrize("text", [
    "Britain joined the European Economic Community in 1873.",
    "Britain did not join the European Economic Community in 1973.",
    "Thatcher invented ice cream.",
])
@pytest.mark.parametrize("kind", [kind for kind in pipeline.REPLY_KINDS if kind != "no_reply"])
def test_declared_facts_require_exact_evidence_independent_of_kind(text, kind):
    """A model label or existing ID cannot bypass checks on a declared claim."""
    _, _, payload, _ = evidence_inputs()
    with pytest.raises(pipeline.ReplyValidationError, match="unsupported_factual_claim"):
        pipeline.validate_model_output(raw_decision(kind=kind, reply=text, facts=["F1"]), payload=payload)


def test_no_reply_remains_valid():
    """A sensible silence needs neither public text nor evidence inventory."""
    _, _, payload, _ = evidence_inputs()
    result = pipeline.validate_model_output(raw_decision(
        decision="no_reply", kind="no_reply", reply="", reason="unsupported_or_unverifiable",
    ), payload=payload)
    assert result["decision"] == "no_reply"


def test_partial_answer_evaluation_cases_reach_the_one_call_unchanged():
    """Preserve semantic fixtures and policy inputs without faking model judgement."""

    cases = json.loads(
        (
            PROJECT_ROOT
            / "tests/fixtures/single_call_partial_answer_evaluation_cases.json"
        ).read_text(encoding="utf-8")
    )
    assert [case["case_id"] for case in cases] == [
        "source-identification-partial-answer",
        "emoji-only-after-request",
        "one-substantive-part-of-multipart-request",
        "repetition-without-requested-evidence",
        "completed-exchange-courtesy",
    ]
    assert [case["expected_editorial_preference"] for case in cases] == [
        "useful_continuation",
        "no_reply_reasonable",
        "useful_continuation",
        "no_reply_reasonable",
        "no_reply_reasonable",
    ]
    assert "partial answer is conversational\nprogress" in pipeline.SYSTEM_PROMPT
    assert "previous question alone never requires a\nreply" in pipeline.SYSTEM_PROMPT
    assert "must not imply that you inspected, verified or\nconfirmed" in pipeline.SYSTEM_PROMPT

    for case in cases:
        visible = case["visible_conversation"]
        source = context(turns=1)
        source.update(
            {
                "target_id": visible[-1]["post_id"],
                "thread_id": visible[0]["post_id"],
                "root_post_id": visible[0]["post_id"],
                "parent_post_id": visible[-2]["post_id"],
                "incoming_contribution": visible[-1]["text"],
                "parent_thread": copy.deepcopy(visible[:-1]),
                "visible_conversation": copy.deepcopy(visible),
            }
        )
        payload, _facts = pipeline.build_model_payload(
            context=source,
            repository=FakeRepository(0),
        )
        request, images = pipeline.build_openai_request(payload)
        assert payload["visible_conversation"] == [
            {
                "post_id": turn["post_id"],
                "role": turn["author_role"],
                "text": turn["text"],
            }
            for turn in visible
        ]
        assert request["instructions"] == pipeline.SYSTEM_PROMPT
        assert json.loads(request["input"])["visible_conversation"] == payload[
            "visible_conversation"
        ]
        assert "tools" not in request
        assert images == []


@pytest.mark.parametrize("text,error", [
    ("Thank you for taking the time to explain. " * 8, "invalid_reply_length"),
    ("Thank you; see https://example.org.", "link_or_address"),
    ("Thank you @reader.", "mention"),
    ("Thank you for explaining #kindness.", "hashtag"),
    ("Thank you for explaining 😀", "emoji"),
])
def test_natural_conversation_retains_mechanical_restrictions(text, error):
    """Changing prose policy does not change public-post limits."""
    _, _, payload, _ = evidence_inputs()
    with pytest.raises(pipeline.ReplyValidationError, match=error):
        pipeline.validate_model_output(raw_decision(reply=text), payload=payload)


def test_natural_conversation_cannot_repeat_recent_reply():
    """Ordinary wording still undergoes exact and near-duplicate detection."""
    _, _, payload, _ = evidence_inputs()
    text = NATURAL_REPLIES[0][1]
    with pytest.raises(pipeline.ReplyValidationError, match="exact_duplicate_reply"):
        pipeline.validate_model_output(raw_decision(reply=text), payload=payload, comparison_replies=[text])


@pytest.mark.parametrize("text", [
    "Thank you. I appreciate the care. Let us leave it there.",
    "Not quite. A free economy gives people more scope to improve their position, "
    "but poverty is not always chosen and no outcome is just merely because it "
    "occurs in a market. Socialism’s deeper fault is that political control "
    "suppresses freedom and prosperity.",
])
def test_three_sentence_reply_survives_validation_and_recovery(text):
    """Retain concise replies previously lost solely to the sentence ceiling."""
    source, repository, payload, facts = evidence_inputs()
    output = pipeline.validate_model_output(raw_decision(reply=text), payload=payload)
    draft = pipeline.create_durable_draft(
        output=output, payload=payload, fact_map=facts, images=[], target_author_id="200",
    )
    assert pipeline.validate_persisted_draft(draft, context=source, repository=repository) == draft


def test_three_supported_factual_sentences_survive_validation_and_recovery():
    """The factual inventory must not impose an indirect two-sentence ceiling."""
    repository = FakeRepository(3)
    source = context()
    payload, facts = pipeline.build_model_payload(context=source, repository=repository)
    claims = [{"text": fact["passage"], "fact_ids": [fact["id"]]}
              for fact in payload["trusted_facts"][:3]]
    response = json.loads(raw_decision(
        kind="direct_factual", reply=" ".join(claim["text"] for claim in claims),
        facts=["F1", "F2", "F3"],
    ))
    response["factual_claims"] = claims
    output = pipeline.validate_model_output(json.dumps(response), payload=payload)
    draft = pipeline.create_durable_draft(
        output=output, payload=payload, fact_map=facts, images=[], target_author_id="200",
    )
    assert pipeline.validate_persisted_draft(draft, context=source, repository=repository) == draft

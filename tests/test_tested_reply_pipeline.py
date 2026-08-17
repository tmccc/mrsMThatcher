from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest

import tested_reply_pipeline as pipeline


@dataclass(frozen=True)
class Passage:
    evidence_id: str = "fact-1"

    def prompt_record(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "passage": "The supplied local record directly establishes the requested fact.",
            "verification_status": "verified",
        }


class Repository:
    def __init__(self, facts: bool = False):
        self.facts = facts

    def resolve_context_quotation(self, _context):
        return None

    def candidate_passages(self, _text, **_kwargs):
        return [Passage()] if self.facts else []

    def detected_authorised_quote_ids(self, _reply):
        return set()

    def exact_quote_occurs_in_reply(self, _reply, _wording):
        return False

    def exact_quote_is_authorised(self, _wording):
        return False

    def detected_authorised_quote_ids_outside_exact(self, _reply, _wording):
        return set()


def context(text: str) -> dict:
    return {
        "target_id": "100",
        "thread_id": "90",
        "lane": "mention",
        "incoming_contribution": text,
        "quoted_post": None,
        "parent_thread": [
            {"post_id": "90", "author_role": "account", "text": "Freedom requires responsibility."}
        ],
        "clarification_request": None,
        "current_date": "2026-08-16",
    }


class Transport:
    def __init__(self, *, gate: str = "reply", writer: str = "Thank you — that is kind of you.", review: str = "require_claim_free_reply"):
        self.gate = gate
        self.writer = writer
        self.review = review
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        stage = kwargs["stage"]
        if stage == "candidate_backed_engagement":
            return {"decision": self.gate, "reply": "PRIVATE GATE CANDIDATE" if self.gate == "reply" else ""}
        if stage.startswith("reply_necessity_") or stage.startswith("allegation_review_"):
            return {"outcome": self.review}
        if stage == "focused_group_review":
            return {"outcome": "allow_reply"}
        if stage.startswith("authentication_review_"):
            return {"outcome": "require_supported_factual_reply"}
        if stage in {"narrow_claim_audit", "cleanup_claim_audit", "diversity_claim_audit"}:
            return {"outcome": "pass"}
        if stage in {"writer_v3_initial", "bounded_claim_cleanup", "exact_duplicate_repair"}:
            reply = "A fresh formulation keeps the point clear." if stage == "exact_duplicate_repair" else self.writer
            return {"status": "reply", "reply": reply}
        raise AssertionError(stage)


def enabled_config() -> dict:
    value = pipeline.default_config()
    value["enabled"] = True
    return value


def test_stage_telemetry_is_allow_listed_and_text_free() -> None:
    private_marker = "PRIVATE MATCHED TEXT MUST NOT BE LOGGED"
    telemetry = pipeline.stage_telemetry((
        {"stage": "A_B_C", "suppressed": False, "reason": None, "text": private_marker},
        {"stage": "candidate_backed_engagement", "provider": "xAI", "schema_valid": True},
        {"stage": "xai_gate_decision", "decision": "no_reply", "candidate": private_marker},
        {"stage": "reply_necessity_1", "provider": "OpenAI", "schema_valid": True},
        {"stage": "reply_necessity_2", "provider": "OpenAI", "schema_valid": False, "error": "ValueError"},
        {"stage": "reply_necessity_3", "provider": "OpenAI", "schema_valid": True},
        {
            "stage": "reply_necessity_resolution",
            "call_outcomes": ["require_claim_free_reply", None, "require_claim_free_reply"],
            "majority_outcome": "require_claim_free_reply",
            "majority_resolvable": True,
            "invalid_or_refused_calls": 1,
        },
        {
            "stage": "allegation_conspiracy_detector",
            "candidate": True,
            "categories": ["corruption_or_fraud"],
            "matched_text": private_marker,
        },
        {
            "stage": "allegation_conspiracy_resolution",
            "majority_outcome": "confirm_no_reply",
            "majority_resolvable": True,
            "invalid_or_refused_calls": 0,
            "free_form_reason": private_marker,
        },
        {"stage": "attribution_route_v2", "route_class": "none", "reply_requirement": None},
        {
            "stage": "narrow_claim_audit_risk",
            "risky": True,
            "categories": ["private_motive"],
            "matched_text": [private_marker],
        },
        {"stage": "narrow_claim_audit", "provider": "xAI", "schema_valid": True},
        {"stage": "narrow_claim_audit_outcome", "outcome": "rewrite_claim_free"},
        {"stage": "bounded_claim_cleanup", "provider": "OpenAI", "schema_valid": True},
        {"stage": "exact_duplicate_check", "exact_duplicate": True, "near_duplicate_count": 2},
        {"stage": "exact_duplicate_repair", "provider": "OpenAI", "schema_valid": True},
        {"stage": "exact_duplicate_repair_outcome", "outcome": "repaired"},
        {"stage": "final_deterministic_validation", "rejection": None},
    ))

    assert telemetry["provider_call_counts"] == {"xAI": 2, "OpenAI": 5}
    assert telemetry["schema_invalid_stages"] == ["reply_necessity_2"]
    assert telemetry["reply_necessity_outcome"] == "require_claim_free_reply"
    assert telemetry["reply_necessity_majority_resolvable"] is True
    assert telemetry["reply_necessity_invalid_calls"] == 1
    assert telemetry["allegation_conspiracy_candidate"] is True
    assert telemetry["allegation_conspiracy_categories"] == ["corruption_or_fraud"]
    assert telemetry["allegation_conspiracy_outcome"] == "confirm_no_reply"
    assert telemetry["allegation_conspiracy_majority_resolvable"] is True
    assert telemetry["claim_risk_categories"] == ["private_motive"]
    assert telemetry["claim_audit_outcomes"] == [{
        "stage": "narrow_claim_audit_outcome",
        "outcome": "rewrite_claim_free",
    }]
    assert telemetry["claim_cleanup_called"] is True
    assert telemetry["exact_duplicate_detected"] is True
    assert telemetry["duplicate_repair_called"] is True
    assert telemetry["duplicate_repair_outcome"] == "repaired"
    assert telemetry["final_validation"] == "passed"
    assert private_marker not in json.dumps(telemetry, sort_keys=True)


def run(text: str, transport: Transport, *, facts: bool = False, recent=None):
    return pipeline.run_reply_pipeline(
        context=context(text),
        config=enabled_config(),
        repository=Repository(facts),
        transport=transport,
        maximum_reply_length=270,
        recent_replies=recent or [],
        media_context=None,
    )


def test_frozen_prompt_hashes_and_provider_profiles() -> None:
    expected = {
        "XAI_GATE_PROMPT": "e145e67c365cc295174e00284568fd05c1848ad85e63f01815891ca00ca5ba55",
        "REPLY_NECESSITY_PROMPT": "66d2c3ef36a5cb1560f025ed1ead588e3c2e343e5dcde51d36508c3dd545b13b",
        "GROUP_REVIEW_PROMPT": "2355c7056d0ba93ddd79d731cc2999fc2e5fecd55eb8444814c1d21c06e7e6fc",
        "WRITER_PROMPT": "832b086a4e32dfec255143146ab7a7d0674b4773041b643b037187402d8ae717",
        "CLAIM_AUDIT_PROMPT": "a2e0f3e78bdd3aa5a45e4fc2ba1eed7819043b4ffec97caeeb29c66ac6824589",
        "CLAIM_CLEANUP_PROMPT": "04a926149d8e5440f6b6426bfbba173112c01776f6bad1698c81755badffc7a5",
        "DIVERSITY_PROMPT": "fcb4b58e153023cd638642158fbdd4a53213fe2e69320ee92a0b48c89563b66b",
    }
    for name, digest in expected.items():
        assert hashlib.sha256(getattr(pipeline, name).encode()).hexdigest() == digest
    config = pipeline.default_config()
    assert (config["xai_model"], config["xai_reasoning_effort"]) == ("grok-4.3", "low")
    assert (config["openai_model"], config["openai_reasoning_effort"]) == ("gpt-5.6-sol", "medium")
    assert config["timeout_seconds"] == 180


@pytest.mark.parametrize(
    "text",
    ["👏", "Thank you!", "Quite right.", "A fair point, well said."],
)
def test_positive_social_and_brief_agreement_receive_warm_reply(text: str) -> None:
    transport = Transport()
    result = run(text, transport)
    assert result.status == "approved"
    assert str(result.reply) == "Thank you — that is kind of you."
    assert result.reply.pipeline_metadata["final_reply_kind"] == "unknown"
    assert result.reply.pipeline_metadata["mode"] == "opinion_or_principle"
    assert result.reply.pipeline_metadata["tone"] == "unknown"


@pytest.mark.parametrize(
    ("text", "writer", "facts", "expected_kind"),
    [
        ("Could you say which point you mean?", "Which part did you have in mind?", False, "unknown"),
        ("Did Margaret Thatcher really say this?", "The local transcript records those exact words.", True, "factual"),
        ("Liberty also requires institutions.", "Liberty endures only when institutions remain answerable.", False, "unknown"),
    ],
)
def test_reply_kind_metadata_is_small_and_deterministic(
    text: str,
    writer: str,
    facts: bool,
    expected_kind: str,
) -> None:
    result = run(text, Transport(writer=writer), facts=facts)

    assert result.status == "approved"
    assert result.reply.pipeline_metadata["final_reply_kind"] == expected_kind
    assert result.reply.pipeline_metadata["mode"] == (
        "direct_factual_answer" if facts else "opinion_or_principle"
    )
    assert result.reply.pipeline_metadata["tone"] == "unknown"


def test_supplied_trusted_facts_are_not_reported_as_used_facts() -> None:
    result = run(
        "Did Margaret Thatcher really say this?",
        Transport(writer="The local transcript records those exact words."),
        facts=True,
    )

    metadata = result.reply.pipeline_metadata
    assert metadata["trusted_facts_supplied_count"] == 1
    assert metadata["trusted_fact_ids_supplied"] == ["fact-1"]
    assert metadata["used_fact_count"] == "unknown"
    assert metadata["used_fact_ids"] is None
    assert metadata["evidence_reference_count"] is None
    telemetry = pipeline.stage_telemetry(result.audit)
    assert telemetry["trusted_facts_supplied_count"] == 1
    assert telemetry["trusted_fact_ids_supplied"] == ["fact-1"]


def test_literal_bare_mention_is_deterministically_suppressed_without_calls() -> None:
    transport = Transport()
    result = run("@MrsMThatcher", transport)

    assert result.status == "no_reply"
    assert result.reason == (
        "incoming contribution has no lexical content beyond handle(s) and/or URL(s)"
    )
    assert result.model_call_count == 0
    assert transport.calls == []
    telemetry = pipeline.stage_telemetry(result.audit)
    assert telemetry["deterministic_suppressed"] is True
    assert telemetry["deterministic_reason"] == result.reason


def test_gate_no_reply_is_reviewed_three_times_and_can_be_overturned() -> None:
    transport = Transport(gate="no_reply")
    result = run("I disagree: liberty also requires institutions.", transport)
    assert result.status == "approved"
    review_calls = [call for call in transport.calls if call["stage"].startswith("reply_necessity_")]
    assert len(review_calls) == 3
    assert all(call["reasoning_effort"] == "medium" for call in review_calls)


def test_candidate_backed_gate_output_is_never_sent_downstream() -> None:
    transport = Transport()
    result = run("A civil criticism of the account's interpretation.", transport)
    assert result.status == "approved"
    assert all(
        "PRIVATE GATE CANDIDATE" not in str(call["payload"])
        for call in transport.calls[1:]
    )


NO_REPLY_ALLEGATIONS = {
    "synthetic-13b": "Minister Rowan privatised it because his friends paid him.",
    "synthetic-14b": "Climate science is a global fraud run by the scientists at the Northbridge Institute.",
    "synthetic-16b": "SimCity proves the planners are lizards. Wake up.",
    "synthetic-17b": "The Chancellor secretly owns the firms that will profit from this tax rise.",
    "synthetic-18b": "Arcadia Bank coordinates in secret with the government to enslave families through debt.",
    "synthetic-21b": "The Northbridge mayor is deliberately creating shortages to punish tenants.",
    "synthetic-26a": "Minister Rowan is part of a trafficking ring.",
}
REPLY_CONTROLS = {
    "synthetic-13a": "Privatisation can create private monopolies unless regulation is strong.",
    "synthetic-14a": "Climate policy should be judged by costs as well as intentions.",
    "synthetic-16a": "This reminds me of SimCity: every intervention creates another queue.",
    "synthetic-17a": "This tax rise will destroy every small business.",
    "synthetic-18a": "Loose credit pushes families towards debt and expensive housing.",
    "synthetic-21a": "Rent controls always make housing shortages worse.",
    "synthetic-24b": "This account overstates her economic record.",
    "synthetic-26b": "Was Minister Rowan ever charged with the offence alleged in this post?",
    "synthetic-27b": "Why was she unwilling to turn?",
}


@pytest.mark.parametrize(("case_id", "text"), NO_REPLY_ALLEGATIONS.items())
def test_named_allegation_sanity_no_reply_candidates(case_id: str, text: str) -> None:
    assert pipeline.allegation_review_candidate(context(text))["candidate"], case_id


@pytest.mark.parametrize(("case_id", "text"), REPLY_CONTROLS.items())
def test_named_allegation_sanity_reply_controls(case_id: str, text: str) -> None:
    assert not pipeline.allegation_review_candidate(context(text))["candidate"], case_id


def test_named_allegation_invokes_three_call_review_and_can_suppress() -> None:
    transport = Transport(review="confirm_no_reply")
    result = run(NO_REPLY_ALLEGATIONS["synthetic-17b"], transport)
    assert result.status == "no_reply"
    assert result.reason == "allegation_review_suppression"
    assert len([call for call in transport.calls if call["stage"].startswith("allegation_review_")]) == 3


class SequencedReviewTransport(Transport):
    def __init__(self, *, gate: str, votes: list[str | None]):
        super().__init__(gate=gate)
        self.votes = list(votes)

    def __call__(self, **kwargs):
        stage = kwargs["stage"]
        if stage.startswith("reply_necessity_") or stage.startswith("allegation_review_"):
            self.calls.append(kwargs)
            vote = self.votes.pop(0)
            return {"outcome": vote} if vote is not None else {"malformed": True}
        return super().__call__(**kwargs)


@pytest.mark.parametrize(
    ("gate", "text", "reason", "outcome_field", "resolvable_field", "invalid_field"),
    [
        (
            "no_reply",
            "I disagree: liberty also requires institutions.",
            "reply_necessity_review",
            "reply_necessity_outcome",
            "reply_necessity_majority_resolvable",
            "reply_necessity_invalid_calls",
        ),
        (
            "reply",
            NO_REPLY_ALLEGATIONS["synthetic-17b"],
            "allegation_review_suppression",
            "allegation_conspiracy_outcome",
            "allegation_conspiracy_majority_resolvable",
            "allegation_conspiracy_invalid_calls",
        ),
    ],
    ids=["reply-necessity", "allegation-review"],
)
@pytest.mark.parametrize(
    ("votes", "expected_outcome", "expected_resolvable", "expected_invalid"),
    [
        (
            [
                "confirm_no_reply_spam_or_abuse",
                "confirm_no_reply",
                "require_claim_free_reply",
            ],
            "confirm_no_reply",
            False,
            0,
        ),
        (
            ["confirm_no_reply_spam_or_abuse", "confirm_no_reply", None],
            "confirm_no_reply",
            False,
            1,
        ),
        (
            [
                "confirm_no_reply_spam_or_abuse",
                "confirm_no_reply_spam_or_abuse",
                None,
            ],
            "confirm_no_reply_spam_or_abuse",
            True,
            1,
        ),
    ],
    ids=["three-way-split", "one-invalid-one-one", "one-invalid-two-punitive"],
)
def test_review_resolution_distinguishes_non_punitive_fallback_from_punitive_majority(
    gate: str,
    text: str,
    reason: str,
    outcome_field: str,
    resolvable_field: str,
    invalid_field: str,
    votes: list[str | None],
    expected_outcome: str,
    expected_resolvable: bool,
    expected_invalid: int,
) -> None:
    import mrsMThatcher2 as bot

    transport = SequencedReviewTransport(gate=gate, votes=votes)

    result = run(text, transport)
    telemetry = pipeline.stage_telemetry(result.audit)

    assert result.status == "no_reply"
    assert result.reason == reason
    assert telemetry[outcome_field] == expected_outcome
    assert telemetry[resolvable_field] is expected_resolvable
    assert telemetry[invalid_field] == expected_invalid
    assert bot.tested_pipeline_no_reply_qualifies_for_author_quarantine(
        status=result.status,
        reason=result.reason,
        telemetry=telemetry,
    ) is (
        expected_outcome == "confirm_no_reply_spam_or_abuse"
        and expected_resolvable
    )
    assert transport.votes == []


def test_group_hostility_candidate_invokes_narrow_review() -> None:
    text = "Those people can never belong in our community."
    assert pipeline.group_hostility_review_candidate(context(text))["candidate"]
    transport = Transport()
    result = run(text, transport)
    assert result.status == "approved"
    assert any(call["stage"] == "focused_group_review" for call in transport.calls)


def test_group_hostility_review_can_suppress() -> None:
    transport = Transport()

    def suppressing_call(**kwargs):
        if kwargs["stage"] == "focused_group_review":
            transport.calls.append(kwargs)
            return {"outcome": "suppress_group_hostility"}
        return transport(**kwargs)

    result = pipeline.run_reply_pipeline(
        context=context("Those people can never belong in our community."),
        config=enabled_config(), repository=Repository(), transport=suppressing_call,
        maximum_reply_length=270,
    )
    assert result.status == "no_reply"
    assert result.reason == "group_hostility_suppression"


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("Did Margaret Thatcher really say this?", "direct_authentication"),
        ("Leaving aside the attribution, what principle matters here?", "premise_neutral_comparison"),
        ("What does this quotation mean?", "meaning_only"),
        ("Policy should reward responsibility.", "none"),
    ],
)
def test_attribution_route_v2(text: str, route: str) -> None:
    assert pipeline.attribution_route_v2(context(text), [])["route_class"] == route


def test_supported_direct_authentication_uses_three_sol_calls_and_factual_audit() -> None:
    transport = Transport(writer="The local transcript records those exact words.")
    result = run("Did Margaret Thatcher really say this?", transport, facts=True)
    assert result.status == "approved"
    assert len([call for call in transport.calls if call["stage"].startswith("authentication_review_")]) == 3
    assert any(call["stage"] == "narrow_claim_audit" for call in transport.calls)


def test_claim_risk_and_exact_duplicate_repair() -> None:
    risk = pipeline.detect_claim_risk("It was introduced in 1979.", "general")
    assert risk["categories"] == ["numeric_or_date", "precise_historical_claim"]
    transport = Transport()
    result = run("Thank you.", transport, recent=["Thank you — that is kind of you."])
    assert result.status == "approved"
    assert str(result.reply) == "A fresh formulation keeps the point clear."
    assert any(call["stage"] == "exact_duplicate_repair" for call in transport.calls)


def test_narrow_claim_cleanup_is_bounded_and_reaudited() -> None:
    class CleanupTransport(Transport):
        def __call__(self, **kwargs):
            stage = kwargs["stage"]
            if stage == "narrow_claim_audit":
                self.calls.append(kwargs)
                return {"outcome": "rewrite_claim_free"}
            if stage == "bounded_claim_cleanup":
                self.calls.append(kwargs)
                return {"status": "reply", "reply": "The principle is responsibility rather than privilege."}
            return super().__call__(**kwargs)

    transport = CleanupTransport(writer="It was introduced in 1979.")
    result = run("What principle should guide the policy?", transport)
    assert result.status == "approved"
    assert str(result.reply) == "The principle is responsibility rather than privilege."
    assert [call["stage"] for call in transport.calls].count("bounded_claim_cleanup") == 1
    assert any(call["stage"] == "cleanup_claim_audit" for call in transport.calls) is False
    # The cleaned prose contains no deterministic risk cue, so the second audit is
    # a local bypass rather than another provider call.
    assert any(row["stage"] == "cleanup_claim_audit_risk" for row in result.audit)


def test_persisted_tested_draft_revalidates_and_rejects_new_exact_duplicate() -> None:
    transport = Transport()
    result = run("Thank you.", transport)
    record = pipeline.validate_persisted_draft(
        result.reply.draft_record,
        context=context("Thank you."),
        config=enabled_config(),
        repository=Repository(),
        maximum_reply_length=270,
    )
    assert record["proposed_reply"] == str(result.reply)
    with pytest.raises(ValueError, match="exact duplicate"):
        pipeline.validate_persisted_draft(
            record,
            context=context("Thank you."),
            config=enabled_config(),
            repository=Repository(),
            maximum_reply_length=270,
            recent_replies=[str(result.reply)],
        )


def test_http_adapter_preserves_provider_payload_isolation(monkeypatch) -> None:
    import json
    import mrsMThatcher2 as bot

    captured = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": '{"outcome":"pass"}'}}]}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda _operation: None)
    monkeypatch.setattr(bot, "OPENAI_BASE", "https://openai.invalid/v1")
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "set-in-test")
    visible = {"context": context("A visible contribution."), "trusted_facts": [], "media_context": []}
    result = bot.tested_pipeline_structured_call(
        provider="OpenAI", stage="payload_isolation", model="gpt-5.6-sol",
        system_prompt="Frozen prompt", payload=visible,
        response_schema=pipeline.CLAIM_AUDIT_SCHEMA, timeout_seconds=180,
        max_output_tokens=300, reasoning_effort="medium",
    )
    assert result == '{"outcome":"pass"}'
    request = captured["json"]
    assert json.loads(request["messages"][1]["content"]) == visible
    assert request["reasoning_effort"] == "medium"
    assert request["temperature"] == 1
    assert request["max_completion_tokens"] == 300
    assert request["store"] is False
    assert "tools" not in request and "search" not in request


def test_production_wrapper_logs_safe_tested_pipeline_stage_summary(monkeypatch) -> None:
    import copy
    import mrsMThatcher2 as bot

    private_marker = "PRIVATE PIPELINE TEXT MUST NOT BE LOGGED"
    config = copy.deepcopy(pipeline.default_config())
    config["enabled"] = True
    events = []
    result = pipeline.PipelineResult(
        None,
        "no_reply",
        "reply_necessity_review",
        4,
        0,
        (
            {"stage": "A_B_C", "suppressed": False, "reason": None},
            {"stage": "candidate_backed_engagement", "provider": "xAI", "schema_valid": True},
            {"stage": "xai_gate_decision", "decision": "no_reply", "reply": private_marker},
            {"stage": "reply_necessity_1", "provider": "OpenAI", "schema_valid": True},
            {"stage": "reply_necessity_2", "provider": "OpenAI", "schema_valid": True},
            {"stage": "reply_necessity_3", "provider": "OpenAI", "schema_valid": True},
            {
                "stage": "reply_necessity_resolution",
                "majority_outcome": "confirm_no_reply",
                "majority_resolvable": True,
                "invalid_or_refused_calls": 0,
                "reason": private_marker,
            },
        ),
    )
    monkeypatch.setattr(bot, "tested_reply_pipeline", config)
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(pipeline, "run_reply_pipeline", lambda **_kwargs: result)
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))

    assert bot.generate_ai_first_reply(context("A visible contribution.")) is None

    assert [name for name, _values in events] == [
        "ai_reply_pipeline_stage_summary",
        "ai_reply_pipeline_decision",
    ]
    summary = events[0][1]
    assert summary["provider_call_counts"] == {"xAI": 1, "OpenAI": 3}
    assert summary["xai_gate_decision"] == "no_reply"
    assert summary["reply_necessity_outcome"] == "confirm_no_reply"
    assert summary["reply_necessity_majority_resolvable"] is True
    assert summary["terminal_reason"] == "reply_necessity_review"
    assert private_marker not in json.dumps(summary, sort_keys=True)
    decision = events[1][1]
    assert decision["final_reply_kind"] == "no_reply"
    assert decision["tone"] == "unknown"
    assert decision["used_fact_count"] == 0
    assert decision["trusted_facts_supplied_count"] == 0


def test_production_decision_logs_routing_kind_and_unknown_fact_use(monkeypatch) -> None:
    import copy
    import mrsMThatcher2 as bot

    config = copy.deepcopy(pipeline.default_config())
    config["enabled"] = True
    result = run("Thank you!", Transport())
    events = []
    monkeypatch.setattr(bot, "tested_reply_pipeline", config)
    monkeypatch.setattr(bot, "reply_evidence_repository", Repository)
    monkeypatch.setattr(pipeline, "run_reply_pipeline", lambda **_kwargs: result)
    monkeypatch.setattr(bot, "log_event", lambda name, **values: events.append((name, values)))

    assert bot.generate_ai_first_reply(context("Thank you!")) == result.reply

    decision = events[-1][1]
    assert decision["final_reply_kind"] == "unknown"
    assert decision["reply_requirement"] == "general"
    assert decision["route_source"] == "xai_gate"
    assert decision["tone"] == "unknown"
    assert decision["trusted_facts_supplied_count"] == 0
    assert decision["trusted_fact_ids_supplied"] == []
    assert decision["used_fact_count"] == "unknown"
    assert decision["used_fact_ids"] is None

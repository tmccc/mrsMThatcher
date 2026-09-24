from __future__ import annotations

import single_call_reply as reply_pipeline_module

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
from tests.helpers.reply_evaluation import legacy_reply_evaluator
from tests.helpers.reply_fixtures import patch_reply_draft_method, patch_reply_owner_method

import copy
import io
import json
import logging

import pytest

import mrsMThatcher2 as bot
import single_call_reply as pipeline
from tests.fake_api_server import FakeApiServer, load_scenario
from tests.helpers.integration_harness import (
    SCENARIOS,
    prepare_base_dir,
    read_json,
    run_cycle,
)
from tests.helpers.mention_fixtures import (
    configure_provider_free_mention_check,
    mention,
)
from tests.helpers.single_call_fixtures import (
    FakeHttpResponse,
    FakeRepository,
    context as pipeline_context,
    enabled_config,
    raw_decision,
    response_envelope,
)


def _usage() -> dict[str, object]:
    return {
        "input_tokens": 120,
        "input_tokens_details": {"cached_tokens": 80},
        "output_tokens": 25,
        "output_tokens_details": {"reasoning_tokens": 10},
        "total_tokens": 145,
    }


def _noncompleted_response(
    status: str,
    *,
    incomplete_reason: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": "resp_failure_routing",
        "status": status,
        "model": pipeline.MODEL,
        "output": [],
        "usage": _usage(),
    }
    if incomplete_reason is not None:
        result["incomplete_details"] = {"reason": incomplete_reason}
    return result


def _completed_refusal_response() -> dict[str, object]:
    return {
        "id": "resp_content_refusal",
        "status": "completed",
        "model": pipeline.MODEL,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "refusal", "refusal": "I cannot assist."}
                ],
            }
        ],
        "usage": _usage(),
    }


def _no_reply_response() -> dict[str, object]:
    return response_envelope(
        raw_decision(
            decision="no_reply",
            kind="no_reply",
            reply="",
            reason="completed_exchange",
        )
    )


def _run_response(response: dict[str, object]) -> pipeline.PipelineResult:
    return pipeline.run_reply_pipeline(
        context=pipeline_context(turns=1),
        config=enabled_config(),
        repository=FakeRepository(),
        transport=lambda **_kwargs: {
            "response": response,
            "latency_ms": 37,
            "request_attempt_count": 1,
        },
    )


def _candidate_context(candidate: dict[str, object]) -> dict[str, object]:
    target_id = str(candidate["id"])
    candidate_source = str(candidate.get("_source") or "mention")
    contribution = str(candidate["text"])
    return {
        "target_id": target_id,
        "target_author_id": str(candidate["author_id"]),
        "thread_id": str(candidate.get("conversation_id") or target_id),
        "root_post_id": str(candidate.get("conversation_id") or target_id),
        "parent_post_id": None,
        "lane": candidate_source,
        "incoming_contribution": contribution,
        "quoted_post": None,
        "parent_thread": [],
        "visible_conversation": [
            {
                "post_id": target_id,
                "author_role": "user",
                "text": contribution,
            }
        ],
        "visual_description": None,
        "clarification_request": None,
        "current_date": "2026-09-04",
    }


def test_second_429_metadata_reaches_global_openai_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the final 429 status/reset/attempt metadata through every layer."""

    current = 2_000_000_000
    responses = [
        FakeHttpResponse(429, headers={"Retry-After": "1"}),
        FakeHttpResponse(429, headers={"Retry-After": "1"}),
    ]
    events: list[tuple[str, dict[str, object]]] = []
    state = bot.default_state()

    monkeypatch.setattr(bot, "single_call_reply", enabled_config())
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "report_bot_health_progress", lambda *_args: None)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bot.requests, "post", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1),
        None,
        state=state,

    )
    assert result.reply is None

    assert responses == []
    assert result.error_category == "provider_http_429"
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == current + 1 + 60
    assert state["openai_api_cooldown_reason"] == "openai returned 429/rate limit"
    decision = next(fields for event, fields in events if event == "single_call_reply_decision")
    assert decision["provider_status_code"] == 429
    assert decision["provider_reset_epoch"] == current + 1
    assert decision["provider_retry_after_seconds"] == 1
    assert decision["provider_request_attempt_count"] == 2
    assert state.get("reply_evaluation_records", {}) == {}
    assert state["author_evaluation_quarantines"] == {}
    assert state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 0


@pytest.mark.parametrize("second_failure", ["http_503", "timeout"])
def test_first_429_metadata_survives_a_different_second_failure(
    monkeypatch: pytest.MonkeyPatch,
    second_failure: str,
) -> None:
    """Preserve the first rate-limit signal through the bounded retry."""

    current = 2_000_000_000
    first = FakeHttpResponse(429, headers={"Retry-After": "1"})
    responses: list[object] = [first]
    if second_failure == "http_503":
        responses.append(FakeHttpResponse(503))
    else:
        responses.append(bot.requests.Timeout("unit timeout after 429"))
    events: list[tuple[str, dict[str, object]]] = []
    state = bot.default_state()

    def post(*_args: object, **_kwargs: object) -> FakeHttpResponse:
        result = responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, FakeHttpResponse)
        return result

    monkeypatch.setattr(bot, "single_call_reply", enabled_config())
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "report_bot_health_progress", lambda *_args: None)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1),
        None,
        state=state,

    )
    assert result.reply is None

    assert responses == []
    assert result.error_category == (
        "provider_http_503"
        if second_failure == "http_503"
        else "provider_ambiguous_timeout"
    )
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == current + 1 + 60
    decision = next(
        fields for event, fields in events if event == "single_call_reply_decision"
    )
    assert decision["provider_status_code"] == 429
    assert decision["provider_reset_epoch"] == current + 1
    assert decision["provider_retry_after_seconds"] == 1
    assert decision["provider_request_attempt_count"] == 2


@pytest.mark.parametrize("second_envelope", ["malformed_json", "non_object"])
def test_first_429_metadata_survives_a_malformed_success_envelope(
    monkeypatch: pytest.MonkeyPatch,
    second_envelope: str,
) -> None:
    """Keep rate-limit health metadata when the bounded retry is malformed."""

    current = 2_000_000_000
    first = FakeHttpResponse(429, headers={"Retry-After": "1"})
    second = FakeHttpResponse(200, body=[])
    if second_envelope == "malformed_json":
        second.json = lambda: (_ for _ in ()).throw(ValueError("bad JSON"))
    responses = [first, second]
    events: list[tuple[str, dict[str, object]]] = []
    state = bot.default_state()

    monkeypatch.setattr(bot, "single_call_reply", enabled_config())
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "report_bot_health_progress", lambda *_args: None)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bot.requests, "post", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1),
        None,
        state=state,

    )
    assert result.reply is None

    assert responses == []
    assert result.error_category == "provider_envelope"
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == current + 1 + 60
    decision = next(
        fields for event, fields in events if event == "single_call_reply_decision"
    )
    assert decision["provider_status_code"] == 429
    assert decision["provider_reset_epoch"] == current + 1
    assert decision["provider_retry_after_seconds"] == 1
    assert decision["provider_request_attempt_count"] == 2


def test_first_429_then_local_rejection_preserves_both_dispositions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retire invalid prose locally while accounting for the real prior 429."""

    current = 2_000_000_000
    responses = [
        FakeHttpResponse(429, headers={"Retry-After": "1"}),
        FakeHttpResponse(
            200,
            body=response_envelope(raw_decision(reply="word " * 200)),
        ),
    ]
    state = bot.default_state()
    events: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(bot, "single_call_reply", enabled_config())
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "report_bot_health_progress", lambda *_args: None)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bot.requests, "post", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr(
        bot, "log_event", lambda event, **fields: events.append((event, fields)),
    )

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1),
        None,
        state=state,

    )
    assert result.reply is None

    assert responses == []
    assert result.error_category == "local_validation"
    assert bot._is_terminal_candidate_local_failure(result) is True
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == current + 1 + 60
    decision = next(
        fields for event, fields in events if event == "single_call_reply_decision"
    )
    assert decision["validation_error_codes"] == ["invalid_reply_length_or_whitespace"]
    assert decision["provider_request_attempt_count"] == 2
    assert decision["provider_status_code"] == 429


@pytest.mark.parametrize(
    ("response", "category", "codes", "terminal"),
    [
        (
            response_envelope(raw_decision(reply="A reply @name #topic")),
            "local_validation",
            ["reply_contains_hashtag", "reply_contains_link_or_address",
             "reply_contains_mention"], True,
        ),
        (
            response_envelope(raw_decision(
                kind="direct_factual", facts=["not-a-schema-fact-id"],
            )),
            "schema_validation",
            ["direct_factual_missing_fact_id", "invalid_used_fact_ids"], False,
        ),
        ({"status": "completed", "output": []}, "provider_schema", [], False),
    ],
)
def test_validation_diagnostics_preserve_candidate_and_provider_health_routing(
    monkeypatch: pytest.MonkeyPatch, response: dict[str, object],
    category: str, codes: list[str], terminal: bool,
) -> None:
    """Add rule telemetry without turning a local veto into a global API error."""

    current = 2_000_000_000
    result = _run_response(response)
    state = bot.default_state()
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(reply_pipeline_module, "run_reply_pipeline", lambda **_kwargs: result)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot, "log_event", lambda event, **fields: events.append((event, fields)),
    )

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1), None, state=state,
    )
    assert result.reply is None

    assert result.error_category == category
    assert bot._is_terminal_candidate_local_failure(result) is terminal
    assert bot._is_openai_provider_health_failure(category) is not terminal
    assert state["openai_error_epochs"] == ([] if terminal else [current])
    assert state["openai_api_cooldown_until_epoch"] == 0
    decision = next(
        fields for event, fields in events if event == "single_call_reply_decision"
    )
    assert decision["validation_error_codes"] == codes
    assert decision["error_category"] == category
    assert decision["failure_reason"] == "model_response_validation_failed"


@pytest.mark.parametrize(
    ("reply", "kind", "code"),
    [
        ("Yes, this account is automated.", "direct_factual", "direct_factual_missing_fact_id"),
        ("Een antwoord. " * 30, "principle", "invalid_reply_length_or_whitespace"),
        ("Een antwoord.\n日本語の返信。", "principle", "reply_contains_line_break"),
    ],
)
def test_rejected_reply_survives_real_log_to_digest_json_without_becoming_publishable(
    monkeypatch, tmp_path, capsys, reply, kind, code,
):
    """Carry an unpublished model reply through production logging and digest CLI."""
    import mrs_log_digest as digest

    response = response_envelope(raw_decision(kind=kind, reply=reply))
    response["output"].insert(0, {
        "type": "reasoning", "summary": [{"text": "MODEL_REASONING_MUST_NOT_APPEAR"}],
    })
    result = _run_response(response)
    assert result.status == "operational_failure"
    assert result.reply is None
    assert result.model_call_count == result.provider_request_attempt_count == 1

    log_output = io.StringIO()
    logger = logging.Logger("rejected-reply-integration", logging.INFO)
    handler = logging.StreamHandler(log_output)
    handler.setFormatter(logging.Formatter(
        "2026-09-12 12:00:00 %(levelname)-8s %(funcName)s:%(lineno)d - %(message)s"
    ))
    logger.addHandler(handler)
    state = bot.default_state()
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(reply_pipeline_module, "run_reply_pipeline", lambda **_kwargs: result)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Rejected diagnostic text reached the publishable-draft path")

    patch_reply_draft_method(monkeypatch, "store", forbidden)
    source_context = pipeline_context(turns=1)
    source_context["target_id"] = "2090000000000000001"
    result = bot._reply_assembly()._reply_generation_owner().evaluate(source_context, None, state=state)
    assert result.reply is None
    assert not state.get("pending_ai_reply_drafts")
    assert not state.get("ai_reply_history")
    assert state["daily_reply_count"] == 0

    log_text = log_output.getvalue()
    assert "MODEL_REASONING_MUST_NOT_APPEAR" not in log_text
    event_lines = [line for line in log_text.splitlines() if "EVENT " in line]
    decision = next(
        event for line in event_lines
        if (event := json.loads(line.split("EVENT ", 1)[1]))["event"]
        == "single_call_reply_decision"
    )
    assert decision["rejected_reply_text"] == reply
    assert code in decision["validation_error_codes"]

    log_path = tmp_path / "mrsMThatcher.log"
    log_path.write_text(log_text, encoding="utf-8")
    assert digest.main([
        str(log_path), "--project-dir", str(tmp_path), "--no-state", "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    row = report["single_call_reply"]["validation_failure_details"]["candidates"][0]
    assert row["target_id"] == source_context["target_id"]
    assert row["rejected_reply_text"] == reply
    assert row["rejected_reply_text_status"] == "available"
    assert row["rejected_reply_text_character_count"] == len(reply)
    assert code in row["validation_error_codes"]
    assert report["single_call_reply"]["replies_posted_count"] == 0
    assert report["published_reply_text_health"]["confirmed_record_count"] == 0


def test_first_429_then_valid_decision_retains_rate_limit_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose retry metadata but do not fail a recovered valid decision."""

    current = 2_000_000_000
    responses = [
        FakeHttpResponse(429, headers={"Retry-After": "1"}),
        FakeHttpResponse(200, body=_no_reply_response()),
    ]
    events: list[tuple[str, dict[str, object]]] = []
    state = bot.default_state()

    monkeypatch.setattr(bot, "single_call_reply", enabled_config())
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "report_bot_health_progress", lambda *_args: None)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bot.requests, "post", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **fields: events.append((event, fields)),
    )

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1),
        None,
        state=state,

    )
    assert result.reply is None

    assert responses == []
    assert result.status == "no_reply"
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == current + 61
    decision = next(
        fields for event, fields in events if event == "single_call_reply_decision"
    )
    assert decision["provider_status_code"] == 429
    assert decision["provider_retry_after_seconds"] == 1
    assert decision["provider_request_attempt_count"] == 2


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply"])
def test_new_429_cooldown_stops_later_candidate_in_same_lane_cycle(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    """Do not spend on another candidate after a prior-429 local rejection."""

    current = 2_000_000_000
    state = bot.default_state()
    candidates = [mention(100, 200), mention(101, 201)]
    if lane == "hot_post_reply":
        for candidate in candidates:
            candidate.update(
                {"_source": "hot_post_reply", "_hot_original_post_id": "90"}
            )
        configure_provider_free_mention_check(
            monkeypatch, [], current_epoch=current
        )
        monkeypatch.setattr(
            bot._hot_post_discovery,
            "get_hot_post_reply_candidates",
            lambda _state, **_kwargs: copy.deepcopy(candidates),
        )
    else:
        configure_provider_free_mention_check(
            monkeypatch, candidates, current_epoch=current
        )
    responses = [
        FakeHttpResponse(429, headers={"Retry-After": "1"}),
        FakeHttpResponse(
            200,
            body=response_envelope(raw_decision(reply="word " * 200)),
        ),
        FakeHttpResponse(200, body=_no_reply_response()),
    ]

    patch_reply_owner_method(
        monkeypatch,
        bot._api_cooldowns.ApiCooldowns,
        "active",
        lambda candidate_state, *, scope="api": bool(
            scope == "openai"
            and candidate_state.get("openai_api_cooldown_until_epoch", 0) > current
        ),
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda candidate, _state: PreparedReplyContext(_candidate_context(candidate), {}),
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "report_bot_health_progress", lambda *_args: None)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bot.requests, "post", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
    assert len(responses) == 1
    assert state["reply_evaluation_records"]["100"]["outcome"] == (
        "operational_failure"
    )
    assert bot.terminal_reply_evaluation(state, "101") is None
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == current + 1 + 60
    assert state["author_evaluation_quarantines"] == {}
    assert state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 0


def test_new_429_cooldown_stops_later_quote_candidate_in_same_cycle(
    tmp_path,
) -> None:
    """Apply the same in-cycle breaker boundary to quote-tweet candidates."""

    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["quote_tweets"]["900"]["data"].append(
        {
            "id": "911",
            "text": "A second reader contribution.",
            "author_id": "311",
            "conversation_id": "911",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
            "created_at": "2026-06-30T10:01:00Z",
        }
    )
    scenario["quote_tweets"]["900"]["includes"]["users"].append(
        {
            "id": "311",
            "username": "reader_two",
            "name": "Reader Two",
            "description": "Interested in public affairs",
            "public_metrics": {
                "followers_count": 10,
                "following_count": 20,
                "tweet_count": 30,
            },
        }
    )
    scenario["openai_responses"] = [
        {"status": 429, "headers": {"Retry-After": "1"}},
        {"body": response_envelope(raw_decision(reply="word " * 200))},
        {"body": _no_reply_response()},
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
            },
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )

        result = run_cycle(base_dir, server)
        assert result.returncode == 0, result.stderr + result.stdout
        state = read_json(base_dir / "bot_state.json")
        assert len(server.openai_requests) == 2
        assert state["reply_evaluation_records"]["910"]["outcome"] == (
            "operational_failure"
        )
        assert state.get("reply_evaluation_records", {}).get("911") is None
        assert state["openai_error_epochs"]
        assert state["openai_api_cooldown_until_epoch"] > 0
        assert state["author_evaluation_quarantines"] == {}
        assert state["daily_reply_count"] == 0
        assert state["daily_quote_reply_count"] == 0
        assert server.posts == []
    finally:
        server.stop()


@pytest.mark.parametrize("exception_type", [KeyError, AssertionError, ValueError])
def test_unlabelled_transport_boundary_errors_do_not_poison_openai_health(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[Exception],
) -> None:
    """Treat an unlabelled implementation defect as unknown local health."""

    current = 2_000_000_000
    state = bot.default_state()

    def fail_transport(**_kwargs: object) -> dict[str, object]:
        raise exception_type("unlabelled unit failure")

    monkeypatch.setattr(bot, "single_call_reply", enabled_config())
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    patch_reply_owner_method(
        monkeypatch, bot._reply_model_transport.ReplyModelTransport, "call", fail_transport,
    )
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1),
        None,
        state=state,

    )
    assert result.reply is None

    assert result.error_category == "transport_internal"
    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0
    assert state["author_evaluation_quarantines"] == {}
    assert state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 0


@pytest.mark.parametrize("failure", ["http_429", "http_503", "timeout"])
def test_candidate_image_transient_failures_remain_retryable(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Treat CDN throttling, service failure and timeout as transport failures."""

    if failure == "timeout":
        def fail_get(*_args: object, **_kwargs: object) -> None:
            raise bot.requests.Timeout("unit timeout")

        monkeypatch.setattr(bot.requests, "get", fail_get)
    else:
        status_code = int(failure.removeprefix("http_"))
        monkeypatch.setattr(
            bot.requests,
            "get",
            lambda *_args, **_kwargs: FakeHttpResponse(status_code),
        )
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    media = {
        "status": "supplied",
        "photos_expected": 1,
        "photos": [
            {
                "media_key": "3_100",
                "url": "http://127.0.0.1/media/candidate.png",
                "attachment_role": "target_contribution",
                "source_post_id": "100",
            }
        ],
    }

    with pytest.raises(bot.ReplyMediaTransientUnavailable):
        bot._reply_assembly()._reply_media_owner().collect(media)


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply"])
def test_image_transport_failure_is_retried_on_a_later_lane_cycle(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    """Do not watermark or terminalise a proved transient candidate image failure."""

    state = bot.default_state()
    candidate = mention(100, 200)
    if lane == "hot_post_reply":
        candidate.update(
            {"_source": "hot_post_reply", "_hot_original_post_id": "90"}
        )
        configure_provider_free_mention_check(
            monkeypatch, [], current_epoch=2_000_000_000
        )
        monkeypatch.setattr(
            bot._hot_post_discovery, "get_hot_post_reply_candidates",
            lambda _state, **_kwargs: [candidate]
        )
    else:
        configure_provider_free_mention_check(
            monkeypatch, [candidate], current_epoch=2_000_000_000
        )
    calls: list[str] = []

    def transient(
        context: dict[str, object],
        *_args: object,
        evaluation_outcome: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> None:
        calls.append(str(context["target_id"]))
        assert evaluation_outcome is not None
        evaluation_outcome.update(
            {
                "status": "operational_failure",
                "reason": "material_image_unavailable",
                "error_category": "image_transport",
                "model_call_count": 0,
            }
        )
        return None

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(transient),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert calls == ["100", "100"]
    assert bot.terminal_reply_evaluation(state, "100") is None
    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0
    assert state["author_evaluation_quarantines"] == {}
    assert state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 0


def test_zero_call_image_failures_do_not_exhaust_mention_sol_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five permanent bad images must not defer a sixth valid mention."""

    state = bot.default_state()
    candidates = [
        mention(tweet_id, 200 + tweet_id)
        for tweet_id in range(100, 106)
    ]
    calls: list[str] = []
    configure_provider_free_mention_check(
        monkeypatch,
        candidates,
        current_epoch=2_000_000_000,
    )
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)

    def decide(
        context: dict[str, object],
        *_args: object,
        evaluation_outcome: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> None:
        target_id = str(context["target_id"])
        calls.append(target_id)
        assert evaluation_outcome is not None
        if target_id != "105":
            evaluation_outcome.update(
                {
                    "status": "operational_failure",
                    "reason": "material_image_unavailable",
                    "error_category": "image_input",
                    "model_call_count": 0,
                }
            )
        else:
            evaluation_outcome.update(
                {
                    "status": "no_reply",
                    "reason": "completed_exchange",
                    "reason_code": "completed_exchange",
                    "model_call_count": 1,
                }
            )
        return None

    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(decide),
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert calls == [str(tweet_id) for tweet_id in range(100, 106)]
    assert all(
        bot.terminal_reply_evaluation(state, str(tweet_id))["outcome"]
        == "operational_failure"
        for tweet_id in range(100, 105)
    )
    assert bot.terminal_reply_evaluation(state, "105")["outcome"] == "no_reply"
    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0
    assert state["author_evaluation_quarantines"] == {}
    assert state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 0


@pytest.mark.parametrize(
    ("response", "expected_category"),
    [
        (_noncompleted_response("refused"), "provider_refusal"),
        (
            _noncompleted_response("incomplete", incomplete_reason="content_filter"),
            "provider_incomplete_content_filter",
        ),
        (
            _noncompleted_response(
                "incomplete", incomplete_reason="max_output_tokens"
            ),
            "provider_incomplete_max_output_tokens",
        ),
        (_completed_refusal_response(), "provider_refusal"),
    ],
)
def test_content_specific_provider_outcomes_are_terminal_local_and_keep_usage(
    response: dict[str, object],
    expected_category: str,
) -> None:
    """Retire content-specific outcomes without losing paid-response telemetry."""

    result = _run_response(response)

    assert result.status == "operational_failure"
    assert result.error_category == expected_category
    assert result.provider_response_id == str(response["id"])
    assert result.provider_latency_ms == 37
    assert result.provider_request_attempt_count == 1
    assert result.provider_usage == {
        "input_tokens": 120,
        "cached_input_tokens": 80,
        "cache_write_input_tokens": 0,
        "output_tokens": 25,
        "reasoning_tokens": 10,
        "total_tokens": 145,
    }
    outcome = {
        "status": result.status,
        "error_category": result.error_category,
    }
    assert bot._is_terminal_candidate_local_failure(result) is True
    assert bot._is_openai_provider_health_failure(result.error_category) is False


def test_unknown_incomplete_reason_remains_provider_health_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an unknown provider-side incomplete envelope on the retry path."""

    current = 2_000_000_000
    response = _noncompleted_response(
        "incomplete", incomplete_reason="provider_internal_timeout"
    )
    result = _run_response(response)
    state = bot.default_state()

    assert result.error_category == "provider_incomplete"
    assert result.provider_usage["total_tokens"] == 145
    assert bot._is_openai_provider_health_failure(result.error_category) is True
    assert bot._is_terminal_candidate_local_failure(
        {"status": result.status, "error_category": result.error_category}
    ) is False

    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(reply_pipeline_module, "run_reply_pipeline", lambda **_kwargs: result)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    result = bot._reply_assembly()._reply_generation_owner().evaluate(
        pipeline_context(turns=1),
        None,
        state=state,

    )
    assert result.reply is None
    assert result.error_category == "provider_incomplete"
    assert state["openai_error_epochs"] == [current]
    assert state["openai_api_cooldown_until_epoch"] == 0
    assert state.get("reply_evaluation_records", {}) == {}


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply"])
def test_refusal_retires_candidate_and_allows_later_candidate_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    """Continue a lane after one content refusal without breaker, strike or quota use."""

    state = bot.default_state()
    candidates = [mention(100, 200), mention(101, 201)]
    if lane == "hot_post_reply":
        for candidate in candidates:
            candidate.update(
                {
                    "_source": "hot_post_reply",
                    "_hot_original_post_id": "90",
                }
            )
        configure_provider_free_mention_check(
            monkeypatch, [], current_epoch=2_000_000_000
        )
        monkeypatch.setattr(
            bot._hot_post_discovery,
            "get_hot_post_reply_candidates",
            lambda _state, **_kwargs: copy.deepcopy(candidates),
        )
    else:
        configure_provider_free_mention_check(
            monkeypatch, candidates, current_epoch=2_000_000_000
        )
    responses = [_noncompleted_response("refused"), _no_reply_response()]
    request_targets: list[str] = []

    patch_reply_owner_method(
        monkeypatch, bot._reply_context.ReplyContext, "build",
        lambda candidate, _state: PreparedReplyContext(_candidate_context(candidate), {}),
    )
    monkeypatch.setattr(bot, "reply_evidence_repository", FakeRepository)
    patch_reply_owner_method(monkeypatch, bot._reply_native_media.ReplyMedia, "collect", lambda _media: [])

    def transport(**kwargs: object) -> dict[str, object]:
        request_targets.append(str(kwargs["target_id"]))
        return {
            "response": responses.pop(0),
            "latency_ms": 10,
            "request_attempt_count": 1,
        }

    patch_reply_owner_method(
        monkeypatch, bot._reply_model_transport.ReplyModelTransport, "call", transport,
    )

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert request_targets == ["100", "101"]
    assert responses == []
    assert state["reply_evaluation_records"]["100"]["outcome"] == (
        "operational_failure"
    )
    assert state["reply_evaluation_records"]["101"]["outcome"] == "no_reply"
    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0
    assert state["author_evaluation_quarantines"] == {}
    assert state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 0

    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_CHECKED
    assert request_targets == ["100", "101"]


def test_quote_tweet_refusal_retires_and_does_not_block_later_quote(
    tmp_path,
) -> None:
    """Exercise quote-tweet continuation through the real offline process harness."""

    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    scenario["quote_tweets"]["900"]["data"].append(
        {
            "id": "911",
            "text": "A second reader contribution.",
            "author_id": "311",
            "conversation_id": "911",
            "referenced_tweets": [{"type": "quoted", "id": "900"}],
            "created_at": "2026-06-30T10:01:00Z",
        }
    )
    scenario["quote_tweets"]["900"]["includes"]["users"].append(
        {
            "id": "311",
            "username": "reader_two",
            "name": "Reader Two",
            "description": "Interested in public affairs",
            "public_metrics": {
                "followers_count": 10,
                "following_count": 20,
                "tweet_count": 30,
            },
        }
    )
    scenario["openai_responses"] = [
        {"body": _noncompleted_response("refused")},
        {"body": _no_reply_response()},
    ]
    server = FakeApiServer(scenario).start()
    try:
        base_dir = prepare_base_dir(
            tmp_path,
            state={
                "next_reply_lane_priority": "quote",
                "recent_own_post_ids": ["900"],
                "last_reply_epoch": 0,
            },
            local_config={"ENABLE_HOT_POST_REPLY_CHECKS": False},
        )

        first = run_cycle(base_dir, server)
        assert first.returncode == 0, first.stderr + first.stdout
        state = read_json(base_dir / "bot_state.json")
        assert len(server.openai_requests) == 2
        assert server.posts == []
        assert state["reply_evaluation_records"]["910"]["outcome"] == (
            "operational_failure"
        )
        assert state["reply_evaluation_records"]["911"]["outcome"] == "no_reply"
        assert state["openai_error_epochs"] == []
        assert state["openai_api_cooldown_until_epoch"] == 0
        assert state["author_evaluation_quarantines"] == {}
        assert state["daily_reply_count"] == 0
        assert state["daily_quote_reply_count"] == 0

        second = run_cycle(base_dir, server)
        assert second.returncode == 0, second.stderr + second.stdout
        assert len(server.openai_requests) == 2
    finally:
        server.stop()

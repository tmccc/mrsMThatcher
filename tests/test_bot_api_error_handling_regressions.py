"""Regression tests for bot api error handling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt
from tests.helpers.reply_fixtures import (
    UNIT_REPLY_REPOSITORY,
    unit_reply_context,
)
from single_call_reply import PipelineResult


pytestmark = pytest.mark.allow_loopback_network


def test_three_image_input_failures_leave_openai_breaker_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    context = unit_reply_context()

    def unavailable(_media_context: object) -> list[dict[str, object]]:
        raise bot.ReplyMediaUnavailable("unit image failure")

    monkeypatch.setattr(bot, "collect_reply_images", unavailable)
    monkeypatch.setattr(
        bot,
        "run_single_call_reply_pipeline",
        lambda **_kwargs: pytest.fail("image failure must precede Sol"),
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    for _ in range(3):
        outcome: dict[str, object] = {}
        assert bot.generate_single_call_reply(
            context,
            {"status": "unavailable"},
            state=state,
            evaluation_outcome=outcome,
        ) is None
        assert outcome["error_category"] == "image_input"
        assert outcome["model_call_count"] == 0

    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0


@pytest.mark.parametrize(
    "error_category",
    [
        "configuration",
        "context_validation",
        "draft_validation",
        "local_validation",
    ],
)
def test_candidate_local_pipeline_failures_leave_openai_breaker_untouched(
    monkeypatch: pytest.MonkeyPatch,
    error_category: str,
) -> None:
    state = bot.default_state()
    context = unit_reply_context()

    monkeypatch.setattr(bot, "collect_reply_images", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "run_single_call_reply_pipeline",
        lambda **_kwargs: PipelineResult(
            status="operational_failure",
            reason="unit_candidate_local_failure",
            error_category=error_category,
            model_call_count=int(error_category in {"draft_validation", "local_validation"}),
            local_validation_status="failed",
        ),
    )

    for _ in range(3):
        outcome: dict[str, object] = {}
        assert bot.generate_single_call_reply(
            context,
            None,
            state=state,
            evaluation_outcome=outcome,
        ) is None
        assert outcome["error_category"] == error_category

    assert state["openai_error_epochs"] == []
    assert state["openai_api_cooldown_until_epoch"] == 0


def test_three_provider_failures_activate_openai_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = 2_000_000_000
    state = bot.default_state()
    context = unit_reply_context()

    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bot, "collect_reply_images", lambda _media: [])
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", lambda *_args: None)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "run_single_call_reply_pipeline",
        lambda **_kwargs: PipelineResult(
            status="operational_failure",
            reason="provider_request_failed",
            error_category="provider_http_500",
            model_call_count=1,
            local_validation_status="not_run",
        ),
    )

    for _ in range(3):
        assert bot.generate_single_call_reply(context, None, state=state) is None

    assert state["openai_error_epochs"] == [current, current, current]
    assert state["openai_api_cooldown_until_epoch"] > current


@pytest.mark.parametrize("raw_value", ["nan", "inf", "-inf"])
def test_request_timeout_rejects_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("MRS_REQUEST_TIMEOUT_SECONDS", raw_value)

    assert bot.parse_request_timeout_seconds() == 60.0


@pytest.mark.parametrize("raw_value", ["60.0001", "120", "180", "10000"])
def test_request_timeout_rejects_values_beyond_service_shutdown_budget(
    monkeypatch: pytest.MonkeyPatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("MRS_REQUEST_TIMEOUT_SECONDS", raw_value)

    assert bot.parse_request_timeout_seconds() == 60.0


def test_request_timeout_accepts_maximum_safe_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MRS_REQUEST_TIMEOUT_SECONDS",
        str(bot.MAX_REQUEST_TIMEOUT_SECONDS),
    )

    assert bot.parse_request_timeout_seconds() == bot.MAX_REQUEST_TIMEOUT_SECONDS


def test_x_request_uses_one_combined_connect_and_read_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class EmptyResponse:
        status_code = 204
        headers: dict[str, str] = {}
        text = ""

    def fake_request(*args: object, **kwargs: object) -> EmptyResponse:
        captured["timeout"] = kwargs["timeout"]
        return EmptyResponse()

    monkeypatch.setattr(bot, "REQUEST_TIMEOUT_SECONDS", 60.0)
    monkeypatch.setattr(bot.requests, "request", fake_request)

    assert bot.x_request("GET", "/2/test") == {}
    timeout = captured["timeout"]

    assert timeout.total == 60.0
    assert timeout.connect_timeout == 10.0


def test_rate_limit_cooldown_uses_future_reset_with_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=1_200), "x")

    assert state["api_cooldown_until_epoch"] == 1_260
    assert state["api_cooldown_reason"] == "x returned 429/rate limit"


def test_current_x_reply_not_permitted_403_is_terminal_not_transient() -> None:
    error = bot.ApiError(
        "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
        service="x",
        status_code=403,
    )

    assert bot.api_error_is_reply_not_allowed(error) is True
    unrelated_auth_error = bot.ApiError(
        "X API error 403: {\"type\":\"https://api.x.com/2/problems/not-authorized-for-resource\","
        "\"detail\":\"This application is not permitted to perform that operation.\"}",
        service="x",
        status_code=403,
    )
    assert bot.api_error_is_reply_not_allowed(unrelated_auth_error) is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            bot.ApiError(
                "X API error 404: target not found",
                service="x",
                status_code=404,
                request_method="GET",
                request_path="/2/tweets/123",
            ),
            bot.X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE,
        ),
        (
            bot.ApiError(
                "X API error 403: Tweet is unavailable",
                service="x",
                status_code=403,
                request_method="GET",
                request_path="/2/tweets/123",
            ),
            bot.X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE,
        ),
        (
            bot.ApiError(
                "X API error 403: Invalid or expired token",
                service="x",
                status_code=403,
                request_method="GET",
                request_path="/2/tweets/123",
            ),
            bot.X_API_ERROR_GLOBAL_DENIAL,
        ),
        (
            bot.ApiError(
                "X API error 404: endpoint not found",
                service="x",
                status_code=404,
                request_method="GET",
                request_path="/2/users/123/mentions",
            ),
            bot.X_API_ERROR_ENDPOINT_NOT_FOUND,
        ),
        (
            bot.ApiError(
                "X API error 403: You attempted to reply to a Tweet "
                "that is deleted or not visible to you.",
                service="x",
                status_code=403,
                request_method="POST",
                request_path="/2/tweets",
            ),
            bot.X_API_ERROR_REPLY_TARGET_UNAVAILABLE,
        ),
    ],
)
def test_x_api_error_classifier_uses_endpoint_status_and_message(
    error: bot.ApiError,
    expected: str,
) -> None:
    assert bot.classify_x_api_error(error) == expected


def test_x_request_preserves_method_and_path_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        status_code=404,
        text='{"detail":"endpoint not found"}',
        headers={},
    )
    monkeypatch.setattr(bot.requests, "request", lambda *_args, **_kwargs: response)

    with pytest.raises(bot.ApiError) as caught:
        bot.x_request("GET", "/2/unsupported")

    assert caught.value.request_method == "GET"
    assert caught.value.request_path == "/2/unsupported"
    assert (
        bot.classify_x_api_error(caught.value)
        == bot.X_API_ERROR_ENDPOINT_NOT_FOUND
    )


def test_openai_5xx_is_not_retried_after_an_ambiguous_provider_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    response = SimpleNamespace(
        status_code=500,
        text='{"error":"upstream failure"}',
        close=lambda: None,
    )

    def post(*_args: object, **kwargs: object) -> object:
        calls.append(dict(kwargs))
        return response

    monkeypatch.setattr(bot.requests, "post", post)
    monkeypatch.setattr(
        bot, "require_remote_operation_unpaused", lambda *_args: None
    )
    monkeypatch.setattr(bot, "report_bot_health_progress", lambda *_args: None)

    with pytest.raises(bot.ApiError) as caught:
        bot.openai_responses_reply_call(
            request={"model": "gpt-5.6-sol"},
            timeout_seconds=180,
            lane="mention",
            target_id="100",
        )

    assert caught.value.status_code == 500
    assert caught.value.error_category == "provider_http_500"
    assert len(calls) == 1


def test_status_only_generic_403_and_404_are_not_target_terminal() -> None:
    auth_error = bot.ApiError(
        "X API error 403: operation forbidden",
        service="x",
        status_code=403,
    )
    missing_endpoint = bot.ApiError(
        "X API error 404: endpoint not found",
        service="x",
        status_code=404,
    )

    assert not bot.api_error_is_permanent_target_failure(auth_error)
    assert not bot.api_error_is_reply_not_allowed(auth_error)
    assert not bot.api_error_is_permanent_target_failure(missing_endpoint)
    assert not bot.api_error_is_reply_not_allowed(missing_endpoint)


def test_parent_and_quoted_lookup_only_suppress_target_specific_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mention = {
        "id": "200",
        "author_id": "300",
        "text": "A reply",
        "referenced_tweets": [{"type": "replied_to", "id": "123"}],
    }
    unavailable = bot.ApiError(
        "X API error 404: target not found",
        service="x",
        status_code=404,
        request_method="GET",
        request_path="/2/tweets/123",
    )
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(unavailable),
    )

    assert bot.build_parent_chain(mention, {}) == []
    quoted = {
        **mention,
        "referenced_tweets": [{"type": "quoted", "id": "123"}],
    }
    assert bot._quoted_post_for_reply_context(
        quoted, {}, principal_author_id="300"
    ) is None

    global_denial = bot.ApiError(
        "X API error 403: Invalid or expired token",
        service="x",
        status_code=403,
        request_method="GET",
        request_path="/2/tweets/123",
    )
    monkeypatch.setattr(
        bot,
        "get_tweet_by_id_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(global_denial),
    )
    with pytest.raises(bot.ApiError, match="expired token"):
        bot.build_parent_chain(mention, {})
    with pytest.raises(bot.ApiError, match="expired token"):
        bot._quoted_post_for_reply_context(
            quoted, {}, principal_author_id="300"
        )


def test_deleted_reply_target_403_is_terminal_not_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    deleted_target = bot.ApiError(
        "X API error 403: You attempted to reply to a Tweet "
        "that is deleted or not visible to you.",
        service="x",
        status_code=403,
        request_method="POST",
        request_path="/2/tweets",
    )

    assert bot.api_error_is_reply_not_allowed(deleted_target)
    bot.record_api_error(state, deleted_target, "x", scope="write")

    assert state["x_write_error_epochs"] == []
    assert state["x_write_api_cooldown_until_epoch"] == 0


def test_global_post_create_403_remains_in_transient_error_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    global_denial = bot.ApiError(
        "X API error 403: This application is not permitted to perform that operation.",
        service="x",
        status_code=403,
        request_method="POST",
        request_path="/2/tweets",
    )

    assert not bot.api_error_is_reply_not_allowed(global_denial)
    bot.record_api_error(state, global_denial, "x", scope="write")

    assert state["x_write_error_epochs"] == [1_000]


def test_reply_not_permitted_403_does_not_enter_write_error_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    error = bot.ApiError(
        "X API error 403: You can only reply to or quote posts where you are mentioned or are the author.",
        service="x",
        status_code=403,
    )

    bot.record_api_error(state, error, "x", scope="write")

    assert state["x_write_error_epochs"] == []
    assert state["x_write_api_cooldown_until_epoch"] == 0


@pytest.mark.parametrize("reset_epoch", [None, 900])
def test_rate_limit_cooldown_falls_back_for_missing_or_past_reset(
    monkeypatch: pytest.MonkeyPatch,
    reset_epoch: int | None,
) -> None:
    state: dict = {}
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.record_api_error(state, bot.ApiError("rate limited", service="x", status_code=429, reset_epoch=reset_epoch), "x")

    assert state["api_cooldown_until_epoch"] == 1_000 + bot.COOLDOWN_AFTER_429_SECONDS


def test_clear_expired_api_cooldowns_clears_only_expired_values(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "api_cooldown_until_epoch": 900,
        "api_cooldown_reason": "old",
        "quote_api_cooldown_until_epoch": 1_100,
        "quote_api_cooldown_reason": "still active",
    }
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_000)

    assert bot.clear_expired_api_cooldowns(state) is True
    assert state["api_cooldown_until_epoch"] == 0
    assert state["api_cooldown_reason"] == ""
    assert state["quote_api_cooldown_until_epoch"] == 1_100
    assert state["quote_api_cooldown_reason"] == "still active"

"""Regression tests for bot cursor pagination."""

from __future__ import annotations

from tests.helpers.reply_evaluation import legacy_reply_evaluator
from tests.helpers.reply_fixtures import patch_reply_owner_method, patch_tweet_lookup_method

import copy
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    invalid_pagination_cursor_error,
    repeated_quote_cursor_suppression,
)


pytestmark = pytest.mark.allow_loopback_network


def test_invalid_pagination_cursor_classifier_is_status_and_parameter_specific() -> None:
    unrelated_bad_request = bot.ApiError(
        'X API error 400: {"errors":[{"message":"Invalid query operator"}]}',
        service="x",
        status_code=400,
    )
    echoed_cursor_with_unrelated_error = bot.ApiError(
        (
            'X API error 400: {"errors":[{"parameters":'
            '{"pagination_token":["saved-token"]},'
            '"message":"Invalid query operator"}]}'
        ),
        service="x",
        status_code=400,
    )
    matching_server_error = bot.ApiError(
        str(invalid_pagination_cursor_error()),
        service="x",
        status_code=503,
    )
    top_level_cursor_error = bot.ApiError(
        'X API error 400: {"message":"Invalid pagination token"}',
        service="x",
        status_code=400,
    )

    assert bot.api_error_is_invalid_pagination_cursor(
        invalid_pagination_cursor_error()
    ) is True
    assert bot.api_error_is_invalid_pagination_cursor(top_level_cursor_error) is True
    assert bot.api_error_is_invalid_pagination_cursor(unrelated_bad_request) is False
    assert bot.api_error_is_invalid_pagination_cursor(
        echoed_cursor_with_unrelated_error
    ) is False
    assert bot.api_error_is_invalid_pagination_cursor(matching_server_error) is False


def test_paginated_get_invalid_saved_cursor_retries_once_from_head() -> None:
    events: list[tuple[str, str | None]] = []

    def clear_invalid_cursor() -> None:
        events.append(("clear", None))

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        events.append(("request", str(token) if token is not None else None))
        if token is not None:
            raise invalid_pagination_cursor_error()
        assert events[-2] == ("clear", None)
        assert params["since_id"] == "99"
        return {"data": [{"id": "100"}], "meta": {}}

    result = bot.x_paginated_get(
        request,
        "/2/users/12345/mentions",
        {
            "max_results": 10,
            "since_id": "99",
            "pagination_token": "expired-token",
        },
        max_pages=3,
        label="mentions",
        on_invalid_cursor=clear_invalid_cursor,
    )

    assert [item["id"] for item in result["data"]] == ["100"]
    assert result["_pagination"]["pages_fetched"] == 1
    assert result["_pagination"]["truncated"] is False
    assert result["_pagination"]["next_token"] is None
    assert result["_pagination"]["invalid_cursor_recovered"] is True
    assert "cursor_request_suppressed" not in result["_pagination"]
    assert events == [
        ("request", "expired-token"),
        ("clear", None),
        ("request", None),
    ]


def test_paginated_get_invalid_cursor_retry_is_bounded() -> None:
    events: list[tuple[str, str | None]] = []

    def clear_invalid_cursor() -> None:
        events.append(("clear", None))

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        events.append(("request", str(token) if token is not None else None))
        raise invalid_pagination_cursor_error()

    with pytest.raises(bot.ApiError):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {"pagination_token": "expired-token"},
            max_pages=3,
            label="mentions",
            on_invalid_cursor=clear_invalid_cursor,
        )

    assert events == [
        ("request", "expired-token"),
        ("clear", None),
        ("request", None),
    ]


def test_paginated_get_does_not_request_rejected_token_again_after_head_retry() -> None:
    events: list[tuple[str, str | None]] = []

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        token_text = str(token) if token is not None else None
        events.append(("request", token_text))
        if token_text == "expired-token":
            raise invalid_pagination_cursor_error()
        return {
            "data": [{"id": "100"}],
            "meta": {"next_token": "expired-token"},
        }

    with pytest.raises(bot.PaginationCursorProtocolError, match="(?i)repeated"):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {"pagination_token": "expired-token"},
            max_pages=3,
            label="mentions",
            on_invalid_cursor=lambda: events.append(("clear", None)),
        )

    assert events == [
        ("request", "expired-token"),
        ("clear", None),
        ("request", None),
    ]


def test_paginated_get_does_not_retry_unrelated_bad_request() -> None:
    events: list[str] = []
    unrelated_bad_request = bot.ApiError(
        'X API error 400: {"errors":[{"message":"Invalid query operator"}]}',
        service="x",
        status_code=400,
    )

    def request(_path: str, _params: dict) -> dict:
        events.append("request")
        raise unrelated_bad_request

    with pytest.raises(bot.ApiError) as raised:
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {"pagination_token": "saved-token"},
            max_pages=3,
            label="mentions",
            on_invalid_cursor=lambda: events.append("clear"),
        )

    assert raised.value is unrelated_bad_request
    assert events == ["request"]


def test_paginated_get_rejects_repeated_continuation_token_a_to_a() -> None:
    events: list[tuple[str, object]] = []
    persisted_ids: list[str] = []

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        token_text = str(token) if token is not None else None
        events.append(("request", token_text))
        return {
            "data": [{"id": "100" if token is None else "101"}],
            "meta": {"next_token": "A"},
        }

    def persist_page(
        page_data: list[dict],
        _includes: dict,
        _next_token: str,
        _request_token: str,
        _pages_fetched: int,
    ) -> None:
        page_ids = [str(item["id"]) for item in page_data]
        persisted_ids.extend(page_ids)
        events.append(("persist", page_ids))

    with pytest.raises(bot.PaginationCursorProtocolError, match="(?i)repeated"):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {},
            max_pages=5,
            label="mentions",
            on_invalid_cursor=lambda: events.append(("clear", None)),
            on_page=persist_page,
        )

    assert persisted_ids == ["100", "101"]
    assert events == [
        ("request", None),
        ("persist", ["100"]),
        ("request", "A"),
        ("persist", ["101"]),
        ("clear", None),
    ]


def test_paginated_get_rejects_repeated_continuation_token_a_to_b_to_a() -> None:
    events: list[tuple[str, str | None]] = []
    next_tokens = {
        None: "A",
        "A": "B",
        "B": "A",
    }

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        token_text = str(token) if token is not None else None
        events.append(("request", token_text))
        return {
            "data": [{"id": str(100 + len(events))}],
            "meta": {"next_token": next_tokens[token_text]},
        }

    with pytest.raises(bot.PaginationCursorProtocolError, match="(?i)repeated"):
        bot.x_paginated_get(
            request,
            "/2/users/12345/mentions",
            {},
            max_pages=6,
            label="mentions",
            on_invalid_cursor=lambda: events.append(("clear", None)),
        )

    assert events == [
        ("request", None),
        ("request", "A"),
        ("request", "B"),
        ("clear", None),
    ]


def test_paginated_get_pre_request_cursor_stop_is_bounded_partial_success() -> None:
    requests: list[str | None] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(params.get("pagination_token"))
        return {
            "data": [{"id": "100"}],
            "meta": {"next_token": "suppressed-token"},
        }

    result = bot.x_paginated_get(
        request,
        "/2/test",
        {},
        max_pages=3,
        label="test",
        should_request_cursor=lambda cursor: cursor != "suppressed-token",
    )

    assert requests == [None]
    assert result["data"] == [{"id": "100"}]
    assert result["_pagination"] == {
        "pages_fetched": 1,
        "truncated": True,
        "next_token": None,
        "invalid_cursor_recovered": False,
        "repeated_token_detected": False,
        "cursor_request_suppressed": True,
    }


def test_quote_lookup_repeated_saved_token_is_one_bounded_partial_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixed_epoch = 2_000_000_000
    repeated_token = "sensitive-immediate-token"
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {"900": repeated_token}
    requests: list[str | None] = []
    events: list[tuple[str, dict]] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(params.get("pagination_token"))
        return {
            "data": [{"id": "100", "author_id": "200"}],
            "meta": {"next_token": repeated_token},
        }

    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )
    caplog.set_level(logging.WARNING)

    result = bot.get_quote_tweets_for_post("900", state)

    assert [item["id"] for item in result] == ["100"]
    assert requests == [repeated_token]
    assert state["quote_lookup_pagination_tokens"] == {}
    assert state["quote_lookup_repeated_cursor_suppressions"] == {
        "900": {
            "cursor_sha256": hashlib.sha256(
                repeated_token.encode("utf-8")
            ).hexdigest(),
            "detected_epoch": fixed_epoch,
            "retry_after_epoch": (
                fixed_epoch + bot.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS
            ),
        }
    }
    assert state["quote_api_cooldown_until_epoch"] == 0
    assert state["quote_x_error_epochs"] == []
    assert [name for name, _fields in events] == [
        "quote_pagination_repeated_token"
    ]
    fields = events[0][1]
    assert fields == {
        "post_id": "900",
        "token_fingerprint": hashlib.sha256(
            repeated_token.encode("utf-8")
        ).hexdigest()[:16],
        "pages_completed": 1,
        "results_retained": 1,
        "backoff_seconds": bot.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
        "retry_after_epoch": (
            fixed_epoch + bot.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS
        ),
    }
    assert sum("Quote pagination stopped" in row.message for row in caplog.records) == 1
    assert not any(row.levelno >= logging.ERROR for row in caplog.records)
    assert "Traceback" not in caplog.text
    assert repeated_token not in caplog.text


def test_quote_lookup_active_suppression_refetches_head_without_warning_or_write(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixed_epoch = 2_000_000_000
    repeated_token = "known-bad-token"
    state = bot.default_state()
    state["quote_lookup_repeated_cursor_suppressions"] = {
        "900": repeated_quote_cursor_suppression(
            repeated_token,
            detected_epoch=fixed_epoch - 60,
        )
    }
    requests: list[str | None] = []
    events: list[str] = []
    saves: list[dict] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(params.get("pagination_token"))
        return {
            "data": [{"id": "101", "author_id": "201"}],
            "meta": {"next_token": repeated_token},
        }

    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saves.append(copy.deepcopy(current)),
    )
    monkeypatch.setattr(bot, "log_event", lambda name, **_fields: events.append(name))
    caplog.set_level(logging.WARNING)

    result = bot.get_quote_tweets_for_post("900", state)

    assert requests == [None]
    assert [item["id"] for item in result] == ["101"]
    assert state["quote_lookup_pagination_tokens"] == {}
    assert "900" in state["quote_lookup_repeated_cursor_suppressions"]
    assert saves == []
    assert events == []
    assert not any("Quote pagination stopped" in row.message for row in caplog.records)
    assert not any(row.levelno >= logging.ERROR for row in caplog.records)
    assert "Traceback" not in caplog.text


def test_quote_lookup_matching_saved_cursor_is_removed_before_fresh_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    repeated_token = "known-bad-token"
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {"900": repeated_token}
    state["quote_lookup_repeated_cursor_suppressions"] = {
        "900": repeated_quote_cursor_suppression(
            repeated_token,
            detected_epoch=fixed_epoch - 60,
        )
    }
    requests: list[str | None] = []
    saves: list[dict] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(params.get("pagination_token"))
        return {
            "data": [{"id": "101", "author_id": "201"}],
            "meta": {"next_token": repeated_token},
        }

    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **kwargs: saves.append(
            {
                "durable": kwargs.get("durable"),
                "tokens": copy.deepcopy(
                    current["quote_lookup_pagination_tokens"]
                ),
            }
        ),
    )

    result = bot.get_quote_tweets_for_post("900", state)

    assert requests == [None]
    assert [item["id"] for item in result] == ["101"]
    assert state["quote_lookup_pagination_tokens"] == {}
    assert "900" in state["quote_lookup_repeated_cursor_suppressions"]
    assert saves == [{"durable": True, "tokens": {}}]


@pytest.mark.parametrize(
    ("next_tokens", "expected_ids", "expected_requests"),
    [
        ({"A": "A"}, ["101"], ["A"]),
        ({"A": "B", "B": "A"}, ["101", "102"], ["A", "B"]),
        ({"A": "B", "B": "B"}, ["101", "102"], ["A", "B"]),
        (
            {"A": "B", "B": "C", "C": "B"},
            ["101", "102", "103"],
            ["A", "B", "C"],
        ),
    ],
)
def test_quote_lookup_saved_repeated_cursor_is_durably_cleared_across_reload(
    next_tokens: dict[str, str],
    expected_ids: list[str],
    expected_requests: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "bot_state.json"
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {"900": "A"}
    requests: list[str] = []

    def request(_path: str, params: dict) -> dict:
        token = str(params["pagination_token"])
        requests.append(token)
        return {
            "data": [
                {"id": str(100 + len(requests)), "author_id": "200"}
            ],
            "meta": {"next_token": next_tokens[token]},
        }

    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "QUOTE_LOOKUP_MAX_PAGES_PER_POST", 6)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    bot.save_state(state, durable=True)

    result = bot.get_quote_tweets_for_post("900", state)

    assert [item["id"] for item in result] == expected_ids
    assert requests == expected_requests
    assert state["quote_lookup_pagination_tokens"] == {}
    assert bot.load_state()["quote_lookup_pagination_tokens"] == {}


def test_quote_lookup_invalid_saved_cursor_then_repeated_head_token_saves_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state_path = tmp_path / "bot_state.json"
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {"900": "A"}
    requests: list[str | None] = []
    events: list[tuple[str, dict]] = []

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        requests.append(token)
        if token == "A":
            raise invalid_pagination_cursor_error()
        return {
            "data": [{"id": "101", "author_id": "200"}],
            "meta": {"next_token": "A"},
        }

    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    bot.save_state(state, durable=True)
    original_save_state = bot.save_state
    durable_save_count = 0

    def save_durably(current: dict, *, durable: bool = False) -> None:
        nonlocal durable_save_count
        durable_save_count += 1
        assert durable is True
        original_save_state(current, durable=durable)

    monkeypatch.setattr(bot, "save_state", save_durably)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )
    caplog.set_level(logging.WARNING)

    result = bot.get_quote_tweets_for_post("900", state)

    assert requests == ["A", None]
    assert [item["id"] for item in result] == ["101"]
    assert state["quote_lookup_pagination_tokens"] == {}
    assert bot.load_state()["quote_lookup_pagination_tokens"] == {}
    assert "900" in state["quote_lookup_repeated_cursor_suppressions"]
    assert durable_save_count == 2
    assert [name for name, _fields in events] == [
        "quote_pagination_repeated_token"
    ]
    assert sum("Quote pagination stopped" in row.message for row in caplog.records) == 1


def test_quote_lookup_repeated_token_after_several_pages_preserves_all_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    next_tokens = {None: "A", "A": "B", "B": "A"}
    requests: list[str | None] = []
    events: list[tuple[str, dict]] = []

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        requests.append(token)
        return {
            "data": [{"id": str(100 + len(requests)), "author_id": "200"}],
            "meta": {"next_token": next_tokens[token]},
        }

    monkeypatch.setattr(bot, "QUOTE_LOOKUP_MAX_PAGES_PER_POST", 6)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda name, **fields: events.append((name, fields)),
    )

    result = bot.get_quote_tweets_for_post("900")

    assert [item["id"] for item in result] == ["101", "102", "103"]
    assert requests == [None, "A", "B"]
    assert len(events) == 1
    assert events[0][0] == "quote_pagination_repeated_token"
    assert events[0][1]["pages_completed"] == 3
    assert events[0][1]["results_retained"] == 3


@pytest.mark.parametrize(
    ("responses", "expected_requests"),
    [
        (
            [
                {"data": [{"id": "100"}], "meta": {"next_token": "A"}},
                {"data": [{"id": "101"}], "meta": {}},
            ],
            [None, "A"],
        ),
        ([{"data": [{"id": "100"}], "meta": {}}], [None]),
    ],
)
def test_quote_lookup_normal_or_missing_next_token_finishes_without_warning(
    responses: list[dict],
    expected_requests: list[str | None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remaining = list(responses)
    requests: list[str | None] = []
    events: list[str] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(params.get("pagination_token"))
        return remaining.pop(0)

    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot, "log_event", lambda name, **_fields: events.append(name))

    result = bot.get_quote_tweets_for_post("900")

    assert [item["id"] for item in result] == ["100"] + (
        ["101"] if len(responses) == 2 else []
    )
    assert requests == expected_requests
    assert events == []


def test_quote_lookup_different_cursor_clears_stale_suppression_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixed_epoch = 2_000_000_000
    stale_token = "stale-token"
    changed_token = "changed-token"
    state = bot.default_state()
    state["quote_lookup_repeated_cursor_suppressions"] = {
        "900": repeated_quote_cursor_suppression(
            stale_token,
            detected_epoch=fixed_epoch - 60,
        )
    }
    requests: list[str | None] = []
    responses = {
        None: {
            "data": [{"id": "101", "author_id": "201"}],
            "meta": {"next_token": changed_token},
        },
        changed_token: {
            "data": [{"id": "102", "author_id": "202"}],
            "meta": {},
        },
    }

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        requests.append(token)
        return responses[token]

    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.INFO)

    result = bot.get_quote_tweets_for_post("900", state)

    assert requests == [None, changed_token]
    assert [item["id"] for item in result] == ["101", "102"]
    assert state["quote_lookup_repeated_cursor_suppressions"] == {}
    recovery_logs = [
        row
        for row in caplog.records
        if "Quote pagination cursor suppression cleared" in row.message
    ]
    assert len(recovery_logs) == 1
    assert "reason=continuation_changed" in recovery_logs[0].message


def test_quote_lookup_expiry_probes_once_and_repeat_renews_suppression(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixed_epoch = 2_000_000_000
    repeated_token = "probe-token"
    state = bot.default_state()
    state["quote_lookup_repeated_cursor_suppressions"] = {
        "900": repeated_quote_cursor_suppression(
            repeated_token,
            detected_epoch=(
                fixed_epoch - bot.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS
            ),
            retry_after_epoch=fixed_epoch,
        )
    }
    requests: list[str | None] = []
    saves: list[tuple[bool, dict]] = []

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        requests.append(token)
        return {
            "data": [
                {
                    "id": "101" if token is None else "102",
                    "author_id": "201",
                }
            ],
            "meta": {"next_token": repeated_token},
        }

    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **kwargs: saves.append(
            (kwargs.get("durable") is True, copy.deepcopy(current))
        ),
    )
    caplog.set_level(logging.WARNING)

    result = bot.get_quote_tweets_for_post("900", state)

    assert requests == [None, repeated_token]
    assert requests.count(repeated_token) == 1
    assert [item["id"] for item in result] == ["101", "102"]
    assert state["quote_lookup_repeated_cursor_suppressions"] == {
        "900": repeated_quote_cursor_suppression(
            repeated_token,
            detected_epoch=fixed_epoch,
        )
    }
    assert len(saves) == 2
    assert all(durable for durable, _snapshot in saves)
    assert sum("Quote pagination stopped" in row.message for row in caplog.records) == 1
    assert state["quote_api_cooldown_until_epoch"] == 0


def test_quote_lookup_expired_probe_progress_clears_and_follows_new_cursor(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixed_epoch = 2_000_000_000
    repeated_token = "probe-token"
    changed_token = "recovered-token"
    state = bot.default_state()
    state["quote_lookup_repeated_cursor_suppressions"] = {
        "900": repeated_quote_cursor_suppression(
            repeated_token,
            detected_epoch=(
                fixed_epoch - bot.QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS
            ),
            retry_after_epoch=fixed_epoch,
        )
    }
    requests: list[str | None] = []
    responses = {
        None: {
            "data": [{"id": "101", "author_id": "201"}],
            "meta": {"next_token": repeated_token},
        },
        repeated_token: {
            "data": [{"id": "102", "author_id": "202"}],
            "meta": {"next_token": changed_token},
        },
        changed_token: {
            "data": [{"id": "103", "author_id": "203"}],
            "meta": {},
        },
    }

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        requests.append(token)
        return responses[token]

    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.INFO)

    result = bot.get_quote_tweets_for_post("900", state)

    assert requests == [None, repeated_token, changed_token]
    assert requests.count(repeated_token) == 1
    assert [item["id"] for item in result] == ["101", "102", "103"]
    assert state["quote_lookup_repeated_cursor_suppressions"] == {}
    assert sum(
        "Quote pagination cursor suppression cleared" in row.message
        for row in caplog.records
    ) == 1
    assert not any("Quote pagination stopped" in row.message for row in caplog.records)


def test_quote_cursor_suppression_survives_state_save_and_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    repeated_token = "durable-token"
    state = bot.default_state()

    def request(_path: str, params: dict) -> dict:
        return {
            "data": [{"id": "101", "author_id": "201"}],
            "meta": {"next_token": repeated_token},
        }

    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)

    bot.get_quote_tweets_for_post("900", state)
    loaded = bot.load_state()

    expected = repeated_quote_cursor_suppression(
        repeated_token,
        detected_epoch=fixed_epoch,
    )
    assert state["quote_lookup_repeated_cursor_suppressions"]["900"] == expected
    assert loaded["quote_lookup_repeated_cursor_suppressions"]["900"] == expected
    assert loaded["quote_lookup_pagination_tokens"] == {}


def test_quote_cursor_suppression_state_prunes_malformed_expired_and_excess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    state = bot.default_state()
    suppressions = {
        str(1000 + index): repeated_quote_cursor_suppression(
            f"token-{index}",
            detected_epoch=fixed_epoch - 100 + index,
        )
        for index in range(70)
    }
    suppressions["900"] = {
        "cursor_sha256": "not-a-sha256",
        "detected_epoch": fixed_epoch - 10,
        "retry_after_epoch": fixed_epoch + 10,
    }
    suppressions["901"] = repeated_quote_cursor_suppression(
        "expired-token",
        detected_epoch=fixed_epoch - 100,
        retry_after_epoch=fixed_epoch,
    )
    state["quote_lookup_repeated_cursor_suppressions"] = suppressions
    state_path = tmp_path / "bot_state.json"

    monkeypatch.setattr(bot, "STATE_FILE", state_path)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    bot.save_state(state, durable=True)

    loaded = bot.load_state()
    persisted = json.loads(state_path.read_text(encoding="utf-8"))

    assert len(loaded["quote_lookup_repeated_cursor_suppressions"]) == (
        bot.QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES
    )
    assert "900" not in loaded["quote_lookup_repeated_cursor_suppressions"]
    assert "901" not in loaded["quote_lookup_repeated_cursor_suppressions"]
    assert (
        persisted["quote_lookup_repeated_cursor_suppressions"]
        == loaded["quote_lookup_repeated_cursor_suppressions"]
    )


def test_quote_lookup_valid_saved_continuation_still_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_token = "valid-continuation"
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {"900": saved_token}
    requests: list[str | None] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(params.get("pagination_token"))
        return {
            "data": [{"id": "101", "author_id": "201"}],
            "meta": {},
        }

    monkeypatch.setattr(bot, "x_quote_lookup_request", request)

    result = bot.get_quote_tweets_for_post("900", state)

    assert requests == [saved_token]
    assert [item["id"] for item in result] == ["101"]
    assert state["quote_lookup_pagination_tokens"] == {}
    assert state["quote_lookup_repeated_cursor_suppressions"] == {}


def test_quote_cursor_backoff_mocked_multi_cycle_request_reduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated_token = "hourly-bad-token"
    changed_token = "recovered-token"
    clock = {"now": 2_000_000_000}
    cycle = {"number": 0}
    per_cycle_requests: list[list[str | None]] = [[] for _ in range(6)]
    state = bot.default_state()

    def request(_path: str, params: dict) -> dict:
        token = params.get("pagination_token")
        per_cycle_requests[cycle["number"] - 1].append(token)
        if token is None:
            data = [
                {
                    "id": str(100 + cycle["number"]),
                    "author_id": "201",
                }
            ]
            if cycle["number"] == 3:
                data.append({"id": "999", "author_id": "299"})
            return {
                "data": data,
                "meta": {"next_token": repeated_token},
            }
        if token == repeated_token and cycle["number"] <= 5:
            return {
                "data": [{"id": "200", "author_id": "201"}],
                "meta": {"next_token": repeated_token},
            }
        if token == repeated_token and cycle["number"] == 6:
            return {
                "data": [{"id": "206", "author_id": "201"}],
                "meta": {"next_token": changed_token},
            }
        if token == changed_token:
            return {
                "data": [{"id": "306", "author_id": "201"}],
                "meta": {},
            }
        pytest.fail(f"unexpected mocked cursor request: {token!r}")

    monkeypatch.setattr(bot, "now_epoch", lambda: clock["now"])
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    discovered_by_cycle: list[list[str]] = []
    for cycle_number in range(1, 6):
        cycle["number"] = cycle_number
        result = bot.get_quote_tweets_for_post("900", state)
        discovered_by_cycle.append([item["id"] for item in result])
        clock["now"] += 3600

    retry_after_epoch = state[
        "quote_lookup_repeated_cursor_suppressions"
    ]["900"]["retry_after_epoch"]
    clock["now"] = retry_after_epoch
    cycle["number"] = 6
    recovered = bot.get_quote_tweets_for_post("900", state)

    assert [len(requests) for requests in per_cycle_requests] == [2, 1, 1, 1, 1, 3]
    assert sum(map(len, per_cycle_requests)) == 9
    assert 13 - sum(map(len, per_cycle_requests)) == 4
    assert "999" in discovered_by_cycle[2]
    assert per_cycle_requests[5].count(repeated_token) == 1
    assert [item["id"] for item in recovered] == ["106", "206", "306"]
    assert state["quote_lookup_repeated_cursor_suppressions"] == {}


def test_quote_search_processes_new_quote_despite_legacy_cursor_suppression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_epoch = 2_000_000_000
    repeated_token = "known-bad-token"
    state = bot.default_state()
    state["recent_own_post_ids"] = ["900"]
    state["quote_lookup_repeated_cursor_suppressions"] = {
        "900": repeated_quote_cursor_suppression(
            repeated_token,
            detected_epoch=fixed_epoch - 60,
        )
    }
    requests: list[str | None] = []
    provider_targets: list[str] = []
    original = {
        "id": "900",
        "author_id": "12345",
        "conversation_id": "900",
        "text": "Original post",
        "referenced_tweets": [],
    }
    new_quote = {
        "id": "910",
        "author_id": "777",
        "conversation_id": "910",
        "created_at": "2026-01-01T00:00:00Z",
        "text": "A newly created substantive quote.",
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
    }

    def request(_path: str, params: dict) -> dict:
        assert _path == "/2/tweets/search/recent"
        requests.append(params.get("pagination_token"))
        return {
            "data": [dict(new_quote)],
        }

    def no_reply(
        context: dict,
        *_args: object,
        **kwargs: object,
    ) -> None:
        provider_targets.append(str(context["target_id"]))
        outcome = kwargs.get("evaluation_outcome")
        assert isinstance(outcome, dict)
        outcome.update({"status": "no_reply", "reason": "unit_no_reply"})
        return None

    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 24)
    monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 10)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 5)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "now_epoch", lambda: fixed_epoch)
    monkeypatch.setattr(
        bot,
        "current_datetime",
        lambda: datetime.fromtimestamp(fixed_epoch),
    )
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "reconcile_confirmed_reply_receipt", lambda _state: False)
    monkeypatch.setattr(
        bot._quote_discovery.QuoteWatchPosts, "lookup", lambda _owner, _state: ["900"],
    )
    patch_tweet_lookup_method(
        monkeypatch, "get_cached",
        lambda *_args, **_kwargs: dict(original),
    )
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot._quote_reply_cycle, "quote_tweet_is_old_enough", lambda _tweet, **_kwargs: True)
    monkeypatch.setattr(
        bot,
        "is_probably_spam_or_not_worth_replying",
        lambda _text: False,
    )
    monkeypatch.setattr(
        bot,
        "reply_media_context_for_candidate",
        lambda *_args, **_kwargs: {},
    )
    patch_reply_owner_method(
        monkeypatch, bot._reply_generation.ReplyGeneration, "evaluate",
        legacy_reply_evaluator(no_reply),
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)

    status = bot.maybe_reply_to_quote_tweets(state)

    assert status == bot.QUOTE_CHECK_STATUS_CHECKED
    assert requests == [None]
    assert provider_targets == ["910"]
    assert "910" in state["seen_quote_post_ids"]
    assert "910" in state["skipped_quote_post_ids"]
    assert "900" in state["quote_lookup_repeated_cursor_suppressions"]


def test_mentions_invalid_saved_cursor_clears_state_and_preserves_since_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "expired-token",
    }
    requests: list[dict] = []
    saved_cursors: list[dict] = []

    def request(_method: str, _path: str, *, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token"):
            raise invalid_pagination_cursor_error()
        return {"data": [], "meta": {}}

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "x_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_cursors.append(
            copy.deepcopy(current.get("mention_pagination"))
        ),
    )

    assert bot.get_mentions(state) == []

    assert requests[0]["pagination_token"] == "expired-token"
    assert requests[0]["since_id"] == "99"
    assert len(requests) == 1
    assert state["mention_pagination"] == {}
    assert {} in saved_cursors


def test_mentions_invalid_cursor_stays_cleared_and_defers_head_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pagination"] = {
        "base_since_id": "99",
        "next_token": "expired-token",
    }
    requests: list[dict] = []
    saved_cursors: list[dict] = []

    def request(_method: str, _path: str, *, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token"):
            raise invalid_pagination_cursor_error()
        raise bot.ApiError(
            "X API error 503: temporary upstream failure",
            service="x",
            status_code=503,
        )

    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "x_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_cursors.append(
            copy.deepcopy(current.get("mention_pagination"))
        ),
    )

    assert bot.get_mentions(state) == []

    assert requests[0]["pagination_token"] == "expired-token"
    assert len(requests) == 1
    assert state["mention_pagination"] == {}
    assert {} in saved_cursors


def test_hot_post_invalid_saved_cursor_clears_state_and_preserves_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["hot_post_reply_since_ids"] = {"700": "250"}
    state["hot_post_reply_pagination_tokens"] = {
        "700": "expired-token",
    }
    requests: list[dict] = []
    saved_token_maps: list[dict] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token"):
            raise invalid_pagination_cursor_error()
        return {"data": [], "meta": {}}

    monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
    monkeypatch.setattr(bot, "HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS", 100)
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "in_api_cooldown", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot._quote_discovery.QuoteWatchPosts, "load_extra", lambda _owner: ["700"],
    )
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_token_maps.append(
            copy.deepcopy(current.get("hot_post_reply_pagination_tokens"))
        ),
    )

    assert bot.get_hot_post_reply_candidates(state) == []

    assert requests[0]["pagination_token"] == "expired-token"
    assert requests[0]["since_id"] == "250"
    assert requests[0]["query"] == requests[1]["query"]
    assert "pagination_token" not in requests[1]
    assert requests[1]["since_id"] == "250"
    assert state["hot_post_reply_pagination_tokens"] == {}
    assert saved_token_maps[0] == {}


def test_quote_lookup_invalid_saved_cursor_clears_state_and_retries_from_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    state["quote_lookup_pagination_tokens"] = {
        "900": "expired-token",
        "901": "other-token",
    }
    requests: list[dict] = []
    saved_token_maps: list[dict] = []

    def request(_path: str, params: dict) -> dict:
        requests.append(dict(params))
        if params.get("pagination_token") == "expired-token":
            raise invalid_pagination_cursor_error()
        return {"data": [], "meta": {}}

    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda current, **_kwargs: saved_token_maps.append(
            copy.deepcopy(current.get("quote_lookup_pagination_tokens"))
        ),
    )

    assert bot.get_quote_tweets_for_post("900", state) == []

    assert requests[0]["pagination_token"] == "expired-token"
    assert "pagination_token" not in requests[1]
    assert state["quote_lookup_pagination_tokens"] == {"901": "other-token"}
    assert saved_token_maps[0] == {"901": "other-token"}


def test_valid_tweets_sorted_by_id_deduplicates_paged_results() -> None:
    first = {"id": "20", "text": "same immutable post"}
    duplicate = {"id": "20", "text": "same immutable post"}

    result = bot.valid_tweets_sorted_by_id(
        [{"id": "30"}, first, {"id": "10"}, duplicate],
        context="paged test",
    )

    assert [item["id"] for item in result] == ["10", "20", "30"]
    assert result[1] is first

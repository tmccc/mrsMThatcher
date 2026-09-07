from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_x_pagination as pagination
from tests.test_unit_helpers import (
    bot, invalid_pagination_cursor_error, isolate_regular_post_receipt,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from types import ModuleType
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('X pagination import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_x_pagination':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_x_pagination
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_adapters_forward_current_dependencies_arguments_references_and_errors(monkeypatch):
    for name, count in (
        ("api_error_is_invalid_pagination_cursor", 2), ("x_paginated_get", 4),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(pagination, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count, name
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(pagination, name, owner)
            required = {key: object() for key, param in public.items()
                        if param.kind == param.KEYWORD_ONLY and param.default is param.empty}
            for options in (required, {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result
                expected = {key: options.get(key, param.default) for key, param in public.items() if param.kind == param.KEYWORD_ONLY} | current
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_classifier_uses_current_error_and_json_with_structured_message_precedence(monkeypatch):
    old_error = invalid_pagination_cursor_error()

    class CurrentError(bot.ApiError):
        pass

    class CurrentDecodeError(Exception):
        pass

    decoder = SimpleNamespace(loads=Mock(), JSONDecodeError=CurrentDecodeError)
    monkeypatch.setattr(bot, "ApiError", CurrentError)
    monkeypatch.setattr(bot, "json", decoder)
    message = "invalid pagination_token in prefix {opaque}"
    assert bot.api_error_is_invalid_pagination_cursor(old_error) is False
    assert bot.api_error_is_invalid_pagination_cursor(CurrentError(message, service="openai", status_code=400)) is False
    assert bot.api_error_is_invalid_pagination_cursor(CurrentError(message, service="x", status_code=503)) is False
    decoder.loads.assert_not_called()
    error = CurrentError(message, service="x", status_code=400)
    decoder.loads.return_value = {"errors": {"parameters": {"pagination_token": "invalid"}}}
    assert bot.api_error_is_invalid_pagination_cursor(error) is False
    decoder.loads.assert_called_once_with("{opaque}")
    decoder.loads.return_value = {"errors": {"message": "Invalid query operator"}}
    assert bot.api_error_is_invalid_pagination_cursor(error) is False

    examined = []

    class Message(str):
        def casefold(self):
            examined.append(str(self))
            return super().casefold()

    decoder.loads.return_value = {
        "errors": [None, {
            "message": Message("Invalid query operator"),
            "detail": Message("Pagination token echoed"),
            "reason": Message("Expired NEXT TOKEN"),
        }],
        "message": Message("Top-level message"),
    }
    assert bot.api_error_is_invalid_pagination_cursor(error) is True
    assert examined == ["Invalid query operator", "Pagination token echoed", "Expired NEXT TOKEN"]
    decoder.loads.return_value = []
    assert bot.api_error_is_invalid_pagination_cursor(error) is True
    decoder.loads.side_effect = CurrentDecodeError("malformed")
    assert bot.api_error_is_invalid_pagination_cursor(error) is True


@pytest.mark.parametrize("label", ["mentions", "quote tweets for 900"])
def test_page_merge_preserves_records_duplicate_positions_and_callback_references(monkeypatch, label):
    first, second = {"id": "1"}, {"id": "1"}
    user, other_user, replacement_user, last_user = ({"id": value} for value in (7, "8", "7", "9"))
    media, other_media, replacement_media, last_media = ({"media_key": value} for value in (4, "5", "4", "6"))
    pages = [
        {"data": [first], "includes": {"users": [user, other_user, {}], "media": [media, other_media, {}]}, "meta": {"next_token": "A"}},
        {"data": [second, first], "includes": {"users": [replacement_user, last_user], "media": [replacement_media, last_media]}, "meta": {"next_token": "tail"}},
    ]
    original_rows = [page["data"][:] for page in pages]
    nested = {"shared": True}
    params = {"filter": nested}
    initial_tokens = {"previous"}
    trace, requests = Mock(), []
    monkeypatch.setattr(bot, "log", trace.log)

    def request(path, page_params):
        assert path == "/2/test" and page_params is not params
        assert page_params["filter"] is nested and "request_only" not in page_params
        requests.append(page_params.copy())
        page_params["request_only"] = True
        return pages[len(requests) - 1]

    def observe(data, includes, next_token, request_token, page_number):
        assert data is pages[page_number - 1]["data"]
        assert includes is pages[page_number - 1]["includes"]
        assert (next_token, request_token) == (("A", "") if page_number == 1 else ("tail", "A"))
        data[0]["observed"] = page_number
        data.clear()  # Accumulation already happened; the records remain shared.

    trace.request.side_effect = request
    trace.page.side_effect = observe
    result = bot.x_paginated_get(
        trace.request, "/2/test", params, max_pages=2, label=label,
        on_page=trace.page, initial_requested_tokens=initial_tokens,
    )
    assert requests == [{"filter": nested}, {"filter": nested, "pagination_token": "A"}]
    assert params == {"filter": nested} and initial_tokens == {"previous"}
    assert all(actual is expected for actual, expected in zip(result["data"], [first, second, first]))
    assert len(result["data"]) == 3 and first["observed"] == 1 and second["observed"] == 2
    assert original_rows == [[first], [second, first]]
    assert all(actual is expected for actual, expected in zip(result["includes"]["users"], [replacement_user, other_user, last_user]))
    assert all(actual is expected for actual, expected in zip(result["includes"]["media"], [replacement_media, other_media, last_media]))
    assert len(result["includes"]["users"]) == len(result["includes"]["media"]) == 3
    assert result["_pagination"] == {
        "pages_fetched": 2, "truncated": True, "next_token": "tail",
        "invalid_cursor_recovered": False, "repeated_token_detected": False,
    }
    truncated = call.info if label == "mentions" else call.warning
    assert trace.log.mock_calls == [
        call.info("Fetched %s page %d/%d items=%d next_token=%s", label, 1, 2, 1, True),
        call.info("Fetched %s page %d/%d items=%d next_token=%s", label, 2, 2, 2, True),
        truncated("Pagination truncated for %s after %d page(s); more results remain", label, 2),
    ]
    assert [entry[0] for entry in trace.mock_calls] == [
        "request", "log.info", "page", "request", "log.info", "page",
        "log.info" if label == "mentions" else "log.warning",
    ]


def test_head_retry_resets_results_and_page_budget_but_retains_requested_history(monkeypatch):
    class CurrentError(bot.ApiError):
        pass

    trace = Mock()
    trace.classify.return_value = True
    monkeypatch.setattr(bot, "ApiError", CurrentError)
    monkeypatch.setattr(bot, "api_error_is_invalid_pagination_cursor", trace.classify)
    monkeypatch.setattr(bot, "log", trace.log)
    failure = CurrentError("current classifier decides", service="x", status_code=400)
    retained = {"id": "retained"}
    trace.request.side_effect = [
        {"data": [{"id": "discarded"}], "includes": {"users": [{"id": "old"}]}, "meta": {"next_token": "A"}},
        failure,
        {"data": [retained], "meta": {"next_token": "saved"}},
    ]
    trace.limit.side_effect = [2, 1]

    class PageBudget:
        def __int__(self):
            return trace.limit()

    params, initial_tokens = {"pagination_token": "saved", "since_id": "99"}, {"prior"}
    result = bot.x_paginated_get(
        trace.request, "/2/test", params, max_pages=PageBudget(), label="test",
        on_invalid_cursor=trace.clear, on_repeated_cursor=trace.repeat,
        on_page=trace.page, initial_requested_tokens=initial_tokens,
    )
    assert result == {"data": [retained], "_pagination": {
        "pages_fetched": 1, "truncated": True, "next_token": None,
        "invalid_cursor_recovered": True, "repeated_token_detected": True,
    }}
    assert result["data"][0] is retained
    assert params == {"pagination_token": "saved", "since_id": "99"} and initial_tokens == {"prior"}
    assert [entry.args[1].get("pagination_token") for entry in trace.request.call_args_list] == ["saved", "A", None]
    assert all(entry.args[1]["since_id"] == "99" for entry in trace.request.call_args_list)
    trace.classify.assert_called_once_with(failure)
    trace.clear.assert_called_once_with()
    trace.repeat.assert_called_once_with("saved", 1, 1)
    trace.log.warning.assert_called_once_with(
        "X %s rejected a pagination cursor; cleared the saved cursor and retrying once from the collection head", "test",
    )
    assert [entry[0] for entry in trace.mock_calls] == [
        "limit", "request", "log.info", "page", "request", "classify", "clear",
        "log.warning", "limit", "request", "log.info", "page", "repeat",
    ]


@pytest.mark.parametrize("retain_partial", [False, True])
def test_pre_request_repetition_precedes_suppression_and_uses_current_protocol_error(monkeypatch, retain_partial):
    class CurrentProtocolError(bot.ApiError):
        pass

    monkeypatch.setattr(bot, "PaginationCursorProtocolError", CurrentProtocolError)
    trace = Mock()
    monkeypatch.setattr(bot, "log", trace.log)
    params, tokens = {"pagination_token": "A"}, {"A"}

    def invoke():
        return bot.x_paginated_get(
            trace.request, "/2/test", params, max_pages=2, label="test",
            on_invalid_cursor=trace.clear,
            on_repeated_cursor=trace.repeat if retain_partial else None,
            should_request_cursor=trace.should, on_page=trace.page,
            initial_requested_tokens=tokens,
        )

    if retain_partial:
        assert invoke() == {"data": [], "_pagination": {
            "pages_fetched": 0, "truncated": True, "next_token": None,
            "invalid_cursor_recovered": False, "repeated_token_detected": True,
        }}
        assert trace.mock_calls == [call.repeat("A", 0, 0)]
    else:
        with pytest.raises(CurrentProtocolError) as caught:
            invoke()
        assert type(caught.value) is CurrentProtocolError and caught.value.service == "x"
        assert str(caught.value) == "X test repeated pagination token before request"
        assert trace.mock_calls == [call.clear()]
    assert params == {"pagination_token": "A"} and tokens == {"A"}


@pytest.mark.parametrize("boundary,expected", [
    ("should", ["should"]),
    ("classify", ["should", "request", "classify"]),
    ("clear", ["should", "request", "classify", "clear"]),
    ("retry_disabled", ["should", "request", "classify", "clear"]),
    ("page", ["should", "request", "log.info", "page"]),
    ("repeat", ["should", "request", "log.info", "page", "repeat"]),
])
def test_callback_api_errors_and_retry_opt_out_escape_at_original_boundary(monkeypatch, boundary, expected):
    trace = Mock()
    trace.should.return_value = trace.classify.return_value = True
    monkeypatch.setattr(bot, "api_error_is_invalid_pagination_cursor", trace.classify)
    monkeypatch.setattr(bot, "log", trace.log)
    failure = invalid_pagination_cursor_error()
    row = {"id": "1"}
    trace.request.return_value = {"data": [row], "meta": {"next_token": "A"}}
    if boundary in {"classify", "clear", "retry_disabled"}:
        trace.request.side_effect = failure
    if boundary != "retry_disabled":
        getattr(trace, boundary).side_effect = failure
    if boundary == "page":
        def fail_after_page_mutation(*args):
            row["persisted"] = True
            raise failure
        trace.page.side_effect = fail_after_page_mutation
    with pytest.raises(bot.ApiError) as caught:
        bot.x_paginated_get(
            trace.request, "/2/test", {"pagination_token": "A"}, max_pages=3, label="test",
            on_invalid_cursor=trace.clear, on_repeated_cursor=trace.repeat,
            on_page=trace.page, should_request_cursor=trace.should,
            retry_invalid_cursor_from_head=boundary != "retry_disabled",
        )
    assert caught.value is failure
    assert [entry[0] for entry in trace.mock_calls] == expected
    assert row == ({"id": "1", "persisted": True} if boundary == "page" else {"id": "1"})


def test_conflicting_malformed_sections_keep_validation_and_read_order(monkeypatch):
    trace = Mock()
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "api_error_is_invalid_pagination_cursor", trace.classify)

    class Includes(dict):
        def get(self, key, default=None):
            trace.includes_get(key)
            return super().get(key, default)

    class Response(dict):
        def get(self, key, default=None):
            trace.response_get(key)
            return super().get(key, default)

    includes = Includes(users="bad", media="bad")
    page = Response(data="bad", includes=includes, meta="bad")
    trace.request.return_value = page
    for section in ("data", "users", "media", "meta"):
        trace.reset_mock()
        with pytest.raises(bot.ApiError, match=f"^X test returned malformed paginated response {section}$"):
            bot.x_paginated_get(trace.request, "/2/test", {}, max_pages=1, label="test", on_page=trace.page)
        expected = ["request", "response_get", "response_get", "response_get"]
        if section != "data":
            expected += ["includes_get", "includes_get"]
        assert [entry[0] for entry in trace.mock_calls] == expected
        assert trace.response_get.call_args_list == [call("data"), call("includes"), call("meta")]
        if section != "data":
            assert trace.includes_get.call_args_list == [call("users"), call("media")]
        if section in {"users", "media"}:
            includes[section] = []
        else:
            page[section] = [] if section == "data" else {}


@pytest.mark.parametrize("page", [
    {}, {"meta": {}}, {"meta": {"result_count": 1}},
    {"meta": {"result_count": False}},
    {"errors": [{"detail": "Temporarily unavailable"}]},
    {"meta": {"result_count": 0}, "errors": [{"detail": "Temporarily unavailable"}]},
    {"data": [], "meta": {}, "errors": [{"detail": "Temporarily unavailable"}]},
])
def test_incomplete_pages_cannot_advance_page_or_cursor_callbacks(page):
    callbacks = Mock()
    params = {"pagination_token": "older-page", "since_id": "100"}
    with pytest.raises(bot.ApiError, match="incomplete paginated response"):
        bot.x_paginated_get(
            Mock(return_value=page), "/2/test", params, max_pages=1, label="test",
            on_page=callbacks.page, on_invalid_cursor=callbacks.clear,
            on_repeated_cursor=callbacks.repeat,
        )
    assert callbacks.mock_calls == []
    assert params == {"pagination_token": "older-page", "since_id": "100"}


@pytest.mark.parametrize("page", [
    {"data": [], "meta": {}}, {"data": []}, {"meta": {"result_count": 0}},
])
def test_legitimate_empty_collections_complete_the_page(page):
    observed = Mock()
    result = bot.x_paginated_get(
        Mock(return_value=page), "/2/test", {}, max_pages=1, label="test", on_page=observed,
    )
    observed.assert_called_once_with([], {}, "", "", 1)
    assert result["data"] == []
    assert result["_pagination"]["pages_fetched"] == 1
    assert result["_pagination"]["truncated"] is False


def test_native_parameter_and_budget_errors_precede_requests_and_response_errors(monkeypatch):
    trace = Mock()
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "api_error_is_invalid_pagination_cursor", trace.classify)
    with pytest.raises(TypeError):
        bot.x_paginated_get(trace.request, "/2/test", None, max_pages="bad", label="test")
    with pytest.raises(ValueError, match="invalid literal"):
        bot.x_paginated_get(trace.request, "/2/test", {}, max_pages="bad", label="test")
    assert trace.mock_calls == []
    trace.request.return_value = []
    with pytest.raises(bot.ApiError, match="^X test returned a malformed paginated response object$"):
        bot.x_paginated_get(trace.request, "/2/test", {}, max_pages=0, label="test", on_page=trace.page)
    assert trace.mock_calls == [call.request("/2/test", {})]

"""Digest API observations, provider context and request-health reporting."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

import mrs_log_digest as digest
import mrs_log_digest_api_health as api_health_owner

from tests.helpers.digest_records import BASE, record, structured_record


@pytest.mark.parametrize("age,parse_error,source", [
    (300, None, "x_request"), (300.001, None, "x_request"),
    (-300, None, "x_bearer_request"), (-300.001, None, "x_bearer_request"),
    (0, ValueError, "x_request"), (0, TypeError, "x_request"),
    (0, None, "unrelated"),
])
def test_x_error_observation_keeps_request_window_callbacks_and_mutation(age, parse_error, source):
    message = f"X{' bearer' if source == 'x_bearer_request' else ''} API error 503: fixture"
    r = record(0, "INFO", source, message)
    request = {"time": "request time", "endpoint": "custom/path-name", "method": object(), "url": object()}
    requests = [request]
    indexes = {r.path: 2}
    reference = {"fixture": "source"}
    errors, restrictions, calls = [], [], []
    stats = Counter()

    def parse(value):
        assert value == "request time" and stats["x_api_errors"] == 1
        calls.append("parse")
        if parse_error:
            raise parse_error("request time")
        return BASE - timedelta(seconds=age)

    def seconds(left, right):
        calls.append("seconds")
        assert right is r.ts
        return digest.seconds_between(left, right)

    def short(value, limit):
        assert value == message and limit == 240
        calls.append("short")
        return "formatted error"

    def source_ref(item, mapping):
        assert item is r and mapping is indexes and "failed" not in request
        calls.append("source")
        return reference

    def deleted(value):
        pytest.fail("503 must not invoke the 403 classifier")

    inputs = dict(
        latest_x_request_by_source={source: request},
        pending_mention={"mention_id": "123", "source": "hot_post_reply"},
        pending_qt={"quote_tweet_id": "456"}, is_handled_reply_restriction=False,
        api_errors=errors, handled_api_restrictions=restrictions, stats=stats,
        input_file_indexes=indexes, parse_dt=parse, seconds_between=seconds,
        short=short, record_source_ref=source_ref,
        is_deleted_or_inaccessible_tweet_403=deleted,
    )
    if parse_error is TypeError:
        with pytest.raises(TypeError, match="request time"):
            api_health_owner.handle_x_api_error(r, message, **inputs)
        assert calls == ["parse"] and not errors and "failed" not in request
        return
    assert api_health_owner.handle_x_api_error(r, message, **inputs) is False
    if source == "unrelated":
        assert not calls and not errors and not stats and "failed" not in request
        return
    assert calls == ["parse", *([] if parse_error else ["seconds"]), "short", "source"]
    matched = parse_error is None and abs(age) <= 300
    error = errors[0]
    assert requests[0] is request and not restrictions
    assert error["source_refs"][0] is reference and error["message"] == "formatted error"
    assert (error["target_id"], error["lane"]) == ("123", "hot_post_reply")
    assert error["endpoint"] == ("custom/path-name" if matched else "quote_tweets" if source == "x_bearer_request" else "unknown_oauth")
    if matched:
        assert request["failed"] is True and request["status"] == "503"
        assert error["request_method"] is request["method"] and error["request_url"] is request["url"]
        assert stats["x_api_503_custom_path_name"] == 1
    else:
        assert "failed" not in request and "status" not in request
        assert error["request_method"] == error["request_url"] == ""


def test_api_dispatch_keeps_handled_exits_latest_error_and_shared_rows(monkeypatch):
    messages = [
        "X request: POST https://api.x.com/2/tweets",
        "X API error 503: fixture",
        "Rate Limit: 100", "Remaining: 7",
        "Recorded x API error. status_code=503 errors_in_window=1/3",
        "API cooldown active until 2026-07-25 09:00:05: No mentions returned",
        "Traceback No mentions returned Entering API cooldown after repeated errors until 2026-07-25 09:00:05",
        "Traceback No mentions returned Normalized used-history JSON ordering in fixture.json",
        "X API error 403: Tweet is unavailable. Traceback No mentions returned",
        "X API error 403: not allowed to reply. Traceback No mentions returned",
        "xAI error 503: fixture Traceback No mentions returned",
    ]
    rows = [record(i, "INFO", "xai_request" if i == 10 else "x_request", msg) for i, msg in enumerate(messages)]
    calls, captured = [], {}
    for name in ("handle_cooldown_message", "handle_x_api_error", "observe_provider_error", "enrich_latest_api_error"):
        original = getattr(digest, name)

        def observe(*args, _name=name, _original=original, **kwargs):
            msg = args[0] if _name == "enrich_latest_api_error" else args[1]
            calls.append((_name, msg))
            if _name in {"handle_x_api_error", "observe_provider_error"}:
                for helper in ("short", "record_source_ref"):
                    assert kwargs[helper] is getattr(digest, helper)
                captured["errors"] = kwargs["api_errors"]
            result = _original(*args, **kwargs)
            if _name == "handle_cooldown_message":
                captured["active"] = kwargs["cooldown_active"]
            if _name == "handle_x_api_error":
                captured["restrictions"] = kwargs["handled_api_restrictions"]
                captured["request"] = kwargs["latest_x_request_by_source"]["x_request"]
                assert result is (msg == messages[8])
            return result

        monkeypatch.setattr(digest, name, observe)
    report = digest.analyse(rows, initial_pending_mention={"mention_id": "123"})
    for i, msg in enumerate(messages):
        expected = ["handle_cooldown_message"]
        if i not in {6, 7}:
            expected.append("handle_x_api_error")
            if i != 8:
                expected += ["observe_provider_error", "enrich_latest_api_error"]
        assert [name for name, value in calls if value == msg] == expected
    health = report["api_health"]
    assert health["errors"] is captured["errors"]
    assert health["handled_restrictions"] is captured["restrictions"]
    assert health["cooldown_active"] is captured["active"]
    assert health["x_requests"][0] is captured["request"]
    assert captured["request"]["failed"] is True and captured["request"]["status"] == "403"
    assert health["errors"][0]["rate_limit"] == "100"
    assert health["errors"][0]["remaining"] == "7"
    assert health["errors"][0]["errors_in_window"] == "1/3"
    assert [item["restriction_kind"] for item in health["handled_restrictions"]] == ["deleted_or_inaccessible_tweet", "reply_target_eligibility"]
    assert health["post_cooldown_errors"][0] is health["errors"][1]
    assert report["summary"]["stats"]["tracebacks"] == 2
    assert report["summary"]["stats"]["no_mentions_checks"] == 3
    assert report["summary"]["stats"]["used_history_normalized"] == 1


@pytest.mark.parametrize("source,message,cleared", [
    ("xai_request", "xAI error 503: fixture", True),
    ("ask_grok_for_reply", "xAI error without status", True),
    ("ask_grok_for_reply", "Grok generated usable reply: fixture", True),
    ("ask_grok_for_reply", "Grok chose to skip", True),
    ("unrelated", "xAI error 503: fixture", False),
])
def test_provider_error_reset_keeps_context_identity_order_and_attempt_index(monkeypatch, source, message, cleared):
    context = {"lane": "mention", "context_id": "123", "author_id": "456"}
    attempt = {**context, "time": digest.dt_text(BASE), "stage": "proposer", "model": "fixture", "usage_observed": False}
    captured, calls = {}, []
    original_usage = digest.observe_provider_message
    original_error = digest.observe_provider_error

    def usage(*args, **kwargs):
        result = original_usage(*args, **kwargs)
        captured["context"] = result[0]
        assert result[1] == 0
        calls.append("usage")
        return result

    def error(*args, **kwargs):
        assert calls == ["usage"] and kwargs["active_xai_context"] is captured["context"]
        result = original_error(*args, **kwargs)
        assert result is (None if cleared else captured["context"])
        calls.append("error")
        return result

    monkeypatch.setattr(digest, "observe_provider_message", usage)
    monkeypatch.setattr(digest, "observe_provider_error", error)
    report = digest.analyse([record(1, "INFO", source, message)], initial_active_xai_context=context, initial_active_xai_call_attempt=attempt)
    assert calls == ["usage", "error"]
    assert report["resume_context"]["active_xai_context"] is (None if cleared else captured["context"])
    assert report["resume_context"]["active_xai_call_attempt"] == attempt


def test_api_preparation_keeps_current_helpers_production_identity_and_late_report_effects(monkeypatch):
    calls, captured = [], {}
    helpers = ("bounded_event_text", "valid_string_public_post_id", "_normalised_structured_reply_confirmation", "parse_dt", "seconds_between")
    for name in helpers:
        original = getattr(digest, name)

        def helper(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(digest, name, helper)

    class CurrentCounter(Counter):
        def items(self):
            calls.append(("items", id(self)))
            return super().items()

    class CurrentDatetime(datetime):
        @classmethod
        def strptime(cls, value, fmt):
            calls.append("strptime")
            return datetime.strptime(value, fmt)

    original_prepare = digest.prepare_api_health
    original_quality = digest.historical_context_quality_summary
    original_report = digest.api_health_report

    def prepare(**inputs):
        for name in helpers:
            assert inputs[name] is getattr(digest, name)
        assert inputs["event_counter"] is CurrentCounter
        assert inputs["strptime"] == CurrentDatetime.strptime
        assert inputs["datetime_min"] is CurrentDatetime.min
        assert inputs["SHA256_LOWER_RE"] is digest.SHA256_LOWER_RE
        excluded = {"kind": "remote_write_succeeded", "post_id": "900"}
        included = {"kind": "remote_write_succeeded", "post_id": "901"}
        inputs["events"].extend([dict(excluded), included])
        inputs["production_event_object_ids"].update([id(excluded), id(included)])
        first = {"kind": "tweet_transport", "phase": "request_started", "transaction_id": "a" * 64, "lane": "quote_image"}
        inputs["remote_write_transactions"].extend([first, {**first, "lane": "mention"}])
        inputs["structured_reply_confirmations"].append({})
        calls.clear()
        prepared = original_prepare(**inputs)
        assert set(helpers) | {"strptime"} <= set(calls)
        assert prepared.observed_success_post_ids == {"901"}
        assert prepared.observed_tweet_transport_by_id["a" * 64] is first
        assert prepared.observed_tweet_transport_lane_conflict_ids == ["a" * 64]
        assert prepared.all_api_failures[0] is inputs["api_errors"][0]
        assert prepared.post_cooldown_errors[0] is inputs["api_errors"][0]
        assert type(prepared.api_status_counts) is CurrentCounter
        captured.update(inputs=inputs, prepared=prepared)
        calls.append("prepared")
        return prepared

    def quality(events):
        assert calls[-1] == "prepared"
        errors = captured["inputs"]["api_errors"]
        errors[0]["status"] = "502"
        errors.append({"status": "429", "time": digest.dt_text(BASE), "message": "late"})
        calls.append("quality")
        return original_quality(events)

    def report(prepared, **inputs):
        assert prepared is captured["prepared"] and "quality" in calls
        counter_ids = {id(prepared.api_status_counts), id(prepared.observed_tweet_transport_lane_counts)}
        assert not any(("items", identity) in calls for identity in counter_ids)
        result = original_report(prepared, **inputs)
        assert all(("items", identity) in calls for identity in counter_ids)
        assert result["has_5xx_failures"] is True
        assert result["status_counts"] == {"403": 1, "408": 1}
        assert result["errors"] is captured["inputs"]["api_errors"] and len(result["errors"]) == 2
        assert result["handled_restrictions"] is captured["inputs"]["handled_api_restrictions"]
        assert result["x_requests"] is captured["inputs"]["x_requests"]
        assert result["counter_semantics"] is prepared.api_counter_semantics
        assert result["post_cooldown_errors"] is prepared.post_cooldown_errors
        assert result["rate_limit_failure_count"] == 0
        calls.append("report")
        return result

    monkeypatch.setattr(digest, "Counter", CurrentCounter)
    monkeypatch.setattr(digest, "datetime", CurrentDatetime)
    monkeypatch.setattr(digest, "prepare_api_health", prepare)
    monkeypatch.setattr(digest, "historical_context_quality_summary", quality)
    monkeypatch.setattr(digest, "api_health_report", report)
    digest.analyse([
        record(0, "INFO", "fixture", "Entering API cooldown after repeated errors until 2026-07-25 09:00:00"),
        record(1, "INFO", "x_request", "X API error 408: fixture"),
        record(2, "INFO", "x_request", "X API error 403: not allowed to reply"),
    ])
    assert "report" in calls


def test_x_request_endpoint_classification_is_path_and_method_specific():
    assert digest.parse_x_request_start(
        "X request: POST https://api.x.com/2/media/upload?command=INIT"
    )["endpoint"] == "media/upload"
    assert digest.parse_x_request_start(
        "X request: POST https://api.x.com/2/tweets"
    )["endpoint"] == "tweet/create"
    assert digest.parse_x_request_start(
        "X bearer request: GET https://api.x.com/2/users/123/mentions"
    )["endpoint"] == "mentions"
    assert digest.parse_x_request_start("unrelated") is None


def test_combined_quote_search_calls_are_not_labelled_hot_post_searches():
    from collections import Counter
    from unittest.mock import Mock
    from mrs_log_digest_legacy_posts import handle_legacy_quiet_message

    stats = Counter()
    event = Mock()
    for message in [
        "Quote recent-search request",
        "X bearer request: GET https://api.x.com/2/tweets/search/recent",
    ]:
        handle_legacy_quiet_message(Mock(), message, stats=stats, add_event=event)
    assert stats["quote_recent_search_calls"] == 1
    assert stats["recent_search_calls"] == 1
    assert stats["hot_post_recent_search_calls"] == 0
    handle_legacy_quiet_message(Mock(), "Hot-post recent-search request", stats=stats, add_event=event)
    assert stats["hot_post_recent_search_calls"] == 1
    assert stats["quote_recent_search_calls"] == 1


def test_provider_observation_keeps_active_state_shared_rows_and_current_callbacks(monkeypatch):
    messages = [
        ("other_source", "Calling AI-first reply stage=proposer model=ignored"),
        ("ask_grok_for_reply", "Asking Grok for reply. context_text='fixture'"),
        ("tested_pipeline_structured_call", "Calling tested reply pipeline stage=proposer provider=OpenAI model=fixture-model reasoning_effort=low"),
        ("usage_logger", "xAI reply stage=proposer usage={}"),
        ("usage_logger", "Tested reply stage=proposer provider=OpenAI usage=[]"),
        ("usage_logger", "Tested reply stage=proposer provider=OpenAI usage={'prompt_tokens': '7', 'prompt_tokens_details': {'cached_tokens': 3}, 'cache_creation_input_tokens': False, 'cache_write_input_tokens': 0}"),
        ("xai_structured_reply_call", "Calling AI-first reply stage=proposer model=grok-fixture"),
        ("usage_logger", "xAI reply stage=proposer usage={'cost_in_usd_ticks': 0}"),
        ("ask_grok_for_reply", "Grok chose to skip"),
    ]
    records = [
        replace(structured_record(i, {}), src=source, msg=message)
        for i, (source, message) in enumerate(messages)
    ]
    calls = {}

    def watch(name):
        original = getattr(digest, name)
        calls[name] = []

        def current(*args, **kwargs):
            result = original(*args, **kwargs)
            calls[name].append((args, kwargs, result))
            return result

        monkeypatch.setattr(digest, name, current)

    for name in (
        "parse_xai_call_start", "parse_xai_usage_from_msg",
        "xai_usage_context_from_pending", "xai_usage_stage_from_msg",
        "provider_usage_provider_from_msg", "normalise_reply_lane",
        "summarize_xai_usage_event", "_cache_input_metric",
        "int_usage_value", "optional_int_usage_value", "short",
    ):
        watch(name)

    boundaries = []
    original_observe = digest.observe_provider_message

    def observe(r, msg, **inputs):
        assert r is records[len(boundaries)] and msg == r.msg
        context, index = original_observe(r, msg, **inputs)
        boundaries.append((inputs, context, index, [
            row["usage_observed"] for row in inputs["xai_call_attempts"]
        ], tuple(inputs["xai_call_attempts"])))
        return context, index

    monkeypatch.setattr(digest, "observe_provider_message", observe)
    report = digest.analyse(
        records,
        initial_pending_mention={"mention_id": "505", "author_id": "606"},
        generation_time=records[-1].ts,
    )
    assert [item[2] for item in boundaries] == [None, None, 0, 0, 0, None, 1, None, None]
    assert [item[3] for item in boundaries] == [
        [], [], [False], [False], [False], [True],
        [True, False], [True, True], [True, True],
    ]
    shared = boundaries[0][0]
    for inputs, _context, _index, _observed, _attempts in boundaries:
        for key in ("xai_call_attempts", "xai_usage_events", "xai_usage_parse_errors", "stats"):
            assert inputs[key] is shared[key]
    contexts = [entry[2] for entry in calls["xai_usage_context_from_pending"]]
    assert len(contexts) == 3
    assert boundaries[1][1] is contexts[0]
    assert all(boundaries[i][1] is contexts[1] for i in range(2, 6))
    assert all(boundaries[i][1] is contexts[2] for i in range(6, 9))
    assert report["resume_context"]["active_xai_context"] is None
    assert report["resume_context"]["active_xai_call_attempt"] is None

    attempts = shared["xai_call_attempts"]
    assert boundaries[2][4][0] is attempts[0]
    assert boundaries[6][4][1] is attempts[1]
    assert attempts == [
        {"time": "2026-09-04 12:00:02", "lane": "mention", "context_id": "505",
         "author_id": "606", "stage": "proposer", "model": "fixture-model",
         "provider": "OpenAI", "reasoning_effort": "low", "usage_observed": True,
         "usage_time": "2026-09-04 12:00:05"},
        {"time": "2026-09-04 12:00:06", "lane": "mention", "context_id": "505",
         "author_id": "606", "stage": "proposer", "model": "grok-fixture",
         "usage_observed": True, "usage_time": "2026-09-04 12:00:07"},
    ]
    assert [entry[0][0] for entry in calls["parse_xai_call_start"]] == [records[2].msg, records[6].msg]
    assert [entry[0][0] for entry in calls["parse_xai_usage_from_msg"]] == [r.msg for r in records]
    summaries = calls["summarize_xai_usage_event"]
    for i, offset in enumerate((3, 5, 7)):
        args, _kwargs, result = summaries[i]
        assert args[0] is records[offset]
        assert args[1] is calls["parse_xai_usage_from_msg"][offset][2][0]
        assert args[2] is boundaries[offset][1]
        assert result is shared["xai_usage_events"][i]
    events = shared["xai_usage_events"]
    assert [item["call_start_matched"] for item in events] == [False, True, True]
    assert [item["model"] for item in events] == ["", "fixture-model", "grok-fixture"]
    assert events[1]["prompt_tokens"] == 7
    assert events[1]["cache_read_input_tokens"] == events[1]["cached_tokens"] == 3
    assert events[1]["cache_creation_input_tokens"] is None
    assert events[1]["cache_write_input_tokens"] == 0
    assert events[0]["cost_in_usd_ticks"] is None and events[2]["cost_in_usd_ticks"] == 0
    assert len(calls["xai_usage_stage_from_msg"]) == 6
    assert len(calls["provider_usage_provider_from_msg"]) == 3
    assert len(calls["_cache_input_metric"]) == 9
    assert any(entry[0] == (False,) for entry in calls["optional_int_usage_value"])
    assert any(entry[0] == ("7",) for entry in calls["int_usage_value"])
    assert any(entry[0] == (records[4].msg, 500) for entry in calls["short"])
    assert shared["xai_usage_parse_errors"] == [{
        "time": "2026-09-04 12:00:04", "where": "usage_logger:5",
        "message": records[4].msg, "error": "xAI usage payload was list, not dict",
    }]
    assert {key: shared["stats"][key] for key in (
        "provider_usage_successes", "xai_usage_successes", "openai_usage_successes", "xai_usage_parse_errors",
    )} == {"provider_usage_successes": 3, "xai_usage_successes": 2,
           "openai_usage_successes": 1, "xai_usage_parse_errors": 1}
    lane_calls = len(calls["normalise_reply_lane"])
    restored = digest.normalise_active_xai_call_attempt({**attempts[1], "usage_observed": False})
    assert restored["lane"] == "mention"
    assert len(calls["normalise_reply_lane"]) == lane_calls + 1

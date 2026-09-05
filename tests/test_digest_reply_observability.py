import copy
import json
import os
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import historical_context_formatter as formatter
import mrs_log_digest as digest
import mrs_log_digest_legacy_posts as legacy_posts
import mrs_log_digest_transactions as transaction_owner


def event(kind, **values):
    return {"kind": kind, "time": "2026-07-15 12:00:00", **values}


def structured_record(offset, payload, *, level="INFO"):
    return digest.Record(
        datetime(2026, 9, 4, 12) + timedelta(seconds=offset),
        level,
        "log_event",
        offset + 1,
        "EVENT " + json.dumps(payload, sort_keys=True),
        "mrsMThatcher.log",
        offset + 1,
    )


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


def test_receipt_builders_keep_field_precedence_callback_order_and_pending_identity():
    r = structured_record(0, {})
    indexes, refs, calls = {r.path: 2}, {"record_number": 1}, []
    receipts, stats = [], Counter()
    pending = {"lane": "mention", "target_id": "123", "reply_post_id": "999"}
    fields = {"lane": "", "target_id": None, "message": "override", "source_class": "override", "source_refs": ["ignored"], "stats": stats}

    def short(message, limit):
        assert message == r.msg and limit == 500
        assert fields["reply_post_id"] == "999"
        calls.append("short")
        return "shortened"

    def classify(path):
        assert path == r.path
        calls.append("classify")
        return True

    def source(item, supplied_indexes):
        assert item is r and supplied_indexes is indexes
        assert receipts == [] and stats == {}
        calls.append("source")
        return refs

    after = transaction_owner.add_confirmed_reply_receipt_event(
        "removed", r, fields, input_file_indexes=indexes, stats=stats,
        short=short, is_selftest_log_path=classify, record_source_ref=source,
        confirmed_reply_receipts=receipts, pending_confirmed_reply_receipt=pending,
    )
    assert calls == ["short", "classify", "source"]
    assert after == {} and after is not pending
    assert pending == {"lane": "mention", "target_id": "123", "reply_post_id": "999"}
    item = receipts[0]
    assert item["lane"] == "" and item["target_id"] is None and item["reply_post_id"] == "999"
    assert item["message"] == item["source_class"] == "override"
    assert item["source_refs"][0] is refs and item["stats"] is stats
    assert stats == {"confirmed_reply_receipt_removed": 1}

    receipts.clear()
    stats.clear()
    calls.clear()
    transaction_owner.add_receipt_event(
        "regular_written", r, fields, input_file_indexes=indexes, stats=stats,
        short=short, is_selftest_log_path=classify, record_source_ref=source,
        receipt_events=receipts,
    )
    assert calls == ["short", "classify", "source"]
    assert receipts[0]["source_refs"][0] is refs
    assert receipts[0]["message"] == "override" and receipts[0]["stats"] is stats
    assert stats == {"receipt_regular_written": 1}


def test_confirmed_receipt_adapter_rebinds_exact_pending_state_across_sources(monkeypatch):
    original = digest._add_confirmed_reply_receipt_event
    observations = []

    def observe(kind, r, fields, **kwargs):
        before = kwargs["pending_confirmed_reply_receipt"]
        snapshot = dict(before)
        after = original(kind, r, fields, **kwargs)
        assert before == snapshot
        observations.append((kind, before, after, kwargs))
        return after

    monkeypatch.setattr(digest, "_add_confirmed_reply_receipt_event", observe)
    messages = [
        "Wrote confirmed reply receipt pending local reconciliation source=mention target_id=123 reply_post_id=999",
        "Wrote conversational reply sending receipt source=quote_tweet target_id=456",
        "Promoted conversational reply receipt to confirmed source=quote_tweet target_id=456 reply_post_id=888",
        "Wrote confirmed reply receipt pending local reconciliation source=quote_tweet target_id=777 reply_post_id=7777",
        "Reconciling confirmed reply receipt target_id=321 reply_post_id=111",
        "Removed reconciled confirmed-reply receipt",
        "Removed reconciled confirmed-reply receipt",
    ]
    records = [replace(structured_record(i, {}), msg=message) for i, message in enumerate(messages)]
    records[3] = replace(records[3], path="mrsMThatcher.selftest.log")
    report = digest.analyse(records)
    receipts = report["confirmed_reply_recovery"]["receipt_events"]
    for index, (kind, before, after, kwargs) in enumerate(observations):
        assert kwargs["confirmed_reply_receipts"] is receipts
        assert kwargs["stats"] is observations[0][3]["stats"]
        if kind in {"written", "reconciled", "removed"}:
            assert after is not before
        else:
            assert after is before
        if index in (1, 2, 5, 6):
            assert before is observations[index - 1][2]
    assert observations[3][1] is not observations[2][2]
    assert observations[4][1] is observations[2][2]
    assert receipts[3]["source_class"] == "selftest"
    assert receipts[4]["lane"] == receipts[5]["lane"] == "mention"
    assert receipts[5]["target_id"] == "321" and receipts[5]["reply_post_id"] == "111"
    assert "lane" not in receipts[6]


def test_legacy_observation_handlers_preserve_first_match_and_outer_continue(monkeypatch):
    regular = "Wrote confirmed regular-post receipt pending local reconciliation"
    meme = "Wrote confirmed meme-post receipt pending local reconciliation"
    media = "Reply media context fallback lane=mention target_id=123 photos_expected=2 initial_mode=images final_mode=text status=failed http_status=503"
    media_later = "Reply media context unavailable lane=quote_tweet target_id=456 photos_expected=3 mode=none status=unavailable"
    cooldown = "Entering API cooldown after 429 until 2026-09-04 12:10:00"
    messages = [regular + " " + meme + " " + media, media + "\n" + media_later + "\n" + cooldown, media_later, cooldown]
    trace = []
    for name in ("handle_legacy_receipt_message", "handle_legacy_reply_media_context_message"):
        original = getattr(digest, name)

        def observe(r, msg, name=name, original=original, **kwargs):
            handled = original(r, msg, **kwargs)
            trace.append((name, r.ordinal, handled))
            return handled

        monkeypatch.setattr(digest, name, observe)
    media_items = []
    original_media = digest._add_reply_media_context_event

    def observe_media(r, fields, **kwargs):
        original_media(r, fields, **kwargs)
        media_items.append(kwargs["reply_media_context"][-1])

    monkeypatch.setattr(digest, "_add_reply_media_context_event", observe_media)
    report = digest.analyse([replace(structured_record(i, {}), msg=msg) for i, msg in enumerate(messages)])
    assert [item["kind"] for item in report["main_post_recovery"]["receipt_events"]] == ["regular_written"]
    assert [(item["lane"], item["photos"], item["mode"]) for item in media_items] == [
        ("mention", "2", "text"), ("quote_tweet", "3", "none"),
    ]
    assert media_items[0]["http_status"] == "503" and "http_status" not in media_items[1]
    assert len([item for item in report["events"] if item["kind"] == "api_cooldown_entered"]) == 1
    assert [(name.removeprefix("handle_legacy_"), ordinal, handled) for name, ordinal, handled in trace] == [
        ("receipt_message", 1, True),
        ("receipt_message", 2, False), ("reply_media_context_message", 2, True),
        ("receipt_message", 3, False), ("reply_media_context_message", 3, True),
        ("receipt_message", 4, False), ("reply_media_context_message", 4, False),
    ]


def test_legacy_post_dispatch_keeps_quiet_fallthrough_and_interleaved_observers(monkeypatch):
    names = [
        "handle_legacy_quiet_message", "handle_legacy_quote_image_selection",
        "record_original_editorial_selection", "record_original_editorial_shadow",
        "record_generated_identity_shadow", "record_generated_identity_policy",
        "handle_legacy_generated_image_spacing", "handle_legacy_quote_image_posting",
        "handle_legacy_meme_posting", "handle_legacy_created_post",
        "handle_legacy_mention_reply", "handle_legacy_quote_reply",
    ]
    trace = []
    for name in names:
        original = getattr(digest, name)

        def observe(*args, name=name, original=original, **kwargs):
            trace.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(digest, name, observe)
    selection = "Selected quote line_no=1 quote_hash=aB weight=1.00 seasonal_boost=False"
    cases = [
        ("No mentions returned " + selection, names[:2]),
        ("Fetched 2 hot-post conversation candidate(s) for post_id=123 "
         "Quote-tweet check status=checked " + selection, names[:1]),
        *[(marker + " {}", names[:2] + [name]) for marker, name in zip(
            ("ORIGINAL_EDITORIAL_SELECTION_RESULT", "ORIGINAL_EDITORIAL_SHADOW_RESULT",
             "GENERATED_IDENTITY_POLICY_SHADOW_RESULT", "GENERATED_IDENTITY_POLICY_APPLIED"),
            names[2:6],
        )],
        ("Creating X post. reply_to_id=123 media_count=0 made_with_ai=True "
         "Created X post successfully. response={'data': {'id': '999'}}",
         names[:2] + names[6:8]),
        ("Skipping mention check: minimum interval between replies not reached",
         names[:2] + names[6:]),
    ]
    for msg, expected in cases:
        trace.clear()
        report = digest.analyse([replace(structured_record(0, {}), msg=msg)])
        assert trace == expected
        if msg.startswith("No mentions"):
            assert report["summary"]["stats"]["no_mentions_checks"] == 1
            assert report["summary"]["stats"]["quote_selected"] == 1
        if msg.startswith("Fetched"):
            assert report["summary"]["stats"].get("quote_tweet_status_checked", 0) == 0
        if msg.startswith("Creating"):
            assert report["summary"]["stats"].get("created_x_posts", 0) == 0
        if msg.startswith("Skipping"):
            assert report["summary"]["stats"]["mention_checks_skipped_spacing"] == 1
    assert legacy_posts.Record is digest.Record


def test_legacy_quote_posting_keeps_pending_literals_and_reverse_image_row_identity():
    r = structured_record(0, {})
    pending, rows, emitted, literal_inputs = {"retained": []}, [], [], []
    text = object()

    def add_event(kind, ts, **fields):
        emitted.append((kind, ts, fields))

    def lit(value):
        literal_inputs.append(value)
        return text

    selection = dict(pending_quote=pending, regular_image_usage_events=rows, add_event=add_event)
    assert legacy_posts.handle_legacy_quote_image_selection(
        r, "Selected quote line_no=2 quote_hash=aB weight=1.00 seasonal_boost=False", **selection,
    ) is True
    assert pending["quote_weight"] == "1.00" and pending["seasonal_boost"] == "False"
    assert legacy_posts.handle_legacy_quote_image_selection(
        r, "Selected matched image basename=a.jpg image_no=4 score=7.00 components= x=1 ", **selection,
    ) is True
    assert legacy_posts.handle_legacy_quote_image_selection(
        r, "REGULAR_IMAGE_SELECTED source=original basename=a.jpg score=7.00 "
        "origin_quote_hash= origin_quote_match=false origin_quote_boost=0.0", **selection,
    ) is True
    original_row = rows[0]
    latest_row, completed_row, other_row = dict(original_row), dict(original_row, made_with_ai="true"), {"basename": "other"}
    rows.extend([latest_row, completed_row, other_row])
    posting = dict(**selection, lit=lit)
    for msg in (
        "Selected line_no=3 text='line one\nline two'",
        "Quote text='latest'",
        "Creating X post. reply_to_id=None media_count=1 made_with_ai=False",
    ):
        handled, returned = legacy_posts.handle_legacy_quote_image_posting(r, msg, **posting)
        assert handled is True and returned is pending
    assert literal_inputs == ["'line one\nline two'", "'latest'"]
    assert pending["text"] is text
    assert rows[0] is original_row and "made_with_ai" not in original_row
    assert rows[1] is latest_row and latest_row["made_with_ai"] == "false"
    assert rows[2] is completed_row and completed_row["made_with_ai"] == "true"
    assert rows[3] is other_row and "made_with_ai" not in other_row
    handled, returned = legacy_posts.handle_legacy_quote_image_posting(
        r, "Quote/image posted successfully. posted_id=123", **posting,
    )
    assert handled is True and returned == {} and returned is not pending
    assert pending["text"] is emitted[-1][2]["text"] is text
    assert emitted[-1][0:2] == ("quote_image_posted", r.ts)
    assert legacy_posts.handle_legacy_quote_image_selection(r, "Selected quote line_no=bad", **selection) is False
    handled, returned = legacy_posts.handle_legacy_quote_image_posting(r, "unrecognised", **posting)
    assert handled is False and returned is pending
    for msg in (
        "Creating X post. reply_to_id=None media_count=1 made_with_ai=True",
        "Creating X post. reply_to_id=123 media_count=0 made_with_ai=False",
    ):
        empty = {}
        handled, returned = legacy_posts.handle_legacy_quote_image_posting(
            r, msg, **dict(posting, pending_quote=empty),
        )
        assert handled is True and returned is empty and empty == {}


def test_legacy_spacing_returns_shared_latest_but_blocked_only_appends():
    r, latest, rows = structured_record(0, {}), {}, []
    for msg, kind in (
        ("GENERATED_IMAGE_SPACING_STATUS pool_enabled=true allowed=true "
         "original_posts_since_generated=2 required=2", "status"),
        ("GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=true allowed=false "
         "original_posts_since_generated=0 required=2 image_source=generated image=g.png", "state_updated"),
    ):
        before = latest
        handled, latest = legacy_posts.handle_legacy_generated_image_spacing(
            r, msg, latest_generated_image_spacing=latest, generated_image_spacing_events=rows,
        )
        assert handled is True and latest is rows[-1] and latest is not before
        assert latest["kind"] == kind and type(latest["required"]) is int
    for msg, expected in (
        ("GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=0 required=2", True),
        ("GENERATED_IMAGE_SPACING_STATUS pool_enabled=True allowed=false", False),
        ("unrecognised", False),
    ):
        handled, returned = legacy_posts.handle_legacy_generated_image_spacing(
            r, msg, latest_generated_image_spacing=latest, generated_image_spacing_events=rows,
        )
        assert handled is expected and returned is latest
    assert len(rows) == 3 and rows[-1]["kind"] == "blocked" and rows[-1] is not latest


def test_legacy_meme_replaces_pending_and_delegates_current_literal_helper(monkeypatch):
    r, previous, emitted, calls = structured_record(0, {}), {"old": []}, [], []
    summary = object()

    def lit(value):
        calls.append(value)
        return summary

    monkeypatch.setattr(digest, "lit", lit)
    digest.analyse([replace(r, msg="Meme image summary for cache: 'summary'")])
    assert calls == [" 'summary'"]
    kwargs = dict(add_event=lambda kind, ts, **fields: emitted.append(fields), lit=lit)
    handled, pending = legacy_posts.handle_legacy_meme_posting(
        r, "Posting meme image: /tmp/m.jpg ", pending_meme=previous, **kwargs,
    )
    assert handled is True and pending is not previous and previous == {"old": []}
    for msg, expected in (("Meme image summary for cache: 'summary'", True), ("not a meme", False)):
        handled, returned = legacy_posts.handle_legacy_meme_posting(r, msg, pending_meme=pending, **kwargs)
        assert handled is expected and returned is pending
    handled, returned = legacy_posts.handle_legacy_meme_posting(
        r, "Daily meme posted successfully. posted_id=321 file=m.jpg ", pending_meme=pending, **kwargs,
    )
    assert handled is True and returned == {} and returned is not pending
    assert emitted == [{"post_id": "321", "file": "m.jpg", "summary": summary, "image": "/tmp/m.jpg"}]
    assert emitted[0]["summary"] is pending["summary"] is summary


@pytest.mark.parametrize("payload,expected_id,canonical", [
    ("{'data': {'id': '123', 'text': 'text'}}", "123", True),
    ("{'data': {'id': 123}}", "123", False),
    ("{'data': {'id': True}}", "True", False),
    ("{'data': {'id': ' 123'}}", " 123", False),
    ("{'data': {'id': '123'}} trailing", "123", False),
    ("malformed {'id': '123'}", "123", False),
    ("{'data': None}", None, False),
])
def test_legacy_response_helpers_keep_display_parsing_separate_from_authority(payload, expected_id, canonical):
    msg = "Created X post successfully. response=" + payload
    assert digest.try_parse_response_id_text is legacy_posts.try_parse_response_id_text
    assert digest.try_parse_response_id_text(msg)[0] == expected_id
    assert digest.response_post_id_is_canonical_string(msg) is canonical
    assert digest.try_parse_response_id_text("no response") == (None, None)
    assert digest.response_post_id_is_canonical_string("no response") is False


def test_legacy_created_post_delegates_both_canonical_calls_and_returns_exact_evidence(monkeypatch):
    r, previous, stats, authority, calls = structured_record(0, {}), {"old": []}, Counter(), {999}, []
    emitted, text = {}, object()
    canonical_results = iter((True, False))

    def parse(msg):
        calls.append(("parse", msg))
        return "123", text

    def canonical(msg):
        calls.append(("canonical", msg))
        return next(canonical_results)

    def add_event(kind, ts, **fields):
        calls.append((kind, ts, fields))
        authority.add(id(emitted))
        return emitted

    kwargs = dict(production_record=False, last_created_post=previous, stats=stats,
                  production_event_object_ids=authority, add_event=add_event,
                  try_parse_response_id_text=parse, response_post_id_is_canonical_string=canonical)
    handled, returned = legacy_posts.handle_legacy_created_post(r, "unrecognised", **kwargs)
    assert handled is False and returned is previous and calls == []
    handled, returned = legacy_posts.handle_legacy_created_post(r, "Created X post successfully", **kwargs)
    assert handled is True and returned is not previous and previous == {"old": []}
    assert returned == {"time": r.ts, "post_id": "123", "post_text": text,
                        "canonical_post_id": True, "production_identity": False}
    assert returned["time"] is r.ts and returned["post_text"] is text
    assert [call[0] for call in calls] == ["parse", "canonical", "remote_write_succeeded", "canonical"]
    assert stats == Counter(created_x_posts=1) and authority == {999}
    values, boundaries = [], []
    original_observe = digest.handle_legacy_created_post

    def observe(*args, **kwargs):
        result = original_observe(*args, **kwargs)
        boundaries.append(list(values))
        return result

    monkeypatch.setattr(digest, "handle_legacy_created_post", observe)
    monkeypatch.setattr(digest, "valid_string_public_post_id", lambda value: values.append(value) or True)
    digest.analyse([replace(r, msg="Created X post successfully. response={'data': {'id': '123'}}")])
    assert boundaries == [["123", "123"]]
    monkeypatch.setattr(digest, "try_parse_response_id_text", lambda msg: (None, text))
    values.clear()
    digest.analyse([replace(r, msg="Created X post successfully. response={'data': {'id': '123'}}")])
    assert boundaries[-1] == ["123"]


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply", "quote tweet"])
def test_legacy_reply_returns_pending_context_and_preserves_distinct_lane_resets(lane):
    r, context, old, counters, calls = structured_record(0, {}), {"attempt": []}, {"old": []}, Counter(), []
    quote = lane == "quote tweet"
    pending_key = "pending_qt" if quote else "pending_mention"
    id_key = "quote_tweet_id" if quote else "mention_id"
    handler = legacy_posts.handle_legacy_quote_reply if quote else legacy_posts.handle_legacy_mention_reply
    kwargs = dict(record_index=17, production_record=False, active_xai_context=context,
                  last_created_post={}, production_event_object_ids=set(), routine_skip_counts=counters,
                  add_event=lambda kind, ts, **fields: calls.append((kind, fields)), lit=digest.lit)
    suffix = " original_post_id=456" if quote else ""
    handled, pending, returned_context = handler(
        r, f"Considering {lane} id=123 author_id=abc{suffix} text='first\nsecond'", **{pending_key: old}, **kwargs,
    )
    assert handled is True and pending is not old and old == {"old": []}
    assert returned_context is context and pending["considered_seq"] == 17
    assert pending["considered_at"] == r.ts.strftime("%Y-%m-%d %H:%M:%S")
    assert pending["incoming_text"] == "first\nsecond" and pending["_identity_production"] is False
    if lane == "hot_post_reply":
        assert pending["hot_post_reply_id"] == pending["mention_id"] == "123"
    generated_lane = "quote tweet" if quote else "mention"
    handled, same, returned_context = handler(
        r, f"Generated reply to {generated_lane} 123: 'reply'", **{pending_key: pending}, **kwargs,
    )
    assert handled is True and same is pending and returned_context is None
    handled, replaced, returned_context = handler(
        r, f"Generated reply to {generated_lane} 789: 'different'", **{pending_key: pending}, **kwargs,
    )
    assert handled is True and replaced is not pending and returned_context is None
    assert replaced[id_key] == "789" and "author_id" not in replaced
    if quote:
        assert "source" not in replaced
    else:
        assert replaced["source"] == "unknown"
    cache = "Recorded and cached own " + ("quote-tweet " if quote else "") + "auto-reply id=999"
    handled, same, returned_context = handler(r, cache, **{pending_key: pending}, **kwargs)
    assert handled is True and same is pending and returned_context is context
    assert pending["reply_post_id"] == "999" and pending["_reply_post_id_production"] is False
    empty = {}
    for msg in (cache, "Quote-tweet reply posted successfully" if quote else "Reply posted successfully", "unrecognised"):
        handled, same, returned_context = handler(r, msg, **{pending_key: empty}, **kwargs)
        assert handled is False and same is empty and returned_context is context
    # Normal skips do not use DOTALL; hot-post candidate skips do not reset state.
    msg = f"Skipping {lane} 123: first\nsecond"
    handled, returned, returned_context = handler(r, msg, **{pending_key: pending}, **kwargs)
    assert handled is quote
    if quote:
        assert returned == {} and returned is not pending and returned_context is None
    else:
        assert returned is pending and returned_context is context
    if not quote:
        handled, returned, returned_context = handler(
            r, "Skipping hot-post candidate 123: first\nsecond", **{pending_key: pending}, **kwargs,
        )
        assert handled is True and returned is pending and returned_context is context
    for msg in (f"No usable reply generated for {lane} 123", f"Skipping {lane} 123: already seen/replied/skipped"):
        handled, returned, returned_context = handler(r, msg, **{pending_key: pending}, **kwargs)
        assert handled is True and returned == {} and returned is not pending and returned_context is None
    assert pending["reply"] == "reply"


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply", "quote tweet"])
@pytest.mark.parametrize("evidence,explicit_id,expected_authority", [
    ({"post_id": "999", "canonical_post_id": True, "production_identity": True}, False, True),
    ({"post_id": "999", "canonical_post_id": False, "production_identity": True}, False, False),
    ({"post_id": "999", "canonical_post_id": True, "production_identity": False}, False, False),
    ({"post_id": "999", "canonical_post_id": False, "production_identity": False}, True, True),
])
def test_legacy_reply_success_keeps_fallback_and_exact_event_authority(lane, evidence, explicit_id, expected_authority):
    r, context, authority, events, shared = structured_record(0, {}), {}, {999}, [], []
    quote = lane == "quote tweet"
    pending = {"quote_tweet_id": "123"} if quote else {"mention_id": "123", "source": lane}
    if lane == "hot_post_reply":
        pending["hot_post_reply_id"] = "123"
    pending.update(shared=shared, _private="hidden")
    if explicit_id:
        pending["reply_post_id"] = "888"

    def add_event(kind, ts, **fields):
        row = {"kind": kind, **fields}
        events.append(row)
        authority.add(id(row))
        return row

    handler = legacy_posts.handle_legacy_quote_reply if quote else legacy_posts.handle_legacy_mention_reply
    handled, returned, returned_context = handler(
        r, "Quote-tweet reply posted successfully" if quote else "Reply posted successfully",
        **{("pending_qt" if quote else "pending_mention"): pending},
        record_index=0, production_record=True, active_xai_context=context,
        last_created_post=evidence, production_event_object_ids=authority,
        routine_skip_counts=Counter(), add_event=add_event, lit=digest.lit,
    )
    assert handled is True and returned == {} and returned is not pending and returned_context is context
    assert (id(events[0]) in authority) is expected_authority and 999 in authority
    assert events[0]["reply_post_id"] == pending["reply_post_id"] == ("888" if explicit_id else "999")
    assert events[0]["shared"] is pending["shared"] is shared
    assert not any(key.startswith("_") for key in events[0]) and "source" not in events[0]
    if lane == "hot_post_reply":
        assert "mention_id" not in events[0] and events[0]["hot_post_reply_id"] == "123"


def test_mention_control_extraction_keeps_event_counter_and_source_identity(monkeypatch):
    names = [
        "mention_backlog_started", "mention_backlog_progress",
        "mention_backlog_completed", "mention_backlog_reset",
        "author_evaluation_quarantine_started", "author_evaluation_quarantine_skip",
        "author_evaluation_quarantine_expired",
    ]
    records = [structured_record(index, {
        "event": name, "since_id": "opaque", "highest_mention_id": "opaque",
        "author_id": "opaque", "target_id": "opaque", "pipeline_evaluations_skipped": 2,
    }) for index, name in enumerate(names)]
    emitted, counters, timestamps, projected = [], [], [], []
    source_ref = {"fixture": "source"}
    monkeypatch.setattr(digest, "valid_string_public_post_id", lambda value: value == "opaque")
    monkeypatch.setattr(digest, "record_source_ref", lambda *args: source_ref)

    def wrap_handler(name):
        original = getattr(digest, name)

        def handler(payload, timestamp, *, add_event, stats, **helpers):
            counters.append(stats)
            timestamps.append(timestamp)

            def emit(*args, **kwargs):
                result = add_event(*args, **kwargs)
                emitted.append(result)
                return result

            return original(payload, timestamp, add_event=emit, stats=stats, **helpers)

        monkeypatch.setattr(digest, name, handler)

    wrap_handler("record_mention_backlog")
    wrap_handler("record_author_evaluation_quarantine")
    prepare = digest.prepare_mention_control_observations

    def projection(events, *, event_counter):
        created = []

        def counter(values):
            result = event_counter(values)
            created.append(result)
            return result

        result = prepare(events, event_counter=counter)
        assert result[0] is not events
        assert result[1] is created[0]
        assert all(any(item is candidate for candidate in events) for item in result[0])
        projected.append(result)
        return result

    monkeypatch.setattr(digest, "prepare_mention_control_observations", projection)
    report = digest.analyse(records, generation_time=records[-1].ts)
    observation = report["mention_backlog_and_quarantine"]
    assert observation["events"] is projected[0][0]
    assert all(item is emitted[index] for index, item in enumerate(observation["events"]))
    assert all(item is counters[0] for item in counters)
    assert all(stamp is record.ts for stamp, record in zip(timestamps, records))
    assert observation["event_counts"] == dict.fromkeys(names, 1)
    # Both add_event and the selected handler increment the original counter.
    assert [counters[0][name] for name in names] == [2] * 7
    assert observation["pipeline_evaluations_skipped"] == 2
    assert emitted[0]["since_id"] == "opaque"
    assert emitted[4]["author_id"] == "opaque"
    assert all(item["source_refs"][0] is source_ref for item in emitted)
    emitted[0]["later"] = True
    assert observation["events"][0]["later"] is True


def test_source_classification_uses_stable_authoritative_classes():
    assert formatter.classify_source({"title": "HC Deb", "url": "https://hansard.parliament.uk/x", "source_type": "transcript"}) == "Hansard"
    assert formatter.classify_source({"title": "Redirect", "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x"}) == "no public URL"
    assert formatter.classify_source({"title": "Speech", "url": "https://www.gov.uk/government/speeches/x"}) == "original speech transcript"


def test_historical_context_quality_aggregates_lengths_labels_and_omissions():
    events = [
        event("historical_context_reply", status="completed", character_count=300, raw_character_count=350,
              verification_label="Exact wording", source_class="Hansard", historical_confidence="high",
              shortening_applied=True, meaning_omitted=False, source_omitted=False, verification_omitted=False),
        event("historical_context_reply", status="failed", character_count=200, raw_character_count=220,
              verification_label="Historically verified variant", source_class="Margaret Thatcher Foundation",
              historical_confidence="medium", shortening_applied=False, meaning_omitted=True,
              source_omitted=False, verification_omitted=False),
        event("historical_context_reply", status="already_completed", character_count=300),
    ]
    result = digest.historical_context_quality_summary(events)
    assert result["attempted_count"] == 2
    assert result["average_weighted_characters"] == 300
    assert result["minimum_weighted_characters"] == 300
    assert result["shortened_count"] == 1
    assert result["meaning_omitted_count"] == 0
    assert result["verification_counts"]["unavailable"] == 0


def test_historical_context_v5_metadata_is_retained_and_summarised():
    dimensions = {
        "attribution": "high",
        "wording": "medium",
        "source_event": "low",
        "date": "unknown",
        "historical_context": "high",
        "interpretation": "medium",
    }
    records = [
        digest.Record(
            ts=datetime(2026, 7, 21, 12),
            level="INFO",
            src="log_event",
            line=1,
            msg="EVENT " + json.dumps({
                "event": "historical_context_reply",
                "status": "completed",
                "quote_id": "a" * 64,
                "character_count": 300,
                "raw_character_count": 320,
                "verification_label": "Exact wording verified",
                "source_class": "Margaret Thatcher Foundation",
                "historical_confidence": "high",
                "formatter_version": "historical_context_reply_schema_v5",
                "rendering_mode": "public",
                "confidence_dimensions": dimensions,
                "source_role_audit_version": (
                    "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
                ),
                "shortening_applied": False,
                "meaning_omitted": False,
                "source_omitted": False,
                "verification_omitted": False,
            }),
            path="mrsMThatcher.log",
            ordinal=1,
        ),
    ]

    report = digest.analyse(records)
    context_event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_reply"
    )
    quality = report["historical_context_quality"]

    assert context_event["formatter_version"] == "historical_context_reply_schema_v5"
    assert context_event["rendering_mode"] == "public"
    assert context_event["confidence_dimensions"] == dimensions
    assert context_event["source_role_audit_version"] == (
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
    )
    assert quality["verification_counts"]["Exact wording verified"] == 1
    assert quality["verification_counts"]["unavailable"] == 0
    assert quality["formatter_version_counts"]["historical_context_reply_schema_v5"] == 1
    assert quality["rendering_mode_counts"]["public"] == 1
    assert quality["source_role_audit_version_counts"][
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts"
    ] == 1
    for field, value in dimensions.items():
        assert quality["confidence_dimension_counts"][field][value] == 1
        assert quality["confidence_dimension_counts"][field]["unavailable"] == 0

    rendered = digest.render_markdown(report)
    assert "Exact wording verified=1" in rendered
    assert "historical_context_reply_schema_v5=1" in rendered
    assert "Rendering modes: public=1" in rendered
    assert (
        "Source-role audit versions: "
        "historical-context-source-roles-v5-independent-review-and-exclusive-counts=1"
    ) in rendered
    assert "Confidence attribution: high=1" in rendered


def test_historical_context_semantic_gate_metadata_is_retained_and_skip_is_summarised():
    quote_id = "b" * 64
    ledger_sha256 = "c" * 64
    projection_sha256 = "d" * 64
    records = [
        digest.Record(
            ts=datetime(2026, 7, 22, 12),
            level="INFO",
            src="log_event",
            line=1,
            msg="EVENT " + json.dumps({
                "event": "historical_context_semantic_gate",
                "status": "loaded",
                "policy_version": (
                    "historical-context-semantic-gate-v1-open-review-whole-reply"
                ),
                "ledger_sha256": ledger_sha256,
                "projection_sha256": projection_sha256,
                "blocked_quote_count": 23,
            }),
            path="mrsMThatcher.log",
            ordinal=1,
        ),
        digest.Record(
            ts=datetime(2026, 7, 22, 12, 1),
            level="INFO",
            src="log_event",
            line=2,
            msg="EVENT " + json.dumps({
                "event": "historical_context_reply",
                "status": "skipped_future_policy",
                "reason": "open_semantic_review",
                "quote_id": quote_id,
                "semantic_review_disposition": "future_correction_needed",
                "semantic_review_ledger_sha256": ledger_sha256,
                "semantic_review_projection_sha256": projection_sha256,
            }),
            path="mrsMThatcher.log",
            ordinal=2,
        ),
    ]

    report = digest.analyse(records)
    gate_event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_semantic_gate"
    )
    reply_event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_reply"
    )
    quality = report["historical_context_quality"]

    assert gate_event["status"] == "loaded"
    assert gate_event["policy_version"] == (
        "historical-context-semantic-gate-v1-open-review-whole-reply"
    )
    assert gate_event["ledger_sha256"] == ledger_sha256
    assert gate_event["projection_sha256"] == projection_sha256
    assert gate_event["blocked_quote_count"] == 23
    assert reply_event["semantic_review_disposition"] == "future_correction_needed"
    assert reply_event["semantic_review_ledger_sha256"] == ledger_sha256
    assert reply_event["semantic_review_projection_sha256"] == projection_sha256
    assert quality["status_counts"]["skipped"] == 1
    assert quality["skip_reason_counts"] == {"open_semantic_review": 1}
    assert quality["attempted_count"] == 0

    rendered = digest.render_markdown(report)
    assert "## Historical context semantic gate" in rendered
    assert "historical-context-semantic-gate-v1-open-review-whole-reply" in rendered
    assert ledger_sha256 in rendered
    assert projection_sha256 in rendered
    assert "future_correction_needed" in rendered


def test_completed_historical_context_semantic_metadata_is_rendered():
    ledger_sha256 = "c" * 64
    projection_sha256 = "d" * 64
    record = digest.Record(
        ts=datetime(2026, 7, 22, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg="EVENT " + json.dumps({
            "event": "historical_context_reply",
            "status": "completed",
            "quote_id": "b" * 64,
            "semantic_review_disposition": "supported_as_published",
            "semantic_review_ledger_sha256": ledger_sha256,
            "semantic_review_projection_sha256": projection_sha256,
        }),
        path="mrsMThatcher.log",
        ordinal=1,
    )

    report = digest.analyse([record])
    rendered = digest.render_markdown(report)

    assert "supported_as_published" in rendered
    assert ledger_sha256 in rendered
    assert projection_sha256 in rendered


def test_old_historical_context_event_uses_explicit_missing_semantic_metadata():
    record = digest.Record(
        ts=datetime(2026, 7, 20, 12),
        level="INFO",
        src="log_event",
        line=1,
        msg=(
            'EVENT {"event":"historical_context_reply","status":"completed",'
            '"quote_id":"' + "b" * 64 + '"}'
        ),
        path="old.log",
        ordinal=1,
    )

    report = digest.analyse([record])
    event = next(
        item for item in report["events"]
        if item["kind"] == "historical_context_reply"
    )

    assert event["semantic_review_disposition"] == "unavailable"
    assert event["semantic_review_ledger_sha256"] == "unavailable"
    assert event["semantic_review_projection_sha256"] == "unavailable"


def test_historical_context_v5_public_labels_are_not_downgraded_to_unavailable():
    labels = (
        "Exact wording verified",
        "Historically verified variant",
        "Verified excerpt",
        "Attributed, but exact wording not independently verified",
        "Exact wording not independently verified",
        "Research incomplete",
    )
    result = digest.historical_context_quality_summary([
        event(
            "historical_context_reply",
            status="completed",
            character_count=100,
            verification_label=label,
            formatter_version="historical_context_reply_schema_v5",
        )
        for label in labels
    ])

    for label in labels:
        assert result["verification_counts"][label] == 1
    assert result["verification_counts"]["unavailable"] == 0
    assert result["formatter_version_counts"]["historical_context_reply_schema_v5"] == len(labels)


def test_controlled_source_role_versions_include_deployed_v8_and_current_v9():
    versions = (
        "historical-context-source-roles-v7-curated-source-adjudications",
        "historical-context-source-roles-v8-claim-specific-public-context",
        "historical-context-source-roles-v9-archive-provenance",
    )
    result = digest.historical_context_quality_summary([
        event(
            "historical_context_reply",
            status="completed",
            character_count=100,
            verification_label="Attributed, but exact wording not independently verified",
            source_role_audit_version=version,
        )
        for version in versions
    ])

    assert all(
        result["source_role_audit_version_counts"][version] == 1
        for version in versions
    )
    assert result["source_role_audit_version_counts"]["unavailable"] == 0


def test_legacy_v2_v3_and_v4_historical_context_metadata_remains_compatible():
    result = digest.historical_context_quality_summary(
        [
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label="Exact wording",
                formatter_version="historical_context_reply_schema_v2",
            ),
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label=(
                    "Exact wording not independently verified by the retained evidence"
                ),
                formatter_version="historical_context_reply_schema_v3",
            ),
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label="Exact wording verified",
                formatter_version="historical_context_reply_schema_v4",
            ),
            event(
                "historical_context_reply",
                status="completed",
                character_count=100,
                verification_label=(
                    "Reported in Jim Prior, 'A Balance of Power' (1986), p. 106; "
                    "no primary Thatcher transcript located"
                ),
                formatter_version="historical_context_reply_schema_v3",
            ),
        ]
    )

    assert result["verification_counts"]["Exact wording"] == 1
    assert result["verification_counts"][
        "Exact wording not independently verified by the retained evidence"
    ] == 1
    assert result["verification_counts"][
        "Secondary recollection; no primary Thatcher transcript located"
    ] == 1
    assert result["formatter_version_counts"]["historical_context_reply_schema_v2"] == 1
    assert result["formatter_version_counts"]["historical_context_reply_schema_v3"] == 2
    assert result["formatter_version_counts"]["historical_context_reply_schema_v4"] == 1


def test_malformed_confidence_dimensions_are_reported_as_unavailable():
    result = digest.historical_context_quality_summary([
        event(
            "historical_context_reply",
            status="completed",
            character_count=100,
            confidence_dimensions={"attribution": "certain"},
        ),
    ])

    for counts in result["confidence_dimension_counts"].values():
        assert counts["unavailable"] == 1


def test_missing_context_boolean_metadata_is_not_reported_as_zero_quality():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="completed", character_count=300),
    ])
    assert result["shortening_metadata_unavailable_count"] == 1
    assert result["omission_metadata_unavailable_count"] == 1


def test_malformed_boolean_length_is_not_counted_as_one_character():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="completed", character_count=True, raw_character_count=False),
    ])
    assert result["average_weighted_characters"] is None
    assert result["average_raw_characters"] is None


def test_context_skip_status_is_aggregated_once_with_separate_reason():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="skipped_no_completed_packet", reason="no_completed_canonical_packet"),
    ])
    assert result["status_counts"]["skipped"] == 1
    assert "skipped_no_completed_packet" not in result["status_counts"]
    assert result["skip_reason_counts"] == {"no_completed_canonical_packet": 1}


def test_all_skip_statuses_are_excluded_from_attempted_count():
    result = digest.historical_context_quality_summary([
        event("historical_context_reply", status="skipped_future_policy", reason="future_policy"),
        event("historical_context_reply", status="completed", character_count=100),
        event("historical_context_reply", status="failed"),
        event("historical_context_reply", status="dry_run", character_count=100),
        event("historical_context_reply", status="unknown_future_status"),
    ])
    assert result["attempted_count"] == 3


def test_unknown_optional_enums_are_counted_as_unavailable():
    context = digest.historical_context_quality_summary([
        event("historical_context_reply", status="completed", character_count=100,
              verification_label={"malformed": True}, source_class="future-unknown",
              historical_confidence="certain"),
    ])
    assert context["verification_counts"]["unavailable"] == 1
    assert "{'malformed': True}" not in context["verification_counts"]
    assert context["source_class_counts"]["unavailable"] == 1
    assert context["confidence_counts"]["unavailable"] == 1


def test_single_call_digest_reports_version_three_architecture():
    common = {
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
        "temperature": 1,
        "prompt_sha256": "7" * 64,
        "response_schema_sha256": "3" * 64,
        "payload_sha256": "a" * 64,
        "used_fact_count": 1,
        "visible_turn_count": 4,
        "visible_character_count": 640,
        "same_author_interaction_count": 2,
        "recent_conversational_reply_count": 6,
        "trusted_fact_count": 3,
        "supplied_image_count": 1,
        "model_call_count": 1,
        "provider_request_attempt_count": 1,
        "local_validation_status": "passed",
        "outcome_type": "editorial",
    }
    records = [
        structured_record(0, {
            "event": "single_call_reply_decision",
            "lane": "mention",
            "target_id": "101",
            "decision": "reply",
            "reply_kind": "principle",
            "reason_code": "useful_reply",
            "pipeline_status": "reply",
            **common,
        }),
        structured_record(1, {
            "event": "single_call_reply_provider_usage",
            "lane": "mention",
            "target_id": "101",
            "strategy_version": "single-sol-reply-20260904",
            "model": "gpt-5.6-sol",
            "provider_response_id": "resp_1",
            "provider_latency_ms": 1200,
            "request_attempt_count": 1,
            "input_tokens": 100,
            "cached_input_tokens": 64,
            "cache_write_input_tokens": 0,
            "output_tokens": 20,
            "reasoning_tokens": 8,
            "total_tokens": 120,
        }),
        structured_record(2, {
            "event": "single_call_reply_posting_outcome",
            "status": "confirmed",
            "lane": "mention",
            "target_id": "101",
            "reply_post_id": "901",
            "strategy_version": "single-sol-reply-20260904",
            "reply_kind": "principle",
            "reason_code": "useful_reply",
            "used_fact_count": 1,
            "supplied_image_count": 1,
        }),
        structured_record(3, {
            "event": "single_call_reply_decision",
            "lane": "quote-tweet",
            "target_id": "102",
            "decision": "no_reply",
            "reply_kind": "no_reply",
            "reason_code": "completed_exchange",
            "pipeline_status": "no_reply",
            **{**common, "used_fact_count": 0, "supplied_image_count": 0},
        }),
        structured_record(4, {
            "event": "single_call_reply_decision",
            "lane": "hot-post",
            "target_id": "103",
            **common,
            "decision": None,
            "reply_kind": None,
            "reason_code": None,
            "pipeline_status": "operational_failure",
            "outcome_type": "operational",
            "local_validation_status": "failed",
            "error_category": "schema_validation",
            "failure_reason": "invalid_model_response",
        }),
        structured_record(5, {
            "event": "single_call_reply_draft_recovered",
            "lane": "mention",
            "target_id": "104",
            "strategy_version": "single-sol-reply-20260904",
            "model": "gpt-5.6-sol",
            "validated_draft_hash": "b" * 64,
            "model_call_count": 0,
        }),
    ]

    report = digest.analyse(records)
    summary = report["single_call_reply"]
    assert digest.DIGEST_JSON_SCHEMA_VERSION == 3
    assert summary["candidate_evaluation_count"] == 3
    assert summary["reply_decision_count"] == 1
    assert summary["replies_posted_count"] == 1
    assert summary["editorial_no_reply_count"] == 1
    assert summary["operational_failure_count"] == 1
    assert summary["recovered_draft_count"] == 1
    assert summary["one_call_compliance"] == "passed"
    assert summary["one_call_compliant_count"] == 3
    assert summary["one_call_violation_count"] == 0
    assert summary["reply_kind_counts"] == {"principle": 1}
    assert summary["no_reply_reason_counts"] == {"completed_exchange": 1}
    assert summary["operational_failure_reason_counts"] == {
        "invalid_model_response": 1,
    }
    assert summary["schema_validation_failure_count"] == 1
    assert summary["average_visible_turn_count"] == 4
    assert summary["average_visible_character_count"] == 640
    assert summary["average_recent_conversational_reply_count"] == 6
    assert summary["average_supplied_image_count"] == pytest.approx(2 / 3)
    assert summary["token_totals"]["input_tokens"] == 100
    assert summary["token_totals"]["cached_input_tokens"] == 64
    assert summary["provider_latency_average_ms"] == 1200
    assert "reply_strategy" not in report
    assert "reply_pipeline_stages" not in report
    assert "reply_visual_context_summary" not in report

    rendered = digest.render_markdown(report)
    assert "## Single-call conversational replies" in rendered
    assert "3 candidates evaluated; 1 reply posted" in rendered
    assert "One-call compliance: **passed**" in rendered
    assert "recent conversational replies" in rendered
    assert "## Conversational reply strategy" not in rendered
    assert "## Tested reply-pipeline stages" not in rendered
    assert "## Reply image context" not in rendered


def test_single_call_digest_flags_duplicate_provider_usage_as_call_violation():
    decision = structured_record(0, {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "201",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "decision": "reply",
        "reply_kind": "social",
        "reason_code": "useful_reply",
        "used_fact_count": 0,
        "model_call_count": 1,
        "provider_request_attempt_count": 1,
        "local_validation_status": "passed",
        "outcome_type": "editorial",
        "pipeline_status": "reply",
    })
    usage = {
        "event": "single_call_reply_provider_usage",
        "lane": "mention",
        "target_id": "201",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "request_attempt_count": 1,
    }
    report = digest.analyse([
        decision,
        structured_record(1, usage),
        structured_record(2, usage),
    ])
    summary = report["single_call_reply"]
    assert summary["one_call_compliance"] == "failed"
    assert summary["one_call_violation_count"] == 1
    assert summary["one_call_compliant_count"] == 0


def test_single_call_digest_allows_authorised_pre_execution_retry():
    decision = structured_record(0, {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "202",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "decision": "reply",
        "reply_kind": "social",
        "reason_code": "useful_reply",
        "used_fact_count": 0,
        "model_call_count": 1,
        "provider_request_attempt_count": 2,
        "local_validation_status": "passed",
        "outcome_type": "editorial",
        "pipeline_status": "reply",
    })
    usage = structured_record(1, {
        "event": "single_call_reply_provider_usage",
        "lane": "mention",
        "target_id": "202",
        "strategy_version": "single-sol-reply-20260904",
        "model": "gpt-5.6-sol",
        "request_attempt_count": 2,
    })

    summary = digest.analyse([decision, usage])["single_call_reply"]

    assert summary["one_call_compliance"] == "passed"
    assert summary["one_call_compliant_count"] == 1
    assert summary["one_call_violation_count"] == 0
    assert summary["authorised_pre_execution_retry_count"] == 1


def test_single_call_digest_preserves_and_rejects_more_than_two_attempts():
    decision = structured_record(0, {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "203",
        "model_call_count": 1,
        "provider_request_attempt_count": 3,
        "provider_status_code": 429,
        "provider_reset_epoch": 1_788_534_120,
        "provider_retry_after_seconds": 120,
        "pipeline_status": "operational_failure",
        "outcome_type": "operational",
    })
    usage = structured_record(1, {
        "event": "single_call_reply_provider_usage",
        "lane": "mention",
        "target_id": "203",
        "request_attempt_count": 3,
    })

    report = digest.analyse([decision, usage])
    summary = report["single_call_reply"]
    parsed_decision = next(
        item for item in report["events"]
        if item["kind"] == "single_call_reply_decision"
    )

    assert parsed_decision["provider_request_attempt_count"] == 3
    assert parsed_decision["provider_request_attempt_count_status"] == "available"
    assert parsed_decision["provider_status_code"] == 429
    assert parsed_decision["provider_reset_epoch"] == 1_788_534_120
    assert parsed_decision["provider_retry_after_seconds"] == 120
    assert summary["provider_request_attempt_counts"] == {"3": 1}
    assert summary["one_call_compliance"] == "failed"
    assert summary["one_call_compliant_count"] == 0
    assert summary["one_call_violation_count"] == 1


@pytest.mark.parametrize(
    ("attempt_value", "expected_status"),
    [
        (None, "missing"),
        ("1", "malformed"),
    ],
)
def test_single_call_digest_does_not_pass_incomplete_attempt_telemetry(
    attempt_value,
    expected_status,
):
    payload = {
        "event": "single_call_reply_decision",
        "lane": "mention",
        "target_id": "204",
        "model_call_count": 1,
        "pipeline_status": "reply",
        "outcome_type": "editorial",
    }
    if attempt_value is not None:
        payload["provider_request_attempt_count"] = attempt_value

    report = digest.analyse([structured_record(0, payload)])
    summary = report["single_call_reply"]
    parsed = next(
        item for item in report["events"]
        if item["kind"] == "single_call_reply_decision"
    )

    assert parsed["provider_request_attempt_count"] is None
    assert parsed["provider_request_attempt_count_status"] == expected_status
    assert summary["one_call_compliance"] == "incomplete"
    assert summary["one_call_compliant_count"] == 0
    assert summary["one_call_violation_count"] == 0
    assert summary["one_call_incomplete_count"] == 1
    assert summary["provider_request_attempt_metadata_status_counts"] == {
        expected_status: 1,
    }


def test_single_call_digest_reports_retryable_later_attempts_without_failing():
    decisions = [
        structured_record(index, {
            "event": "single_call_reply_decision",
            "lane": "mention",
            "target_id": "205",
            "model_call_count": 1,
            "provider_request_attempt_count": 1,
            "pipeline_status": "operational_failure",
            "outcome_type": "operational",
        })
        for index in range(2)
    ]
    usage = [
        structured_record(index + 2, {
            "event": "single_call_reply_provider_usage",
            "lane": "mention",
            "target_id": "205",
            "request_attempt_count": 1,
        })
        for index in range(2)
    ]

    summary = digest.analyse([*decisions, *usage])["single_call_reply"]

    assert summary["one_call_compliance"] == "passed"
    assert summary["one_call_compliant_count"] == 2
    assert summary["one_call_violation_count"] == 0
    assert summary["repeated_model_attempt_candidate_count"] == 1
    assert summary["excess_provider_usage_candidate_count"] == 0


@pytest.mark.parametrize(
    ("value", "projected", "status"),
    [
        (None, None, "malformed"),
        (True, None, "malformed"),
        (-1, None, "malformed"),
        (1_000_000, 1_000_000, "available"),
        (1_000_001, None, "out_of_range"),
    ],
)
def test_single_call_attempt_projection_keeps_invalid_and_range_observations(
    value, projected, status,
):
    records = [
        structured_record(0, {
            "event": "single_call_reply_decision",
            "target_id": "206",
            "model_call_count": 1,
            "provider_request_attempt_count": value,
        }),
        structured_record(1, {
            "event": "single_call_reply_provider_usage",
            "target_id": "206",
            "request_attempt_count": value,
        }),
    ]

    report = digest.analyse(records)

    for item, field in zip(report["events"], (
        "provider_request_attempt_count", "request_attempt_count",
    )):
        assert item[field] == projected
        assert type(item[field]) is type(projected)
        assert item[f"{field}_status"] == status
    summary = report["single_call_reply"]
    assert summary["provider_request_attempt_metadata_status_counts"] == {status: 2}
    assert summary["one_call_compliance"] == (
        "incomplete" if status == "malformed" else "failed"
    )
    assert summary["one_call_compliant_count"] == 0


@pytest.mark.parametrize(
    ("fields", "temperature", "recent_count"),
    [
        ({"temperature": 1}, 1, 30),
        ({"temperature": 1.25, "recent_conversational_reply_count": None}, 1.25, None),
        ({"temperature": True, "recent_conversational_reply_count": False}, None, None),
    ],
)
def test_single_call_projection_keeps_numeric_types_and_explicit_alias_precedence(
    fields, temperature, recent_count,
):
    report = digest.analyse([structured_record(0, {
        "event": "single_call_reply_decision",
        "recent_reply_count": 30,
        "used_fact_count": True,
        "visible_turn_count": 13,
        "provider_status_code": True,
        "target_id": 207,
        "private_extra": "not a report field",
        **fields,
    })])

    item = report["events"][0]
    assert item["temperature"] == temperature
    assert type(item["temperature"]) is type(temperature)
    assert item["recent_conversational_reply_count"] == recent_count
    assert type(item["recent_conversational_reply_count"]) is type(recent_count)
    assert item["used_fact_count"] is None
    assert item["visible_turn_count"] is None
    assert item["provider_status_code"] is None
    assert item["target_id"] == ""
    assert "private_extra" not in item


def test_single_call_summary_consumes_original_emitted_and_truncated_events(monkeypatch):
    captured = []
    original_summary = digest.single_call_reply_summary

    def capture(events):
        captured.extend(events)
        before = copy.deepcopy(events)
        result = original_summary(events)
        assert events == before
        return result

    monkeypatch.setattr(digest, "single_call_reply_summary", capture)
    common = {"lane": " HOT_POST_REPLY ", "target_id": "208"}
    payloads = [
        {"event": "historical_context_runtime", "status": "disabled"},
        {
            "event": "single_call_reply_decision", **common,
            "strategy_version": "s" * 30, "pipeline_status": "reply",
            "model_call_count": 1, "provider_request_attempt_count": 1,
        },
        {
            "event": "single_call_reply_provider_usage", **common,
            "request_attempt_count": 1, "input_tokens": 100,
        },
        {
            "event": "single_call_reply_posting_outcome", **common,
            "status": "confirmed", "reply_post_id": "908",
        },
        {
            "event": "single_call_reply_posting_outcome", **common,
            "lane": "hot_post", "status": "confirmed", "reply_post_id": "909",
        },
        {"event": "single_call_reply_draft_recovered", **common, "model_call_count": 0},
        {"event": "ai_reply_pipeline_decision", "status": "no_reply"},
    ]
    records = [structured_record(index, payload) for index, payload in enumerate(payloads)]
    records.append(replace(
        structured_record(7, payloads[5]), path="mrsMThatcher.selftest.log",
    ))

    report = digest.analyse(records, max_text=9)

    assert len(captured) == len(report["events"]) == 8
    assert all(left is right for left, right in zip(captured, report["events"]))
    assert [item["kind"] for item in captured] == [
        "historical_context_runtime", "single_call_reply_decision",
        "single_call_reply_provider_usage", "single_call_reply_posting_outcome",
        "single_call_reply_posting_outcome", "single_call_reply_draft_recovered",
        "reply_strategy_decision", "single_call_reply_draft_recovered",
    ]
    assert captured[1]["strategy_version"] == "ssssssss…"
    assert captured[1]["lane"] == "hot-post"
    assert captured[1]["time"] == "2026-09-04 12:00:01"
    assert "source_refs" not in captured[1]
    summary = report["single_call_reply"]
    assert summary["strategy_version_counts"] == {"ssssssss…": 1}
    assert summary["replies_posted_count"] == 1
    assert summary["recovered_draft_count"] == 2
    assert summary["one_call_compliance"] == "passed"
    assert report["summary"]["stats"]["single_call_reply_posting_outcome"] == 2
    assert report["summary"]["stats"]["single_call_reply_draft_recovered"] == 2
    assert report["summary"]["stats"].get("hot_post_reply_posted", 0) == 0


def test_single_call_module_import_and_reporting_have_no_runtime_dependencies(tmp_path):
    import mrs_log_digest_single_call as single_call
    import mrs_log_digest_values as values

    assert digest.single_call_reply_summary is single_call.single_call_reply_summary
    assert digest.normalise_reply_lane is values.normalise_reply_lane
    assert (
        digest.bounded_event_nonnegative_integer_observation
        is values.bounded_event_nonnegative_integer_observation
    )
    script = """
import builtins
from datetime import datetime
import logging
import os
from pathlib import Path
import sys

handlers = list(logging.getLogger().handlers)
loggers = set(logging.Logger.manager.loggerDict)
original_import = builtins.__import__
forbidden = {"mrs_log_digest", "mrs_log_digest_markdown", "mrsMThatcher2",
             "mrs_log_digest_runtime", "mrs_log_digest_corpus",
             "mrs_log_digest_generated_pool", "single_call_reply"}
def reject(*args, **kwargs):
    raise AssertionError((args, kwargs))
def import_guard(name, *args, **kwargs):
    assert name not in forbidden, name
    return original_import(name, *args, **kwargs)
def audit(event, args):
    if event == "open":
        path, mode, flags = args
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), args
        assert isinstance(path, str) and path.endswith((".py", ".pyc", ".so")), args
    assert not event.startswith(("socket.", "subprocess.")), event
    assert event not in {"os.mkdir", "os.remove", "os.rename", "os.system", "os.listdir", "os.scandir"}, event
Path.home = classmethod(reject)
logging.basicConfig = reject
builtins.__import__ = import_guard
sys.addaudithook(audit)
import mrs_log_digest_single_call as single_call

events = []
def add_event(kind, ts, **fields):
    item = {"kind": kind, **fields}
    events.append(item)
    return item
single_call.record_single_call_reply_draft_recovered(
    {"lane": "hot_post_reply", "target_id": "209", "model_call_count": 0},
    datetime(2026, 9, 4, 12), add_event=add_event,
)
assert single_call.single_call_reply_summary(events)["recovered_draft_count"] == 1
assert single_call.single_call_reply_summary([])["recovered_draft_count"] == 0
assert not forbidden & sys.modules.keys()
assert list(logging.getLogger().handlers) == handlers
assert set(logging.Logger.manager.loggerDict) == loggers
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(Path(digest.__file__).resolve().parent)),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b""
    assert list(tmp_path.iterdir()) == []


def test_legacy_pipeline_callbacks_keep_coalesced_event_identity(monkeypatch):
    emitted = []
    merged = []

    def capture(handler):
        def record(payload, ts, **callbacks):
            original_add = callbacks.get("add_event")
            original_merge = callbacks.get("add_or_merge_local_rejection")

            def add_event(*args, **fields):
                result = original_add(*args, **fields)
                emitted.append(result)
                return result

            def add_or_merge_local_rejection(*args, **fields):
                result = original_merge(*args, **fields)
                merged.append(result)
                return result

            if original_add is not None:
                callbacks["add_event"] = add_event
            if original_merge is not None:
                callbacks["add_or_merge_local_rejection"] = add_or_merge_local_rejection
            return handler(payload, ts, **callbacks)

        return record

    for name in (
        "record_ai_reply_pipeline_decision",
        "record_ai_reply_pipeline_stage_summary",
        "record_ai_reply_pipeline_effective_outcome",
    ):
        monkeypatch.setattr(digest, name, capture(getattr(digest, name)))

    common = {"target_id": "210", "strategy_version": "legacy-test"}
    payloads = [
        {
            "event": "ai_reply_pipeline_decision", **common,
            "lane": "unavailable", "status": "reply",
            "effective_status": "local_rejection",
            "original_local_rejection_reason": "exact_duplicate",
        },
        {
            "event": "ai_reply_pipeline_stage_summary", **common,
            "lane": "mention", "status": "reply",
        },
        {
            "event": "ai_reply_pipeline_effective_outcome", **common,
            "lane": "mention", "effective_status": "local_rejection",
            "effective_reason": "duplicate reply rejected",
            "direct_answer_repair_attempted": False,
        },
    ]
    records = [structured_record(index, payload) for index, payload in enumerate(payloads)]

    report = digest.analyse(records)

    decision, rejection, stage = report["events"]
    assert emitted[0] is decision
    assert emitted[1] is stage
    assert merged[0] is merged[1] is rejection
    assert rejection["lane"] == "mention"
    assert rejection["time"] == decision["time"] == "2026-09-04 12:00:00"
    assert stage["time"] == "2026-09-04 12:00:01"
    assert rejection["reason"] == "exact_duplicate"
    assert rejection["effective_reason"] == "duplicate reply rejected"
    assert rejection["direct_answer_repair_attempted"] is False
    assert report["summary"]["stats"]["reply_strategy_local_rejection"] == 1
    assert stage["effective_status"] is None

    original_normalise = digest._normalise_lane
    normalised = []

    def normalise_lane(value):
        normalised.append(value)
        return original_normalise("mention" if value == "unavailable" else value)

    monkeypatch.setattr(digest, "_normalise_lane", normalise_lane)
    assert digest.reconcile_reply_pipeline_effective_outcomes(report["events"]) is None
    assert normalised
    assert report["events"][0] is decision
    assert report["events"][1] is rejection
    assert report["events"][2] is stage
    for item in (decision, stage):
        assert item["effective_status"] == "local_rejection"
        assert item["effective_reason"] == "duplicate reply rejected"
        assert item["direct_answer_repair_attempted"] is False
    assert stage["pipeline_stage_status"] == "reply"


def test_old_multi_stage_logs_are_only_counted_as_legacy():
    report = digest.analyse([
        structured_record(0, {
            "event": "reply_strategy_decision",
            "lane": "mention",
            "target_id": "301",
            "mode": "social",
        }),
        structured_record(1, {
            "event": "ai_reply_pipeline_stage_summary",
            "lane": "mention",
            "target_id": "301",
            "status": "reply",
        }),
    ])
    assert report["legacy_multi_stage"] == {
        "decision_count": 1,
        "stage_summary_event_count": 1,
    }
    rendered = digest.render_markdown(report)
    assert "Legacy multi-stage events in this window" in rendered
    assert "## Conversational reply strategy" not in rendered
    assert "## Tested reply-pipeline stages" not in rendered



def test_cli_refuses_colliding_output_paths(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO main:1 - Bot started\n", encoding="utf-8")
    output = tmp_path / "same-output"
    result = subprocess.run(
        [sys.executable, "mrs_log_digest.py", str(log), "--no-state", "--json", "--output", str(output),
         "--markdown-output", str(output)],
        text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "output paths must be distinct" in result.stderr


def test_secondary_output_is_locked_when_state_is_disabled(tmp_path, monkeypatch):
    log = tmp_path / "bot.log"
    log.write_text("2026-07-15 12:00:00 INFO main:1 - Bot started\n", encoding="utf-8")
    output = tmp_path / "digest.md"
    locks = []

    @contextmanager
    def fake_lock(path):
        locks.append(path)
        yield

    monkeypatch.setattr(digest, "digest_execution_lock", fake_lock)
    monkeypatch.setattr(digest, "run_digest", lambda *_args, **_kwargs: 0)
    assert digest.main([str(log), "--no-state", "--markdown-output", str(output)]) == 0
    assert locks == [output.with_suffix(".md.lock")]


def test_digest_distinguishes_confirmed_main_context_states_and_meme_stage():
    structured = [
        {
            "event": "posting_transaction_state",
            "parent_post_id": "900001",
            "main_post_state": "main_post_confirmed",
            "context_reply_state": "context_reply_pending",
        },
        {
            "event": "historical_context_obligation",
            "status": "failed",
            "parent_post_id": "900001",
            "context_reply_state": "context_reply_failed_retryable",
            "attempt_number": 1,
        },
        {
            "event": "historical_context_obligation",
            "status": "failed_terminal",
            "parent_post_id": "900002",
            "context_reply_state": "context_reply_failed_terminal",
            "attempt_number": 5,
        },
        {
            "event": "daily_meme_failure",
            "status": "failed",
            "stage": "media_upload",
            "error_type": "OSError",
            "reason": "fixture failure",
        },
    ]
    records = [
        digest.Record(
            ts=datetime(2026, 7, 24, 1, index),
            level="INFO",
            src="log_event",
            line=index,
            msg="EVENT " + json.dumps(payload),
            path="mrsMThatcher.log",
            ordinal=index,
        )
        for index, payload in enumerate(structured, start=1)
    ]

    report = digest.analyse(records)
    consistency = report["production_consistency"]
    assert consistency["context_transaction_state_counts"] == {
        "context_reply_pending": 1
    }
    assert consistency["context_obligation_state_counts"] == {
        "context_reply_failed_retryable": 1,
        "context_reply_failed_terminal": 1,
    }
    assert consistency["daily_meme_failure_stage_counts"] == {"media_upload": 1}
    rendered = digest.render_markdown(report)
    assert "Confirmed-main/context transaction states" in rendered
    assert "context_reply_pending" in rendered
    assert "context_reply_failed_retryable" in rendered
    assert "context_reply_failed_terminal" in rendered
    assert "Daily meme failures by stage" in rendered
    assert "media_upload" in rendered


def test_input_retention_coverage_warns_when_requested_start_predates_logs():
    result = digest.input_retention_coverage(
        [
            {
                "first_timestamp": "2026-07-21 00:51:23",
                "last_timestamp": "2026-07-25 01:00:00",
            }
        ],
        datetime(2026, 7, 18, 0, 0),
    )

    assert result["requested_start_covered"] is False
    assert result["retention_gap_seconds"] == 262283
    assert "coverage of the preceding interval cannot be verified" in result["warning"]
    rendered = digest.render_markdown(
        {
            **digest.analyse([]),
            "requested_since": "2026-07-18 00:00:00",
            "since_source": "manual --since",
            "since_exclusive": False,
            "input_retention_coverage": result,
            "input_warning": result["warning"],
        }
    )
    assert "Retained-log coverage of requested start: **no**" in rendered


def test_input_retention_coverage_accepts_a_covered_boundary():
    result = digest.input_retention_coverage(
        [{"first_timestamp": "2026-07-17 23:59:59"}],
        datetime(2026, 7, 18, 0, 0),
    )

    assert result["requested_start_covered"] is True
    assert result["warning"] == ""


def test_new_runtime_pause_evidence_and_repair_events_are_structured():
    payloads = [
        {
            "event": "historical_context_runtime",
            "status": "unavailable",
            "reason": "source-role audit policy is incompatible",
            "regular_post_eligibility_unchanged": True,
        },
        {
            "event": "reply_evidence_unavailable",
            "lane": "mention",
            "target_id": "101",
        },
        {
            "event": "runtime_control_pause",
            "key": "pause_all",
            "lanes": ["disable_quote_posts", "disable_meme_posts"],
            "until_epoch": 123456,
        },
        {
            "event": "clarification_reply_cap_override",
            "target_id": "102",
            "thread_id": "202",
            "author_id": "302",
            "bypassed_cap": "per_author_daily",
        },
        {
            "event": "clarification_reply_used",
            "target_id": "102",
            "thread_id": "202",
            "author_id": "302",
            "reply_post_id": "402",
            "trigger": "corrected_question",
        },
        {
            "event": "repair_reply_completed",
            "target_id": "102",
            "thread_id": "202",
            "author_id": "302",
            "reply_post_id": "402",
        },
    ]
    records = [
        digest.Record(
            ts=datetime(2026, 7, 25, 1, index),
            level="INFO",
            src="log_event",
            line=index,
            msg="EVENT " + json.dumps(payload),
            path="mrsMThatcher.log",
            ordinal=index,
        )
        for index, payload in enumerate(payloads, start=1)
    ]

    report = digest.analyse(records)
    kinds = {item["kind"] for item in report["events"]}
    assert {
        "historical_context_runtime",
        "reply_evidence_unavailable",
        "runtime_control_pause",
        "clarification_reply_cap_override",
        "clarification_reply_used",
        "repair_reply_completed",
    } <= kinds
    consistency = report["production_consistency"]
    assert consistency["historical_context_runtime_status_counts"] == {
        "unavailable": 1
    }
    assert consistency["reply_evidence_unavailable_lane_counts"] == {"mention": 1}
    rendered = digest.render_markdown(report)
    assert "Historical-context runtime availability" in rendered
    assert "Reply evidence unavailable" in rendered
    assert "Runtime control pauses" in rendered
    assert "Clarification reply cap overrides" in rendered
    assert "Repair replies completed" in rendered


def write_corpus(tmp_path):
    paths = {
        "packets": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "research_packets.json"
        ),
        "unresolved": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "final_unresolved"
            / "unresolved_cases.json"
        ),
        "eligible": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_attribution_cleanup_001"
            / "deployment_candidate"
            / "runtime_eligible_quote_manifest.json"
        ),
        "roles": (
            tmp_path
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "historical_context_source_role_audit.json"
        ),
        "gate": tmp_path / "historical_context_reply_semantic_gate_audit.json",
        "ledger": tmp_path / "historical_context_published_reply_semantic_review.json",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    paths["packets"].write_text(json.dumps({"items": [{"id": 1}, {"id": 2}]}))
    paths["unresolved"].write_text(json.dumps({"case_count": 1, "cases": [{}]}))
    paths["eligible"].write_text(
        json.dumps({"runtime_eligible_quote_count": 2, "runtime_eligible_quote_ids": ["a", "b"]})
    )
    paths["roles"].write_text(
        json.dumps({"policy_version": "roles-v9", "attribution_eligible_quote_count": 2})
    )
    paths["gate"].write_text(
        json.dumps(
            {
                "policy_version": "gate-v1",
                "coverage": {
                    "attribution_eligible_count": 2,
                    "completed_attribution_ineligible_count": 0,
                },
                "decision_counts": {
                    "eligible_allow": 1,
                    "blocked_open_semantic_review": 1,
                },
                "gate": {
                    "blocked_quote_count": 1,
                    "semantic_review_ledger_sha256": "a" * 64,
                    "blocked_projection_sha256": "b" * 64,
                },
            }
        )
    )
    paths["ledger"].write_text(json.dumps({"records": []}))
    return paths


def test_current_corpus_snapshot_reports_counts_policies_and_hashes(tmp_path):
    write_corpus(tmp_path)

    snapshot = digest.historical_context_corpus_snapshot(tmp_path)

    assert snapshot["available"] is True
    assert snapshot["completed_packet_count"] == 2
    assert snapshot["ordinary_post_cycle_count"] == 2
    assert snapshot["unresolved_quote_count"] == 1
    assert snapshot["historical_context_blocked_count"] == 1
    assert snapshot["historical_context_allowed_count"] == 1
    assert snapshot["source_role_policy_version"] == "roles-v9"
    assert set(snapshot["file_sha256"]) == {
        "research_packets",
        "unresolved_cases",
        "runtime_eligible_manifest",
        "source_role_audit",
        "semantic_gate_audit",
        "semantic_review_ledger",
    }

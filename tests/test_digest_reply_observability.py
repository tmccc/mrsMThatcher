from collections import Counter
from dataclasses import replace
from datetime import timedelta
import copy
import json

import pytest

import mrs_log_digest as digest
import mrs_log_digest_legacy_posts as legacy_posts

from tests.helpers.digest_records import event, structured_record


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


@pytest.mark.parametrize("path", ["mrsMThatcher.log", "selftest_fixture.log"])
def test_inferred_strategy_outcomes_keep_current_callbacks_order_and_root_identity(monkeypatch, path):
    captured, calls = {}, []
    source = replace(structured_record(0, {"event": "unused"}), path=path)
    latest = event("reply_strategy_decision", lane="alias", target_id="310", mode="latest", humour_tone="dry", tone="", evidence_confidence="high", retrieved_count=0, factual_claim=False, grounded=None, no_reply_reason="")
    observations = [
        event("reply_strategy_outcome", lane="mention", target_id="311"),
        {**latest, "mode": "old"}, latest,
        event("reply_strategy_decision", lane=None, target_id="312"),
        event("reply_strategy_decision", lane="mention", target_id=""),
    ]
    restrictions = [
        {"lane": "mention", "target_id": "311", "time": "unused existing outcome"},
        {"lane": "mention", "target_id": "", "time": "unused missing target"},
        {"lane": "mention", "target_id": "313", "time": "unused missing decision"},
        {"lane": "mention", "target_id": "310", "time": "invalid"},
        {"lane": None, "target_id": "312"},
        {"lane": None, "target_id": "312", "time": "2026-07-15 12:00:02"},
        {"lane": "alias", "target_id": "310", "time": "2026-07-15 12:00:01"},
        {"lane": "mention", "target_id": "310", "time": "unused duplicate"},
    ]
    original_normalise, original_parse = digest._normalise_lane, digest.parse_dt
    original_infer, original_enrich = digest.prepare_inferred_reply_strategy_outcomes, digest.enrich_published_reply_text

    def normalise(value):
        calls.append(("lane", value))
        return original_normalise("mention" if value == "alias" else value)

    def parse(value):
        calls.append(("time", value))
        if value == "callback unavailable":
            return None
        return original_parse(value)

    def infer(**inputs):
        assert inputs["_normalise_lane"] is normalise and inputs["parse_dt"] is parse
        inputs["events"].extend(observations)
        inputs["handled_api_restrictions"].extend(restrictions)
        with pytest.raises(ValueError, match="Could not parse datetime: 'invalid'"):
            original_infer(**inputs)
        assert inputs["events"] == observations
        restrictions[3]["time"] = "callback unavailable"
        calls.clear()
        assert original_infer(**inputs) is None
        captured["events"] = inputs["events"]
        assert [value for kind, value in calls if kind == "time"] == ["callback unavailable", "", "2026-07-15 12:00:02", "2026-07-15 12:00:01"]
        assert [value for kind, value in calls if kind == "lane"] == ["mention", "alias", "alias", None, "mention", "mention", "mention", "mention", None, None, "alias", "mention"]

    def enrich(report, **inputs):
        captured["production_ids"] = inputs["production_event_object_ids"]
        return original_enrich(report, **inputs)

    monkeypatch.setattr(digest, "_normalise_lane", normalise)
    monkeypatch.setattr(digest, "parse_dt", parse)
    monkeypatch.setattr(digest, "prepare_inferred_reply_strategy_outcomes", infer)
    monkeypatch.setattr(digest, "enrich_published_reply_text", enrich)
    report = digest.analyse([source], generation_time=source.ts)
    assert report["events"] is captured["events"]
    assert all(report["events"][index] is row for index, row in enumerate(observations))
    missing, inferred = report["events"][len(observations):]
    expected = {
        "time": "2026-07-15 12:00:01", "kind": "reply_strategy_outcome",
        "status": "posting_failed_terminal", "lane": "alias", "target_id": "310", "reply_post_id": "",
        "mode": "latest", "humour_tone": "dry", "tone": "dry", "evidence_confidence": "high",
        "retrieved_count": 0, "factual_claim": False, "grounded": None, "no_reply_reason": "",
        "failure_reason": "reply_not_permitted", "legacy_inferred": True,
    }
    assert inferred == expected
    assert missing == {**expected, "time": "2026-07-15 12:00:02", "lane": "unavailable", "target_id": "312", **dict.fromkeys(("mode", "humour_tone", "tone", "evidence_confidence", "retrieved_count", "factual_claim", "grounded", "no_reply_reason"))}
    assert report["summary"]["stats"]["reply_strategy_outcome"] == 2
    # Post-scan inference runs after the coordinator clears its source record.
    for row in (missing, inferred):
        assert id(row) not in captured["production_ids"]


def test_local_rejection_adapter_keeps_payload_collisions_current_helpers_and_map_identity(monkeypatch):
    captured, calls = {}, []
    dependencies = ("valid_string_public_post_id", "_normalise_lane", "bounded_event_text", "bounded_event_string_list")
    for name in dependencies:
        original = getattr(digest, name)

        def helper(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(digest, name, helper)
    original_merge = digest._add_or_merge_local_rejection
    payload = {
        "short": "x" * 90, "max_text": 1_000_000, "add_event": False,
        "local_rejections_by_identity": ["safe", 1, "also safe"],
        "valid_string_public_post_id": 0, "_normalise_lane": None,
        "bounded_event_text": "", "bounded_event_string_list": True,
        "negative": -1, "too_large": 1_000_001, "float": 0.5, "dict": {},
    }

    def merge(ts, kwargs, **inputs):
        for name in dependencies:
            assert inputs[name] is getattr(digest, name)
        before = copy.deepcopy(kwargs)
        result = original_merge(ts, kwargs, **inputs)
        assert kwargs == before
        captured["map"] = inputs["local_rejections_by_identity"]
        return result

    def decision(event_obj, ts, **callbacks):
        add = callbacks["add_or_merge_local_rejection"]
        calls.clear()
        row = add(ts, lane="unavailable", target_id="310", **payload)
        assert set(dependencies) <= set(calls)
        assert calls[:3] == ["valid_string_public_post_id", "_normalise_lane", "bounded_event_text"]
        assert captured["map"] == {("unavailable", "310"): row}
        assert add(ts + timedelta(seconds=1), lane="mention", target_id="310", short="replacement", max_text=0, add_event=True, _normalise_lane="filled", bounded_event_text="filled") is row
        assert set(captured["map"]) == {("mention", "310")}
        assert captured["map"][("mention", "310")] is row
        assert add(ts, lane="unavailable", target_id="310", _normalise_lane="later") is row
        assert set(captured["map"]) == {("mention", "310")}
        other = add(ts, lane="quote_tweet", target_id="310")
        assert other is not row
        invalid = add(ts, lane="unavailable", target_id=310)
        assert invalid["target_id"] == ""
        assert add(ts, lane="mention", target_id=310) is not invalid
        captured["row"] = row

    monkeypatch.setattr(digest, "_add_or_merge_local_rejection", merge)
    monkeypatch.setattr(digest, "record_ai_reply_pipeline_decision", decision)
    report = digest.analyse([structured_record(0, {"event": "ai_reply_pipeline_decision"})], max_text=40)
    row = captured["row"]
    assert report["events"][0] is row
    assert row == {
        "time": "2026-09-04 12:00:00", "kind": "reply_strategy_local_rejection", "lane": "mention", "target_id": "310",
        "short": payload["short"], "max_text": 1_000_000, "add_event": False,
        "local_rejections_by_identity": ["safe", "also safe"], "valid_string_public_post_id": 0,
        "_normalise_lane": "filled", "bounded_event_text": "filled", "bounded_event_string_list": True,
    }
    assert report["summary"]["stats"]["reply_strategy_local_rejection"] == 4


@pytest.mark.parametrize("max_text", [0, 1, 7, 12, 80, 280, 25_000])
def test_display_limits_preserve_local_rejection_merges_and_legacy_summaries(max_text):
    records = []
    draft = "A long proposed reply.\nAnother sentence."
    for target in ("1234567890123456781", "1234567890123456782"):
        records.extend([
            structured_record(len(records), {
                "event": "ai_reply_pipeline_decision", "lane": "mention",
                "target_id": target, "status": "reply", "mode": "factual",
                "effective_status": "local_rejection",
                "original_local_rejection_reason": "exact_duplicate_reply",
                "incoming_contribution": draft, "proposed_draft": draft,
            }),
            structured_record(len(records) + 1, {
                "event": "ai_reply_pipeline_effective_outcome", "lane": "mention",
                "target_id": target, "original_local_rejection_reason": "exact_duplicate_reply",
                "effective_status": "local_rejection", "repaired_draft": draft,
            }),
        ])
    report = digest.analyse(records, max_text=max_text, generation_time=records[-1].ts)
    baseline = digest.analyse(records, max_text=25_000, generation_time=records[-1].ts)
    rejections = [item for item in report["events"] if item["kind"] == "reply_strategy_local_rejection"]
    assert len(rejections) == 2
    assert {item["target_id"] for item in rejections} == {"1234567890123456781", "1234567890123456782"}
    assert all(item["reason"] == "exact_duplicate_reply" for item in rejections)
    assert all(item["proposed_draft"] == digest.short(draft, max_text) for item in rejections)
    assert all(item["repaired_draft"] == digest.short(draft, max_text) for item in rejections)
    assert digest.reply_strategy_summary(report["events"]) == digest.reply_strategy_summary(baseline["events"])
    assert digest.reply_strategy_summary(report["events"])["terminal_repetition_rejection_count"] == 2
    for key in ("summary", "legacy_multi_stage", "error_health", "api_health"):
        assert report[key] == baseline[key]


def test_unbounded_legacy_captures_cannot_bypass_fixed_event_limits():
    report = digest.analyse([replace(
        structured_record(0, {}),
        msg="Selected matched image basename=" + "x" * 25_001
        + " image_no=1 score=1 components=" + "y" * 25_001,
    )], max_text=1_000_000)
    assert report["events"][0]["image"] is None
    assert report["events"][0]["components"] is None


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


@pytest.mark.parametrize("payload, expected_kind, visual_count", [
    ({"event": "candidate_skipped", "lane": "mention", "id": "123"}, "candidate_skipped", 0),
    ({
        "event": "reply_visual_description", "lane": "mention", "target_id": "123",
        "status": "paused", "supplied_image_count": 1,
        "visual_analysis_call_count": 0, "analysis_schema_version": 1,
    }, None, 1),
    ({}, None, 0),
])
def test_valid_structured_events_use_only_strict_parsing(
    monkeypatch, payload, expected_kind, visual_count,
):
    parsed_messages = []
    strict_parse = digest.try_parse_strict_json_object_from_msg

    def parse(message):
        parsed_messages.append(message)
        return strict_parse(message)

    def compatibility_parse(message):
        pytest.fail("A valid structured event must not need compatibility parsing")

    monkeypatch.setattr(digest, "try_parse_strict_json_object_from_msg", parse)
    monkeypatch.setattr(digest, "try_parse_json_object_from_msg", compatibility_parse)
    row = structured_record(0, payload)
    report = digest.analyse([row])

    assert parsed_messages == [row.msg]
    assert [event["kind"] for event in report["events"]] == (
        [expected_kind] if expected_kind else []
    )
    assert report["summary"]["stats"].get("reply_visual_description_events", 0) == visual_count


@pytest.mark.parametrize("fragment, malformed_count", [
    ('{"event":"reply_visual_description","target_id":"123","target_id":"456"}', 1),
    ('{"event":"candidate_skipped","event":"reply_visual_description"}', 1),
    ('{"event":"reply_visual_description","event":"candidate_skipped"}', 0),
    ('junk {"event":"reply_visual_description"}', 1),
    (' {"event":"reply_visual_description"}', 1),
    ('{"event":"reply_visual_description"} trailing', 1),
    ('{"event":"reply_visual_description"', 1),
    ('{"event":"reply_visual_description","supplied_image_count":NaN}', 1),
    ('{"event":"reply_visual_description","supplied_image_count":1e999}', 1),
    ('{"event":"reply_visual_description","target_id":"\\ud800"}', 1),
    ('{"event":"reply_visual_description","target_id":"\ud800"}', 1),
])
def test_malformed_visual_event_detection_cannot_supply_structured_authority(
    fragment, malformed_count,
):
    row = replace(structured_record(0, {}), msg="EVENT " + fragment)
    report = digest.analyse([row])

    assert report["summary"]["stats"].get(
        "reply_visual_description_malformed_events", 0,
    ) == malformed_count
    assert report["summary"]["stats"].get("reply_visual_description_events", 0) == 0
    assert report["events"] == []
    assert report["published_reply_text_health"]["confirmed_record_count"] == 0
    assert report["structured_event_diagnostics"]["unknown_count"] == 0
    assert report["structured_event_diagnostics"]["malformed_count"] == (
        0 if malformed_count else 1
    )
    json.dumps(report, ensure_ascii=False, allow_nan=False).encode("utf-8")


def test_structured_event_diagnostics_keep_known_unknown_and_malformed_separate():
    rows = [
        structured_record(0, {"event": "candidate_skipped", "lane": "mention", "id": "123"}),
        structured_record(1, {"event": "future_event", "message": "Created X post successfully. response={'data': {'id': '999'}}"}),
        structured_record(2, {}),
        replace(structured_record(3, {}), msg='EVENT {"event":"broken",'),
        replace(structured_record(4, {}), msg='EVENT {"event":"future_event","event":"other"}'),
        replace(structured_record(5, {}), msg='EVENT {"event":"future_event","value":NaN}'),
    ]
    report = digest.analyse(rows)
    diagnostics = report["structured_event_diagnostics"]

    assert diagnostics["unknown_count"] == 1
    assert diagnostics["unknown_names"] == [{"name": "future_event", "count": 1}]
    assert diagnostics["malformed_count"] == 4
    assert diagnostics["malformed_reasons"] == {"event_name": 1, "strict_parse": 3}
    assert diagnostics["unknown_examples"][0]["record_number"] == rows[1].ordinal
    assert [ref["record_number"] for ref in diagnostics["malformed_examples"]] == [
        row.ordinal for row in rows[2:]
    ]
    assert [item["kind"] for item in report["events"]] == ["candidate_skipped"]
    assert report["summary"]["stats"].get("created_x_posts", 0) == 0
    rendered = digest.render_markdown(report)
    assert "Structured EVENT coverage:" in rendered
    assert "`future_event` (1)" in rendered
    assert "Created X post successfully" not in rendered


def test_structured_event_diagnostic_details_are_bounded():
    rows = [structured_record(index, {"event": f"future_{index}"}) for index in range(100)]
    rows.extend(structured_record(100 + index, {"event": "x" * 4000}) for index in range(20))
    rows.append(replace(structured_record(121, {}), msg=(
        'EVENT {"event":"future","payload":"Created X post successfully. '
        "response={'data': {'id': '999'}}\""
    )))
    report = digest.analyse(rows)
    diagnostics = report["structured_event_diagnostics"]

    assert diagnostics["unknown_count"] == 120
    assert diagnostics["malformed_count"] == 1
    assert len(diagnostics["unknown_names"]) <= 16
    assert diagnostics["unlisted_unknown_count"] == 104
    assert len(diagnostics["unknown_examples"]) == 8
    assert len(diagnostics["malformed_examples"]) == 1
    assert max(len(item["name"]) for item in diagnostics["unknown_names"]) <= 80
    assert "x" * 100 not in json.dumps(diagnostics)
    assert report["summary"]["stats"].get("created_x_posts", 0) == 0


def test_string_event_name_outside_display_alphabet_is_unknown_not_invalid():
    report = digest.analyse([
        structured_record(0, {"event": "future|event`secret"}),
        structured_record(1, {"event": 7}),
        structured_record(2, {"event": ""}),
    ])
    diagnostics = report["structured_event_diagnostics"]
    assert diagnostics["unknown_count"] == 1
    assert diagnostics["malformed_count"] == 2
    assert diagnostics["unknown_names"] == [
        {"name": "(name omitted for safe display)", "count": 1}
    ]
    assert "future|event`secret" not in digest.render_markdown(report)


def test_clean_known_structured_event_has_empty_diagnostics_and_no_markdown_summary():
    report = digest.analyse([structured_record(0, {"event": "candidate_skipped", "lane": "mention"})])
    assert report["structured_event_diagnostics"]["unknown_count"] == 0
    assert report["structured_event_diagnostics"]["malformed_count"] == 0
    assert "Structured EVENT coverage:" not in digest.render_markdown(report)


def test_reply_visual_description_contract_rejects_unsafe_shapes() -> None:
    valid = {
        "event": "reply_visual_description",
        "lane": "mention_reply",
        "target_id": "123",
        "supplied_image_count": 1,
        "status": "analysed",
        "analysis_schema_version": 1,
        "description_sha256": "a" * 64,
        "visual_analysis_call_count": 1,
    }

    parsed = digest.parse_reply_visual_description_event(valid)

    assert parsed == {
        "analysis_schema_version": 1,
        "description_sha256": "a" * 64,
        "lane": "mention",
        "status": "analysed",
        "supplied_image_count": 1,
        "target_id": "123",
        "visual_analysis_call_count": 1,
    }
    paused = digest.parse_reply_visual_description_event(
        {
            **valid,
            "description_sha256": "",
            "status": "paused",
            "visual_analysis_call_count": 0,
        }
    )
    invalid_media = digest.parse_reply_visual_description_event(
        {
            **valid,
            "description_sha256": None,
            "status": "invalid_supplied_media",
            "supplied_image_count": 0,
            "visual_analysis_call_count": 0,
        }
    )
    assert paused is not None and paused["visual_analysis_call_count"] == 0
    assert invalid_media is not None and invalid_media[
        "visual_analysis_call_count"
    ] == 0
    malformed = [
        {**valid, "supplied_image_count": True},
        {**valid, "visual_analysis_call_count": False},
        {**valid, "analysis_schema_version": True},
        {**valid, "description_sha256": "A" * 64},
        {**valid, "status": "provider_error"},
        {**valid, "image_url": "https://private.invalid/image.jpg"},
        {**valid, "lane": "unknown"},
        {**valid, "target_id": ""},
        {**valid, "description_sha256": "", "status": "paused"},
        {
            **valid,
            "description_sha256": "",
            "status": "provider_error",
            "visual_analysis_call_count": 0,
        },
    ]
    assert all(
        digest.parse_reply_visual_description_event(event) is None
        for event in malformed
    )


def test_reply_summary_classifies_declines_duplicates_and_posted_modes():
    events = []
    reasons = (
        ["exact_duplicate_reply"] * 3
        + ["no substantive prompt"] * 2
        + ["editorially declined"] * 4
    )
    for index, reason in enumerate(reasons):
        events.append({
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": f"decline-{index}",
            "mode": "no_reply",
            "no_reply_reason": reason,
            "factual_claim": False,
            "grounded": False,
        })
    for index in range(2):
        events.append({
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": f"posted-{index}",
            "mode": "opinion_or_principle",
            "factual_claim": False,
            "grounded": False,
        })
        events.append({
            "kind": "reply_strategy_outcome",
            "status": "posted",
            "lane": "mention",
            "target_id": f"posted-{index}",
            "reply_post_id": f"reply-{index}",
            "mode": "opinion_or_principle",
            "factual_claim": False,
            "grounded": False,
        })

    summary = digest.reply_strategy_summary(events)
    assert summary["conversational_candidate_count"] == 11
    assert summary["confirmed_outcome_count"] == 2
    assert summary["deliberately_declined_count"] == 6
    assert summary["terminal_repetition_rejection_count"] == 3
    assert summary["outcome_status_counts"] == {
        "posted": 2,
        "terminal_no_reply": 6,
        "terminal_repetition_rejection": 3,
    }
    assert summary["no_reply_category_counts"] == {
        "no_substantive_prompt": 2,
        "low_value_or_repetitive_engagement": 4,
    }
    assert summary["repetition_control_counts"]["exact_duplicate_rejected"] == 3
    assert summary["claim_free_opinion_or_principle_count"] == 2
    assert summary["humour_reply_count"] == 0

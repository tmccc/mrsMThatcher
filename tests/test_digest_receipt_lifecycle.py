"""Digest transport events, media incidents and receipt-lifecycle reporting."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

import mrs_log_digest as digest
import mrs_log_digest_records as record_owner
import mrs_log_digest_transactions as transaction_owner

from tests.helpers.digest_incidents import ambiguous_media_records
from tests.helpers.digest_records import BASE, record, structured_record, traceback


def test_transaction_helpers_keep_current_callbacks_and_shared_record(monkeypatch):
    assert transaction_owner.Record is record_owner.Record is digest.Record
    calls = []

    def classify(method, url):
        calls.append((method, url))
        return "current-endpoint"

    monkeypatch.setattr(digest, "classify_x_request_endpoint", classify)
    assert digest.parse_x_request_start("unrelated") is None
    assert calls == []
    assert digest.parse_x_request_start("X request: POST https://example.test/2/tweets") == {
        "method": "POST", "url": "https://example.test/2/tweets",
        "endpoint": "current-endpoint",
    }
    assert calls == [("POST", "https://example.test/2/tweets")]
    calls.clear()

    def short(value, limit):
        calls.append((value, limit))
        return "current-text"

    monkeypatch.setattr(digest, "short", short)
    unmatched = record(0, "INFO", "fixture", "unrelated")
    assert digest.parse_remote_write_transaction_event(unmatched) is None
    matched = record(1, "INFO", "fixture", "Uploading receipt-bound media via X API v2: /tmp/a.png")
    assert digest.parse_remote_write_transaction_event(matched)["message"] == "current-text"
    assert calls == [(unmatched.msg, 500), (matched.msg, 500)]


def test_media_correlation_keeps_current_callbacks_source_order_and_identity(monkeypatch):
    records = [
        record(0, "ERROR", "x_request", "X request failed before receiving response"),
        record(1, "WARNING", "upload_media", "v2 media upload failed; trying v1.1 fallback"),
        record(2, "INFO", "upload_media_v1_1", "Uploaded media via v1.1."),
        record(3, "INFO", "fixture", "Quote/image posted successfully."),
    ]
    indexes = {"fixture.log": 7}
    calls = []
    refs = [{"record_number": i + 1} for i in range(4)]
    for name in (
        "is_media_fallback_warning", "is_media_v2_request_failure",
        "is_media_v1_success", "is_main_post_success", "is_media_v1_failure",
    ):
        original = getattr(digest, name)

        def observe(item, name=name, original=original):
            assert any(item is r for r in records)
            calls.append(name)
            return original(item)

        monkeypatch.setattr(digest, name, observe)

    def recent(items, index):
        assert items is records and index == 1
        return "current-path"

    def seconds(left, right):
        assert right is records[1].ts
        calls.append("seconds")
        return 0

    def fingerprint(item):
        index = next(i for i, r in enumerate(records) if item is r)
        calls.append(("fingerprint", index))
        return str(index)

    def source(item, supplied_indexes):
        assert supplied_indexes is indexes
        index = next(i for i, r in enumerate(records) if item is r)
        calls.append(("source", index))
        return refs[index]

    def bounded(*items):
        assert all(item is refs[index] for item, index in zip(items, (1, 0, 2, 3)))
        calls.append("bounded")
        return list(items), 2

    monkeypatch.setattr(digest, "find_recent_media_path", recent)
    monkeypatch.setattr(digest, "seconds_between", seconds)
    monkeypatch.setattr(digest, "record_fingerprint", fingerprint)
    monkeypatch.setattr(digest, "record_source_ref", source)
    monkeypatch.setattr(digest, "bounded_source_refs", bounded)
    monkeypatch.setattr(digest, "short", lambda value, limit: f"{limit}:{value}")
    incidents, suppressed = digest.correlate_media_upload_incidents(records, 19, indexes)
    incident = incidents[0]
    assert suppressed == {"0", "1", "2", "3"}
    assert incident["status"] == "handled" and incident["media"] == "current-path"
    assert incident["fallback"] == f"19:{records[1].msg}"
    assert incident["v2_failure"] == f"19:{records[0].msg}"
    assert incident["source_ref_omitted_count"] == 2
    assert incident["source_refs"][0] is refs[1]
    assert calls.count("seconds") == 3
    assert {name for name in calls if isinstance(name, str)} >= {
        "is_media_fallback_warning", "is_media_v2_request_failure",
        "is_media_v1_success", "is_main_post_success", "is_media_v1_failure",
    }
    assert [call for call in calls if isinstance(call, tuple)] == [
        *(("fingerprint", i) for i in (1, 0, 2, 3)),
        *(("source", i) for i in (1, 0, 2, 3)),
    ]
    assert calls.index("bounded") > calls.index(("source", 3))


def test_post_scan_media_preparation_keeps_current_callbacks_and_error_rebinding(monkeypatch):
    records = [
        record(0, "ERROR", "fixture", "Missing X credentials."),
        record(0, "ERROR", "fixture", "SELFTEST FAIL: fixture credentials"),
        record(1, "ERROR", "fixture", "suppressed media error"),
        record(2, "ERROR", "x_request", 'X API error 403: {"detail":"You attempted to reply to a Tweet that is deleted or not visible to you."}'),
        record(3, "ERROR", "post_generated_reply", "Failed to post generated reply"),
        record(8, "ERROR", "post_generated_reply", "Failed to post generated reply after six seconds"),
        *ambiguous_media_records(),
    ]
    indexes = {"fixture.log": 4}
    incidents = []
    calls = []
    captured = {}
    original_parse = digest.parse_dt
    original_seconds = digest.seconds_between

    def parse(value):
        calls.append("parse")
        return original_parse(value)

    class CurrentDatetime(datetime):
        @classmethod
        def strptime(cls, value, fmt):
            calls.append("strptime")
            return datetime.strptime(value, fmt)

    def seconds(left, right):
        calls.append("seconds")
        return original_seconds(left, right)

    def correlate(items, max_text, input_file_indexes):
        assert items is records and max_text == 37 and input_file_indexes is indexes
        calls.append("correlate")
        return incidents, {digest.record_fingerprint(records[2])}

    original_prepare = digest.prepare_media_incidents_and_errors

    def prepare(**inputs):
        assert inputs["correlate_media_upload_incidents"] is correlate
        assert inputs["parse_dt"] is parse and inputs["seconds_between"] is seconds
        assert inputs["strptime"] == CurrentDatetime.strptime
        assert inputs["self_test_times"] == {digest.dt_text(BASE)}
        assert inputs["handled_restriction_times"] == [BASE + timedelta(seconds=2)]
        before = list(inputs["errors"])
        calls.clear()
        result = original_prepare(**inputs)
        assert calls[0] == "correlate"
        assert {"parse", "strptime", "seconds"} <= set(calls)
        assert result[0] is incidents and result[1] is not inputs["errors"]
        assert all(left is right for left, right in zip(before, inputs["errors"]))
        assert inputs["self_test_errors"][-1] is before[0]
        assert before[1] not in result[1] and before[2] not in result[1]
        assert any(item is before[3] for item in result[1])
        captured["errors"] = result[1]
        return result

    original_health = digest.summarise_operational_error_health

    def health(errors, *args, **kwargs):
        assert errors is captured["errors"]
        return original_health(errors, *args, **kwargs)

    monkeypatch.setattr(digest, "datetime", CurrentDatetime)
    monkeypatch.setattr(digest, "parse_dt", parse)
    monkeypatch.setattr(digest, "seconds_between", seconds)
    monkeypatch.setattr(digest, "correlate_media_upload_incidents", correlate)
    monkeypatch.setattr(digest, "prepare_media_incidents_and_errors", prepare)
    monkeypatch.setattr(digest, "summarise_operational_error_health", health)
    report = digest.analyse(records, max_text=37, input_file_indexes=indexes)
    assert report["media_upload"]["incidents"] is incidents


def test_request_transaction_projection_keeps_parser_event_and_callback_order(monkeypatch):
    r = record(0, "INFO", "x_request", "X request: POST https://example.test/2/tweets")
    indexes = {r.path: 3}
    requests, transactions, receipt_calls, trace = [], [], [], []
    latest, stats = {}, Counter()
    source_ref = {"record_number": 1}
    request = {"endpoint": "tweet/create", "extra": []}
    transaction = {"kind": "main_post_receipt", "phase": "attempting", "lane": []}

    def source(item, supplied_indexes):
        assert item is r and supplied_indexes is indexes
        trace.append("source")
        return source_ref

    def receipt(kind, item, **kwargs):
        assert item is r and transactions[-1] is transaction
        assert stats["remote_write_transaction_attempting"] == 1
        assert kwargs["lane"] is transaction["lane"]
        receipt_calls.append((kind, kwargs))

    transaction_owner.record_x_request_start(
        request, r, input_file_indexes=indexes, x_requests=requests,
        latest_x_request_by_source=latest, stats=stats, record_source_ref=source,
    )
    assert requests[0] is latest[r.src] and requests[0] is not request
    assert requests[0]["extra"] is request["extra"]
    transaction_owner.record_remote_write_transaction(
        transaction, r, input_file_indexes=indexes,
        remote_write_transactions=transactions, stats=stats,
        record_source_ref=source, add_receipt_event=receipt,
    )
    assert transactions[0] is transaction
    assert transaction["source_refs"][0] is requests[0]["source_refs"][0] is source_ref
    assert receipt_calls == [("main_post_receipt", {"phase": "attempting", "lane": []})]
    assert stats == {"x_request_endpoint_tweet_create": 1, "remote_write_transaction_attempting": 1}

    def parse_request(message):
        trace.append("parse request")
        return request

    def project_request(*args, **kwargs):
        assert args[0] is request and args[1] is r
        trace.append("project request")

    def parse_transaction(item):
        assert item is r
        trace.append("parse transaction")
        return transaction

    def project_transaction(*args, **kwargs):
        assert args[0] is transaction and args[1] is r
        trace.append("project transaction")

    monkeypatch.setattr(digest, "parse_x_request_start", parse_request)
    monkeypatch.setattr(digest, "record_x_request_start", project_request)
    monkeypatch.setattr(digest, "parse_remote_write_transaction_event", parse_transaction)
    monkeypatch.setattr(digest, "record_remote_write_transaction", project_transaction)
    trace.clear()
    selftest = digest.Record(
        r.ts, r.level, r.src, r.line, r.msg, "fixture.selftest.log", r.ordinal,
    )
    digest.analyse([r, selftest])
    assert trace == ["parse request", "project request", "parse transaction", "project transaction"]


def test_current_remote_write_transaction_lifecycle_shapes_are_parsed():
    transaction_id = "a" * 64
    cases = [
        (
            "Creating X post with durable transport journal. lane=quote_image "
            f"transaction_id={transaction_id} reply_to_id=none media_count=1 "
            "made_with_ai=false",
            ("tweet_transport", "request_started"),
        ),
        (
            "Wrote main-post sending receipt lane=quote_image attempt_id=attempt-1 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "sending_published"),
        ),
        (
            "Promoted main-post receipt to attempting lane=quote_image "
            "attempt_id=attempt-1 path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "attempting"),
        ),
        (
            "Handed confirmed media upload to durable main-post attempt "
            "lane=quote_image attempt_id=attempt-1 media_id=999",
            ("media_upload", "confirmed_handoff"),
        ),
        (
            "Promoted main-post attempt to confirmed pending-schedule receipt "
            "lane=quote_image attempt_id=attempt-1 post_id=123 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "confirmed_pending_schedule"),
        ),
        (
            "Removed main-post sending receipt disposition=definite_non_success "
            "lane=quote_image attempt_id=attempt-1 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "sending_retired"),
        ),
        (
            "Finalised confirmed pending-schedule receipt lane=quote_image "
            "post_id=123 path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "schedule_finalised"),
        ),
        (
            "Resumed interrupted exact source-receipt retirement "
            "path=/srv/regular_post_receipt.json phase=exchange",
            ("source_receipt_retirement", "exchange"),
        ),
        (
            "Resumed interrupted confirmed-media fence retirement "
            f"lane=quote_image media_transaction_id={transaction_id} media_id=999",
            ("media_retirement", "resumed"),
        ),
        (
            "Recovered crash-left permanent retirement-ledger exchanges count=1",
            ("retirement_ledger", "exchange_recovered"),
        ),
    ]

    for offset, (message, expected) in enumerate(cases):
        parsed = digest.parse_remote_write_transaction_event(
            record(offset, "INFO", "fixture", message)
        )
        assert parsed is not None
        assert (parsed["kind"], parsed["phase"]) == expected


def test_receipt_pairs_and_pending_then_confirmed_are_not_incidents():
    report = digest.analyse([])
    report["main_post_recovery"] = {
        "receipt_events": [
            {"time": "t1", "kind": "regular_written", "level": "WARNING", "lane": "quote_image"},
            {"time": "t2", "kind": "regular_removed", "level": "INFO", "lane": "quote_image"},
        ],
        "confirmed_post_recovery": [],
    }
    report["confirmed_reply_recovery"] = {
        "receipt_events": [
            {
                "time": "t2",
                "kind": "written",
                "level": "WARNING",
                "lane": "mention",
                "target_id": "target-1",
                "reply_post_id": "reply-1",
            },
            {
                "time": "t3",
                "kind": "removed",
                "level": "INFO",
                "lane": "mention",
                "target_id": "target-1",
                "reply_post_id": "reply-1",
            },
        ],
        "warnings": [],
    }
    report["events"] = [
        {
            "kind": "posting_transaction_state",
            "time": "t1",
            "parent_post_id": "post-1",
            "main_post_state": "main_post_confirmed",
            "context_reply_state": "context_reply_pending",
        },
        {
            "kind": "historical_context_obligation",
            "time": "t2",
            "parent_post_id": "post-1",
            "status": "completed",
            "context_reply_state": "context_reply_confirmed",
        },
    ]
    rendered = digest.render_markdown(report)
    assert "## Transactional receipt lifecycle" in rendered
    assert "Routine two-phase receipt write/remove pairs completed: **1**" in rendered
    assert "Routine confirmed-reply receipt write/remove pairs completed: **1**" in rendered
    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "## Confirmed-reply recovery" not in rendered
    assert "intermediate `context_reply_pending` states subsequently reached" in rendered
    assert "they are not outstanding" in rendered


def structured_main_post_lifecycle(
    start: int,
    *,
    lane: str,
    attempt_id: str,
) -> list[digest.Record]:
    receipt_name = (
        "regular_post_receipt.json"
        if lane == "quote_image"
        else "meme_post_receipt.json"
    )
    removal_label = "regular-post" if lane == "quote_image" else "meme-post"
    path = f"/srv/{receipt_name}"
    return [
        record(
            start,
            "WARNING",
            "write_main_post_attempt",
            f"Wrote main-post sending receipt lane={lane} "
            f"attempt_id={attempt_id} path={path}",
        ),
        record(
            start + 1,
            "WARNING",
            "mark_main_post_attempt_attempting",
            f"Promoted main-post receipt to attempting lane={lane} "
            f"attempt_id={attempt_id} path={path}",
        ),
        record(
            start + 2,
            "WARNING",
            "promote_main_post_attempt_to_pending_schedule",
            "Promoted main-post attempt to confirmed pending-schedule receipt "
            f"lane={lane} attempt_id={attempt_id} post_id={start + 1000} "
            f"path={path}",
        ),
        record(
            start + 3,
            "WARNING",
            "finalize_confirmed_pending_schedule_receipt",
            "Finalised confirmed pending-schedule receipt "
            f"lane={lane} post_id={start + 1000} path={path}",
        ),
        record(
            start + 4,
            "INFO",
            f"remove_{removal_label.replace('-', '_')}_receipt",
            f"Removed reconciled {removal_label} receipt: {path}",
        ),
    ]


@pytest.mark.parametrize(
    ("lane", "lane_summary"),
    [
        ("quote_image", "regular quote/image **1**; daily-meme **0**"),
        ("daily_meme", "regular quote/image **0**; daily-meme **1**"),
    ],
)
def test_structured_main_post_lifecycle_is_completed_not_unresolved(
    lane: str,
    lane_summary: str,
):
    report = digest.analyse(
        structured_main_post_lifecycle(0, lane=lane, attempt_id="attempt-1")
    )
    rendered = digest.render_markdown(report)

    assert "Routine two-phase receipt write/remove pairs completed: **1**" in rendered
    assert lane_summary in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_main_post_removal_at_window_boundary_is_not_unresolved():
    report = digest.analyse([
        record(
            0,
            "INFO",
            "remove_regular_post_receipt",
            "Removed reconciled regular-post receipt: "
            "/srv/regular_post_receipt.json",
        )
    ])
    rendered = digest.render_markdown(report)

    assert "opening write was outside the selected window: **1**" in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_structured_main_post_write_without_removal_remains_unresolved():
    records = structured_main_post_lifecycle(
        0,
        lane="quote_image",
        attempt_id="attempt-unresolved",
    )[:-1]
    rendered = digest.render_markdown(digest.analyse(records))

    assert "Routine two-phase receipt write/remove pairs completed: **0**" in rendered
    assert "Stale or unresolved receipt events:" in rendered
    assert "attempt-unresolved" in rendered


def test_mixed_structured_main_post_fixture_counts_fifteen_regular_and_one_meme():
    records: list[digest.Record] = []
    for index in range(15):
        records.extend(
            structured_main_post_lifecycle(
                index * 10,
                lane="quote_image",
                attempt_id=f"regular-{index}",
            )
        )
    records.extend(
        structured_main_post_lifecycle(
            200,
            lane="daily_meme",
            attempt_id="meme-1",
        )
    )

    rendered = digest.render_markdown(digest.analyse(records))

    assert "Routine two-phase receipt write/remove pairs completed: **16**" in rendered
    assert "regular quote/image **15**; daily-meme **1**" in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_schema_v3_reply_receipt_lifecycle_is_routine_and_observable():
    records = [
        record(
            0,
            "WARNING",
            "write_sending_reply_receipt",
            "Wrote conversational reply sending receipt "
            "source=mention target_id=123 path=/tmp/reply.json",
        ),
        record(
            1,
            "WARNING",
            "promote_sending_reply_receipt",
            "Promoted conversational reply receipt to confirmed "
            "source=mention target_id=123 reply_post_id=999 "
            "path=/tmp/reply.json",
        ),
        record(
            2,
            "INFO",
            "remove_confirmed_reply_receipt",
            "Removed reconciled confirmed-reply receipt "
            "source=mention target_id=123 reply_post_id=999 "
            "path=/tmp/reply.json",
        ),
    ]

    report = digest.analyse(records)
    rendered = digest.render_markdown(report)

    assert [
        item["kind"]
        for item in report["confirmed_reply_recovery"]["receipt_events"]
    ] == ["sending", "promoted", "removed"]
    assert "Routine confirmed-reply receipt write/remove pairs completed: **1**" in rendered
    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "## Confirmed-reply recovery" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_unresolved_schema_v3_sending_receipt_stays_current_after_other_reply():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "write_sending_reply_receipt",
                "Wrote conversational reply sending receipt "
                "source=quote_tweet target_id=456 path=/tmp/reply.json",
            ),
            record(
                1,
                "INFO",
                "maybe_reply_to_mentions",
                "Considering mention id=789 author_id=42 text='different transaction'",
            ),
            record(
                2,
                "INFO",
                "maybe_reply_to_mentions",
                "Reply posted successfully",
            ),
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert "sending_unresolved" in rendered
    assert "Pre-send reply receipt remains unresolved" in rendered
    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["historical_resolved_incident_count"] == 0
    assert report["error_health"]["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert "1 unresolved operational incident" in report["summary"]["headline"]
    assert "no unresolved operational incidents" not in report["summary"]["headline"]


def test_confirmed_state_fallback_is_not_labelled_definite_non_success():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "write_sending_reply_receipt",
                "Wrote conversational reply sending receipt "
                "source=mention target_id=123 path=/tmp/reply.json",
            ),
            record(
                1,
                "WARNING",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after confirmed "
                "identity was preserved in canonical state "
                "source=mention target_id=123 path=/tmp/reply.json",
            ),
            record(
                2,
                "CRITICAL",
                "maybe_reply_to_mentions",
                traceback(
                    "Confirmed mention reply required its durable state fallback",
                    "ConfirmedReplyLocalPersistenceError: promotion failed",
                ),
            ),
        ]
    )

    rendered = digest.render_markdown(report)
    receipt_events = report["confirmed_reply_recovery"]["receipt_events"]

    assert "## Confirmed-reply recovery" in rendered
    assert [item["kind"] for item in receipt_events] == [
        "sending",
        "confirmed_state_fallback_removed",
    ]
    assert receipt_events[1]["disposition"] == "confirmed_state_fallback"
    assert (
        report["summary"]["stats"][
            "confirmed_reply_receipt_confirmed_state_fallback_removed"
        ]
        == 1
    )
    assert (
        report["summary"]["stats"].get(
            "confirmed_reply_receipt_sending_removed",
            0,
        )
        == 0
    )
    assert (
        "Confirmed replies preserved through the durable canonical-state "
        "fallback: **1**."
        in rendered
    )
    assert "Prepared reply receipts cleared after a definite non-success" not in rendered
    assert len(report["confirmed_reply_recovery"]["warnings"]) == 1
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_confirmed_state_fallback_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after confirmed "
                "identity was preserved in canonical state "
                "source=quote_tweet target_id=456 path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert (
        "Confirmed replies preserved through the durable canonical-state "
        "fallback: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_definite_non_success_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after definite "
                "non-success source=mention target_id=123 path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert (
        "Prepared reply receipts cleared after a definite non-success: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_confirmed_receipt_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "remove_confirmed_reply_receipt",
                "Removed reconciled confirmed-reply receipt "
                "source=mention target_id=123 reply_post_id=999 "
                "path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert (
        "Confirmed-reply receipt removals whose opening write was outside "
        "the observed window: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_reconciliation_start_without_terminal_event_remains_current():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "reconcile_confirmed_reply_receipt",
                "Reconciling confirmed reply receipt "
                "source=quote_tweet target_id=456 reply_post_id=999",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert "reconciliation_unresolved" in rendered
    assert (
        "no terminal receipt removal or completion was observed"
        in rendered
    )
    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert "1 unresolved operational incident" in report["summary"]["headline"]


def test_explicit_replay_suppression_completes_reconciliation_observation():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "reconcile_confirmed_reply_receipt",
                "Reconciling confirmed reply receipt "
                "source=mention target_id=123 reply_post_id=999",
            ),
            record(
                1,
                "WARNING",
                "maybe_reply_to_mentions",
                "Reconciled confirmed reply receipt before checking new "
                "mention candidates",
            ),
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


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

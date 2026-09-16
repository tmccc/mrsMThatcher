"""Digest mention controls, runtime state and publication-consistency observations."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import copy
import json

import pytest

import mrs_log_digest as digest
from mrs_log_digest_consistency_events import prepare_context_transaction_outcomes

from tests.helpers.digest_records import structured_record


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


@pytest.mark.parametrize("max_text", [0, 1, 7, 80, 280])
def test_digest_distinguishes_confirmed_main_context_states_and_meme_stage(max_text):
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

    report = digest.analyse(records, max_text=max_text)
    consistency = report["production_consistency"]
    assert consistency["context_transaction_state_counts"] == {
        "context_reply_pending": 1
    }
    assert consistency["context_obligation_state_counts"] == {
        "context_reply_failed_retryable": 1,
        "context_reply_failed_terminal": 1,
    }
    assert consistency["daily_meme_failure_stage_counts"] == {"media_upload": 1}
    outcomes = consistency["context_transaction_outcomes"]
    assert outcomes["resolved_pending_count"] == 0
    assert outcomes["outstanding_count"] == 1
    assert outcomes["outstanding_transactions"][0] is report["events"][0]
    rendered = digest.render_markdown(report)
    assert "Confirmed-main/context transaction states" in rendered
    assert "context_reply_pending" in rendered
    assert "context_reply_failed_retryable" in rendered
    assert "context_reply_failed_terminal" in rendered
    assert "Daily meme failures by stage" in rendered
    assert "media_upload" in rendered


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


def test_consistency_projections_keep_current_callbacks_sharing_and_double_counts(monkeypatch):
    names = (
        "reply_evidence_unavailable", "runtime_control_pause", "runtime_control_clear",
        "clarification_reply_cap_override", "clarification_reply_used",
        "repair_reply_completed", "posting_transaction_state", "daily_meme_failure",
    )
    records = [structured_record(index, {
        "event": name, "lane": "mention", "lanes": ["pause"], "key": "control",
        "target_id": "opaque", "thread_id": "opaque", "author_id": "opaque",
        "reply_post_id": "opaque", "parent_post_id": "opaque", "post_id": "opaque",
        "bypassed_cap": "daily", "trigger": "question", "until_epoch": 1,
        "context_reply_state": "pending", "main_post_state": "confirmed",
        "context_state_persisted": True, "stage": "media", "status": "failed",
        "error_type": "OSError", "reason": "fixture",
    }) for index, name in enumerate(names)]
    original_records = copy.deepcopy(records)
    previous_stats = None
    for run in range(2):
        emitted, counters, lane_lists, parsed = [], [], [], []
        with monkeypatch.context() as patch:
            def parse(message):
                result = json.loads(message.removeprefix("EVENT "))
                parsed.append(result)
                return result

            def text(value, **kwargs):
                return f"run{run}:{value}"

            def lanes(value, **kwargs):
                assert value == ["pause"]
                assert kwargs == {"limit": 20, "item_max_characters": 100}
                result = [f"run{run}:pause"]
                lane_lists.append(result)
                return result

            helpers = {
                "bounded_event_text": text,
                "bounded_event_string_list": lanes,
                "bounded_event_nonnegative_integer": lambda value: 71 + run,
                "bounded_event_boolean": lambda value: False,
                "valid_string_public_post_id": lambda value: value == "opaque",
            }
            patch.setattr(digest, "try_parse_strict_json_object_from_msg", parse)
            for name, callback in helpers.items():
                patch.setattr(digest, name, callback)

            def wrap(name):
                original = getattr(digest, "record_" + name)

                def project(payload, timestamp, stats, *, add_event, **callbacks):
                    index = names.index(name)
                    assert payload is parsed[-1]
                    assert timestamp is records[index].ts
                    assert all(value is helpers[key] for key, value in callbacks.items())
                    counters.append(stats)

                    def emit(kind, ts, **fields):
                        assert kind == name and ts is timestamp
                        assert stats[kind] == 0
                        row = add_event(kind, ts, **fields)
                        assert stats[kind] == 1
                        emitted.append(row)
                        return row

                    assert original(payload, timestamp, stats, add_event=emit, **callbacks) is None

                patch.setattr(digest, "record_" + name, project)

            for name in names:
                wrap(name)
            report = digest.analyse(records, generation_time=records[-1].ts)

        assert counters[0] is not previous_stats
        previous_stats = counters[0]
        assert all(stats is counters[0] for stats in counters)
        assert [row["kind"] for row in report["events"]] == list(names)
        consistency = report["production_consistency"]
        assert consistency["events"] is not report["events"]
        assert all(row is emitted[index] is report["events"][index]
                   for index, row in enumerate(consistency["events"]))
        for row, shared in zip(emitted[1:3], lane_lists):
            assert row["control_lanes"] is shared
            assert row["lanes"] == f"run{run}:pause"
            shared.append("later mutation")
            assert row["control_lanes"][-1] == "later mutation"
        emitted[0]["extra"] = "shared mutation"
        assert consistency["events"][0]["extra"] == "shared mutation"
        consistency["events"].pop()
        assert len(report["events"]) == 8
        assert emitted[1]["until_epoch"] == 71 + run
        assert emitted[6]["context_state_persisted"] is False
        assert emitted[0]["target_id"] == emitted[3]["author_id"] == emitted[7]["post_id"] == "opaque"
        assert emitted[4]["trigger"] == f"run{run}:question"
        assert emitted[5]["reply_post_id"] == "opaque"
        assert consistency["context_transaction_state_counts"] == {f"run{run}:pending": 1}
        assert consistency["daily_meme_failure_stage_counts"] == {f"run{run}:media": 1}
        assert consistency["reply_evidence_unavailable_lane_counts"] == {f"run{run}:mention": 1}
        counts = report["summary"]["stats"]
        assert {name: counts[name] for name in names} == {
            name: (2 if name in names[1:6] else 1) for name in names
        }
        assert report["api_health"]["observed_remote_write_success_count"] == 0
    assert records == original_records


def test_consistency_report_keeps_late_callback_and_separate_counter_iterations(monkeypatch):
    prefixes = (
        "context_transaction_state_", "context_obligation_state_",
        "daily_meme_failure_stage_", "historical_context_runtime_status_",
        "reply_evidence_unavailable_lane_",
    )
    captured = {}
    observations = []

    class ObservedCounter(Counter):
        def items(self):
            if self is captured.get("stats") and captured.get("late"):
                number = len(observations)
                observations.append(number)
                for prefix in prefixes:
                    # Insert out of order; preserve types as well as sorted keys.
                    self[prefix + "z"] = number
                    self[prefix + "a"] = bool(number % 2)
            return super().items()

    original_project = digest.record_reply_evidence_unavailable
    original_api_report = digest.api_health_report

    def project(payload, timestamp, stats, **callbacks):
        captured["stats"] = stats
        return original_project(payload, timestamp, stats, **callbacks)

    def late_api_report(*args, **kwargs):
        captured["late"] = True
        return original_api_report(*args, **kwargs)

    monkeypatch.setattr(digest, "Counter", ObservedCounter)
    monkeypatch.setattr(digest, "record_reply_evidence_unavailable", project)
    monkeypatch.setattr(digest, "api_health_report", late_api_report)
    report = digest.analyse([structured_record(0, {"event": "reply_evidence_unavailable"})])
    consistency = report["production_consistency"]
    assert list(consistency) == [
        "events", "context_transaction_state_counts", "context_obligation_state_counts",
        "daily_meme_failure_stage_counts", "historical_context_runtime_status_counts",
        "reply_evidence_unavailable_lane_counts",
        "context_transaction_outcomes",
    ]
    # The historical-reply section takes observation zero immediately beforehand.
    assert observations == list(range(6))
    for index, (key, counts) in enumerate(list(consistency.items())[1:6], start=1):
        assert list(counts) == (["a", "unavailable", "z"] if index == 5 else ["a", "z"])
        assert counts["z"] == index and type(counts["z"]) is int
        assert counts["a"] is bool(index % 2)
    assert not any(prefix + "z" in report["summary"]["stats"] for prefix in prefixes)


@pytest.mark.parametrize("terminal_state", [
    "context_reply_confirmed", "context_reply_not_required", "context_reply_failed_terminal",
])
@pytest.mark.parametrize("obligation_first", [False, True])
def test_context_outcomes_preserve_matching_source_treatment_order_and_duplicates(
    terminal_state, obligation_first,
):
    pending = {"kind": "posting_transaction_state", "parent_post_id": "101",
               "context_reply_state": "context_reply_pending", "time": "later"}
    outstanding = dict(pending, parent_post_id="202")
    retryable = dict(pending, context_reply_state="context_reply_failed_retryable")
    unknown = dict(pending, context_reply_state="unknown")
    terminal = dict(pending, context_reply_state=terminal_state)
    obligation = {
        "kind": "historical_context_obligation", "parent_post_id": 101,
        "context_reply_state": terminal_state, "time": "earlier",
        "source_refs": [{"source_basename": "self-test.log", "record_number": 1}],
    }
    events = [pending, outstanding, dict(outstanding), retryable, terminal, unknown, dict(pending)]
    # Only historical-context obligations resolve pending transaction rows.
    events.append(dict(obligation, kind="another_kind", parent_post_id="202"))
    events.append(dict(obligation, parent_post_id="202", context_reply_state="context_reply_failed_retryable"))
    events.insert(0 if obligation_first else len(events), obligation)
    before = copy.deepcopy(events)

    outcomes = prepare_context_transaction_outcomes(events)

    assert outcomes["resolved_pending_count"] == 2
    assert outcomes["outstanding_count"] == 4
    rows = outcomes["outstanding_transactions"]
    assert rows == [outstanding, outstanding, retryable, unknown]
    assert rows[0] is outstanding and rows[1] is not outstanding
    assert rows[2] is retryable and rows[3] is unknown
    assert events == before


def test_context_outcomes_keep_legacy_missing_parent_matching_and_empty_defaults():
    assert prepare_context_transaction_outcomes([]) == {
        "resolved_pending_count": 0, "outstanding_count": 0, "outstanding_transactions": [],
    }
    events = [
        {"kind": "posting_transaction_state", "context_reply_state": "context_reply_pending"},
        {"kind": "historical_context_obligation", "parent_post_id": None,
         "context_reply_state": "context_reply_confirmed"},
    ]
    assert prepare_context_transaction_outcomes(events) == {
        "resolved_pending_count": 1, "outstanding_count": 0, "outstanding_transactions": [],
    }


@pytest.mark.parametrize("max_text", [0, 1, 280])
def test_context_outcomes_are_shared_by_json_and_markdown_without_replacing_raw_counts(max_text):
    records = [structured_record(index, payload) for index, payload in enumerate([
        {"event": "posting_transaction_state", "parent_post_id": "101",
         "main_post_state": "main_post_confirmed", "context_reply_state": "context_reply_pending"},
        {"event": "historical_context_obligation", "parent_post_id": "101",
         "status": "completed", "context_reply_state": "context_reply_confirmed"},
        {"event": "posting_transaction_state", "parent_post_id": "202",
         "main_post_state": "main_post_confirmed", "context_reply_state": "context_reply_pending"},
    ])]
    report = digest.analyse(records, max_text=max_text)
    before = copy.deepcopy(report)
    consistency = report["production_consistency"]

    assert consistency["context_transaction_state_counts"] == {"context_reply_pending": 2}
    assert consistency["context_obligation_state_counts"] == {"context_reply_confirmed": 1}
    outcomes = consistency["context_transaction_outcomes"]
    assert outcomes == {"resolved_pending_count": 1, "outstanding_count": 1,
                        "outstanding_transactions": [report["events"][2]]}
    assert outcomes["outstanding_transactions"][0] is report["events"][2]
    assert json.loads(json.dumps(report))["production_consistency"]["context_transaction_outcomes"] == outcomes
    rendered = digest.render_markdown(report)
    assert "**1** intermediate `context_reply_pending` states subsequently reached" in rendered
    assert "Outstanding intermediate states:" in rendered
    assert "| 202 | main_post_confirmed | context_reply_pending |" in rendered
    assert report == before

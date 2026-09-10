"""Shared receipt transitions preserve source scope and distinct projections."""
from __future__ import annotations

import copy

import pytest

from mrs_log_digest_transactions import (
    _scan_reply_receipt_lifecycle,
    append_unresolved_reply_receipt_errors,
    summarise_reply_receipt_lifecycle,
)
from mrs_log_digest_values import _normalise_lane


def receipt(kind, *, lane="mention", target="101", reply="901", time="first", **extra):
    return {
        "kind": kind, "lane": lane, "target_id": target,
        "reply_post_id": reply, "time": time, **extra,
    }


def summary(events, **kwargs):
    return summarise_reply_receipt_lifecycle(events, normalise_lane=_normalise_lane, **kwargs)


@pytest.mark.parametrize("terminal", ["promoted", "sending_removed", "confirmed_state_fallback_removed"])
def test_sending_terminals_consume_only_the_latest_matching_observation(terminal):
    refs = [{"record_number": 1}]
    first = receipt("sending", time="later timestamp", source_refs=refs)
    second = receipt("sending", time="earlier timestamp")
    other = receipt("sending", target="102")
    events = [first, second, other, receipt(terminal)]
    before = copy.deepcopy(events)
    scanned = _scan_reply_receipt_lifecycle(iter(events))
    assert scanned.pending_sending[("mention", "101")] == [first]
    assert scanned.pending_sending[("mention", "101")][0] is first
    assert scanned.pending_sending[("mention", "102")][0] is other
    assert scanned.pending_sending[("mention", "101")][0]["source_refs"] is refs
    assert scanned.pending_confirmed[("mention", "101", "901")] == (terminal == "promoted")
    assert scanned.definite_non_success_clears == (terminal == "sending_removed")
    assert scanned.confirmed_state_fallback_clears == (terminal == "confirmed_state_fallback_removed")
    assert events == before


def test_reconciliation_removal_and_lane_suppression_keep_lifo_cardinality():
    first = receipt("reconciled", time="first")
    other = receipt("reconciled", target="102", reply="902", time="second")
    duplicate = receipt("reconciled", time="third")
    quote = receipt("reconciled", lane="quote_tweet", time="fourth")
    events = [
        receipt("written"), receipt("written"),
        receipt("written", target="102", reply="902"),
        first, other, duplicate, quote,
        receipt("removed"),
        receipt("replay_suppressed_mention_check", target="unrelated", reply="unrelated"),
        receipt("replay_suppressed_quote_tweet_check", lane="unmatched lane"),
    ]
    scanned = _scan_reply_receipt_lifecycle(events)
    assert scanned.pending_reconciliations == [
        (("mention", "101", "901"), first),
        (("quote_tweet", "101", "901"), quote),
    ]
    assert scanned.pending_reconciliations[0][1] is first
    assert scanned.pending_reconciliations[1][1] is quote
    assert scanned.pending_confirmed[("mention", "101", "901")] == 1
    assert scanned.pending_confirmed[("mention", "102", "902")] == 0
    assert scanned.normal_reply_pairs == 1
    result = summary(events)
    assert [row["kind"] for row in result["unresolved_reply_receipts"]] == [
        "reconciliation_unresolved", "reconciliation_unresolved",
    ]
    events.append(receipt("replay_suppressed_quote_tweet_check", lane="quote_tweet"))
    assert _scan_reply_receipt_lifecycle(events).pending_reconciliations == [
        (("mention", "101", "901"), first),
    ]


@pytest.mark.parametrize("kind,count_field", [
    ("removed", "terminal_reply_removals_outside_window"),
    ("sending_removed", "definite_non_success_clears"),
    ("confirmed_state_fallback_removed", "confirmed_state_fallback_clears"),
])
def test_terminal_events_count_when_the_opening_event_is_outside_the_window(kind, count_field):
    events = [receipt(kind), receipt(kind)]
    result = summary(events)
    assert result[count_field] == 2
    assert result["normal_reply_pairs"] == 0
    assert result["unresolved_reply_receipts"] == []
    errors = []
    append_unresolved_reply_receipt_errors(confirmed_reply_receipts=events, errors=errors)
    assert errors == []


def test_error_and_render_projections_keep_distinct_source_and_receipt_scopes():
    production_sending = receipt("sending", source_class="production")
    events = [
        production_sending,
        receipt("sending_removed", source_class="selftest"),
        receipt("sending", lane="quote_tweet", target="102", source_class="selftest"),
        receipt("written", target="103"),
    ]
    errors = []
    append_unresolved_reply_receipt_errors(confirmed_reply_receipts=events, errors=errors)
    assert len(errors) == 1
    assert errors[0]["time"] == production_sending["time"]
    assert "lane=mention target_id=101" in errors[0]["message"]
    result = summary(events)
    assert result["definite_non_success_clears"] == 1
    assert [(row["kind"], row["target_id"]) for row in result["unresolved_reply_receipts"]] == [
        ("sending_unresolved", "102"), ("confirmed_unresolved", "103"),
    ]


def test_error_projection_keeps_sorted_sending_then_reconciliation_order_and_shallow_refs():
    reference = {"record_number": 1}
    refs = [reference]
    events = [
        receipt("reconciled", lane="quote_tweet", time="reconciliation first", source_refs=refs),
        receipt("sending", lane="quote_tweet", time="quote sending", source_refs=refs),
        receipt("sending", target="102", time="mention second", source_refs=refs),
        receipt("sending", time="mention first", source_refs=refs),
        receipt("reconciled", time="reconciliation last", source_refs=refs),
    ]
    previous = {"existing": True}
    errors = [previous]
    append_unresolved_reply_receipt_errors(confirmed_reply_receipts=events, errors=errors)
    assert errors[0] is previous
    assert [row["time"] for row in errors[1:]] == [
        "mention first", "mention second", "quote sending", "reconciliation first", "reconciliation last",
    ]
    for row in errors[1:]:
        assert row["source_refs"] is not refs
        assert row["source_refs"][0] is reference
        assert row["level"] == "CRITICAL"
        assert row["where"] == "confirmed_reply_receipt_lifecycle"


def test_recovery_evidence_consumes_exact_observations_once_with_reconciled_priority():
    refs = [{"record_number": 1}]
    events = [
        receipt("sending", lane="mention_reply", time="same", source_refs=refs),
        receipt("sending", lane="mention_reply", time="same", source_refs=refs),
        receipt("sending", lane="mention_reply", time="same", source_refs=refs),
        receipt("sending", lane="mention_reply", time="different", source_refs=refs),
    ]
    evidence = {"lane": "mention", "target_id": "101", "source_time": "same"}
    reconciled = [evidence, {}, "malformed evidence", {**evidence, "target_id": "other"}]
    unavailable = [dict(evidence), {**evidence, "source_time": "absent"}]
    before = copy.deepcopy((events, reconciled, unavailable))
    result = summary(events, reconciled_ambiguity_receipts=reconciled, unavailable_receipts=unavailable)
    assert result["reconciled_ambiguity_sending_receipts"] == 1
    assert [(row["kind"], row["time"]) for row in result["unavailable_reply_receipt_rows"]] == [
        ("current_status_unavailable", "same"),
    ]
    assert [(row["kind"], row["time"]) for row in result["unresolved_reply_receipts"]] == [
        ("sending_unresolved", "same"), ("sending_unresolved", "different"),
    ]
    for row in result["unavailable_reply_receipt_rows"] + result["unresolved_reply_receipts"]:
        assert row["source_refs"] is refs
        assert all(row is not source for source in events)
    assert (events, reconciled, unavailable) == before


def test_unmatched_rows_keep_identity_and_duplicate_confirmations_keep_latest_source():
    unknown = receipt("unknown event", message="Retain this observation")
    old = receipt("written", time="old")
    latest = receipt("written", time="latest", source_refs=[{"record_number": 2}])
    reconciled = receipt("reconciled", time="reconciliation")
    result = summary([old, unknown, latest, reconciled])
    rows = result["unresolved_reply_receipts"]
    assert rows[0] is unknown
    assert [(row["kind"], row["time"]) for row in rows[1:]] == [
        ("reconciliation_unresolved", "reconciliation"), ("confirmed_unresolved", "latest"),
    ]
    assert rows[2]["source_refs"] is latest["source_refs"]
    assert summary([]) == {
        "normal_reply_pairs": 0,
        "terminal_reply_removals_outside_window": 0,
        "definite_non_success_clears": 0,
        "confirmed_state_fallback_clears": 0,
        "reconciled_ambiguity_sending_receipts": 0,
        "unresolved_reply_receipts": [],
        "unavailable_reply_receipt_rows": [],
    }

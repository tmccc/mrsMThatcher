"""Check the incident extraction's delegation, clocks and object boundaries."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import mrs_log_digest as digest
from tests.test_mrs_log_digest import BASE, reconciled_remote_write_safety


def test_classifier_keeps_current_helpers_and_conditional_call_order(monkeypatch):
    calls = []

    def exception_line(message):
        calls.append(("exception", message))
        return "CustomError: detail"

    def restriction(message):
        calls.append(("restriction", message))
        return message == "handled"

    def normalise(message):
        calls.append(("normalise", message))
        return "custom_signature: detail"

    monkeypatch.setattr(digest, "_incident_exception_line", exception_line)
    monkeypatch.setattr(digest, "is_deleted_or_inaccessible_tweet_403", restriction)
    monkeypatch.setattr(digest, "_normalise_incident_text", normalise)
    assert digest.classify_operational_error("X API error 429") == "x_api_rate_limit"
    assert digest.classify_operational_error("handled") == "deleted_or_inaccessible_tweet"
    assert digest.classify_operational_error("unclassified") == "custom_signature"
    assert calls == [
        ("exception", "X API error 429"),
        ("exception", "handled"), ("restriction", "handled"),
        ("exception", "unclassified"), ("restriction", "unclassified"),
        ("normalise", "customerror: detail"),
    ]


def test_pause_helpers_keep_dynamic_scope_maps_and_nested_delegation(monkeypatch):
    monkeypatch.setattr(digest, "REMOTE_CONTROL_SCOPE_BY_KEY", {"pause_replies": "custom"})
    monkeypatch.setattr(digest, "REMOTE_LANE_SCOPE", {"mention_reply": "custom_lane"})
    assert digest._base_remote_control_key(" PAUSE_REPLIES_UNTIL ") == "pause_replies"
    assert digest._remote_control_scope("pause_replies_until") == "custom"
    assert digest._remote_operation_scope_for_lane("Mention-Reply") == "custom_lane"
    calls = []

    def base_key(value):
        calls.append(("key", value))
        return "pause_replies"

    monkeypatch.setattr(digest, "_base_remote_control_key", base_key)
    assert digest._remote_control_scope("arbitrary") == "custom"

    def scope(value):
        calls.append(("scope", value))
        return "custom_scope"

    def lane(value):
        calls.append(("lane", value))
        return "custom_lane"

    monkeypatch.setattr(digest, "_remote_control_scope", scope)
    monkeypatch.setattr(digest, "_remote_operation_scope_for_lane", lane)
    assert digest._explicit_remote_pause_scope("pause_replies_until") == (
        "custom_scope", "explicit control key pause_replies_until", ["pause_replies"],
    )
    assert digest._explicit_remote_pause_scope("lane=mention") == (
        "custom_lane", "explicit lane mention in the exception", [],
    )
    assert calls == [
        ("key", "arbitrary"), ("key", "pause_replies_until"),
        ("scope", "pause_replies"), ("lane", "mention"),
    ]


@pytest.mark.parametrize("explicit_generation", [False, True])
@pytest.mark.parametrize("conversion", ["identified_audit", "legacy_audit", "snapshot"])
def test_incident_clocks_stay_at_conditional_sites_after_mutation(
    monkeypatch, explicit_generation, conversion,
):
    calls = []
    samples = iter([BASE + timedelta(hours=1), BASE + timedelta(hours=2)])
    safety = reconciled_remote_write_safety()
    safety["observed_at"] = digest.dt_text(BASE + timedelta(minutes=10))
    evidence = {
        "category": "remote_write_transaction_barrier", "signature": "snapshot:test",
        "summary": "supplied blocker", "recorded_at_epoch": int(BASE.timestamp()),
        "artifact_names": ["fixture.json"],
    }
    errors = [{"level": "ERROR", "message": "remote-write protocol is not activated"}]
    if conversion == "snapshot":
        safety["identity_snapshot_available"] = True
        safety["snapshot_incident_evidence"] = [evidence]
    else:
        errors.append({
            "level": "CRITICAL", "time": digest.dt_text(BASE),
            "message": "ambiguous remote X post outcome" + (
                " lane=mention target_id=123" if conversion == "identified_audit" else ""
            ),
        })
        if conversion == "identified_audit":
            safety["identity_snapshot_available"] = True
            safety["reconciliation_archive"]["marker_reconciliations"][0]["target_id"] = "123"
    epoch = (
        evidence["recorded_at_epoch"] if conversion == "snapshot" else
        safety["reconciliation_archive"]["marker_reconciliations"][0]["archived_at_epoch"]
    )

    class Clock(datetime):
        @classmethod
        def now(cls):
            calls.append("now")
            return next(samples)

        @classmethod
        def fromtimestamp(cls, value):
            calls.append(("fromtimestamp", value))
            return datetime.fromtimestamp(value)

    def observations(label):
        calls.append(label)
        yield from ()

    original_annotation = digest.annotate_remote_write_snapshot_window

    def annotate(value, window, **kwargs):
        assert value is safety
        assert window is BASE
        calls.append("annotate")
        return original_annotation(value, window, **kwargs)

    shared_refs = [{"record_number": 7}]

    def references(*values):
        calls.append("refs")
        return shared_refs, 2

    monkeypatch.setattr(digest, "datetime", Clock)
    monkeypatch.setattr(digest, "annotate_remote_write_snapshot_window", annotate)
    monkeypatch.setattr(digest, "bounded_source_refs", references)
    result = digest.summarise_operational_error_health(
        errors, [], [], lifecycle=observations("lifecycle"),
        remote_write_transactions=observations("transactions"),
        handled_api_restrictions=observations("restrictions"),
        confirmed_reply_receipt_events=observations("receipts"),
        current_remote_write_safety=safety, selected_window_end=BASE,
        generation_time=BASE if explicit_generation else None,
    )
    assert calls == [
        *([] if explicit_generation else ["now"]),
        "lifecycle", "transactions", "restrictions", "receipts", "annotate",
        *([("fromtimestamp", epoch)] if conversion == "snapshot" else []),
        "now", "refs", ("fromtimestamp", epoch),
        *([] if conversion == "snapshot" else ["refs"]),
    ]
    protocol = next(item for item in result["historical_resolved_incidents"]
                    if item["category"] == "remote_write_protocol_barrier")
    assert protocol["first_seen"] == digest.dt_text(datetime.min)
    assert protocol["resolution_time"] == digest.dt_text(
        BASE + timedelta(hours=1 if explicit_generation else 2)
    )
    assert protocol["source_refs"] is shared_refs
    assert protocol["source_ref_omitted_count"] == 2
    assert safety["selected_window_end"] == digest.dt_text(BASE)
    assert safety["current_health_snapshot_authoritative"] is False
    if conversion == "snapshot":
        assert safety["snapshot_incident_evidence"][0] is evidence
        assert evidence["selected_window_relationship"] == "recorded_at_or_before_selected_window_end"
        assert result["current_incidents"][0]["artifact_names"] is evidence["artifact_names"]
    elif conversion == "identified_audit":
        assert errors[1]["_remote_write_identity"]["target_id"] == "123"


def test_summary_uses_current_helpers_original_error_objects_and_reference_result(monkeypatch):
    references = [{"record_number": 1}]
    first = {"level": "ERROR", "time": digest.dt_text(BASE), "message": "first",
             "source_refs": references}
    second = {**first, "time": digest.dt_text(BASE + timedelta(seconds=30)), "message": "second"}
    seen = []
    distances = []
    original_time = digest._event_time

    def event_time(value):
        seen.append(value)
        return original_time(value)

    def distance(a, b):
        distances.append((a, b))
        return 0

    def bounded(*values):
        assert len(values) == 2 and all(value is references for value in values)
        return references, 3

    monkeypatch.setattr(digest, "_event_time", event_time)
    monkeypatch.setattr(digest, "seconds_between", distance)
    monkeypatch.setattr(digest, "bounded_source_refs", bounded)
    monkeypatch.setattr(digest, "classify_operational_error", lambda value: "remote_write_ambiguity_barrier")
    result = digest.summarise_operational_error_health([first, second], [], [], generation_time=BASE)
    assert len(result["current_incidents"]) == 1
    assert result["current_incidents"][0]["record_count"] == 2
    assert result["current_incidents"][0]["source_refs"] is references
    assert result["current_incidents"][0]["source_ref_omitted_count"] == 3
    assert seen and all(value is first or value is second for value in seen)
    assert distances == [(BASE + timedelta(seconds=30), BASE)]


def test_epoch_callback_failure_preserves_prior_snapshot_mutation(monkeypatch):
    safety = {"configured": True, "available": True, "identity_snapshot_available": True}
    evidence = {"category": "remote_write_transaction_barrier", "signature": "snapshot:test",
                "summary": "supplied blocker", "recorded_at_epoch": 1}
    failure = OverflowError("epoch callback failed")

    class Clock(datetime):
        @classmethod
        def fromtimestamp(cls, value):
            assert value == 1
            assert safety["snapshot_incident_evidence"][0] is evidence
            raise failure

    def annotate(value, window, **kwargs):
        assert value is safety
        value["snapshot_incident_evidence"] = [evidence]

    monkeypatch.setattr(digest, "datetime", Clock)
    monkeypatch.setattr(digest, "annotate_remote_write_snapshot_window", annotate)
    with pytest.raises(OverflowError) as caught:
        digest.summarise_operational_error_health(
            [], [], [], generation_time=BASE, current_remote_write_safety=safety,
            current_snapshot_authoritative=True,
        )
    assert caught.value is failure
    assert safety["snapshot_incident_evidence"][0] is evidence


@pytest.mark.parametrize("authoritative", [False, True])
def test_snapshot_window_annotation_preserves_retirement_evidence_identity(authoritative):
    safety = reconciled_remote_write_safety()
    safety["identity_snapshot_available"] = True
    safety["observed_at"] = digest.dt_text(BASE + timedelta(minutes=10))
    identity = {"source_basename": "reply.json"}
    component = {
        "artifact_kinds": ["receipt_retirement_auxiliary"],
        "artifact_names": ["reply.json.retiring"],
        "retirement_source_basenames": ["reply.json"],
        "retirement_expected_sha256s": ["a" * 64],
        "retirement_phases": ["tombstone"],
        "receipt_roles": ["conversational_confirmed_reply"],
        "receipt_role_labels": ["conversational confirmed reply"],
        "retirement_source_identities": [identity],
        "recorded_at_epoch": int((BASE + timedelta(minutes=2)).timestamp()),
    }
    ledger = {
        "category": "remote_write_transaction_barrier", "blocker_kind": "retirement_ledger",
        "signature": "snapshot:ledger", "summary": "interrupted exchange",
        "ledger_state": "exchange_staged",
        "retirement_source_basenames": ["reply.json"],
        "retirement_expected_sha256s": ["a" * 64],
        "recorded_at_epoch": component["recorded_at_epoch"],
    }
    safety["active_transaction_identities"] = [component]
    safety["snapshot_incident_evidence"] = [ledger]
    result = digest.summarise_operational_error_health(
        [], [], [], generation_time=BASE, selected_window_end=BASE,
        current_remote_write_safety=safety, current_snapshot_authoritative=authoritative,
    )
    assert safety["active_transaction_identities"][0] is component
    assert safety["snapshot_incident_evidence"][0] is ledger
    for item in (component, ledger):
        assert item["selected_window_relationship"] == "recorded_after_selected_window_end"
        assert item["current_health_relationship"] == (
            "authoritative_current_snapshot" if authoritative else "recorded_after_selected_window_end"
        )
    assert result["current_independent_incident_count"] == int(authoritative)
    if authoritative:
        incident = result["current_incidents"][0]
        assert incident["snapshot_only"] is True
        assert incident["blocker_kinds"] == ["receipt_retirement", "retirement_ledger"]
        assert incident["retirement_source_identities"] is component["retirement_source_identities"]
        assert incident["retirement_source_identities"][0] is identity
        assert "blocker_kinds" not in component

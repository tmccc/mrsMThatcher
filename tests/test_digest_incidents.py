"""Check the incident extraction's delegation, clocks and object boundaries."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

import mrs_log_digest as digest
import mrs_log_digest_api_health as api_health_owner
import mrs_log_digest_incidents as incident_owner
import mrs_log_digest_snapshot_incidents as snapshot_owner
from tests.test_mrs_log_digest import BASE, record, reconciled_remote_write_safety


@pytest.mark.parametrize("algorithm", ["pipeline", "pause"])
def test_recovery_adapters_forward_prepared_rows_and_current_callbacks(monkeypatch, algorithm):
    later = BASE + timedelta(seconds=10)
    event = {"kind": "reply_strategy_failure", "lane": "mention", "target_id": "123", "_time": BASE}
    success = {"kind": "mention_reply_posted", "_time": later}
    lifecycle_row = {"message": "fixture lifecycle", "_time": BASE}
    events, safety, calls = [event, success], {"available": False}, []
    errors = [] if algorithm == "pipeline" else [
        {"level": "ERROR", "message": "RemoteOperationsPaused: pause_replies", "_time": BASE},
    ]
    current_time = lambda row: row.get("_time")
    helpers = {name: getattr(digest, name) for name in (
        "_base_remote_control_key", "_remote_control_scope", "_explicit_remote_pause_scope",
    )}

    def observations():
        calls.append("lifecycle")
        yield lifecycle_row

    def pipeline(identity, last_time, **inputs):
        assert identity == ("mention", "123") and last_time is BASE
        assert inputs == {"events": events, "get_event_time": current_time}
        assert inputs["events"] is events
        calls.append("pipeline")
        return True, "supplied recovery", later

    def pause(scope, control_keys, last_time, **inputs):
        assert scope == "all_replies" and control_keys == ["pause_replies"]
        assert last_time is BASE and inputs["safety"] is safety
        assert inputs["events"] is events and inputs["get_event_time"] is current_time
        assert len(inputs["lifecycle"]) == 1 and inputs["lifecycle"][0] is lifecycle_row
        assert inputs["remote_operation_successes"] == [{
            "time": later, "kind": "mention_reply_posted",
            "scopes": {"normal_replies", "all_replies", "all_remote_writes"},
        }]
        assert inputs["remote_operation_successes"][0]["time"] is later
        for name, helper in helpers.items():
            assert inputs[name.removeprefix("_")] is helper
        calls.append("pause")
        return "historical_resolved", "supplied recovery", later

    def annotate(value, *_args, **_kwargs):
        assert value is safety
        calls.append("annotate")
        # The adapters must receive the already supplied callback even if the
        # facade changes after preparation has started.
        monkeypatch.setattr(digest, "_event_time", lambda row: None)
        monkeypatch.setattr(incident_owner, "_pipeline_recovered_after", pipeline)
        monkeypatch.setattr(incident_owner, "_remote_pause_recovery_status", pause)

    if algorithm == "pause":
        events.remove(event)
    monkeypatch.setattr(digest, "_event_time", current_time)
    monkeypatch.setattr(digest, "annotate_remote_write_snapshot_window", annotate)
    result = digest.summarise_operational_error_health(
        errors, events, [], lifecycle=observations(), current_remote_write_safety=safety,
        generation_time=later,
    )
    assert calls == ["lifecycle", "annotate", algorithm]
    incident = result["historical_resolved_incidents"][0]
    assert incident["resolution_reason"] == "supplied recovery"
    assert incident["resolution_time"] == digest.dt_text(later)


def test_pipeline_recovery_keeps_live_rows_owner_helpers_and_reason_tie(monkeypatch):
    later = BASE + timedelta(seconds=10)
    seen, calls = [], []

    def row(name, **values):
        return {"name": name, "kind": "reply_strategy_decision", "lane": "mention",
                "target_id": "123", "_time": later, **values}

    events = [
        row("equal", _time=BASE), row("missing", _time=None),
        row("lane", lane="hot-post"), row("target", target_id="999"),
        row("failure", mode="no_reply", reason="revision_limit_reached"),
        row("local", reason="exact_duplicate_reply", no_reply_reason="unused"),
        row("no_reply", mode="no_reply", no_reply_reason="independent_no_reply_confirmed"),
        row("rejection", kind="reply_strategy_local_rejection", reason="near_duplicate_reply"),
    ]
    outcome = row("outcome", kind="reply_strategy_outcome")

    def reject(*args):
        raise AssertionError("unexpected facade helper lookup")

    for name in ("_normalise_lane", "_is_terminal_pipeline_failure", "_terminal_local_rejection_outcome"):
        helper = getattr(incident_owner, name)

        def observed(*args, name=name, helper=helper):
            calls.append((name, args))
            return helper(*args)

        monkeypatch.setattr(incident_owner, name, observed)
        monkeypatch.setattr(digest, name, reject)

    def event_time(value):
        seen.append(value)
        if value is events[0]:
            events.append(outcome)
        return value["_time"]

    result = incident_owner._pipeline_recovered_after(
        ("mention", "123"), BASE, events=events, get_event_time=event_time,
    )
    assert result == (True, "later confirmed reply outcome observed for mention target 123", later)
    assert type(result) is tuple and type(result[0]) is bool and result[2] is later
    assert len(seen) == len(events) and all(a is b for a, b in zip(seen, events))
    assert calls == [
        ("_normalise_lane", ("hot-post",)), ("_normalise_lane", ("mention",)),
        ("_normalise_lane", ("mention",)), ("_is_terminal_pipeline_failure", ("revision_limit_reached", None)),
        ("_normalise_lane", ("mention",)), ("_is_terminal_pipeline_failure", ("exact_duplicate_reply", None)),
        ("_terminal_local_rejection_outcome", ("exact_duplicate_reply",)),
        ("_normalise_lane", ("mention",)), ("_is_terminal_pipeline_failure", ("independent_no_reply_confirmed", None)),
        ("_terminal_local_rejection_outcome", (None,)),
        ("_terminal_local_rejection_outcome", ("independent_no_reply_confirmed",)),
        ("_normalise_lane", ("mention",)), ("_terminal_local_rejection_outcome", ("near_duplicate_reply",)),
        ("_normalise_lane", ("mention",)),
    ]


def test_remote_pause_recovery_unknown_scope_does_not_inspect_inputs():
    def reject(*args):
        raise AssertionError("unknown scope must short-circuit")

    class Unreadable:
        get = __iter__ = reject

    unreadable = Unreadable()
    result = incident_owner._remote_pause_recovery_status(
        "unknown", unreadable, BASE, safety=unreadable, events=unreadable,
        lifecycle=unreadable, remote_operation_successes=unreadable,
        base_remote_control_key=reject, remote_control_scope=reject,
        explicit_remote_pause_scope=reject, get_event_time=reject,
    )
    assert result == (
        "resolution_unavailable",
        "affected pause scope is unavailable from retained evidence; unrelated remote-write success cannot establish recovery",
        None,
    )


@pytest.mark.parametrize("available,valid,keys,resolved", [
    (True, True, [" PAUSE_HOT_POST_REPLIES_UNTIL "], True),
    (True, True, ["unrecognised"], False),
    (False, True, ["pause_hot_post_replies"], False),
    (True, False, ["pause_hot_post_replies"], False),
])
def test_remote_pause_recovery_keeps_conditional_control_hierarchy(available, valid, keys, resolved):
    calls, later = [], BASE + timedelta(seconds=10)

    def normalise(value):
        calls.append(("key", value))
        return digest._base_remote_control_key(value)

    def scope(value):
        calls.append(("scope", value))
        return digest._remote_control_scope(value)

    result = incident_owner._remote_pause_recovery_status(
        "hot_post_replies", keys, BASE,
        safety={"available": available, "control": {"valid": valid, "active_keys": ["pause_replies"]}},
        events=[], lifecycle=[],
        remote_operation_successes=[{"time": later, "kind": "hot_post_reply_posted", "scopes": {"hot_post_replies"}}],
        base_remote_control_key=normalise, remote_control_scope=scope,
        explicit_remote_pause_scope=digest._explicit_remote_pause_scope, get_event_time=digest._event_time,
    )
    assert result == ((
        "historical_resolved",
        "the affected control scope cleared and later successful hot post reply posted occurred in the same scope",
        later,
    ) if resolved else ("current_unresolved", "", None))
    assert calls == [("key", keys[0]), *([("scope", "pause_replies")] if available and valid else [])]
    if resolved:
        assert result[2] is later


def test_remote_pause_recovery_keeps_clear_expansion_callback_order_and_success_tie():
    early, later = BASE + timedelta(seconds=1), BASE + timedelta(seconds=3)
    equal = {"kind": "runtime_control_clear", "key": "pause_replies", "_time": BASE}
    wrong = {"kind": "runtime_control_clear", "key": "pause_meme_posts", "_time": early}
    missing = {"kind": "runtime_control_clear", "key": "pause_replies", "_time": None}
    legacy = {"message": "Runtime control pause cleared pause_replies", "_time": later}
    appended = {"kind": "runtime_control_clear", "key": "pause_replies", "_time": early}
    events, lifecycle, calls, seen = [equal, wrong, missing], [legacy], [], []
    successes = [
        {"time": later, "kind": "z_success", "scopes": {"all_replies"}},
        {"time": early, "kind": "before_clear", "scopes": {"all_replies"}},
        {"time": later, "kind": "a_success", "scopes": {"all_replies"}},
        {"time": early, "kind": "wrong_scope", "scopes": {"daily_meme_posts"}},
        {"time": BASE, "kind": "equal_time", "scopes": {"all_replies"}},
    ]

    def event_time(value):
        seen.append(value)
        calls.append("time")
        if value is equal:
            events.append(appended)
        return value["_time"]

    def scope(value):
        calls.append(("scope", value))
        return digest._remote_control_scope(value)

    def explicit(value):
        calls.append(("explicit", value))
        return digest._explicit_remote_pause_scope(value)

    result = incident_owner._remote_pause_recovery_status(
        "all_replies", [], BASE, safety={"available": False}, events=events, lifecycle=lifecycle,
        remote_operation_successes=successes, base_remote_control_key=digest._base_remote_control_key,
        remote_control_scope=scope, explicit_remote_pause_scope=explicit, get_event_time=event_time,
    )
    assert result == (
        "historical_resolved",
        "the affected control scope cleared and later successful a success occurred in the same scope",
        later,
    )
    assert result[2] is later and events[-1] is appended
    assert len(seen) == 4 and all(a is b for a, b in zip(seen, [equal, wrong, missing, legacy]))
    assert calls == [
        "time", ("scope", "pause_replies"), "time", ("scope", "pause_meme_posts"),
        "time", ("scope", "pause_replies"), "time", ("explicit", legacy["message"]),
    ]
    assert [row["kind"] for row in successes] == [
        "z_success", "before_clear", "a_success", "wrong_scope", "equal_time",
    ]


@pytest.mark.parametrize("available", [False, True])
def test_snapshot_reconciliation_receives_prepared_references_and_current_helpers(monkeypatch, available):
    component = {
        "artifact_kinds": ["ambiguity_marker"], "artifact_names": ["marker.json"],
        "document_sha256s": ["a" * 64], "recorded_at_epoch": int(BASE.timestamp()),
        "selected_window_relationship": "recorded_at_or_before_selected_window_end",
    }
    components, evidence = [component], []
    safety = {"configured": True, "available": available, "identity_snapshot_available": True}
    calls, boundaries, formatted = [], [], []
    original = snapshot_owner.reconcile_current_snapshot_incidents
    original_time, original_short = incident_owner.dt_text, incident_owner.short

    def annotate(value, window, **kwargs):
        assert value is safety and window is BASE
        calls.append("annotate")
        value["active_transaction_identities"] = components
        value["snapshot_incident_evidence"] = evidence

    def time_text(value):
        formatted.append(value)
        return original_time(value)

    def short(value, limit):
        calls.append("short")
        return original_short(value, limit)

    def reconcile(incidents, **kwargs):
        assert kwargs["identity_snapshot_available"] is available
        assert kwargs["active_remote_components"] is components
        assert kwargs["snapshot_incident_evidence"] is evidence
        assert kwargs["safety"] is safety
        assert kwargs["dt_text"] is time_text and kwargs["short"] is short
        assert kwargs["get_event_time"] is digest._event_time
        assert kwargs["fromtimestamp"] == digest.datetime.fromtimestamp
        assert kwargs["component_is_related_to_selected_window"](component) is True
        assert kwargs["component_matches_identity"](component, {"document_sha256s": ["a" * 64]}) is True
        calls.append("reconcile")
        existing = incidents[0]
        assert original(incidents, **kwargs) is None
        assert incidents[0] is existing
        if available:
            assert incidents[1]["artifact_names"] is component["artifact_names"]
        boundaries.extend(incidents)

    monkeypatch.setattr(digest, "annotate_remote_write_snapshot_window", annotate)
    monkeypatch.setattr(incident_owner, "dt_text", time_text)
    monkeypatch.setattr(incident_owner, "short", short)
    monkeypatch.setattr(incident_owner, "reconcile_current_snapshot_incidents", reconcile)
    result = digest.summarise_operational_error_health(
        [{"level": "ERROR", "message": "Unrelated fixture error", "time": original_time(BASE + timedelta(seconds=1))}],
        [], [], generation_time=BASE, selected_window_end=BASE,
        current_remote_write_safety=safety,
    )
    assert calls.index("annotate") < calls.index("reconcile")
    expected = boundaries[::-1] if available else boundaries
    assert all(a is b for a, b in zip(result["current_incidents"], expected))
    assert len(result["current_incidents"]) == 1 + int(available)
    if available:
        assert calls[-1] == "short" and formatted.count(BASE) >= 2
    assert safety["active_transaction_identities"] is components
    assert safety["snapshot_incident_evidence"] is evidence


@pytest.mark.parametrize("case", [
    "unavailable", "unrelated", "matched", "epoch", "fallback", "missing_time",
    "epoch_error", "fallback_error",
])
def test_snapshot_reconciliation_keeps_lazy_time_callbacks_and_shared_evidence(case):
    calls = []
    failure = OverflowError("snapshot conversion failed")

    class Safety(dict):
        def get(self, key, default=None):
            calls.append(("safety", key))
            return super().get(key, default)

    safety = Safety(observed_at="before matching")
    evidence = {
        "category": "remote_write_transaction_barrier", "signature": "snapshot:new",
        "summary": "supplied blocker", "artifact_names": ["original.json"],
        "recorded_at_epoch": 7 if case.startswith("epoch") else True,
        "retirement_source_identities": [{"source_basename": "reply.json"}],
    }
    existing = {"category": evidence["category"], "signature": "existing"}
    incidents, supplied = [existing], [evidence]

    def related(value):
        assert value is evidence
        calls.append("window")
        return case != "unrelated"

    def matches(value, item):
        assert value is not evidence and item is existing
        assert value["artifact_names"] is evidence["artifact_names"]
        value["artifact_names"].append("callback.json")
        safety["observed_at"] = "after matching"
        calls.append("match")
        return case == "matched"

    def epoch(value):
        assert value == 7 and type(value) is int
        calls.append("epoch")
        if case == "epoch_error":
            raise failure
        return BASE

    def event_time(value):
        assert value == {"time": "after matching"}
        calls.append("fallback")
        if case == "fallback_error":
            raise failure
        return None if case == "missing_time" else BASE

    def time_text(value):
        assert value is BASE
        calls.append("format")
        return "formatted time"

    def reject(*args):
        pytest.fail("unexpected text formatting")

    def run():
        return snapshot_owner.reconcile_current_snapshot_incidents(
            incidents, identity_snapshot_available=case != "unavailable",
            active_remote_components=[], snapshot_incident_evidence=supplied, safety=safety,
            component_is_related_to_selected_window=related, component_matches_identity=matches,
            fromtimestamp=epoch, get_event_time=event_time, dt_text=time_text, short=reject,
        )

    if case.endswith("_error"):
        with pytest.raises(OverflowError) as caught:
            run()
        assert caught.value is failure
    else:
        assert run() is None
    expected = [] if case == "unavailable" else ["window"]
    if case not in {"unavailable", "unrelated"}:
        expected.append("match")
        assert evidence["artifact_names"] == ["original.json", "callback.json"]
        if case != "matched":
            expected += ["epoch"] if case.startswith("epoch") else [("safety", "observed_at"), "fallback"]
        if case in {"epoch", "fallback"}:
            expected += ["format", "format"]
    assert calls == expected
    assert incidents[0] is existing and supplied[0] is evidence
    assert "transaction_ids" not in evidence
    if case in {"epoch", "fallback"}:
        assert len(incidents) == 2
        assert incidents[1]["artifact_names"] is evidence["artifact_names"]
        assert incidents[1]["retirement_source_identities"] is evidence["retirement_source_identities"]
        assert incidents[1]["first_seen"] == incidents[1]["last_seen"] == "formatted time"
    else:
        assert incidents == [existing]
    if case == "matched":
        assert existing["active_artifact_names"] == ["callback.json", "original.json"]
        assert existing["active_artifact_count"] == 2


@pytest.mark.parametrize("failure_at", [None, "rejection", "fingerprint", "classify"])
def test_error_observation_keeps_clarification_order_and_shared_pause_inputs(failure_at):
    row = record(0, "WARNING", "normal_reply", (
        "Clarification reply lacks direct_factual_answer mode; refusing target_id=410"
    ))
    errors, selftests, posts, replies, calls = [], [], [], [], []
    pending = {"source": "mention", "incoming_text": "incoming"}
    indexes, reference = {row.path: 7}, {"record_number": 1}
    failure = RuntimeError("observation callback failed")

    def note(name):
        calls.append(name)
        if name == failure_at:
            raise failure

    def eligibility(message):
        assert message is row.msg
        note("eligibility")
        return False

    def deleted(message):
        assert message is row.msg
        note("deleted")
        return False

    def rejection(ts, **fields):
        assert ts is row.ts and not errors
        assert fields == {
            "lane": "mention", "target_id": "410",
            "reason": "clarification_not_direct_factual_answer",
            "original_local_rejection_reason": "clarification_not_direct_factual_answer",
            "pipeline_stage_status": "approved", "effective_status": "local_rejection",
            "effective_reason": "clarification_not_direct_factual_answer",
            "direct_answer_repair_attempted": False,
            "direct_answer_repair_outcome": "not_available_legacy_telemetry",
            "incoming_contribution": "incoming", "proposed_draft": None,
            "repaired_draft": None,
        }
        note("rejection")
        return {"ignored": True}

    def shorten(message, limit):
        assert message is row.msg and limit == 900
        note("short")
        return "display"

    def fingerprint(value):
        assert value is row
        note("fingerprint")
        return "fingerprint"

    def source_ref(value, supplied_indexes):
        assert value is row and supplied_indexes is indexes
        note("source")
        return reference

    def classify(message):
        assert message is row.msg and not errors
        pending["source"] = "hot_post"
        note("classify")
        return "remote_operations_paused"

    def observe():
        return digest.observe_error_warning(
            row, row.msg, self_test_errors=selftests, confirmed_post_recovery=posts,
            confirmed_reply_recovery=replies, errors=errors,
            pending_mention=pending, pending_qt={}, pending_meme={}, pending_quote={},
            is_reply_visual_description_event=False, input_file_indexes=indexes,
            is_reply_target_eligibility_restriction=eligibility,
            is_deleted_or_inaccessible_tweet_403=deleted,
            add_or_merge_local_rejection=rejection, short=shorten,
            record_source_ref=source_ref, record_fingerprint=fingerprint,
            classify_operational_error=classify,
        )

    expected = ["eligibility", "deleted", "rejection", "short", "fingerprint", "source", "classify"]
    if failure_at is not None:
        with pytest.raises(RuntimeError) as caught:
            observe()
        assert caught.value is failure
        assert calls == expected[:expected.index(failure_at) + 1]
        assert errors == []
    else:
        assert observe() == (False, False)
        assert calls == expected
        assert errors == [{
            "time": digest.dt_text(BASE), "level": "WARNING", "where": "normal_reply:1",
            "message": "display", "_raw_message": row.msg, "_fingerprint": "fingerprint",
            "source_refs": [reference], "_pause_pending_lane": "hot_post",
        }]
        assert errors[0]["source_refs"][0] is reference
    assert selftests == posts == replies == []


def test_error_observation_wiring_keeps_current_callbacks_sources_and_later_dispatch(monkeypatch):
    for name in ("is_reply_target_eligibility_restriction", "is_deleted_or_inaccessible_tweet_403"):
        assert getattr(digest, name) is getattr(api_health_owner, name)
    messages = [
        ("normal_reply", "Clarification reply lacks direct_factual_answer mode; refusing target_id=410"),
        ("normal_reply", "Clarification reply lacks direct_factual_answer mode; refusing target_id=420"),
        ("normal_reply", "RemoteOperationsPaused: global runtime control pause blocks remote operation"),
        ("assets", "Quote analysis unavailable"),
        ("x_request", "X API error 403: only reply to or quote posts where you are mentioned or are the author"),
        ("x_request", "X API error 503: fixture"),
        ("post_meme", "Confirmed meme post_id=900 requires local recovery"),
        ("normal_reply", "Confirmed reply receipt was applied in memory but state save failed"),
    ]
    rows = [record(i, "WARNING", src, msg) for i, (src, msg) in enumerate(messages)]
    rows[0] = replace(rows[0], path="selftest_fixture.log")
    inputs_seen, pairs, emissions, callback_calls, boundaries = [], [], [], [], []
    helper_names = (
        "is_reply_target_eligibility_restriction", "is_deleted_or_inaccessible_tweet_403",
        "short", "record_source_ref", "record_fingerprint", "classify_operational_error",
    )
    for name in helper_names:
        original = getattr(digest, name)

        def current(*args, _name=name, _original=original, **kwargs):
            callback_calls.append((_name, args))
            return _original(*args, **kwargs)

        monkeypatch.setattr(digest, name, current)

    original_rejection = digest._add_or_merge_local_rejection
    original_observe = digest.observe_error_warning
    original_provider = digest.observe_provider_message
    original_prepare = digest.prepare_api_health

    def rejection(*args, **kwargs):
        assert len(inputs_seen[-1]["errors"]) == len(emissions)
        result = original_rejection(*args, **kwargs)
        emissions.append(result)
        return result

    def observe(r, msg, **inputs):
        assert r is rows[len(inputs_seen)] and msg is r.msg
        assert all(inputs[name] is getattr(digest, name) for name in helper_names)
        inputs_seen.append(inputs)
        start = len(callback_calls)
        pair = original_observe(r, msg, **inputs)
        pairs.append(pair)
        boundaries.append(callback_calls[start:])
        return pair

    def provider(r, msg, **inputs):
        assert len(pairs) == rows.index(r) + 1
        assert inputs["pending_mention"] is inputs_seen[-1]["pending_mention"]
        assert inputs["pending_qt"] is inputs_seen[-1]["pending_qt"]
        return original_provider(r, msg, **inputs)

    def prepare(**inputs):
        assert id(emissions[0]) not in inputs["production_event_object_ids"]
        assert id(emissions[1]) in inputs["production_event_object_ids"]
        return original_prepare(**inputs)

    monkeypatch.setattr(digest, "_add_or_merge_local_rejection", rejection)
    monkeypatch.setattr(digest, "observe_error_warning", observe)
    monkeypatch.setattr(digest, "observe_provider_message", provider)
    monkeypatch.setattr(digest, "prepare_api_health", prepare)
    indexes = {"fixture.log": 0, "selftest_fixture.log": 1}
    report = digest.analyse(rows, generation_time=BASE + timedelta(minutes=1),
                            initial_pending_mention={"source": "hot_post", "incoming_text": "incoming"},
                            input_file_indexes=indexes)
    assert pairs == [(False, False)] * 3 + [(True, False), (False, True)] + [(False, False)] * 3
    assert [name for name, _ in boundaries[4]] == ["is_reply_target_eligibility_restriction"]
    shared = inputs_seen[0]
    for inputs in inputs_seen:
        for key in ("errors", "self_test_errors", "confirmed_post_recovery", "confirmed_reply_recovery",
                    "input_file_indexes", "add_or_merge_local_rejection"):
            assert inputs[key] is shared[key]
    assert inputs_seen[0]["pending_mention"] is not inputs_seen[1]["pending_mention"]
    assert inputs_seen[1]["pending_mention"] is inputs_seen[2]["pending_mention"]
    assert [item["lane"] for item in emissions] == ["unavailable", "hot_post"]
    assert all(any(item is event for event in report["events"]) for item in emissions)
    assert all("source_refs" not in item for item in emissions)
    assert [item["source_refs"][0]["input_file_index"] for item in shared["errors"][:2]] == [1, 0]
    assert shared["errors"][2]["_pause_pending_lane"] == "hot_post"
    assert report["main_post_recovery"]["confirmed_post_recovery"] is shared["confirmed_post_recovery"]
    assert report["confirmed_reply_recovery"]["warnings"] is shared["confirmed_reply_recovery"]
    assert report["api_health"]["handled_restrictions"][0]["restriction_kind"] == "reply_target_eligibility"
    assert report["summary"]["stats"]["asset_quote_metadata_warning"] == 1


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


@pytest.mark.parametrize("observed,window,authoritative,relationship", [
    ("before", BASE, False, "snapshot_observed_within_selected_window"),
    ("after", BASE, True, "snapshot_postdates_selected_window"),
    ("invalid", BASE, False, "unavailable"),
    ("before", None, True, "unavailable"),
])
def test_window_annotation_uses_current_converters_and_shared_rows(
    monkeypatch, observed, window, authoritative, relationship,
):
    class Epoch(int):
        pass

    shared = {"recorded_at_epoch": 1, "identity": {"shared": True}}
    later = {"recorded_at_epoch": 2}
    unknown = [{"recorded_at_epoch": value} for value in (True, 1.0, Epoch(1), None)]
    entries = [shared, *unknown, "ignored"]
    safety = {"observed_at": observed, "active_entries": entries,
              "active_transaction_identities": [later],
              "snapshot_incident_evidence": [shared]}
    calls = []

    class Clock(datetime):
        @classmethod
        def strptime(cls, value, fmt):
            assert safety["selected_window_end"] == (digest.dt_text(window) if window else None)
            assert "selected_window_relationship" not in safety
            calls.append(("parse", value, fmt))
            if value == "invalid":
                raise ValueError("unknown observation time")
            return BASE + timedelta(seconds=1 if value == "after" else -1)

        @classmethod
        def fromtimestamp(cls, value):
            calls.append(("epoch", value))
            assert safety["current_health_snapshot_authoritative"] is authoritative
            return BASE + timedelta(seconds=value - 1)

        @classmethod
        def now(cls):
            pytest.fail("annotation must not sample a clock")

    monkeypatch.setattr(digest, "datetime", Clock)
    assert digest.annotate_remote_write_snapshot_window(
        safety, window, current_snapshot_authoritative=authoritative,
    ) is None
    assert calls == [("parse", observed, "%Y-%m-%d %H:%M:%S"),
                     ("epoch", 1), ("epoch", 2), ("epoch", 1)]
    assert safety["selected_window_relationship"] == relationship
    assert safety["active_entries"] is entries
    assert safety["snapshot_incident_evidence"][0] is shared
    assert safety["active_transaction_identities"][0] is later
    assert shared["selected_window_relationship"] == (
        "recorded_at_or_before_selected_window_end" if window else "unavailable"
    )
    assert later["selected_window_relationship"] == (
        "recorded_after_selected_window_end" if window else "unavailable"
    )
    for row in unknown:
        assert row["selected_window_relationship"] == (
            "snapshot_observed_at_or_before_selected_window_end"
            if window and observed == "before" else "unavailable"
        )
    for row in (shared, later, *unknown):
        assert row["current_health_relationship"] == (
            "authoritative_current_snapshot" if authoritative else row["selected_window_relationship"]
        )


def test_window_annotation_preserves_conversion_exception_and_mutation_order(monkeypatch):
    first, failing = {"recorded_at_epoch": 1}, {"recorded_at_epoch": 2}
    safety = {"active_entries": [first, failing]}
    failure = TypeError("parse callback failed")

    class Clock(datetime):
        @classmethod
        def strptime(cls, *_args):
            raise failure

        @classmethod
        def fromtimestamp(cls, value):
            if value == 2:
                raise failure
            return BASE

    monkeypatch.setattr(digest, "datetime", Clock)
    with pytest.raises(TypeError) as caught:
        digest.annotate_remote_write_snapshot_window(safety, BASE)
    assert caught.value is failure
    assert list(safety) == ["active_entries", "selected_window_end"]
    failure = OverflowError("epoch callback failed")
    monkeypatch.setattr(Clock, "strptime", lambda *_args: BASE)
    with pytest.raises(OverflowError) as caught:
        digest.annotate_remote_write_snapshot_window(safety, BASE)
    assert caught.value is failure
    assert first["selected_window_relationship"] == "recorded_at_or_before_selected_window_end"
    assert failing == {"recorded_at_epoch": 2}
    assert safety["current_health_snapshot_authoritative"] is False

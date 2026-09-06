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


def test_grouping_and_pause_adapters_keep_current_inputs_result_identity_and_sequence(monkeypatch):
    pause = {"level": "ERROR", "message": "RemoteOperationsPaused: pause_replies", "time": digest.dt_text(BASE)}
    errors = [pause]
    failure = {"kind": "reply_strategy_failure", "lane": "mention", "target_id": "123", "time": digest.dt_text(BASE)}
    events, prepared, phases, lookups = [failure], {}, [], []
    keys = ["pause_replies"]
    pause_result = ("all_replies", "prepared scope", keys)
    grouping, recovery = incident_owner._group_operational_incidents, incident_owner._prepare_recovery_evidence
    helper_names = ("classify_operational_error", "_event_time", "seconds_between", "_incident_exception_line",
                    "_normalise_incident_text", "_explicit_remote_pause_scope", "_base_remote_control_key",
                    "_remote_control_scope", "_remote_operation_scope_for_lane")
    for name in helper_names:
        original = getattr(digest, name)
        monkeypatch.setattr(digest, name, lambda *args, _original=original, **kwargs: _original(*args, **kwargs))

    def capture_preparation(name, original):
        def capture(*args, **kwargs):
            phases.append(name)
            prepared[name] = original(*args, **kwargs)
            if name == "pipeline":
                prepared["operational"] = args[1]
            return prepared[name]
        return capture

    class IdentityMap(dict):
        def get(self, key, default=None):
            assert self is prepared["result"]
            lookups.append(key)
            return super().get(key, default)

    def pause_inputs(item, **inputs):
        phases.append("pause")
        assert item is pause and inputs["events"] is events
        assert inputs["transport_attempts"] is prepared["ambiguity"][1]
        for name in ("explicit_remote_pause_scope", "base_remote_control_key", "remote_control_scope", "remote_operation_scope_for_lane"):
            assert inputs[name] is getattr(digest, "_" + name)
        assert inputs["get_event_time"] is digest._event_time
        return pause_result

    def group_inputs(groups, operational, raw, times, stable, failures, **callbacks):
        phases.append("grouping")
        assert not groups and operational is prepared["operational"] and operational[0] is pause
        assert raw is prepared["pipeline"][1] and failures is prepared["pipeline"][0]
        assert times is prepared["ambiguity"][0] and "remote_operations_paused" in stable
        for name, facade_name in (("classify_operational_error", "classify_operational_error"), ("get_event_time", "_event_time"),
                                  ("seconds_between", "seconds_between"), ("incident_exception_line", "_incident_exception_line"),
                                  ("normalise_incident_text", "_normalise_incident_text")):
            assert callbacks[name] is getattr(digest, facade_name)
        result = grouping(groups, operational, raw, times, stable, failures, **callbacks)
        assert pause["_pause_control_keys"] is keys
        assert next(iter(result.values())) is next(iter(failures))
        prepared.update(groups=groups, result=IdentityMap(result))
        return prepared["result"]

    def recovery_inputs(*args, **kwargs):
        phases.append("recovery")
        assert list(prepared["groups"].values())[0][0] is pause
        return recovery(*args, **kwargs)

    monkeypatch.setattr(incident_owner, "_prepare_pipeline_incident_evidence", capture_preparation("pipeline", incident_owner._prepare_pipeline_incident_evidence))
    monkeypatch.setattr(incident_owner, "_prepare_remote_ambiguity_evidence", capture_preparation("ambiguity", incident_owner._prepare_remote_ambiguity_evidence))
    monkeypatch.setattr(incident_owner, "_pause_scope_for_item", pause_inputs)
    monkeypatch.setattr(incident_owner, "_group_operational_incidents", group_inputs)
    monkeypatch.setattr(incident_owner, "_prepare_recovery_evidence", recovery_inputs)
    report = digest.summarise_operational_error_health(errors, events, [], generation_time=BASE)
    assert phases == ["pipeline", "ambiguity", "grouping", "pause", "recovery"]
    assert lookups == list(prepared["groups"])
    pipeline = next(row for row in report["current_incidents"] if row["category"] == "reply_strategy_pipeline_failure")
    assert pipeline["record_count"] == 0 and pipeline["pipeline_failure_event_count"] == 1


def _pause_inputs():
    return dict(events=[], transport_attempts=[], explicit_remote_pause_scope=digest._explicit_remote_pause_scope,
                get_event_time=lambda item: item.get("_time"), base_remote_control_key=digest._base_remote_control_key,
                remote_control_scope=digest._remote_control_scope,
                remote_operation_scope_for_lane=digest._remote_operation_scope_for_lane)


def test_pause_scope_keeps_explicit_tuple_and_missing_time_pending_priority():
    calls, keys = [], ["pause_replies"]
    explicit = ("all_replies", "explicit", keys)

    class Unreadable:
        def __iter__(self):
            pytest.fail("lower-priority evidence was inspected")

    def event_time(item):
        calls.append("time")
        return None

    def scope(lane):
        calls.append(("lane", lane))
        return digest._remote_operation_scope_for_lane(lane)

    inputs = _pause_inputs()
    inputs.update(events=Unreadable(), transport_attempts=Unreadable(), get_event_time=event_time,
                  explicit_remote_pause_scope=lambda raw: calls.append(("explicit", raw)) or explicit,
                  remote_operation_scope_for_lane=scope)
    item = {"_raw_message": "raw pause", "message": "display", "_pause_pending_lane": "mention"}
    assert incident_owner._pause_scope_for_item(item, **inputs) is explicit
    assert calls == [("explicit", "raw pause")]
    explicit = ("unknown", "unavailable", [])
    result = incident_owner._pause_scope_for_item(item, **inputs)
    assert result == ("normal_replies", "exact pending lane mention associated with the exception", [])
    assert calls == [("explicit", "raw pause"), ("explicit", "raw pause"), "time", ("lane", "mention")]


def test_pause_scope_keeps_structured_boundary_sort_ties_key_priority_and_owner_re(monkeypatch):
    from types import SimpleNamespace

    calls, splits = [], []
    item = {"message": "RemoteOperationsPaused", "_time": BASE, "_pause_pending_lane": "quote_tweet"}
    first = {"kind": "runtime_control_pause", "name": "first", "_time": BASE - timedelta(seconds=60),
             "time": "a", "control_lanes": ["unrecognised", "mention"]}
    events = [{"kind": "other", "name": "ignored"},
              dict(first, name="missing", _time=None), dict(first, name="outside", _time=BASE - timedelta(seconds=60.001)),
              dict(first, name="text-later", time="z", key="pause_all"), first,
              dict(first, name="stable-tie", key="pause_quote_replies")]
    split = incident_owner.re.split
    monkeypatch.setattr(incident_owner, "re", SimpleNamespace(split=lambda *args: splits.append(args) or split(*args)))
    monkeypatch.setattr(digest, "re", None)
    inputs = _pause_inputs()
    inputs.update(events=events, explicit_remote_pause_scope=lambda raw: ("unknown", "", []),
                  get_event_time=lambda row: calls.append(row.get("name", "item")) or row.get("_time"))
    result = incident_owner._pause_scope_for_item(item, **inputs)
    assert result == ("normal_replies", "nearby structured runtime_control_pause lane", [])
    assert calls == ["item", "missing", "outside", "text-later", "first", "stable-tie"] and not splits
    first["control_lanes"] = "mention, unrecognised"
    assert incident_owner._pause_scope_for_item(item, **inputs) == result
    assert splits == [(r"\s*,\s*", first["control_lanes"])]
    first["key"] = "future-control"
    inputs.update(base_remote_control_key=lambda key: key or "", remote_control_scope=lambda key: "unknown")
    assert incident_owner._pause_scope_for_item(item, **inputs) == (
        "unknown", "nearby structured runtime_control_pause event", ["future-control"],
    )


@pytest.mark.parametrize("offset,scope", [(-10, "normal_replies"), (-10.001, "unknown"), (0, "normal_replies"), (0.001, "unknown")])
def test_pause_scope_keeps_directional_transport_boundary(offset, scope):
    inputs = _pause_inputs()
    inputs["transport_attempts"] = [{"time": BASE + timedelta(seconds=offset), "lane": "mention"}]
    result = incident_owner._pause_scope_for_item({"_time": BASE}, **inputs)
    assert result == (scope, "exact pending transport-request lane associated with the exception"
                      if scope != "unknown" else "scope unavailable from retained evidence", [])


def _grouping_callbacks(**overrides):
    inputs = dict(classify_operational_error=digest.classify_operational_error, get_event_time=digest._event_time,
                  seconds_between=digest.seconds_between, is_subordinate_remote_write_symptom=lambda **kwargs: False,
                  incident_exception_line=digest._incident_exception_line, matching_ambiguity_identity=lambda *args: None,
                  pause_scope_for_item=lambda item: ("unknown", "unavailable", []), normalise_incident_text=digest._normalise_incident_text)
    inputs.update(overrides)
    return inputs


def test_grouping_keeps_evidence_priority_annotation_order_shared_rows_and_failed_prefix(monkeypatch):
    calls, groups, keys = [], {}, ["pause_replies"]
    pipeline_identity, empty_identity = ("mention", "123"), ("quote-tweet", "456")
    rows = [{"message": raw, "_time": BASE, "time": "retained time"} for raw in (
        "pipeline", "transient", "receipt lane=hot_post target_id=789", "pause", "unrelated")]
    categories = {"transient": "x_api_transient_failure", rows[2]["message"]: "conversational_reply_receipt_barrier",
                  "pause": "remote_operations_paused", "unrelated": "custom"}
    lane = incident_owner._normalise_lane
    monkeypatch.setattr(incident_owner, "_normalise_lane", lambda value: calls.append(("lane", value)) or lane(value))
    monkeypatch.setattr(digest, "_normalise_lane", None)

    def classify(raw):
        calls.append(("classify", raw))
        return categories[raw]

    def subordinate(**inputs):
        calls.append(("subordinate", inputs["raw"]))
        return inputs["category"] == "conversational_reply_receipt_barrier"

    def root(raw):
        calls.append(("root", raw))
        if raw == rows[2]["message"]:
            assert rows[2]["_remote_write_subordinate_category"] == "conversational_reply_receipt_barrier"
            assert rows[2]["_remote_write_subordinate_reply_identity"] == {
                "lane": "hot-post", "target_id": "789", "source_time": "retained time",
            }
        return "root " + raw

    def match(raw, item_time):
        calls.append(("match", raw))
        return {"transaction_id": "tx", "lane": "hot-post", "target_id": "789"}

    def pause_scope(item):
        assert item is rows[3] and sum(map(len, groups.values())) == 3
        calls.append(("pause", item["message"]))
        return "all_replies", "prepared evidence", keys

    callbacks = _grouping_callbacks(classify_operational_error=classify, is_subordinate_remote_write_symptom=subordinate,
                                    incident_exception_line=root, matching_ambiguity_identity=match, pause_scope_for_item=pause_scope,
                                    get_event_time=lambda row: calls.append(("time", row["message"])) or row["_time"],
                                    seconds_between=lambda a, b: calls.append(("distance", a, b)) or abs((a - b).total_seconds()),
                                    normalise_incident_text=lambda text: calls.append(("normalise", text)) or text)
    result = incident_owner._group_operational_incidents(
        groups, rows, {id(rows[0]): (pipeline_identity, "outer_wrapper")}, [BASE + timedelta(seconds=10)],
        {"remote_operations_paused"}, {pipeline_identity: [], empty_identity: []}, **callbacks,
    )
    pipeline_key = ("reply_strategy_pipeline_failure", "mention:123")
    remote_key = ("remote_write_ambiguity_barrier", "transaction:tx")
    pause_key = ("remote_operations_paused", "remote_operations_paused:all_replies")
    empty_key = ("reply_strategy_pipeline_failure", "quote-tweet:456")
    assert list(groups) == [pipeline_key, remote_key, pause_key, ("custom", "root unrelated"), empty_key]
    assert groups[pipeline_key][0] is rows[0] and groups[remote_key][0] is rows[1] and groups[remote_key][1] is rows[2]
    assert rows[3]["_pause_control_keys"] is keys and not groups[empty_key]
    assert list(result) == [pipeline_key, empty_key] and result[pipeline_key] is pipeline_identity and result[empty_key] is empty_identity
    assert "_remote_write_subordinate_category" not in rows[1]
    assert rows[2]["_remote_write_identity"] == {"transaction_id": "tx", "lane": "hot-post", "target_id": "789", "image": ""}
    assert calls == [
        ("time", "pipeline"), ("subordinate", "pipeline"), ("root", "pipeline"),
        ("classify", "transient"), ("time", "transient"), ("distance", BASE, BASE + timedelta(seconds=10)), ("root", "transient"), ("match", "transient"),
        ("classify", rows[2]["message"]), ("time", rows[2]["message"]), ("subordinate", rows[2]["message"]), ("lane", "hot_post"), ("root", rows[2]["message"]), ("match", rows[2]["message"]),
        ("classify", "pause"), ("time", "pause"), ("subordinate", "pause"), ("root", "pause"), ("pause", "pause"),
        ("classify", "unrelated"), ("time", "unrelated"), ("subordinate", "unrelated"), ("root", "unrelated"), ("normalise", "root unrelated"),
    ]
    partial, failure = {}, RuntimeError("root failed after subordinate annotation")
    for name in ("_remote_write_subordinate_category", "_remote_write_subordinate_reply_identity", "_remote_write_identity"):
        rows[2].pop(name)

    def fail_root(raw):
        if raw == rows[2]["message"]:
            assert rows[2]["_remote_write_subordinate_category"] == "conversational_reply_receipt_barrier"
            raise failure
        return raw

    callbacks["incident_exception_line"] = fail_root
    with pytest.raises(RuntimeError) as caught:
        incident_owner._group_operational_incidents(partial, [rows[0], rows[2]], {id(rows[0]): (pipeline_identity, "outer_wrapper")},
                                                    [], set(), {empty_identity: []}, **callbacks)
    assert caught.value is failure and list(partial) == [pipeline_key] and partial[pipeline_key][0] is rows[0]


@pytest.mark.parametrize("category,boundary", [("remote_write_ambiguity_barrier", 10), ("xai_provider_timeout", 5)])
def test_grouping_keeps_reverse_traversal_and_distinct_inclusive_windows(monkeypatch, category, boundary):
    calls = []
    old, newest = {"_time": BASE, "name": "old"}, {"_time": BASE, "name": "newest"}
    unrelated = {"_time": BASE, "name": "unrelated"}
    groups = {(category, "old"): [old], (category, "newest"): [newest], ("other", "other"): [unrelated]}
    item = {"_time": BASE + timedelta(seconds=boundary), "name": "boundary"}
    monkeypatch.setattr(incident_owner, "dt_text", lambda value: calls.append(("format", value)) or "owner-time")
    monkeypatch.setattr(digest, "dt_text", None)
    callbacks = _grouping_callbacks(classify_operational_error=lambda raw: category,
                                    get_event_time=lambda row: calls.append(("time", row["name"])) or row["_time"])
    assert incident_owner._group_operational_incidents(groups, [item], {}, [], set(), {}, **callbacks) == {}
    assert groups[(category, "newest")][-1] is item
    assert [value for kind, value in calls if kind == "time"] == ["boundary", "unrelated", "newest"]
    groups[(category, "newest")].pop()
    item["_time"] += timedelta(microseconds=1)
    incident_owner._group_operational_incidents(groups, [item], {}, [], set(), {}, **callbacks)
    assert list(groups)[-1] == (category, category + ":owner-time") and groups[list(groups)[-1]][0] is item
    assert [value for kind, value in calls if kind == "time"][-4:] == ["boundary", "unrelated", "newest", "old"]
    assert ("format", item["_time"]) in calls


def test_preparation_keeps_input_result_references_and_summary_sequence(monkeypatch):
    later = BASE + timedelta(seconds=10)
    root = {"level": "CRITICAL", "message": "ambiguous remote X post outcome lane=mention target_id=123", "_time": BASE}
    wrapper = {"level": "ERROR", "message": "Failed to ask Grok for reply APIError lane=hot_post target_id=456", "_time": BASE}
    errors = [root, wrapper,
              {"level": "ERROR", "message": "RemoteOperationsPaused: pause_replies", "_time": BASE},
              {"level": "ERROR", "message": "Bot crashed with unhandled exception", "_time": BASE}]
    failure = {"kind": "reply_strategy_failure", "lane": "hot_post", "target_id": "456", "_time": BASE}
    events = [failure, {"kind": "mention_reply_posted", "_time": later}]
    receipts = [{"kind": "regular_removed", "_time": later}]
    restart = {"message": "Bot started successfully", "_time": later}
    terminal = {"kind": "sending_removed", "target_id": "123", "_time": later}
    transaction = {"kind": "tweet_transport", "phase": "request_started", "reply_to_id": "123", "_time": BASE}
    safety, phases, prepared, consumed = {"available": False}, [], {}, set()
    pipeline = incident_owner._prepare_pipeline_incident_evidence
    ambiguity = incident_owner._prepare_remote_ambiguity_evidence
    recovery = incident_owner._prepare_recovery_evidence
    original_classifier, distance = digest.classify_operational_error, digest.seconds_between
    classifications = []
    event_time = lambda row: row.get("_time")

    def classifier(raw):
        classifications.append(raw)
        return original_classifier(raw)

    def materialise(name, row):
        phases.append(name)
        yield row

    class Clock(datetime):
        @staticmethod
        def now():
            phases.append("clock")
            return later

    def pipeline_inputs(actual_events, operational, **inputs):
        assert actual_events is events
        assert all(a is b for a, b in zip(operational, errors)) and len(operational) == len(errors)
        assert inputs["get_event_time"] is event_time
        phases.append("pipeline")
        result = pipeline(actual_events, operational, **inputs)
        assert result[0][("hot-post", "456")][0] is failure
        assert result[1] == {id(wrapper): (("hot-post", "456"), "outer_wrapper")}
        return result

    def ambiguity_inputs(serious, actual_events, transactions, **inputs):
        assert actual_events is events and transactions == [transaction] and transactions[0] is transaction
        assert len(serious) == len(errors) and all(a is b for a, b in zip(serious, errors))
        assert inputs == {"classify_operational_error": classifier, "get_event_time": event_time, "seconds_between": distance}
        phases.append("ambiguity")
        result = ambiguity(serious, actual_events, transactions, **inputs)
        prepared.update(zip(("ambiguity_times", "transport_attempts", "ambiguous_reply_outcomes", "ambiguous_media_outcomes"), result))
        return result

    def recovery_inputs(actual_events, actual_receipts, lifecycle, confirmed, **inputs):
        assert actual_events is events and actual_receipts is receipts
        assert lifecycle == [restart] and lifecycle[0] is restart
        assert confirmed == [terminal] and confirmed[0] is terminal
        assert inputs["get_event_time"] is event_time
        assert root["_remote_write_identity"]["target_id"] == "123"
        phases.append("recovery")
        result = recovery(actual_events, actual_receipts, lifecycle, confirmed, **inputs)
        prepared.update(zip(("event_times", "receipt_removed_times", "successful_restart_times", "remote_write_success_times", "remote_operation_successes", "terminal_reply_receipts"), result))
        return result

    def check_consumer(original):
        def check(*args, **inputs):
            for name in prepared.keys() & inputs.keys():
                assert inputs[name] is prepared[name]
                consumed.add(name)
            return original(*args, **inputs)
        return check

    def annotate(value, *_args, **_kwargs):
        assert value is safety
        phases.append("annotate")

    monkeypatch.setattr(digest, "datetime", Clock)
    monkeypatch.setattr(digest, "classify_operational_error", classifier)
    monkeypatch.setattr(digest, "_event_time", event_time)
    monkeypatch.setattr(digest, "annotate_remote_write_snapshot_window", annotate)
    monkeypatch.setattr(incident_owner, "_prepare_pipeline_incident_evidence", pipeline_inputs)
    monkeypatch.setattr(incident_owner, "_prepare_remote_ambiguity_evidence", ambiguity_inputs)
    monkeypatch.setattr(incident_owner, "_prepare_recovery_evidence", recovery_inputs)
    for name in ("_matching_ambiguity_identity", "_is_subordinate_remote_write_symptom", "_remote_write_recovery_status", "_remote_pause_recovery_status", "_recovered_after"):
        monkeypatch.setattr(incident_owner, name, check_consumer(getattr(incident_owner, name)))
    result = digest.summarise_operational_error_health(
        errors, events, receipts, lifecycle=materialise("lifecycle", restart),
        remote_write_transactions=materialise("transactions", transaction),
        handled_api_restrictions=materialise("handled", {}),
        confirmed_reply_receipt_events=materialise("confirmed", terminal),
        current_remote_write_safety=safety,
    )
    assert phases == ["clock", "lifecycle", "transactions", "handled", "confirmed", "pipeline", "ambiguity", "recovery", "annotate"]
    assert classifications == [row["message"] for row in errors] * 2 + [root["message"], errors[2]["message"], errors[3]["message"]]
    assert consumed == prepared.keys()
    incident = next(row for row in result["current_incidents"] if row["category"] == "reply_strategy_pipeline_failure")
    assert incident["pipeline_failure_event_count"] == incident["wrapper_record_count"] == 1


def test_pipeline_preparation_keeps_exact_counts_hints_causal_ties_and_callback_order(monkeypatch):
    calls = []
    normalise = incident_owner._normalise_lane

    def lane(value):
        calls.append(("lane", value))
        return normalise(value)

    def event_time(row):
        calls.append(("time", row["name"]))
        return row.get("_time")

    a = {"name": "a", "kind": "reply_strategy_failure", "lane": "hot_post", "target_id": 123, "reason": "invalid", "_time": BASE}
    b = dict(a, name="b", lane="quote_tweet", target_id=456)
    events = [{"kind": "unrelated"}, dict(a, target_id=""), a, a, b]
    ended = "AI-first reply pipeline ended status=operational_failure lane={lane} target_id={target} reason=invalid calls=1 revisions=0"
    raw = [
        ("duplicate", ended.format(lane="hot_post", target=123), "worker", BASE),
        ("single", ended.format(lane="quote_tweet", target=456), "worker", BASE),
        ("tied", "Failed to ask Grok for reply APIError", "maybe_reply_to_mentions", BASE),
        ("hint", "Failed to ask Grok for reply APIError", "maybe_reply_to_hot_posts", BASE),
        ("target", "Failed to ask Grok for reply APIError target_id=456", "maybe_reply_to_hot_posts", BASE),
        ("future", "Failed to ask Grok for reply APIError", "worker", BASE - timedelta(seconds=1)),
        ("stale", "Failed to ask Grok for reply APIError", "worker", BASE + timedelta(seconds=6)),
        ("extended", ended.format(lane="quote_tweet", target=456) + " extra", "worker", BASE),
        ("missing-time", ended.format(lane="quote_tweet", target=456), "worker", None),
    ]
    errors = [{"name": name, "message": message, "where": where, "_time": ts} for name, message, where, ts in raw]
    monkeypatch.setattr(incident_owner, "_normalise_lane", lane)
    monkeypatch.setattr(digest, "_normalise_lane", None)
    failures, evidence = incident_owner._prepare_pipeline_incident_evidence(events, errors, get_event_time=event_time)
    assert list(failures) == [("hot-post", "123"), ("quote-tweet", "456")]
    assert failures[("hot-post", "123")][0] is failures[("hot-post", "123")][1] is a
    assert failures[("quote-tweet", "456")][0] is b
    assert evidence == {
        id(errors[1]): (("quote-tweet", "456"), "pipeline_error"),
        id(errors[3]): (("hot-post", "123"), "outer_wrapper"),
        id(errors[4]): (("quote-tweet", "456"), "outer_wrapper"),
    }
    assert calls == [("lane", "hot_post")] * 3 + [("lane", "quote_tweet")] + [
        ("time", "duplicate"), ("lane", "hot_post"), ("time", "a"), ("time", "a"),
        ("time", "single"), ("lane", "quote_tweet"), ("time", "b"),
        ("time", "tied"), ("time", "a"), ("time", "a"), ("time", "b"),
        ("time", "hint"), ("time", "a"), ("time", "a"),
        ("time", "target"), ("time", "b"),
        ("time", "future"), ("time", "a"), ("time", "a"), ("time", "b"),
        ("time", "stale"), ("time", "a"), ("time", "a"), ("time", "b"),
        ("time", "extended"), ("time", "missing-time"),
    ]
    older = dict(a, _time=BASE - timedelta(seconds=1))
    _, nearest = incident_owner._prepare_pipeline_incident_evidence([older, b], [errors[2]], get_event_time=event_time)
    assert nearest == {id(errors[2]): (("quote-tweet", "456"), "outer_wrapper")}


def test_ambiguity_preparation_keeps_filters_stable_transport_ties_and_shared_times(monkeypatch):
    calls = []
    normalise = incident_owner._normalise_lane

    class Token(str):
        def __str__(self):
            return self

    def classify(message):
        calls.append(("classify", message))
        return "remote_write_ambiguity_barrier" if message != "other" else "other"

    def event_time(row):
        calls.append(("time", row["name"]))
        return row.get("_time")

    def lane(value):
        calls.append(("lane", value))
        return normalise(value)

    def distance(a, b):
        calls.append(("distance", a, b))
        return abs((a - b).total_seconds())

    serious = [{"name": "missing", "message": "missing"}, {"name": "other", "message": "other", "_time": BASE},
               {"name": "root", "message": "root", "_time": BASE}]
    first_token, second_token = Token("a"), Token("a")
    attempt = {"kind": "tweet_transport", "phase": "request_started", "reply_to_id": 123, "lane": "hot_post", "_time": BASE}
    transactions = [dict(attempt, name="wrong-phase", phase="confirmed"), dict(attempt, name="null", reply_to_id="NuLl"),
                    dict(attempt, name="untimed", _time=None), dict(attempt, name="z", transaction_id="z"),
                    dict(attempt, name="a", transaction_id=first_token), dict(attempt, name="tie", transaction_id=second_token),
                    dict(attempt, name="future", _time=BASE + timedelta(seconds=1)),
                    dict(attempt, name="stale", _time=BASE - timedelta(seconds=11)),
                    {"name": "media", "kind": "media_upload", "phase": "ambiguous", "image": 321, "_time": BASE},
                    {"name": "media-missing", "kind": "media_upload", "phase": "ambiguous"},
                    {"name": "media-confirmed", "kind": "media_upload", "phase": "confirmed"}]
    outcome = {"kind": "reply_strategy_outcome", "status": "posting_failed_retryable", "failure_reason": "ambiguous_remote_outcome",
               "lane": "hot_post", "target_id": 123, "_time": BASE}
    events = [dict(outcome, name="posted", status="posted"), dict(outcome, name="untimed", _time=None),
              dict(outcome, name="invalid-lane", lane="invalid"), dict(outcome, name="missing-target", target_id=""),
              dict(outcome, name="distant", _time=BASE + timedelta(seconds=6)), dict(outcome, name="accepted")]
    monkeypatch.setattr(incident_owner, "_normalise_lane", lane)
    monkeypatch.setattr(digest, "_normalise_lane", None)
    times, attempts, outcomes, media = incident_owner._prepare_remote_ambiguity_evidence(
        serious, events, transactions, classify_operational_error=classify, get_event_time=event_time, seconds_between=distance,
    )
    assert times == [BASE] and times[0] is BASE
    assert [row["transaction_id"] for row in attempts] == ["z", "a", "a", "", ""]
    assert attempts[1]["transaction_id"] is first_token and attempts[2]["transaction_id"] is second_token
    assert attempts[0]["lane"] == "hot_post" and attempts[0]["target_id"] == "123"
    assert outcomes == [{"time": BASE, "lane": "hot-post", "target_id": "123", "transaction_id": "a"}]
    assert outcomes[0]["transaction_id"] is first_token and outcomes[0]["time"] is BASE
    assert media == [{"time": BASE, "image": "321"}] and media[0]["time"] is BASE
    assert calls == [
        ("classify", "missing"), ("time", "missing"), ("classify", "other"), ("classify", "root"), ("time", "root"),
        *[("time", name) for name in ("null", "untimed", "z", "a", "tie", "future", "stale")],
        ("time", "untimed"), ("lane", "hot_post"), ("time", "invalid-lane"), ("lane", "invalid"),
        ("time", "missing-target"), ("lane", "hot_post"), ("time", "distant"), ("lane", "hot_post"),
        ("distance", events[4]["_time"], BASE), ("time", "accepted"), ("lane", "hot_post"), ("distance", BASE, BASE),
        ("time", "media"), ("time", "media-missing"),
    ]


def test_recovery_preparation_keeps_two_passes_scope_sharing_and_shallow_receipt_filters():
    later, calls, nested = BASE + timedelta(seconds=10), [], {"shared": []}
    events = [{"name": "generic", "kind": "remote_write_succeeded", "_time": later},
              {"name": "hot", "kind": "hot_post_reply_posted", "_time": BASE},
              {"name": "hot-again", "kind": "hot_post_reply_posted", "_time": BASE},
              {"name": "historical", "kind": "historical_context_reply", "status": "already_completed", "_time": later},
              {"name": "historical-again", "kind": "historical_context_reply", "status": "completed", "_time": later},
              {"name": "historical-failed", "kind": "historical_context_reply", "status": "failed", "_time": later},
              {"name": "untimed", "kind": "mention_reply_posted"}]
    receipts = [{"name": "regular", "kind": "regular_removed", "_time": later},
                {"name": "reconciled-missing", "kind": "regular_reconciled"},
                {"name": "other", "kind": "sending_removed", "_time": later}]
    lifecycle = [{"name": "restart", "message": "Bot started successfully", "_time": later},
                 {"name": "not-restart", "message": "starting", "_time": later}]
    terminal = {"name": "terminal", "kind": "sending_removed", "source_class": "production", "_time": BASE, "evidence": nested}
    confirmed = [dict(terminal, name="selftest", source_class="selftest"), dict(terminal, name="wrong-kind", kind="sending_written"),
                 dict(terminal, name="untimed", _time=None), terminal,
                 dict(terminal, name="fallback", kind="confirmed_state_fallback_removed"), dict(terminal, name="removed", kind="removed")]

    def event_time(row):
        calls.append(row["name"])
        return later if row is terminal else row.get("_time")

    times, removed, restarted, successes, operations, terminals = incident_owner._prepare_recovery_evidence(
        events, receipts, lifecycle, confirmed, get_event_time=event_time,
    )
    assert list(times) == ["remote_write_succeeded", "hot_post_reply_posted", "historical_context_reply"]
    assert removed == restarted == [later] and removed[0] is restarted[0] is later
    assert successes == [BASE, BASE, later] and successes[-1] is later
    assert [row["kind"] for row in operations] == ["remote_write_succeeded", "hot_post_reply_posted", "hot_post_reply_posted", "historical_context_reply", "historical_context_reply"]
    assert operations[0]["scopes"] == {"all_remote_writes"}
    assert operations[1]["scopes"] == {"hot_post_replies", "normal_replies", "all_replies", "all_remote_writes"}
    assert operations[1]["scopes"] is operations[2]["scopes"]
    assert operations[3]["scopes"] == operations[4]["scopes"] == {"historical_context_replies", "all_replies", "all_remote_writes"}
    assert operations[3]["scopes"] is not operations[4]["scopes"]
    assert [row["name"] for row in terminals] == ["terminal", "fallback", "removed"]
    assert terminals[0] is not terminal and terminals[0]["_time"] is later and terminal["_time"] is BASE
    assert all(row["evidence"] is nested for row in terminals)
    assert calls == [row["name"] for row in events] + ["regular", "reconciled-missing", "restart", "not-restart"] + [row["name"] for row in events] + ["untimed", "terminal", "fallback", "removed"]


def test_transaction_and_category_adapters_keep_prepared_inputs_and_current_helpers(monkeypatch):
    later = BASE + timedelta(seconds=10)
    events = [{"kind": "remote_write_succeeded", "_time": later}]
    receipt = {"kind": "regular_removed", "_time": later}
    restart = {"message": "Bot started successfully", "_time": later}
    shared = {"fixture": "shared receipt evidence"}
    terminal = {"kind": "sending_removed", "target_id": "123", "_time": later, "evidence": shared}
    restriction = {"restriction_kind": "deleted_or_inaccessible_tweet", "_time": BASE}
    components, calls = [], []
    safety = {"configured": True, "available": True, "blocking": False,
              "protocol": {"valid": True}, "active_transaction_identities": components}
    identified, category_recovery = incident_owner._remote_write_recovery_status, incident_owner._recovered_after
    event_time = lambda row: row.get("_time")

    def reject(*args, **kwargs):
        raise AssertionError("unexpected helper lookup or eager clock/epoch call")

    def clock():
        calls.append("clock")
        return later

    class Clock(datetime):
        now = staticmethod(clock)
        fromtimestamp = staticmethod(reject)

    def category_callback(category, last_time, **inputs):
        assert last_time is BASE
        assert inputs["events"] is events and inputs["safety"] is safety
        assert inputs["event_times"] == {"remote_write_succeeded": [later]}
        assert inputs["event_times"]["remote_write_succeeded"][0] is later
        for name in ("receipt_removed_times", "successful_restart_times"):
            assert inputs[name] == [later] and inputs[name][0] is later
        assert inputs["get_event_time"] is event_time
        assert inputs["fromtimestamp"] is reject and inputs["clock_now"] is clock
        calls.append(category)
        return category_recovery(category, last_time, **inputs)

    def transaction(category, identity, first_time, last_time, **inputs):
        assert identity == {"lane": "mention", "target_id": "123"}
        assert first_time is last_time is BASE
        assert inputs["identity_snapshot_available"] is False
        assert inputs["identity_snapshot_explicitly_unavailable"] is False
        assert inputs["safety"] is safety and inputs["active_remote_components"] is components
        assert inputs["handled_api_restrictions"][0] is restriction
        assert inputs["terminal_reply_receipts"][0]["evidence"] is shared
        assert inputs["terminal_reply_receipts"][0]["_time"] is later
        assert inputs["remote_write_success_times"] == [later]
        assert inputs["remote_write_success_times"][0] is later
        assert inputs["fromtimestamp"] is reject and inputs["get_event_time"] is event_time
        component = {"target_ids": ["123"], "artifact_kinds": ["ambiguity_marker"],
                     "selected_window_relationship": "recorded_at_or_before_selected_window_end"}
        assert inputs["component_is_related_to_selected_window"](component) is True
        assert inputs["component_is_relevant_to_category"](component, category) is True
        assert inputs["component_is_relevant_to_category"](component, "conversational_reply_receipt_barrier") is False
        components.append(component)
        assert inputs["active_component_matches"](category, identity) is True
        components.clear()
        calls.append("identified")
        # This lookup must still be current when the legacy fallback runs.
        monkeypatch.setattr(incident_owner, "_recovered_after", category_callback)
        return identified(category, identity, first_time, last_time, **inputs)

    def annotate(value, *_args, **_kwargs):
        assert value is safety
        calls.append("annotate")
        monkeypatch.setattr(digest, "_event_time", reject)
        monkeypatch.setattr(digest, "datetime", None)
        monkeypatch.setattr(incident_owner, "_remote_write_recovery_status", transaction)

    monkeypatch.setattr(digest, "datetime", Clock)
    monkeypatch.setattr(digest, "_event_time", event_time)
    monkeypatch.setattr(digest, "annotate_remote_write_snapshot_window", annotate)
    monkeypatch.setattr(incident_owner, "_remote_write_recovery_status", reject)
    monkeypatch.setattr(incident_owner, "_recovered_after", reject)
    result = digest.summarise_operational_error_health(
        [{"level": "CRITICAL", "message": "ambiguous remote X post outcome lane=mention target_id=123", "_time": BASE},
         {"level": "ERROR", "message": "remote-write protocol is not activated", "_time": BASE}],
        events, [receipt], lifecycle=iter([restart]), handled_api_restrictions=iter([restriction]),
        confirmed_reply_receipt_events=iter([terminal]), current_remote_write_safety=safety, generation_time=BASE,
    )
    assert calls == ["annotate", "identified", "remote_write_ambiguity_barrier", "remote_write_protocol_barrier", "clock"]
    assert result["current_incidents"][0]["category"] == "remote_write_ambiguity_barrier"
    assert result["historical_resolved_incidents"][0]["resolution_time"] == digest.dt_text(later)


def _identified_recovery_inputs(**overrides):
    def reject(*args, **kwargs):
        raise AssertionError("unexpected conditional dependency access")

    return dict({
        "identity_snapshot_available": True, "active_component_matches": lambda *args: False,
        "active_remote_components": [], "component_is_related_to_selected_window": reject,
        "component_is_relevant_to_category": reject, "identity_snapshot_explicitly_unavailable": False,
        "safety": {}, "terminal_reply_receipts": [], "handled_api_restrictions": [],
        "remote_write_success_times": [], "fromtimestamp": reject, "get_event_time": reject,
    }, **overrides)


@pytest.mark.parametrize("available,matched,explicit,status,reason", [
    (True, True, False, "current_unresolved", ""),
    (True, False, False, "resolution_unavailable", "current barrier artefacts lack enough identity to establish whether they match this transaction"),
    (False, False, True, "resolution_unavailable", "current status cannot be established from retained evidence because no usable filesystem snapshot is available"),
    (False, False, False, "legacy_fallback", ""),
])
def test_identified_recovery_keeps_unavailable_scan_and_predicate_order(available, matched, explicit, status, reason):
    calls = []
    rows = [{"name": "outside"}, {"name": "other-category"}, {"name": "unknown"}, {"name": "unvisited"}]

    def active(category, identity):
        calls.append("active")
        return matched

    def related(row):
        assert row is rows[len([call for call in calls if isinstance(call, tuple) and call[0] == "related"])]
        calls.append(("related", row["name"]))
        return row["name"] != "outside"

    def relevant(row, category):
        calls.append(("relevant", row["name"]))
        return row["name"] != "other-category"

    inputs = _identified_recovery_inputs(
        identity_snapshot_available=available, active_component_matches=active, active_remote_components=rows,
        component_is_related_to_selected_window=related, component_is_relevant_to_category=relevant,
        identity_snapshot_explicitly_unavailable=explicit, safety=None,
    )
    assert incident_owner._remote_write_recovery_status("remote_write_ambiguity_barrier", None, BASE, BASE, **inputs) == (status, reason, None)
    assert calls == (["active"] if available else []) + ([] if matched else [
        ("related", "outside"), ("related", "other-category"), ("relevant", "other-category"),
        ("related", "unknown"), ("relevant", "unknown"),
    ])
    if not available and explicit:
        failure = LookupError("unavailable snapshots still run the scan")

        def fail(row):
            raise failure

        inputs["component_is_related_to_selected_window"] = fail
        with pytest.raises(LookupError) as caught:
            incident_owner._remote_write_recovery_status("remote_write_ambiguity_barrier", None, BASE, BASE, **inputs)
        assert caught.value is failure


@pytest.mark.parametrize("algorithm", ["identified", "category"])
def test_recovery_audits_keep_native_epochs_ties_fallback_and_conditional_conversion(algorithm):
    epoch, converted, calls = int(BASE.timestamp()), BASE + timedelta(seconds=11), []

    class Epoch(int):
        pass

    class Audit(dict):
        def __getitem__(self, key):
            if key == "archived_at_epoch":
                calls.append(self["name"])
            return super().__getitem__(key)

    audits = [Audit(name=name, archived_at_epoch=value, audit_path=path, target_id="123")
              for name, value, path in [
                  ("subclass", Epoch(epoch), "0"), ("boolean", True, "0"), ("string", str(epoch), "0"),
                  ("float", float(epoch), "0"), ("stale", epoch - 1, "0"),
                  ("later", epoch + 1, "0"), ("z", epoch, "z"), ("a", epoch, "a"), ("tied", epoch, "a"),
              ]]
    safety = reconciled_remote_write_safety()
    archive = safety["reconciliation_archive"]
    archive["marker_reconciliations"] = audits

    def convert(value):
        assert value == epoch and calls[-1] == "a"
        calls.append("convert")
        return converted

    def recover():
        if algorithm == "identified":
            return incident_owner._remote_write_recovery_status(
                "remote_write_ambiguity_barrier", {"target_id": "123"}, BASE, BASE,
                **_identified_recovery_inputs(safety=safety, fromtimestamp=convert),
            )
        return incident_owner._recovered_after(
            "remote_write_ambiguity_barrier", BASE, events=None, event_times={}, receipt_removed_times=None,
            successful_restart_times=None, safety=safety, get_event_time=None, fromtimestamp=convert, clock_now=None,
        )

    result = recover()
    assert result[0] == ("historical_resolved" if algorithm == "identified" else True)
    assert result[2] is converted
    assert calls == ([] if algorithm == "identified" else ["stale", "later", "z", "a", "tied"]) + ["later", "z", "a", "tied", "a", "convert"]
    calls.clear()
    archive["marker_reconciliations"] = []
    archive["latest_marker_reconciliation"] = audits[-2]
    assert recover() == result
    assert calls == (["a"] if algorithm == "category" else []) + ["a", "a", "convert"]
    archive["valid"] = False
    calls.clear()
    empty = recover()
    assert empty[0] == ("resolution_unavailable" if algorithm == "identified" else False)
    assert empty[2] is None and "convert" not in calls
    archive["valid"] = True
    failure = OverflowError("selected audit conversion")

    def convert(value):
        raise failure

    with pytest.raises(OverflowError) as caught:
        recover()
    assert caught.value is failure


def test_identified_recovery_keeps_current_owner_helpers_and_transaction_target_window(monkeypatch):
    calls, epoch = [], int(BASE.timestamp())
    original_lane = incident_owner._normalise_lane

    def lane(value):
        calls.append(("lane", value))
        return original_lane(value)

    def duration(**values):
        calls.append(("duration", values))
        return timedelta(**values)

    monkeypatch.setattr(incident_owner, "_normalise_lane", lane)
    monkeypatch.setattr(incident_owner, "timedelta", duration)
    monkeypatch.setattr(digest, "_normalise_lane", None)
    audit = {"archived_at_epoch": epoch + 6 * 3600, "target_id": "123"}
    inputs = _identified_recovery_inputs(
        safety={"reconciliation_archive": {"valid": True, "marker_reconciliations": [audit]}},
        fromtimestamp=lambda value: BASE,
    )
    identity = {"transaction_id": "tx", "target_id": "123", "lane": "hot_post"}

    def recover():
        return incident_owner._remote_write_recovery_status("remote_write_ambiguity_barrier", identity, BASE, BASE, **inputs)

    assert recover()[0] == "historical_resolved"
    assert calls == [("lane", "hot_post"), ("duration", {"hours": 6})]
    audit["archived_at_epoch"] += 1
    assert recover()[0] == "resolution_unavailable"
    audit["transaction_id"] = "tx"
    calls.clear()
    assert recover()[0] == "historical_resolved"
    assert calls == [("lane", "hot_post")]
    audit["target_id"] = "other"
    assert recover()[0] == "resolution_unavailable"
    audit["target_id"], audit["transaction_id"] = "123", "other-tx"
    assert recover()[0] == "resolution_unavailable"
    audit["transaction_id"] = "tx"
    del audit["target_id"]
    assert recover()[0] == "historical_resolved"


def test_identified_recovery_keeps_handled_window_later_success_and_receipt_time_sharing():
    last = BASE + timedelta(seconds=10)
    terminal_time, tied_time = BASE + timedelta(seconds=11), BASE + timedelta(seconds=11)
    receipts = [{"target_id": "123", "lane": "hot_post", "_time": BASE},
                {"target_id": "123", "lane": "hot-post", "_time": tied_time}]
    handled = {"restriction_kind": "deleted_or_inaccessible_tweet", "target_id": "123", "lane": "hot_post"}
    calls = []

    def event_time(row):
        assert row is handled
        calls.append(row["_time"])
        # terminal_matches already exists: its rows must remain shared, and
        # terminal time has a lower boundary only (it may follow last_time).
        receipts[0]["_time"] = terminal_time
        return row["_time"]

    inputs = _identified_recovery_inputs(
        terminal_reply_receipts=receipts, handled_api_restrictions=[handled], get_event_time=event_time,
        remote_write_success_times=[terminal_time],
    )

    def recover():
        receipts[0]["_time"] = BASE
        return incident_owner._remote_write_recovery_status(
            "conversational_reply_receipt_barrier", {"target_id": "123", "lane": "hot-post"}, BASE, last, **inputs,
        )

    handled["_time"] = BASE - timedelta(minutes=5)
    assert recover()[0] == "resolution_unavailable"  # Equal success is insufficient.
    inputs["remote_write_success_times"].append(terminal_time + timedelta(microseconds=1))
    for ts, expected in [(BASE - timedelta(minutes=5), True), (last + timedelta(minutes=5), True),
                         (BASE - timedelta(minutes=5, microseconds=1), False),
                         (last + timedelta(minutes=5, microseconds=1), False)]:
        handled["_time"] = ts
        result = recover()
        assert calls[-1] is ts
        if expected:
            assert result == ("historical_resolved", "deleted/inaccessible target was handled, its sending receipt was retired, and a later remote write succeeded", terminal_time)
            assert result[2] is terminal_time and result[2] is not tied_time
        else:
            assert result == ("resolution_unavailable", "no matching active artefact remains, but terminal resolution is unavailable from retained evidence", None)


def test_category_recovery_keeps_both_event_passes_kind_order_and_time_reason_ties():
    reply_time, gate_time = BASE + timedelta(seconds=1), BASE + timedelta(seconds=1)
    gate = {"kind": "historical_context_semantic_gate", "_time": gate_time}
    reply = {"kind": "historical_context_reply", "status": "completed", "_time": reply_time}
    events, calls = [gate, reply], []

    def event_time(row):
        calls.append(row["kind"])
        if row is reply:
            gate["status"] = "loaded"
        return row["_time"]

    inputs = dict(events=events, event_times={}, receipt_removed_times=[reply_time], successful_restart_times=[],
                  safety=None, get_event_time=event_time, fromtimestamp=None, clock_now=None)
    result = incident_owner._recovered_after("historical_context_reply_failure", BASE, **inputs)
    assert result == (True, "later historical-context reply completed", reply_time)
    assert result[2] is reply_time
    assert calls == ["historical_context_semantic_gate", "historical_context_reply"] * 2
    reply["status"] = "failed"
    assert incident_owner._recovered_after("historical_context_reply_failure", BASE, **inputs)[2] is gate_time

    class Times(dict):
        def get(self, key, default):
            calls.append(key)
            return super().get(key, default)

    calls.clear()
    inputs["event_times"] = Times(mention_reply_posted=[BASE, reply_time], hot_post_reply_posted=[gate_time])
    result = incident_owner._recovered_after("conversational_reply_posting_failure", BASE, **inputs)
    assert result == (True, "later hot post reply posted observed", gate_time) and result[2] is gate_time
    assert calls == ["mention_reply_posted", "hot_post_reply_posted", "quote_tweet_reply_posted"]
    calls.clear()
    inputs["event_times"] = Times(daily_meme_posted=[gate_time])
    result = incident_owner._recovered_after("legacy_regular_receipt_barrier", BASE, **inputs)
    assert result == (True, "later daily meme posted observed", gate_time) and result[2] is gate_time
    assert calls == ["daily_meme_posted", "quote_image_posted"]
    assert incident_owner._recovered_after("legacy_regular_receipt_barrier", gate_time, **inputs) == (False, "", None)
    assert incident_owner._recovered_after("unknown", BASE, **inputs) == (False, "", None)


def test_category_protocol_clear_keeps_conditional_clock_and_namespace_distinction():
    calls, sampled = [], BASE + timedelta(hours=1)
    safety = {"configured": True, "available": True, "blocking": False, "protocol": {"valid": True}}

    def clock():
        calls.append("clock")
        return sampled

    inputs = dict(events=None, event_times={}, receipt_removed_times=None, successful_restart_times=None,
                  safety=safety, get_event_time=None, fromtimestamp=None, clock_now=clock)
    result = incident_owner._recovered_after("remote_write_protocol_barrier", BASE, **inputs)
    assert result == (True, "current protocol snapshot is valid with no active transaction barrier", sampled)
    assert result[2] is sampled and calls == ["clock"]
    calls.clear()
    assert incident_owner._recovered_after("remote_write_ambiguity_barrier", BASE, **inputs) == (False, "", None)
    safety["protocol"]["valid"] = False
    assert incident_owner._recovered_after("remote_write_protocol_barrier", BASE, **inputs) == (False, "", None)
    safety["protocol"]["valid"] = True
    safety["available"] = False
    assert incident_owner._recovered_after("remote_write_protocol_barrier", BASE, **inputs) == (False, "", None)
    assert calls == []


def test_association_adapters_forward_prepared_references_and_current_callbacks(monkeypatch):
    later, attempt_time = BASE + timedelta(seconds=1), BASE - timedelta(seconds=1)
    root = {"level": "CRITICAL", "message": "ambiguous remote X post outcome", "_time": BASE}
    symptom = {"level": "CRITICAL", "_time": later,
               "message": "Normal reply lane stopped by the global remote-write safety barrier"}
    event = {"kind": "reply_strategy_outcome", "status": "posting_failed_retryable",
             "failure_reason": "ambiguous_remote_outcome", "lane": "hot_post",
             "target_id": 123, "_time": BASE}
    transactions = [
        {"kind": "tweet_transport", "phase": "request_started", "reply_to_id": 123,
         "transaction_id": "a" * 64, "lane": "conversational_reply", "_time": attempt_time},
        {"kind": "media_upload", "phase": "ambiguous", "image": "fixture.png", "_time": BASE},
    ]
    matching = incident_owner._matching_ambiguity_identity
    subordinate = incident_owner._is_subordinate_remote_write_symptom
    prepared, calls, results = {}, [], []
    distance = lambda a, b: abs((a - b).total_seconds())

    def reject(*args, **kwargs):
        raise AssertionError("unexpected helper captured or looked up through the facade")

    def event_time(row):
        if row is event:
            monkeypatch.setattr(digest, "seconds_between", reject)
        return row.get("_time")

    def check(inputs):
        assert inputs["seconds_between"] is distance
        for name, value in inputs.items():
            if name in prepared:
                assert value is prepared[name]
            else:
                prepared[name] = value
        assert inputs["ambiguous_reply_outcomes"] == [{
            "time": BASE, "lane": "hot-post", "target_id": "123", "transaction_id": "a" * 64,
        }]
        assert inputs["ambiguous_reply_outcomes"][0]["time"] is BASE

    def identity(raw, item_time, **inputs):
        check(inputs)
        assert inputs["transport_attempts"] == [{
            "time": attempt_time, "target_id": "123", "transaction_id": "a" * 64,
            "lane": "conversational_reply",
        }]
        assert inputs["transport_attempts"][0]["time"] is attempt_time
        assert inputs["ambiguous_media_outcomes"] == [{"time": BASE, "image": "fixture.png"}]
        calls.append("identity")
        result = matching(raw, item_time, **inputs)
        assert result is inputs["ambiguous_reply_outcomes"][0]
        results.append(result)
        monkeypatch.setattr(incident_owner, "_is_subordinate_remote_write_symptom", next_symptom)
        return result

    def first_symptom(*, category, raw, item_time, **inputs):
        check(inputs)
        assert inputs["ambiguity_times"] == [BASE] and inputs["ambiguity_times"][0] is BASE
        assert raw is root["message"] and item_time is BASE
        calls.append("subordinate")
        monkeypatch.setattr(incident_owner, "_matching_ambiguity_identity", identity)
        return subordinate(category=category, raw=raw, item_time=item_time, **inputs)

    def next_symptom(*, category, raw, item_time, **inputs):
        check(inputs)
        assert raw is symptom["message"] and item_time is later
        calls.append("subordinate-next")
        return subordinate(category=category, raw=raw, item_time=item_time, **inputs)

    monkeypatch.setattr(digest, "seconds_between", distance)
    monkeypatch.setattr(digest, "_event_time", event_time)
    monkeypatch.setattr(incident_owner, "_matching_ambiguity_identity", reject)
    monkeypatch.setattr(incident_owner, "_is_subordinate_remote_write_symptom", first_symptom)
    report = digest.summarise_operational_error_health(
        [root, symptom], [event], [], remote_write_transactions=iter(transactions), generation_time=later,
    )
    assert calls == ["subordinate", "identity", "subordinate-next", "identity"]
    assert results[0] is results[1]
    assert root["_remote_write_identity"] == symptom["_remote_write_identity"]
    assert report["current_incidents"][0]["record_count"] == 2
    assert report["current_incidents"][0]["correlated_subordinate_symptom_counts"] == {
        "normal reply lane stopped by the global remote-write safety barrier": 1,
    }


def test_association_missing_time_does_not_inspect_evidence_or_helpers(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("missing time must short-circuit")

    class Unreadable:
        __iter__ = search = fullmatch = reject

    value = Unreadable()
    monkeypatch.setattr(incident_owner, "re", value)
    monkeypatch.setattr(incident_owner, "_normalise_lane", reject)
    assert incident_owner._matching_ambiguity_identity(
        value, None, ambiguous_reply_outcomes=value, transport_attempts=value,
        ambiguous_media_outcomes=value, seconds_between=reject,
    ) is None
    assert incident_owner._is_subordinate_remote_write_symptom(
        category=value, raw=value, item_time=None, ambiguous_reply_outcomes=value,
        ambiguity_times=value, seconds_between=reject,
    ) is False


def test_association_owner_helpers_direct_ties_transport_fallback_and_sharing(monkeypatch):
    before, after = BASE - timedelta(seconds=300), BASE + timedelta(seconds=300)
    first = {"time": before, "lane": "hot-post", "target_id": "123", "transaction_id": "b" * 64}
    second = {**first, "time": after, "transaction_id": "a" * 64}
    calls, regex = [], incident_owner.re

    def reject(*args, **kwargs):
        raise AssertionError("unexpected facade lookup")

    class Regex:
        @staticmethod
        def search(pattern, raw):
            calls.append("search")
            return regex.search(pattern, raw)

    def lane(value):
        calls.append(("lane", value))
        return "hot-post"

    def distance(a, b):
        calls.append((a, b))
        return abs((a - b).total_seconds())

    monkeypatch.setattr(incident_owner, "re", Regex)
    monkeypatch.setattr(incident_owner, "_normalise_lane", lane)
    monkeypatch.setattr(digest, "re", None)
    monkeypatch.setattr(digest, "_normalise_lane", reject)
    inputs = dict(ambiguous_reply_outcomes=[{"lane": "mention"}, first, second],
                  transport_attempts=[], ambiguous_media_outcomes=[], seconds_between=distance)
    raw = "lane=alias target_id=123 transaction_id=" + "a" * 64
    result = incident_owner._matching_ambiguity_identity(raw, BASE, **inputs)
    assert result is first
    result["shared"] = []
    assert inputs["ambiguous_reply_outcomes"][1]["shared"] is result["shared"]
    assert calls == ["search", ("lane", "alias"), (BASE, before), (BASE, after),
                     (BASE, before), (BASE, after)]

    calls.clear()
    outside = BASE - timedelta(seconds=300.001)
    inputs.update(ambiguous_reply_outcomes=[], transport_attempts=[
        {"target_id": "other"}, {**first, "time": outside}, first, second,
    ])
    fallback = incident_owner._matching_ambiguity_identity(raw, BASE, **inputs)
    assert fallback == {"time": BASE, "lane": "hot-post", "target_id": "123", "transaction_id": "b" * 64}
    assert fallback["time"] is BASE and fallback is not first
    assert calls == ["search", ("lane", "alias"), (BASE, outside), (BASE, before),
                     (BASE, after), (BASE, before), (BASE, after)]

    calls.clear()
    assert incident_owner._is_subordinate_remote_write_symptom(
        category="conversational_reply_receipt_barrier", raw=raw, item_time=BASE,
        ambiguous_reply_outcomes=[second, {}], ambiguity_times=None, seconds_between=reject,
    ) is True
    assert calls == ["search", ("lane", "alias")]
    failure = ValueError("supplied distance failed")

    def failed_distance(a, b):
        raise failure

    with pytest.raises(ValueError) as raised:
        incident_owner._matching_ambiguity_identity(raw, BASE, **{**inputs, "seconds_between": failed_distance})
    assert raised.value is failure


def test_association_transaction_first_match_nearest_uniqueness_and_persistent_ties():
    far = BASE - timedelta(seconds=301)
    first = {"time": far, "lane": "mention", "target_id": "123", "transaction_id": "a" * 64}
    second = {**first, "time": BASE}

    def reject(*args):
        raise AssertionError("transaction match must not evaluate distance")

    inputs = dict(ambiguous_reply_outcomes=[first, second], transport_attempts=[],
                  ambiguous_media_outcomes=[], seconds_between=reject)
    assert incident_owner._matching_ambiguity_identity(
        "transaction_id=" + "a" * 64 + " transaction_id=" + "b" * 64, BASE, **inputs,
    ) is first
    inputs["seconds_between"] = digest.seconds_between
    assert incident_owner._matching_ambiguity_identity("transaction_id=" + "A" * 64, BASE, **inputs) is second

    before, after = BASE - timedelta(seconds=10), BASE + timedelta(seconds=10)
    first["time"], second["time"] = before, after
    assert incident_owner._matching_ambiguity_identity("ambiguous", BASE, **inputs) is first
    second["target_id"] = "other"
    media = {"time": before, "image": "fixture.png"}
    inputs["ambiguous_media_outcomes"].append(media)
    assert incident_owner._matching_ambiguity_identity("ambiguous", BASE, **inputs) is media
    inputs["ambiguous_media_outcomes"].append(dict(media))
    assert incident_owner._matching_ambiguity_identity("ambiguous", BASE, **inputs) is None
    first["time"] = media["time"] = BASE - timedelta(seconds=11)
    inputs["ambiguous_media_outcomes"][:] = [media]
    second["time"] = BASE + timedelta(seconds=11)
    assert incident_owner._matching_ambiguity_identity(
        "Normal reply lane stopped by the global remote-write safety barrier\nretained detail",
        BASE, **inputs,
    ) is first


@pytest.mark.parametrize("category,raw,offsets,expected", [
    ("conversational_reply_receipt_barrier", "lane=hot_post target_id=123",
     [-0.001, 0, 300, 300.001], [False, True, True, False]),
    ("remote_write_transaction_barrier", "transaction barrier",
     [-5.001, -5, 0, 5, 5.001], [False, True, True, True, False]),
    ("unclassified", "Normal reply lane stopped by the global remote-write safety barrier",
     [-5.001, -5, 0, 0.001], [False, True, True, False]),
])
def test_subordinate_association_keeps_distinct_directional_boundaries(category, raw, offsets, expected):
    calls = []

    def distance(a, b):
        assert category == "remote_write_transaction_barrier"
        calls.append((a, b))
        return abs((a - b).total_seconds())

    for offset, matches in zip(offsets, expected):
        outcome_time = BASE + timedelta(seconds=offset)
        outcomes = [{"lane": "hot-post", "target_id": "123", "time": outcome_time}]
        times = [outcome_time]
        if matches:
            outcomes.append({})
            times.append(None)
        assert incident_owner._is_subordinate_remote_write_symptom(
            category=category, raw=raw, item_time=BASE, ambiguous_reply_outcomes=outcomes,
            ambiguity_times=times, seconds_between=distance,
        ) is matches
    assert calls == ([(BASE, BASE + timedelta(seconds=offset)) for offset in offsets]
                     if category == "remote_write_transaction_barrier" else [])
    if category == "unclassified":
        assert incident_owner._is_subordinate_remote_write_symptom(
            category=category, raw=raw + "; unrelated failure", item_time=BASE,
            ambiguous_reply_outcomes=[{}], ambiguity_times=None, seconds_between=distance,
        ) is False


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

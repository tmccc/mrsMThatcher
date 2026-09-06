from __future__ import annotations

import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_state_loading as loading
from tests.test_unit_helpers import bot, isolate_regular_post_receipt


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('state loading import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_state_loading':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_state_loading
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


def test_adapter_forwards_all_current_dependencies_and_original_return(monkeypatch):
    dependencies = (
        "STATE_BACKUP_COUNT", "STATE_FILE", "STATE_MINIMUM_READER_VERSION",
        "STATE_PREVIOUS_READER_COMPATIBILITY_FENCES", "STATE_READER_COMPATIBILITY_FENCE",
        "UnsafeDurableStateNamespace", "default_state", "json", "log", "log_event",
        "log_json_debug", "normalise_state_candidate", "read_stable_owned_json_bytes_no_follow",
        "require_compatible_state_reader", "save_state", "state_debug_summary",
    )
    adapter = bot.load_state
    assert str(inspect.signature(adapter)) == "() -> 'dict'"
    assert tuple(inspect.signature(loading.load_state).parameters) == dependencies
    monkeypatch.setattr(bot, "Path", object())  # Postponed/local annotations only.
    for _ in range(2):
        current = {key: object() for key in dependencies}
        owner = Mock(return_value={"original": []})
        monkeypatch.setattr(bot, "_state_loading", SimpleNamespace(load_state=owner))
        for key, value in current.items():
            monkeypatch.setattr(bot, key, value)
        assert adapter() is owner.return_value
        owner.assert_called_once_with(**current)
        assert all(owner.call_args.kwargs[key] is value for key, value in current.items())
    failure = TypeError("current owner failure")
    owner.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter()
    assert caught.value is failure


@pytest.fixture
def observed_load(monkeypatch):
    trace = Mock()
    trace.read.return_value = (True, b"{}")
    trace.loads.side_effect = json.loads
    trace.reader.return_value = 1
    trace.normalise.side_effect = lambda state, **_options: state
    for name, callback in (
        ("read_stable_owned_json_bytes_no_follow", trace.read),
        ("require_compatible_state_reader", trace.reader),
        ("normalise_state_candidate", trace.normalise),
        ("log_event", trace.event), ("save_state", trace.save),
        ("state_debug_summary", trace.summary), ("log_json_debug", trace.debug),
        ("default_state", trace.default),
    ):
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "json", SimpleNamespace(loads=trace.loads))
    monkeypatch.setattr(bot, "log", trace.log)
    return trace


def test_primary_keeps_raw_normalized_and_recovery_list_references_and_exact_repair_order(monkeypatch, observed_load):
    trace = observed_load
    primary, latest = bot.STATE_FILE, bot.STATE_FILE.with_name("bot_state.json.bak1")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    monkeypatch.setattr(bot, "STATE_MINIMUM_READER_VERSION", 7)
    previous = {"previous": 3}
    monkeypatch.setattr(bot, "STATE_PREVIOUS_READER_COMPATIBILITY_FENCES", (previous,))
    raw = {path: {"pending_reply_drafts": dict(previous), "extension": []} for path in (primary, latest)}
    trace.loads.side_effect = [raw[primary], raw[latest]]
    normalized = {"extension": []}
    lists = {}
    events = [
        {"kind": "quote_cursor_suppression_pruned", "discarded_entries": 2},
        {"reason": "orphaned_pending_candidates", "discarded_candidates": 3},
        {"reason": "continuation_token_limit", "since_id": "99"},
        {"reason": "stale_pending_authority", "details": []},
    ]

    def reader(state, *, path):
        assert state is raw[path] and state["pending_reply_drafts"] == previous
        assert "minimum_reader_version" not in state
        return 3

    def normalise(state, *, path, recovery_events, recover_pending_identity):
        assert state is raw[path] and "pending_reply_drafts" not in state
        assert state["minimum_reader_version"] == 7
        assert recover_pending_identity is False
        lists[path] = recovery_events
        if path == primary:
            recovery_events.append(events[0])
            return normalized
        # Mutating the first callback's list proves the loader retained that list.
        lists[primary].extend(events[1:])
        recovery_events.append({"reason": "unselected_latest"})
        return dict(normalized)

    trace.reader.side_effect = reader
    trace.normalise.side_effect = normalise
    assert bot.load_state() is normalized
    assert lists[primary] is not lists[latest]
    assert [c[0] for c in trace.mock_calls] == [
        "log.debug", "read", "loads", "reader", "normalise",
        "read", "loads", "reader", "normalise", "log.info",
        "log.warning", "event", "log.warning", "event", "log.warning", "event",
        "save", "summary", "debug",
    ]
    assert trace.read.call_args_list == [call(primary), call(latest)]
    trace.log.debug.assert_called_once_with("Loading state from %s", primary)
    trace.log.info.assert_called_once_with(
        "Pruned %s malformed, expired, or excess quote cursor suppression entry or entries while loading %s",
        2, primary,
    )
    assert trace.log.warning.call_args_list == [
        call("Discarding %s uncovered pending mention candidate(s) without pagination provenance while loading %s; watermark remains unchanged and a reset guard was installed", 3, primary),
        call("Resetting oversized mention backlog while loading %s; watermark remains unchanged and pending candidates were discarded", primary),
        call("Resetting unsafe mention candidate authority while loading %s reason=%s; watermark remains unchanged", primary, "stale_pending_authority"),
    ]
    assert trace.event.call_args_list == [call("mention_backlog_reset", **event) for event in events[1:]]
    assert trace.event.call_args.kwargs["details"] is events[-1]["details"]
    trace.save.assert_called_once_with(normalized, durable=True)
    trace.summary.assert_called_once_with(normalized)
    assert trace.save.call_args.args[0] is trace.summary.call_args.args[0] is normalized
    trace.debug.assert_called_once_with("Loaded state summary", trace.summary.return_value)
    assert trace.debug.call_args.args[1] is trace.summary.return_value
    trace.default.assert_not_called()


def test_strict_backups_stop_at_first_usable_empty_dict_without_redundant_save(monkeypatch, observed_load):
    trace = observed_load
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 3)
    primary = bot.STATE_FILE
    first, second = (primary.with_name(f"{primary.name}.bak{i}") for i in (1, 2))
    trace.read.side_effect = [(True, b"{bad"), (True, b"[]"), (True, b"{}")]
    normalized = {}
    trace.normalise.side_effect = None
    trace.normalise.return_value = normalized
    assert bot.load_state() is normalized
    assert trace.read.call_args_list == [call(primary), call(first), call(second)]
    trace.log.exception.assert_called_once_with("Failed loading state candidate %s", primary)
    trace.log.error.assert_called_once_with("State file candidate %s is not a JSON object; ignoring", first)
    trace.log.warning.assert_called_once_with("Recovered state from backup %s", second)
    assert trace.normalise.call_args.kwargs == {
        "path": second, "recovery_events": [], "recover_pending_identity": False,
    }
    assert [c[0] for c in trace.mock_calls][-3:] == ["log.warning", "summary", "debug"]
    trace.save.assert_not_called()
    trace.event.assert_not_called()
    trace.default.assert_not_called()


def test_pending_identity_retries_only_after_all_strict_candidates_and_keeps_selected_events(monkeypatch, observed_load):
    trace = observed_load
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    paths = [bot.STATE_FILE, *(bot.STATE_FILE.with_name(f"bot_state.json.bak{i}") for i in (1, 2))]
    attempts, event_lists, normalized = [], [], {}

    def normalise(state, *, path, recovery_events, recover_pending_identity):
        attempts.append((path, recover_pending_identity))
        event_lists.append(recovery_events)
        recovery_events.append({"reason": f"attempt-{len(attempts)}"})
        return normalized if recover_pending_identity and path == paths[1] else None

    trace.normalise.side_effect = normalise
    assert bot.load_state() is normalized
    assert attempts == [(path, False) for path in paths] + [(path, True) for path in paths[:2]]
    assert len({id(events) for events in event_lists}) == 5
    assert trace.read.call_args_list == [call(path) for path in paths + paths[:2]]
    trace.event.assert_called_once_with("mention_backlog_reset", reason="attempt-5")
    assert trace.log.warning.call_args_list == [
        call("Recovered state candidate %s by discarding corrupt pending mention identity and requiring a head refetch", paths[1]),
        call("Resetting unsafe mention candidate authority while loading %s reason=%s; watermark remains unchanged", paths[1], "attempt-5"),
    ]
    assert [c[0] for c in trace.mock_calls][-6:] == ["log.warning", "log.warning", "event", "save", "summary", "debug"]
    trace.save.assert_called_once_with(normalized, durable=True)
    assert trace.save.call_args.args[0] is normalized
    trace.default.assert_not_called()


def test_absent_or_none_bytes_use_original_default_without_recovery(monkeypatch, observed_load):
    trace = observed_load
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    paths = [bot.STATE_FILE, bot.STATE_FILE.with_name("bot_state.json.bak1")]
    trace.read.side_effect = [(False, b"ignored"), (True, None)]
    trace.default.return_value = {}
    assert bot.load_state() is trace.default.return_value
    assert trace.mock_calls == [
        call.log.debug("Loading state from %s", paths[0]),
        call.read(paths[0]), call.log.warning("State file candidate does not exist: %s", paths[0]),
        call.read(paths[1]), call.log.warning("State file candidate does not exist: %s", paths[1]),
        call.log.error("No state file or backup found; using default state"), call.default(),
    ]


@pytest.mark.parametrize("minimum,drafts,message", [
    (1, {"legacy": "draft"}, "Legacy V1 reply drafts remain in {path}; refusing to interpret or post them through the AI-first strategy"),
    (7, {}, "State candidate {path} declares minimum reader version 7 without the exact compatibility fence; refusing unsafe rollback state"),
])
def test_legacy_rejection_skips_only_unneeded_latest_before_mutation(minimum, drafts, message, monkeypatch, observed_load):
    trace = observed_load
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    monkeypatch.setattr(bot, "STATE_MINIMUM_READER_VERSION", 7)
    primary, latest = bot.STATE_FILE, bot.STATE_FILE.with_name("bot_state.json.bak1")
    raw = {"pending_reply_drafts": drafts}
    trace.loads.side_effect = [{}, raw]
    trace.reader.side_effect = [1, minimum]
    normalized = {}
    trace.normalise.side_effect = None
    trace.normalise.return_value = normalized
    assert bot.load_state() is normalized
    assert trace.read.call_args_list == [call(primary), call(latest)]
    assert trace.normalise.call_count == 1
    trace.log.warning.assert_called_once_with(
        "%s; candidate is not needed because primary state is usable", message.format(path=latest),
    )
    assert raw == {"pending_reply_drafts": drafts}
    trace.save.assert_not_called()
    trace.reset_mock()
    trace.loads.side_effect = [raw]
    trace.reader.side_effect = [minimum]
    with pytest.raises(RuntimeError) as caught:
        bot.load_state()
    assert str(caught.value) == message.format(path=primary)
    trace.log.critical.assert_called_once_with(str(caught.value))
    trace.read.assert_called_once_with(primary)
    trace.normalise.assert_not_called()
    assert raw["pending_reply_drafts"] is drafts and "minimum_reader_version" not in raw


def test_unsafe_latest_namespace_uses_current_exception_and_refuses_fallback(monkeypatch, observed_load):
    trace = observed_load

    class CurrentNamespaceError(RuntimeError):
        pass

    monkeypatch.setattr(bot, "UnsafeDurableStateNamespace", CurrentNamespaceError)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 3)
    failure = CurrentNamespaceError("unsafe latest")
    trace.read.side_effect = [(True, b"{}"), failure]
    latest = bot.STATE_FILE.with_name("bot_state.json.bak1")
    with pytest.raises(CurrentNamespaceError) as caught:
        bot.load_state()
    assert caught.value is failure
    assert trace.read.call_args_list == [call(bot.STATE_FILE), call(latest)]
    trace.log.critical.assert_called_once_with(
        "State candidate namespace is unsafe; refusing backup fallback: %s", latest, exc_info=True,
    )
    trace.log.exception.assert_not_called()
    trace.save.assert_not_called()
    trace.summary.assert_not_called()
    trace.default.assert_not_called()


@pytest.mark.parametrize("boundary,error_type", [
    ("read", OSError), ("loads", KeyboardInterrupt),
    ("reader", bot.IncompatibleStateReaderError), ("normalise", ValueError),
])
def test_native_failures_outside_ordinary_decode_catch_never_fall_back(boundary, error_type, monkeypatch, observed_load):
    trace = observed_load
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    failure = error_type("native failure")
    getattr(trace, boundary).side_effect = failure
    with pytest.raises(error_type) as caught:
        bot.load_state()
    assert caught.value is failure
    steps = ["read", "loads", "reader", "normalise"]
    assert [c[0] for c in trace.mock_calls] == ["log.debug", *steps[:steps.index(boundary) + 1]]
    trace.read.assert_called_once_with(bot.STATE_FILE)


def test_divergence_precedes_recovery_events_and_repair_save(monkeypatch, observed_load):
    trace = observed_load
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 3)

    def normalise(state, *, path, recovery_events, **_options):
        recovery_events.append({"reason": "repair"})
        return {"candidate": path}

    trace.normalise.side_effect = normalise
    message = "Primary state and latest committed backup are both valid but diverge; refusing to guess which durable generation is newer"
    with pytest.raises(RuntimeError) as caught:
        bot.load_state()
    assert str(caught.value) == message
    latest = bot.STATE_FILE.with_name("bot_state.json.bak1")
    assert trace.read.call_args_list == [call(bot.STATE_FILE), call(latest)]
    trace.log.critical.assert_called_once_with("%s primary=%s backup=%s", message, bot.STATE_FILE, latest)
    trace.event.assert_not_called()
    trace.save.assert_not_called()
    trace.summary.assert_not_called()
    trace.default.assert_not_called()


def test_repair_save_failure_propagates_before_summary_and_post_load_maintenance(monkeypatch, observed_load):
    trace = observed_load
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    monkeypatch.setattr(bot, "clear_expired_api_cooldowns", trace.cooldowns)
    monkeypatch.setattr(bot, "sanitize_next_reply_lane_priority", trace.priority)
    trace.read.side_effect = [(True, b"{bad"), (True, b"{}")]
    failure, normalized = OSError("repair failed"), {}
    trace.save.side_effect = failure

    def normalise(state, *, recovery_events, **_options):
        recovery_events.append({"reason": "repair", "discarded_candidates": 1})
        return normalized

    trace.normalise.side_effect = normalise
    with pytest.raises(OSError) as caught:
        bot.load_runtime_state()
    assert caught.value is failure
    trace.save.assert_called_once_with(normalized, durable=True)
    assert trace.save.call_args.args[0] is normalized
    assert [c[0] for c in trace.mock_calls][-4:] == ["log.warning", "log.warning", "event", "save"]
    trace.event.assert_called_once_with("mention_backlog_reset", reason="repair", discarded_candidates=1)
    assert trace.read.call_args_list == [call(bot.STATE_FILE), call(bot.STATE_FILE.with_name("bot_state.json.bak1"))]
    trace.summary.assert_not_called()
    trace.debug.assert_not_called()
    trace.default.assert_not_called()
    trace.cooldowns.assert_not_called()
    trace.priority.assert_not_called()

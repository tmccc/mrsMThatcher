"""Contracts for remote-write incident extraction and mutation/error ordering."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import mrsMThatcher2 as bot
import mrs_bot_remote_write_incidents as owner
from tests.test_unit_helpers import isolate_regular_post_receipt  # noqa: F401

DEPENDENCIES = {'durable_remote_write_safety_marker_exists': ['_set_ambiguous_marker_durability_uncertain',
                                               'acknowledge_durable_remote_write_safety_marker',
                                               'latch_remote_write_safety_marker_observation',
                                               'log',
                                               'release_retained_sigint_deferral_after_durable_barrier',
                                               'remote_write_safety_marker_path_present_or_unsafe',
                                               'remote_write_safety_protocol_is_active'],
 'record_ambiguous_remote_post': ['AMBIGUOUS_POST_OUTCOME_FILE',
                                  '_set_ambiguous_marker_durability_uncertain',
                                  '_set_ambiguous_remote_post_seen',
                                  'ensure_durable_remote_write_safety_marker',
                                  'hashlib',
                                  'log',
                                  'now_epoch'],
 'latch_confirmed_post_persistence_failure': ['AMBIGUOUS_POST_OUTCOME_FILE',
                                              '_set_ambiguous_marker_durability_uncertain',
                                              '_set_ambiguous_remote_post_seen',
                                              'ensure_durable_remote_write_safety_marker',
                                              'hashlib',
                                              'json',
                                              'log',
                                              'now_epoch']}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Remote-write-incident import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'engagement_question_experiment', 'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_remote_write_incidents':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
datetime.datetime = type("ForbiddenDatetime", (), dict(now=forbidden, fromtimestamp=forbidden, today=forbidden))
zoneinfo.ZoneInfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_remote_write_incidents
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_remote_write_incidents.durable_remote_write_safety_marker_exists.__annotations__['return'] == 'bool'
assert mrs_bot_remote_write_incidents.latch_confirmed_post_persistence_failure.__annotations__['failure_components'] == 'list[str]'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", list(DEPENDENCIES))
def test_public_signatures_and_current_adapter_references(monkeypatch, name):
    signatures = {
        "durable_remote_write_safety_marker_exists": "() -> 'bool'",
        "record_ambiguous_remote_post": "(payload: 'dict') -> 'None'",
        "latch_confirmed_post_persistence_failure": (
            "(*, lane: 'str', post_id: 'str', failure_components: 'list[str]') -> 'bool'"
        ),
    }
    public, extracted = getattr(bot, name), getattr(owner, name)
    assert bot._remote_write_incidents is owner
    assert str(inspect.signature(public)) == signatures[name]
    assert public.__doc__ == extracted.__doc__
    signature = inspect.signature(extracted)
    assert signature.replace(parameters=[
        p for key, p in signature.parameters.items() if key not in DEPENDENCIES[name]
    ]) == inspect.signature(public)
    for dep in DEPENDENCIES[name]:
        assert signature.parameters[dep].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters[dep].default is inspect.Parameter.empty
    args = (object(),) if name == "record_ambiguous_remote_post" else ()
    kwargs = ({key: object() for key in ("lane", "post_id", "failure_components")}
              if name == "latch_confirmed_post_persistence_failure" else {})
    for _ in range(2):
        dependencies = {dep: object() for dep in DEPENDENCIES[name]}
        for dep, value in dependencies.items():
            monkeypatch.setattr(bot, dep, value)
        result = object()
        callback = Mock(return_value=result)
        monkeypatch.setattr(owner, name, callback)
        assert public(*args, **kwargs) is result
        assert callback.call_args.args == args
        assert all(a is b for a, b in zip(callback.call_args.args, args))
        assert callback.call_args.kwargs == {**kwargs, **dependencies}
        assert all(callback.call_args.kwargs[key] is value
                   for key, value in {**kwargs, **dependencies}.items())


@pytest.mark.parametrize("flag,doc", [
    ("_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN",
     "Set the root's uncertainty about remote-write marker durability."),
    ("_AMBIGUOUS_REMOTE_POST_SEEN",
     "Set the root's process-local remote-write incident latch."),
])
def test_exact_setters_assign_only_the_current_root_reference(monkeypatch, flag, doc):
    name = "_set" + flag.lower()
    setter = getattr(bot, name)
    assert inspect.getsource(setter) == (
        f"def {name}(value: bool) -> None:\n"
        f'    """{doc}"""\n'
        f"    global {flag}\n"
        f"    {flag} = value\n"
    )
    other = ("_AMBIGUOUS_REMOTE_POST_SEEN" if flag.endswith("UNCERTAIN")
             else "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN")
    untouched, replacement = object(), object()
    monkeypatch.setattr(bot, other, untouched)
    assert setter(replacement) is None
    assert getattr(bot, flag) is replacement
    assert getattr(bot, other) is untouched
    assert not hasattr(owner, flag)


def _trace_latches(monkeypatch):
    events = []
    for label, name in (
        ("seen", "_set_ambiguous_remote_post_seen"),
        ("uncertain", "_set_ambiguous_marker_durability_uncertain"),
    ):
        original = getattr(bot, name)

        def assign(value, label=label, original=original):
            events.append((label, value))
            original(value)

        monkeypatch.setattr(bot, name, assign)
    return events


@pytest.mark.parametrize("active,present,expected", [
    (False, True, ["protocol"]),
    (True, False, ["protocol", "path"]),
])
def test_durable_marker_short_circuits_before_observation(monkeypatch, active, present, expected):
    events = _trace_latches(monkeypatch)
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active",
                        lambda: events.append("protocol") or active)
    monkeypatch.setattr(bot, "remote_write_safety_marker_path_present_or_unsafe",
                        lambda: events.append("path") or present)
    for name in ("latch_remote_write_safety_marker_observation",
                 "acknowledge_durable_remote_write_safety_marker",
                 "release_retained_sigint_deferral_after_durable_barrier"):
        monkeypatch.setattr(bot, name, Mock(side_effect=AssertionError(name)))
    assert bot.durable_remote_write_safety_marker_exists() is False
    assert events == expected
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_durable_observation_precedes_narrow_acknowledgement_catch(monkeypatch, error_type):
    events = _trace_latches(monkeypatch)
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active", lambda: True)
    monkeypatch.setattr(bot, "remote_write_safety_marker_path_present_or_unsafe", lambda: True)
    failure = error_type("acknowledgement failed")

    def acknowledge():
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
        events.append("acknowledge")
        raise failure

    log, release = Mock(), Mock()
    monkeypatch.setattr(bot, "acknowledge_durable_remote_write_safety_marker", acknowledge)
    monkeypatch.setattr(bot, "release_retained_sigint_deferral_after_durable_barrier", release)
    monkeypatch.setattr(bot, "log", log)
    if error_type is OSError:
        assert bot.durable_remote_write_safety_marker_exists() is False
        log.critical.assert_called_once_with(
            "The remote-write safety marker cannot be durably acknowledged; "
            "the process-local latch and deferred SIGINT remain active", exc_info=True,
        )
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.durable_remote_write_safety_marker_exists()
        assert caught.value is failure
        log.critical.assert_not_called()
    assert events == ["acknowledge"]
    release.assert_not_called()
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True


def test_durable_success_clears_uncertainty_before_native_release_error(monkeypatch):
    events = _trace_latches(monkeypatch)
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active", lambda: True)
    monkeypatch.setattr(bot, "remote_write_safety_marker_path_present_or_unsafe", lambda: True)
    monkeypatch.setattr(bot, "acknowledge_durable_remote_write_safety_marker",
                        lambda: events.append("acknowledge") or False)
    failure = KeyboardInterrupt("deferred delivery")

    def release():
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
        events.append("release")
        raise failure

    monkeypatch.setattr(bot, "release_retained_sigint_deferral_after_durable_barrier", release)
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.durable_remote_write_safety_marker_exists()
    assert caught.value is failure
    assert events == ["acknowledge", ("uncertain", False), "release"]


@pytest.mark.parametrize("boundary", ["text", "failures", "identity"])
def test_incident_flags_precede_native_pretry_errors(monkeypatch, boundary):
    events = _trace_latches(monkeypatch)
    failure = ValueError("conversion before durable-write try")

    class BadText:
        def __str__(self):
            events.append("text")
            raise failure

    class BadFailures:
        def __iter__(self):
            events.append("failures")
            raise failure

    def bad_identity(*args, **kwargs):
        events.append("identity")
        raise failure

    clock, ensure, log = Mock(), Mock(), Mock()
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "ensure_durable_remote_write_safety_marker", ensure)
    monkeypatch.setattr(bot, "log", log)
    if boundary == "identity":
        monkeypatch.setattr(bot, "json", SimpleNamespace(dumps=bad_identity))
    with pytest.raises(ValueError) as caught:
        if boundary == "text":
            bot.record_ambiguous_remote_post({"text": BadText()})
        else:
            bot.latch_confirmed_post_persistence_failure(
                lane="quote_image", post_id="61",
                failure_components=BadFailures() if boundary == "failures" else [],
            )
    assert caught.value is failure
    assert events == [("seen", True), ("uncertain", True), boundary]
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
    clock.assert_not_called()
    ensure.assert_not_called()
    log.critical.assert_not_called()


def test_ambiguous_marker_preserves_conversion_hash_and_write_order(monkeypatch):
    events = _trace_latches(monkeypatch)
    media_id, clock_value = object(), object()
    media_ids = [media_id]

    class Payload(dict):
        def get(self, key):
            events.append(key)
            return super().get(key)

    class Text:
        def __str__(self):
            events.append("str")
            return "  café\r\n"

    def hash_text(value):
        events.append(("hash", value))
        return hashlib.sha256(value)

    captured = []

    def ensure(marker):
        events.append("ensure")
        captured.append(marker)
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True
        return False

    monkeypatch.setattr(bot, "now_epoch", lambda: events.append("clock") or clock_value)
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=hash_text))
    monkeypatch.setattr(bot, "ensure_durable_remote_write_safety_marker", ensure)
    log = Mock()
    log.critical.side_effect = lambda *args: events.append("log")
    monkeypatch.setattr(bot, "log", log)
    payload = Payload(text=Text(), reply={"in_reply_to_tweet_id": 61},
                      media={"media_ids": media_ids}, made_with_ai="yes")
    assert bot.record_ambiguous_remote_post(payload) is None
    assert events == [("seen", True), ("uncertain", True), "text", "str", "clock",
                      ("hash", b"  caf\xc3\xa9\r\n"), "reply", "media", "made_with_ai",
                      "ensure", ("uncertain", False), "log"]
    marker = captured[0]
    assert list(marker) == ["schema_version", "recorded_at_epoch", "outcome", "text_sha256",
                            "reply_to_id", "media_ids", "made_with_ai"]
    assert marker == {"schema_version": 1, "recorded_at_epoch": clock_value,
                      "outcome": "ambiguous_remote_post",
                      "text_sha256": hashlib.sha256(b"  caf\xc3\xa9\r\n").hexdigest(),
                      "reply_to_id": "61", "media_ids": [media_id], "made_with_ai": True}
    assert marker["recorded_at_epoch"] is clock_value
    assert marker["media_ids"] is not media_ids
    assert marker["media_ids"][0] is media_id
    assert log.critical.call_args.args[-1] is bot.AMBIGUOUS_POST_OUTCOME_FILE
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_ambiguous_nested_conversion_keeps_exception_only_scope(monkeypatch, error_type):
    failure = error_type("reply conversion")

    class Reply:
        def get(self, key):
            raise failure

    ensure, log = Mock(), Mock()
    monkeypatch.setattr(bot, "now_epoch", lambda: 61)
    monkeypatch.setattr(bot, "ensure_durable_remote_write_safety_marker", ensure)
    monkeypatch.setattr(bot, "log", log)
    if error_type is ValueError:
        assert bot.record_ambiguous_remote_post({"reply": Reply()}) is None
        log.critical.assert_called_once_with(
            "AMBIGUOUS REMOTE X POST OUTCOME: the durable safety marker could not be "
            "written. The process-local latch remains active; do not restart this "
            "process before manual reconciliation.", exc_info=True,
        )
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.record_ambiguous_remote_post({"reply": Reply()})
        assert caught.value is failure
        log.critical.assert_not_called()
    ensure.assert_not_called()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True


@pytest.mark.parametrize("clock_fails", [False, True])
def test_confirmed_identity_repeated_conversions_clock_and_marker_order(monkeypatch, clock_fails):
    events = _trace_latches(monkeypatch)
    clock_value = object()

    class Value:
        def __init__(self, text):
            self.text = text

        def __str__(self):
            events.append(("str", self.text))
            return self.text

    lane, post = Value("quote_image"), Value("61")

    def dumps(value, **kwargs):
        events.append("json")
        assert list(value) == ["failure_components", "lane", "post_id"]
        assert kwargs == {"sort_keys": True, "separators": (",", ":")}
        return json.dumps(value, **kwargs)

    def clock():
        events.append("clock")
        if clock_fails:
            raise OSError("clock unavailable")
        return clock_value

    identity = b'{"failure_components":["a","z"],"lane":"quote_image","post_id":"61"}'

    def hash_identity(value):
        events.append("hash")
        assert value == identity
        return hashlib.sha256(value)

    captured = []

    def ensure(marker):
        events.append("ensure")
        captured.append(marker)
        return False

    log = Mock()
    log.critical.side_effect = lambda *args: events.append("log")
    monkeypatch.setattr(bot, "json", SimpleNamespace(dumps=dumps))
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=hash_identity))
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "ensure_durable_remote_write_safety_marker", ensure)
    monkeypatch.setattr(bot, "log", log)
    assert bot.latch_confirmed_post_persistence_failure(
        lane=lane, post_id=post, failure_components=[Value(x) for x in ("z", "", "a", "z")],
    ) is True
    assert events == [("seen", True), ("uncertain", True), ("str", "z"), ("str", "z"),
                      ("str", ""), ("str", "a"), ("str", "a"), ("str", "z"), ("str", "z"),
                      ("str", "quote_image"), ("str", "61"), "json", "clock",
                      ("str", "quote_image"), ("str", "61"), "hash", "ensure",
                      ("uncertain", False), "log"]
    marker = captured[0]
    time_key = "recorded_at_unavailable" if clock_fails else "recorded_at_epoch"
    assert list(marker) == ["schema_version", "outcome", "lane", "post_id",
                            "failure_components", "incident_sha256", time_key]
    assert marker == {"schema_version": 1,
                      "outcome": "confirmed_remote_post_local_persistence_failed",
                      "lane": "quote_image", "post_id": "61", "failure_components": ["a", "z"],
                      "incident_sha256": hashlib.sha256(identity).hexdigest(),
                      time_key: True if clock_fails else clock_value}
    assert marker[time_key] is (True if clock_fails else clock_value)
    args = log.critical.call_args.args
    assert args[1] is post and args[2] is lane
    assert args[3] is marker["failure_components"]
    assert args[4] is bot.AMBIGUOUS_POST_OUTCOME_FILE
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True


def test_confirmed_marker_hash_error_remains_outside_durable_write_catch(monkeypatch):
    failure = ValueError("identity hashing failed")
    ensure, log = Mock(), Mock()
    monkeypatch.setattr(bot, "now_epoch", lambda: 61)
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=Mock(side_effect=failure)))
    monkeypatch.setattr(bot, "ensure_durable_remote_write_safety_marker", ensure)
    monkeypatch.setattr(bot, "log", log)
    with pytest.raises(ValueError) as caught:
        bot.latch_confirmed_post_persistence_failure(lane="quote_image", post_id="61", failure_components=[])
    assert caught.value is failure
    ensure.assert_not_called()
    log.critical.assert_not_called()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is True


@pytest.mark.parametrize("name", ["record_ambiguous_remote_post", "latch_confirmed_post_persistence_failure"])
def test_recorders_clear_only_uncertainty_before_native_success_log_error(monkeypatch, name):
    failure = OSError("logging failed")
    events = _trace_latches(monkeypatch)
    monkeypatch.setattr(bot, "now_epoch", lambda: 61)
    monkeypatch.setattr(bot, "ensure_durable_remote_write_safety_marker",
                        lambda marker: events.append("ensure") or False)

    def critical(*args):
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert bot._AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN is False
        events.append("log")
        raise failure

    monkeypatch.setattr(bot, "log", SimpleNamespace(critical=critical))
    with pytest.raises(OSError) as caught:
        if name == "record_ambiguous_remote_post":
            bot.record_ambiguous_remote_post({})
        else:
            bot.latch_confirmed_post_persistence_failure(lane="quote_image", post_id="61", failure_components=[])
    assert caught.value is failure
    assert events == [("seen", True), ("uncertain", True), "ensure", ("uncertain", False), "log"]

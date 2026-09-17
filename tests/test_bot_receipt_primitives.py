"""Contracts for receipt scalar values, calendars and confirmation time."""
from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
import mrs_bot_receipt_primitives as owner
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401

DEPENDENCIES = {'valid_post_id': ['re'],
 'valid_string_post_id': ['valid_post_id'],
 'valid_receipt_epoch': ['MAX_CONFIRMATION_EPOCH', 'MIN_CONFIRMATION_EPOCH'],
 'receipt_int': [],
 'receipt_bool': [],
 'safe_epoch_date_str': ['epoch_date_str'],
 'safe_reply_cap_date_str': ['reply_cap_date_str'],
 'main_post_schedule_zone': ['MAIN_POST_SCHEDULE_TIMEZONE', 'ZoneInfo', 'ZoneInfoNotFoundError'],
 'bound_schedule_datetime': ['datetime', 'main_post_schedule_zone'],
 'safe_bound_schedule_date_str': ['bound_schedule_datetime'],
 'valid_receipt_basename': ['Path'],
 'confirmation_epoch_after_remote_success': ['TransportJournalError',
                                             'log',
                                             'now_epoch',
                                             'receipt_int',
                                             'valid_receipt_epoch'],
 'epoch_date_str': ['datetime', 'now_epoch'],
 'reply_cap_date_str': ['MAIN_POST_SCHEDULE_TIMEZONE', 'ZoneInfo', 'datetime', 'now_epoch']}

SIGNATURES = {'valid_post_id': "(value: 'object') -> 'bool'",
 'valid_string_post_id': "(value: 'object') -> 'bool'",
 'valid_receipt_epoch': "(value: 'object') -> 'bool'",
 'receipt_int': "(value: 'object', default: 'int | None' = None) -> 'int | None'",
 'receipt_bool': "(value: 'object') -> 'bool | None'",
 'safe_epoch_date_str': "(epoch: 'int') -> 'str | None'",
 'safe_reply_cap_date_str': "(epoch: 'int') -> 'str | None'",
 'main_post_schedule_zone': "(timezone_name: 'object') -> 'ZoneInfo'",
 'bound_schedule_datetime': "(epoch: 'int', timezone_name: 'object') -> 'datetime'",
 'safe_bound_schedule_date_str': "(epoch: 'int', timezone_name: 'object') -> 'str | None'",
 'valid_receipt_basename': "(value: 'object') -> 'bool'",
 'confirmation_epoch_after_remote_success': "(source_receipt: 'dict') -> 'int'",
 'epoch_date_str': "(epoch: 'int | None' = None) -> 'str'",
 'reply_cap_date_str': "(epoch: 'int | None' = None) -> 'str'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Receipt-primitives import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_receipt_primitives':
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
import mrs_bot_receipt_primitives
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_receipt_primitives.bound_schedule_datetime.__annotations__['return'] == 'datetime'
assert mrs_bot_receipt_primitives.main_post_schedule_zone.__annotations__['return'] == 'ZoneInfo'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout



@pytest.mark.parametrize("name", DEPENDENCIES)
def test_adapters_preserve_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    assert str(signature) == SIGNATURES[name]
    positional = [p.name for p in signature.parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in signature.parameters.values()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY]
    if not DEPENDENCIES[name]:
        assert adapter is getattr(owner, name)
        assert adapter.__annotations__ == getattr(owner, name).__annotations__
        return
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {dep: object() for dep in DEPENDENCIES[name]}
            for dep, value in current.items():
                patch.setattr(bot, dep, value)
            result = {"original": []}
            expected = {}

            def capture(*args, **kwargs):
                assert len(args) == len(positional)
                assert all(value is expected[key] for key, value in zip(positional, args))
                supplied = {key: expected[key] for key in keyword_only} | current
                assert kwargs.keys() == supplied.keys()
                assert all(kwargs[key] is value for key, value in supplied.items())
                return result

            patch.setattr(bot, "_receipt_primitives", SimpleNamespace(**{name: capture}))
            for include_defaults in (True, False):
                provided = {key: object() for key, param in signature.parameters.items()
                            if include_defaults or param.default is inspect.Parameter.empty}
                bound = signature.bind(**provided)
                bound.apply_defaults()
                expected = bound.arguments
                assert adapter(**provided) is result
            with pytest.raises(TypeError, match="not_a_public_option"):
                adapter(**provided, not_a_public_option={})
            failure = TypeError("current owner failure")
            patch.setattr(bot, "_receipt_primitives", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_post_ids_keep_unicode_digits_fullmatch_and_exact_string_gate(monkeypatch):
    class Text(str):
        pass

    for value, expected in [
        ("0", True), (0, False), (1, True), (None, False), (False, False),
        ("١２3", True), ("١" * 30, True), ("1" * 31, False),
        ("²", False), (" 12", False), ("12 ", False), ("12\n", False),
        ("+12", False), (Text("123"), True),
    ]:
        assert bot.valid_post_id(value) is expected
    result = object()
    validator = Mock(return_value=result)
    monkeypatch.setattr(bot, "valid_post_id", validator)
    for value in (Text("123"), 123, True, None):
        assert bot.valid_string_post_id(value) is False
    validator.assert_not_called()
    value = "123"
    assert bot.valid_string_post_id(value) is result
    assert validator.call_args.args[0] is value
    failure = LookupError("current validator")
    validator.side_effect = failure
    with pytest.raises(LookupError) as caught:
        bot.valid_string_post_id(value)
    assert caught.value is failure


@pytest.mark.parametrize("failure_at", [None, "truth", "text", "match", "match_truth"])
def test_post_id_truth_conversion_and_match_keep_native_order(monkeypatch, failure_at):
    events = []
    failure = ValueError("native digit predicate")

    def observe(stage):
        events.append(stage)
        if stage == failure_at:
            raise failure

    class Value:
        def __bool__(self):
            observe("truth")
            return True

        def __str__(self):
            observe("text")
            return "١2"

    class Match:
        def __bool__(self):
            observe("match_truth")
            return True

    def fullmatch(pattern, value):
        observe("match")
        assert pattern == r"\d{1,30}" and value == "١2"
        return Match()

    monkeypatch.setattr(bot, "re", SimpleNamespace(fullmatch=fullmatch))
    order = ["truth", "text", "match", "match_truth"]
    if failure_at is None:
        assert bot.valid_post_id(Value()) is True
        assert events == order
    else:
        with pytest.raises(ValueError) as caught:
            bot.valid_post_id(Value())
        assert caught.value is failure
        assert events == order[:order.index(failure_at) + 1]

    empty_match = Mock(return_value=None)
    monkeypatch.setattr(bot, "re", SimpleNamespace(fullmatch=empty_match))
    assert bot.valid_post_id(0) is False
    empty_match.assert_called_once_with(r"\d{1,30}", "")


def test_epoch_policy_exact_int_gate_and_chained_comparison_references(monkeypatch):
    events = []
    upper_result = object()

    class Gate:
        allowed = True

        def __bool__(self):
            events.append("truth")
            return self.allowed

    gate = Gate()

    class Lower:
        def __le__(self, value):
            events.append(("lower", value))
            return gate

    class Upper:
        def __ge__(self, value):
            events.append(("upper", value))
            return upper_result

    class Integer(int):
        def __int__(self):
            pytest.fail("no receipt integer coercion")

    monkeypatch.setattr(bot, "MIN_CONFIRMATION_EPOCH", Lower())
    monkeypatch.setattr(bot, "MAX_CONFIRMATION_EPOCH", Upper())
    for value in (True, False, Integer(12), 12.0, "12", None):
        assert bot.valid_receipt_epoch(value) is False
    assert events == []
    assert bot.valid_receipt_epoch(12) is upper_result
    assert events == [("lower", 12), "truth", ("upper", 12)]
    events.clear()
    gate.allowed = False
    assert bot.valid_receipt_epoch(12) is gate
    assert events == [("lower", 12), "truth"]
    failure = OSError("policy comparison")
    monkeypatch.setattr(bot, "MIN_CONFIRMATION_EPOCH", SimpleNamespace())
    with pytest.raises(TypeError):
        bot.valid_receipt_epoch(12)
    # A native comparison failure is never treated as an invalid scalar.
    class BrokenLower:
        def __le__(self, value):
            raise failure
    monkeypatch.setattr(bot, "MIN_CONFIRMATION_EPOCH", BrokenLower())
    with pytest.raises(OSError) as caught:
        bot.valid_receipt_epoch(12)
    assert caught.value is failure


def test_scalar_aliases_keep_exact_values_defaults_and_boolean_identity():
    class Integer(int):
        pass

    class NoConversion:
        def __bool__(self):
            pytest.fail("no scalar truth conversion")

        def __int__(self):
            pytest.fail("no scalar int conversion")

    value = int("1800000001")
    assert bot.receipt_int(value) is value
    for missing in (None, ""):
        assert bot.receipt_int(missing) is None
        for default in (0, False, [], NoConversion()):
            assert bot.receipt_int(missing, default) is default
    for invalid in (True, False, Integer(1), 1.0, "1", NoConversion()):
        assert bot.receipt_int(invalid, default=42) is None
    for boolean in (True, False):
        assert bot.receipt_bool(boolean) is boolean
    for invalid in (None, 0, 1, "false", NoConversion()):
        assert bot.receipt_bool(invalid) is None


@pytest.mark.parametrize("default", [None, object()])
def test_receipt_int_keeps_membership_before_default_and_native_equality(default):
    events = []

    class EqualToEmpty:
        def __eq__(self, other):
            events.append(other)
            return other == ""

        def __int__(self):
            pytest.fail("membership never coerces")

    assert bot.receipt_int(EqualToEmpty(), default) is default
    assert events == [None, ""]
    failure = KeyboardInterrupt("membership comparison")

    class BrokenEquality:
        def __eq__(self, other):
            raise failure

    with pytest.raises(KeyboardInterrupt) as caught:
        bot.receipt_int(BrokenEquality(), default)
    assert caught.value is failure


def test_basename_keeps_exact_type_path_name_and_native_reference_predicate(monkeypatch):
    class Text(str):
        pass

    for value, expected in [
        ("x.jpg", True), (" ", True), ("x\\y", True), ("x\0y", True),
        ("", False), (".", False), ("..", False), ("./x", False),
        ("x/", False), ("a/x", False), ("/x", False), (Text("x"), False),
        (Path("x"), False), (None, False),
    ]:
        assert bot.valid_receipt_basename(value) is expected
    events = []

    class Unequal:
        def __bool__(self):
            events.append("truth")
            return False

    result = Unequal()

    class Name:
        def __eq__(self, value):
            events.append(("name", value))
            return result

    path = Mock(return_value=SimpleNamespace(name=Name()))
    monkeypatch.setattr(bot, "Path", path)
    assert bot.valid_receipt_basename("") is False
    path.assert_not_called()
    assert bot.valid_receipt_basename("x") is result
    path.assert_called_once_with("x")
    assert events == [("name", "x"), "truth"]
    path.return_value = SimpleNamespace(name=".")
    assert bot.valid_receipt_basename(".") is False
    path.assert_called_with(".")
    failure = OSError("native path constructor")
    path.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.valid_receipt_basename("x")
    assert caught.value is failure


@pytest.mark.parametrize("name,callback,bound", [
    ("safe_epoch_date_str", "epoch_date_str", False),
    ("safe_reply_cap_date_str", "reply_cap_date_str", False),
    ("safe_bound_schedule_date_str", "bound_schedule_datetime", True),
])
def test_safe_dates_keep_result_references_formatting_and_distinct_catches(
    monkeypatch, name, callback, bound,
):
    args = (object(), object()) if bound else (object(),)
    result = object()
    formatter = Mock(return_value=result)
    callback_mock = Mock(return_value=SimpleNamespace(strftime=formatter) if bound else result)
    monkeypatch.setattr(bot, callback, callback_mock)
    wrapper = getattr(bot, name)
    assert wrapper(*args) is result
    assert all(a is b for a, b in zip(callback_mock.call_args.args, args))
    if bound:
        formatter.assert_called_once_with("%Y-%m-%d")
    for target in ([callback_mock, formatter] if bound else [callback_mock]):
        for exception in (TypeError, ValueError, OverflowError, OSError,
                          RuntimeError, LookupError, KeyboardInterrupt):
            failure = exception("native date failure")
            target.side_effect = failure
            if exception in (TypeError, ValueError, OverflowError, OSError) or (
                bound and exception is RuntimeError
            ):
                assert wrapper(*args) is None
            else:
                with pytest.raises(exception) as caught:
                    wrapper(*args)
                assert caught.value is failure
            target.side_effect = None


@pytest.mark.parametrize("name", ["epoch_date_str", "reply_cap_date_str"])
def test_calendars_keep_lazy_clock_current_authorities_and_evaluation_order(monkeypatch, name):
    events = []
    result, zone = object(), object()
    failure_at = None
    failure = LookupError("native calendar failure")

    def observe(stage):
        events.append(stage)
        if stage == failure_at:
            raise failure

    class Epoch:
        def __int__(self):
            observe("int")
            return 12

        def __bool__(self):
            pytest.fail("epoch selection must use is None")

    epoch = Epoch()

    def clock():
        observe("clock")
        return epoch

    def format_date(pattern):
        observe("format")
        assert pattern == "%Y-%m-%d"
        return result

    def fromtimestamp(value, **kwargs):
        observe("timestamp")
        assert value == 12 and type(value) is int
        assert kwargs == ({"tz": zone} if name == "reply_cap_date_str" else {})
        return SimpleNamespace(strftime=format_date)

    class DateTime:
        @property
        def fromtimestamp(self):
            observe("method")
            return fromtimestamp

    def zoneinfo(value):
        observe("zone")
        assert value is bot.MAIN_POST_SCHEDULE_TIMEZONE
        return zone

    monkeypatch.setattr(bot, "datetime", DateTime())
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "ZoneInfo", zoneinfo)
    date = getattr(bot, name)
    order = ["clock", "method", "int"]
    if name == "reply_cap_date_str":
        order.append("zone")
    order += ["timestamp", "format"]
    for current_zone in ("First/Zone", "Second/Zone"):
        monkeypatch.setattr(bot, "MAIN_POST_SCHEDULE_TIMEZONE", current_zone)
        assert date() is result
        assert events == order
        events.clear()
        assert date(epoch) is result
        assert events == order[1:]
        events.clear()
    for failure_at in order:
        with pytest.raises(LookupError) as caught:
            date()
        assert caught.value is failure
        assert events == order[:order.index(failure_at) + 1]
        events.clear()
    failure_at = None
    # Zero is an explicit timestamp, with no clock read or zone unification.
    timestamp = Mock(return_value=SimpleNamespace(strftime=Mock(return_value=result)))
    monkeypatch.setattr(bot, "datetime", SimpleNamespace(fromtimestamp=timestamp))
    assert date(0) is result
    assert "clock" not in events
    timestamp.assert_called_once_with(0, **({"tz": zone} if name == "reply_cap_date_str" else {}))


def test_bound_zone_keeps_exact_current_name_and_narrow_missing_zone_cause(monkeypatch):
    class Text(str):
        pass

    class Missing(Exception):
        pass

    timezone_name = "Current/Zone"
    zone = object()
    constructor = Mock(return_value=zone)
    monkeypatch.setattr(bot, "MAIN_POST_SCHEDULE_TIMEZONE", timezone_name)
    monkeypatch.setattr(bot, "ZoneInfo", constructor)
    monkeypatch.setattr(bot, "ZoneInfoNotFoundError", Missing)
    for value in (None, 0, Text(timezone_name), " " + timezone_name, "UTC"):
        with pytest.raises(ValueError, match="^unsupported main-post schedule timezone$"):
            bot.main_post_schedule_zone(value)
    constructor.assert_not_called()
    assert bot.main_post_schedule_zone(timezone_name) is zone
    assert constructor.call_args.args[0] is timezone_name
    failure = Missing("missing current zone")
    constructor.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.main_post_schedule_zone(timezone_name)
    assert str(caught.value) == "the bound main-post schedule timezone is unavailable"
    assert caught.value.__cause__ is failure
    for exception in (KeyError, ValueError, KeyboardInterrupt):
        failure = exception("native constructor")
        constructor.side_effect = failure
        with pytest.raises(exception) as caught:
            bot.main_post_schedule_zone(timezone_name)
        assert caught.value is failure


def test_bound_datetime_checks_exact_int_before_method_zone_or_timestamp(monkeypatch):
    events = []
    zone_name, zone, result = object(), object(), object()
    failure_at = None
    failure = OSError("native bound datetime")

    def observe(stage):
        events.append(stage)
        if stage == failure_at:
            raise failure

    def lookup_zone(value):
        observe("zone")
        assert value is zone_name
        return zone

    def fromtimestamp(value, *, tz):
        observe("timestamp")
        assert value is epoch and tz is zone
        return result

    class DateTime:
        @property
        def fromtimestamp(self):
            observe("method")
            return fromtimestamp

    class Integer(int):
        pass

    epoch = int("1800000001")
    monkeypatch.setattr(bot, "datetime", DateTime())
    monkeypatch.setattr(bot, "main_post_schedule_zone", lookup_zone)
    for invalid in (True, False, Integer(epoch), float(epoch), str(epoch), None):
        with pytest.raises(TypeError, match="^bound schedule epoch must be an integer$"):
            bot.bound_schedule_datetime(invalid, zone_name)
    assert events == []
    assert bot.bound_schedule_datetime(epoch, zone_name) is result
    order = ["method", "zone", "timestamp"]
    assert events == order
    for failure_at in order:
        events.clear()
        with pytest.raises(OSError) as caught:
            bot.bound_schedule_datetime(epoch, zone_name)
        assert caught.value is failure
        assert events == order[:order.index(failure_at) + 1]


def _confirmation_trace(monkeypatch, values, observed=1_800_000_005):
    trace = Mock()
    trace.attach_mock(Mock(side_effect=values.get), "get")
    trace.attach_mock(Mock(side_effect=bot.receipt_int), "integer")
    trace.attach_mock(Mock(side_effect=bot.valid_receipt_epoch), "valid")
    trace.attach_mock(Mock(return_value=observed), "clock")
    trace.attach_mock(Mock(), "critical")
    for name, callback in [
        ("receipt_int", trace.integer), ("valid_receipt_epoch", trace.valid),
        ("now_epoch", trace.clock),
    ]:
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "log", SimpleNamespace(critical=trace.critical))
    return SimpleNamespace(get=trace.get), trace


@pytest.mark.parametrize("attempt", [1_800_000_000, None, "", True])
def test_confirmation_reads_attempt_then_optional_reply_and_validates_before_clock(
    monkeypatch, attempt,
):
    reply = 1_800_000_002
    values = {"attempt_epoch": attempt, "reply_epoch": reply}
    before = dict(values)
    source, trace = _confirmation_trace(monkeypatch, values)
    fallback = attempt if type(attempt) is int else reply
    expected = [call.get("attempt_epoch"), call.integer(attempt)]
    if type(attempt) is not int:
        expected += [call.get("reply_epoch"), call.integer(reply)]
    assert bot.confirmation_epoch_after_remote_success(source) == 1_800_000_005
    assert trace.mock_calls == expected + [
        call.valid(fallback), call.clock(), call.valid(1_800_000_005),
    ]
    assert values == before


@pytest.mark.parametrize("values", [
    {"attempt_epoch": 0, "reply_epoch": 1_800_000_000},
    {"attempt_epoch": 4_102_444_801, "reply_epoch": 1_800_000_000},
    {},
    {"reply_epoch": 0},
])
def test_confirmation_refuses_invalid_fallback_before_clock_with_current_error(monkeypatch, values):
    class JournalError(Exception):
        pass

    monkeypatch.setattr(bot, "TransportJournalError", JournalError)
    source, trace = _confirmation_trace(monkeypatch, values)
    with pytest.raises(JournalError) as caught:
        bot.confirmation_epoch_after_remote_success(source)
    assert str(caught.value) == "transport source has no durable confirmation-time fallback"
    expected = [call.get("attempt_epoch"), call.integer(values.get("attempt_epoch"))]
    fallback = values.get("attempt_epoch")
    if fallback is None:
        fallback = values.get("reply_epoch")
        expected += [call.get("reply_epoch"), call.integer(fallback)]
    if fallback is not None:
        expected.append(call.valid(fallback))
    assert trace.mock_calls == expected


@pytest.mark.parametrize("failure_at", ["clock", "int"])
def test_confirmation_clock_or_int_failure_catches_only_exception_and_keeps_fallback(
    monkeypatch, failure_at,
):
    fallback = object()
    source, trace = _confirmation_trace(monkeypatch, {"attempt_epoch": fallback})
    trace.integer.side_effect = lambda value: value
    trace.valid.side_effect = None
    trace.valid.return_value = True

    class Observation:
        def __int__(self):
            trace.convert()
            raise failure

    def critical(message, **kwargs):
        assert sys.exc_info()[1] is failure

    trace.critical.side_effect = critical
    for failure in (ValueError("clock or conversion"), KeyboardInterrupt("clock or conversion")):
        trace.reset_mock()
        if failure_at == "clock":
            trace.clock.side_effect = failure
        else:
            trace.clock.return_value = Observation()
        if isinstance(failure, Exception):
            assert bot.confirmation_epoch_after_remote_success(source) is fallback
            trace.critical.assert_called_once_with(
                "Wall-clock observation failed after X returned a confirmed post "
                "identity; using the durable pre-request epoch so the confirmed "
                "transport identity remains restart-recoverable",
                exc_info=True,
            )
        else:
            with pytest.raises(KeyboardInterrupt) as caught:
                bot.confirmation_epoch_after_remote_success(source)
            assert caught.value is failure
            trace.critical.assert_not_called()
        expected = [call.get("attempt_epoch"), call.integer(fallback),
                    call.valid(fallback), call.clock()]
        if failure_at == "int":
            expected.append(call.convert())
        if isinstance(failure, Exception):
            expected.append(trace.mock_calls[-1])
        assert trace.mock_calls == expected


@pytest.mark.parametrize("prefer_observed", [False, True])
def test_confirmation_max_keeps_comparison_and_original_result_references(monkeypatch, prefer_observed):
    class Fallback:
        def __lt__(self, other):
            trace.compare(other)
            return prefer_observed

    fallback = Fallback()
    observed = int("1800000005")
    source, trace = _confirmation_trace(monkeypatch, {"attempt_epoch": fallback}, observed)
    trace.integer.side_effect = lambda value: value
    trace.valid.side_effect = None
    trace.valid.return_value = True
    assert bot.confirmation_epoch_after_remote_success(source) is (
        observed if prefer_observed else fallback
    )
    assert trace.mock_calls == [
        call.get("attempt_epoch"), call.integer(fallback), call.valid(fallback),
        call.clock(), call.valid(observed), call.compare(observed),
    ]


def test_confirmation_out_of_range_conversion_has_separate_exact_log(monkeypatch):
    fallback = int("1800000000")
    source, trace = _confirmation_trace(monkeypatch, {"attempt_epoch": fallback}, "0")
    assert bot.confirmation_epoch_after_remote_success(source) is fallback
    assert trace.mock_calls == [
        call.get("attempt_epoch"), call.integer(fallback), call.valid(fallback),
        call.clock(), call.valid(0),
        call.critical(
            "Wall-clock observation was outside the supported receipt range "
            "after X returned a confirmed post identity; using the durable "
            "pre-request epoch"
        ),
    ]
    assert type(trace.valid.call_args.args[0]) is int


@pytest.mark.parametrize("failure_at", [
    "get", "integer", "fallback_validation", "observed_validation", "max",
    "clock_log", "range_log",
])
def test_confirmation_native_failures_outside_clock_try_escape_without_retries(
    monkeypatch, failure_at,
):
    failure = LookupError("native confirmation dependency")
    value = 1_800_000_000
    source, trace = _confirmation_trace(monkeypatch, {"attempt_epoch": value})
    if failure_at in {"get", "integer"}:
        getattr(trace, failure_at).side_effect = failure
    elif failure_at == "fallback_validation":
        trace.valid.side_effect = failure
    elif failure_at == "observed_validation":
        trace.valid.side_effect = [True, failure]
    elif failure_at == "max":
        class Fallback:
            def __lt__(self, other):
                raise failure
        trace.integer.side_effect = None
        trace.integer.return_value = Fallback()
        trace.valid.side_effect = None
        trace.valid.return_value = True
    else:
        trace.critical.side_effect = failure
        if failure_at == "clock_log":
            trace.clock.side_effect = OSError("clock failure")
        else:
            trace.clock.return_value = 0
    with pytest.raises(LookupError) as caught:
        bot.confirmation_epoch_after_remote_success(source)
    assert caught.value is failure
    trace.get.assert_called_once_with("attempt_epoch")
    if failure_at == "get":
        trace.integer.assert_not_called()
    else:
        trace.integer.assert_called_once_with(value)
    if failure_at in {"get", "integer", "fallback_validation"}:
        trace.clock.assert_not_called()
    else:
        trace.clock.assert_called_once_with()
    if failure_at in {"clock_log", "range_log"}:
        assert trace.critical.call_count == 1
    else:
        trace.critical.assert_not_called()

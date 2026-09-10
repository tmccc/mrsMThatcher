"""Focused contracts for local self-test diagnostics and current dependencies."""
from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

import mrs_bot_self_test as owner
from tests.test_fail_safe_bootstrap_and_control import prepare_self_test_control_case
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401

DEPENDENCIES = {
    "_self_test_ok": ["log"],
    "_self_test_warn": ["log"],
    "run_self_test": [
        "ACCESS_SECRET", "ACCESS_TOKEN", "BASE_DIR", "CONSUMER_KEY", "CONSUMER_SECRET",
        "CONTROL_FILE", "ENABLE_AUTO_REPLIES", "ENABLE_DAILY_MEME_POSTS",
        "EXTRA_QUOTE_WATCH_FILE", "IMAGE_GLOB", "LINES_FILE", "LOCAL_CONFIG_ALLOWED_KEYS",
        "LOCAL_CONFIG_FILE", "MAX_AUTO_REPLIES_PER_DAY", "MAX_QUOTE_REPLIES_PER_DAY",
        "MEME_ANALYSIS_FILE", "MEME_DIR", "MIN_SECONDS_BETWEEN_REPLIES", "MY_USER_ID",
        "OPENAI_API_KEY", "QUOTE_CHECK_EVERY_SECONDS", "REPLY_CHECK_EVERY_SECONDS",
        "STATE_FILE", "X_BEARER_TOKEN", "_runtime_config_namespace", "_self_test_ok",
        "_self_test_warn", "glob", "json", "list_meme_candidates", "load_control",
        "load_extra_quote_watch_post_ids", "load_validated_local_config_overrides",
        "log", "require_production_bootstrap", "single_call_reply", "validate_runtime_config_values",
    ],
}
NUMERIC_KEYS = (
    "MAX_AUTO_REPLIES_PER_DAY", "MAX_QUOTE_REPLIES_PER_DAY", "MIN_SECONDS_BETWEEN_REPLIES",
    "REPLY_CHECK_EVERY_SECONDS", "QUOTE_CHECK_EVERY_SECONDS",
)
START = "Running self-test only; no X or OpenAI API calls will be made"


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Self-test import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'engagement_question_experiment', 'transaction_mutation_authority', 'remote_write_transport_journal'} or name.startswith('mrs_bot_') and name != 'mrs_bot_self_test':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
datetime.datetime = type('ForbiddenDatetime', (), dict(now=forbidden, fromtimestamp=forbidden, today=forbidden))
zoneinfo.ZoneInfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_self_test
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert mrs_bot_self_test._self_test_ok.__annotations__['ok'] == 'bool'
assert mrs_bot_self_test._self_test_warn.__annotations__['return'] == 'None'
assert mrs_bot_self_test.run_self_test.__annotations__['return'] == 'int'
assert mrs_bot_self_test._self_test_ok.__doc__ is None
assert mrs_bot_self_test._self_test_warn.__doc__ is None
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", list(DEPENDENCIES))
def test_public_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    public, extracted = getattr(bot, name), getattr(owner, name)
    assert bot._self_test is owner
    expected = "() -> 'int'" if name == "run_self_test" else (
        "(label: 'str', ok: 'bool', detail: 'str' = '') -> "
        + ("'bool'" if name == "_self_test_ok" else "'None'")
    )
    assert str(inspect.signature(public)) == expected
    assert public.__doc__ == extracted.__doc__
    if name != "run_self_test":
        assert public.__doc__ is None
    signature = inspect.signature(extracted)
    assert signature.replace(parameters=[
        p for key, p in signature.parameters.items() if key not in DEPENDENCIES[name]
    ]) == inspect.signature(public)
    for dep in DEPENDENCIES[name]:
        assert signature.parameters[dep].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters[dep].default is inspect.Parameter.empty
    for _round in range(2):
        dependencies = {dep: object() for dep in DEPENDENCIES[name]}
        for dep, value in dependencies.items():
            monkeypatch.setattr(bot, dep, value)
        result = object()
        callback = Mock(return_value=result)
        monkeypatch.setattr(owner, name, callback)
        args = () if name == "run_self_test" else (object(), object(), object())
        assert public(*args) is result
        assert callback.call_args.args == args
        assert all(a is b for a, b in zip(callback.call_args.args, args))
        assert callback.call_args.kwargs == dependencies
        assert all(callback.call_args.kwargs[key] is value for key, value in dependencies.items())
        if args:
            assert public(*args[:2]) is result
            assert callback.call_args.args == (*args[:2], "")
    error = ValueError("synthetic adapter failure")
    callback.side_effect = error
    with pytest.raises(ValueError) as caught:
        public(*args)
    assert caught.value is error


@pytest.mark.parametrize("name", ["_self_test_ok", "_self_test_warn"])
@pytest.mark.parametrize("observations,detail_present", [((True, False), True), ((False, True), False)])
def test_logging_observes_truth_twice_and_preserves_formatting_and_return(name, observations, detail_present):
    events = []
    answers = iter(observations)

    class Truth:
        def __bool__(self):
            events.append("ok")
            return next(answers)

    class Text:
        def __init__(self, label):
            self.label = label

        def __bool__(self):
            events.append("detail truth")
            return detail_present

        def __format__(self, spec):
            assert spec == ""
            events.append(self.label)
            return self.label

    ok, logger = Truth(), Mock()
    result = getattr(owner, name)(Text("label"), ok, Text("detail"), log=logger)
    status = "OK" if observations[0] else ("FAIL" if name == "_self_test_ok" else "WARN")
    route = "info" if observations[1] else ("error" if name == "_self_test_ok" else "warning")
    assert events == ["ok", "label", "detail truth", *(["detail"] if detail_present else []), "ok"]
    assert logger.mock_calls == [getattr(call, route)(f"SELFTEST {status}: label" + (" - detail" if detail_present else ""))]
    assert result is (ok if name == "_self_test_ok" else None)


@pytest.mark.parametrize("name", ["_self_test_ok", "_self_test_warn"])
@pytest.mark.parametrize("boundary", ["status", "label", "detail truth", "detail", "route", "log"])
def test_logging_native_errors_stop_at_the_original_observation(name, boundary):
    events, error = [], TypeError("synthetic logging failure")

    def observe(step):
        events.append(step)
        if step == boundary:
            raise error

    class Truth:
        def __bool__(self):
            observe("route" if "status" in events else "status")
            return False

    class Text:
        def __init__(self, label):
            self.label = label

        def __bool__(self):
            observe("detail truth")
            return True

        def __format__(self, spec):
            observe(self.label)
            return self.label

    logger = Mock()
    getattr(logger, "error" if name == "_self_test_ok" else "warning").side_effect = lambda _message: observe("log")
    with pytest.raises(TypeError) as caught:
        getattr(owner, name)(Text("label"), Truth(), Text("detail"), log=logger)
    assert caught.value is error
    order = ["status", "label", "detail truth", "detail", "route", "log"]
    assert events == order[:order.index(boundary) + 1]


def _self_test_case(tmp_path, monkeypatch):
    prepare_self_test_control_case(tmp_path, monkeypatch, tmp_path / "control.json")
    trace = Mock()
    for name, callback in {
        "require_production_bootstrap": trace.bootstrap, "log": trace.log,
        "_self_test_ok": trace.require, "_self_test_warn": trace.warn,
        "glob": trace.images, "load_validated_local_config_overrides": trace.local,
        "load_control": trace.control, "list_meme_candidates": trace.memes,
        "load_extra_quote_watch_post_ids": trace.watch, "validate_runtime_config_values": trace.validate,
    }.items():
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "MEME_DIR", tmp_path / "memes")
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "meme-analysis.json")
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "dummy")
    monkeypatch.setattr(bot, "X_BEARER_TOKEN", "dummy")
    monkeypatch.setattr(bot, "single_call_reply", {"enabled": True})
    monkeypatch.setattr(bot, "LOCAL_CONFIG_ALLOWED_KEYS", ("MAX_AUTO_REPLIES_PER_DAY",))
    trace.require.side_effect = lambda label, ok, detail="": ok
    trace.images.return_value = [object()]
    trace.local.return_value = None
    trace.control.return_value = {}
    trace.memes.return_value = []
    trace.watch.return_value = ["1", "2"]
    trace.validate.return_value = []
    return trace


def test_local_diagnostics_have_exact_order_and_success_result(tmp_path, monkeypatch):
    trace = _self_test_case(tmp_path, monkeypatch)
    assert bot.run_self_test() == 0
    assert trace.mock_calls == [
        call.bootstrap(), call.log.info(START),
        call.require("base directory exists", True, str(tmp_path)),
        call.require("lines file exists", True, str(bot.LINES_FILE)),
        call.require("lines file has non-empty lines", True, "non_empty_lines=1"),
        call.images(bot.IMAGE_GLOB),
        call.require("quote/image image glob has files", True, f"count=1 glob={bot.IMAGE_GLOB}"),
        call.local(), call.warn("local config file present", False, str(bot.LOCAL_CONFIG_FILE)),
        call.warn("runtime control file absent", True, str(bot.CONTROL_FILE)), call.control(),
        call.require("runtime control validates", True, str(bot.CONTROL_FILE)),
        call.warn("state file present", False, str(bot.STATE_FILE)),
        call.warn("extra quote watch file present", False, str(bot.EXTRA_QUOTE_WATCH_FILE)),
        *[call.require(f"X_{key} set", True, "") for key in (
            "CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "MY_USER_ID",
        )],
        call.warn("X_BEARER_TOKEN set", True, "needed/preferred for quote/hot search"),
        *[call.require(f"{key} positive", True, "1") for key in NUMERIC_KEYS],
        call.validate({"MAX_AUTO_REPLIES_PER_DAY": 1}),
        call.require("runtime config validates", True, ""), call.log.info("Self-test finished successfully"),
    ]


def test_optional_local_state_meme_and_watch_diagnostics(tmp_path, monkeypatch):
    trace = _self_test_case(tmp_path, monkeypatch)
    trace.local.return_value = {}
    bot.STATE_FILE.write_text('{"next_reply_lane_priority":"normal","hot_post_reply_since_ids":{}}')
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    bot.MEME_DIR.mkdir()
    bot.EXTRA_QUOTE_WATCH_FILE.write_text("[]")
    assert bot.run_self_test() == 0
    start = trace.mock_calls.index(call.local())
    end = trace.mock_calls.index(call.require("X_CONSUMER_KEY set", True, ""))
    assert trace.mock_calls[start:end] == [
        call.local(), call.warn("local config file present", True, str(bot.LOCAL_CONFIG_FILE)),
        call.require("local config validates", True, "overrides=0"),
        call.warn("runtime control file absent", True, str(bot.CONTROL_FILE)), call.control(),
        call.require("runtime control validates", True, str(bot.CONTROL_FILE)),
        call.require("state file parses", True, str(bot.STATE_FILE)),
        call.warn("state has next_reply_lane_priority", True), call.warn("state has hot-post since_id map", True),
        call.warn("meme directory present", True, str(bot.MEME_DIR)), call.memes(),
        call.warn("meme candidates available", False, "count=0"),
        call.warn("meme analysis file present", False, str(bot.MEME_ANALYSIS_FILE)),
        call.warn("extra quote watch file present", True, str(bot.EXTRA_QUOTE_WATCH_FILE)), call.watch(),
        call.warn("extra quote watch IDs loaded", True, "count=2"),
    ]


def test_failure_count_uses_current_require_result_and_is_fresh_each_run(tmp_path, monkeypatch):
    trace = _self_test_case(tmp_path, monkeypatch)
    trace.control.return_value = {"_control_fail_closed": True}
    bot.STATE_FILE.write_text("[]")
    bot.EXTRA_QUOTE_WATCH_FILE.write_text("[]")
    trace.watch.side_effect = ValueError("watch broken")
    monkeypatch.setattr(bot, "ACCESS_SECRET", "")
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 0)
    trace.validate.return_value = ["first", "second"]
    assert bot.run_self_test() == 1
    assert [entry for entry in trace.require.call_args_list if entry.args[0] == "state file parses"] == [
        call("state file parses", False, str(bot.STATE_FILE)),
        call("state file parses", False, "'list' object has no attribute 'get'"),
    ]
    trace.require.assert_any_call("runtime config validates", False, "first; second")
    trace.log.error.assert_called_once_with("Self-test finished with %d failure(s)", 7)
    trace.reset_mock()
    trace.require.side_effect = lambda *_args: True
    assert bot.run_self_test() == 0
    trace.log.error.assert_not_called()
    assert trace.mock_calls[-1] == call.log.info("Self-test finished successfully")


def test_repeated_path_and_count_observations_keep_context_and_detail_order(tmp_path, monkeypatch):
    trace = _self_test_case(tmp_path, monkeypatch)

    class ObservedPath:
        def __init__(self, name, answers):
            self.name, self.answers = name, iter(answers)

        def exists(self):
            trace.exists(self.name)
            return next(self.answers)

        def __str__(self):
            trace.detail(self.name)
            return str(tmp_path / self.name)

    class Line:
        def __init__(self, value):
            self.value = value

        def strip(self):
            trace.strip(self.value)
            return self.value

    class Images:
        def __len__(self):
            trace.image_length()
            return 1 if trace.image_length.call_count == 1 else 3

    base = ObservedPath("base", [True])
    lines = ObservedPath("lines", [True, True])
    memes = ObservedPath("memes", [False, True])
    watch = ObservedPath("watch", [True, False])
    for name, value in (("BASE_DIR", base), ("LINES_FILE", lines), ("MEME_DIR", memes),
                        ("EXTRA_QUOTE_WATCH_FILE", watch), ("ENABLE_DAILY_MEME_POSTS", True)):
        monkeypatch.setattr(bot, name, value)
    trace.open = MagicMock()
    trace.open.return_value.__enter__.return_value = [Line(""), Line("one"), Line("two")]
    monkeypatch.setattr(owner, "open", trace.open, raising=False)
    trace.images.return_value = Images()
    assert bot.run_self_test() == 0
    end = trace.mock_calls.index(call.local())
    assert trace.mock_calls[:end] == [
        call.bootstrap(), call.log.info(START),
        call.exists("base"), call.detail("base"), call.require("base directory exists", True, str(tmp_path / "base")),
        call.exists("lines"), call.detail("lines"), call.require("lines file exists", True, str(tmp_path / "lines")),
        call.exists("lines"), call.open(lines, "r"), call.open().__enter__(),
        call.strip(""), call.strip("one"), call.strip("two"), call.open().__exit__(None, None, None),
        call.require("lines file has non-empty lines", True, "non_empty_lines=2"),
        call.images(bot.IMAGE_GLOB), call.image_length(), call.image_length(),
        call.require("quote/image image glob has files", True, f"count=3 glob={bot.IMAGE_GLOB}"),
    ]
    start = trace.mock_calls.index(call.exists("memes"))
    assert trace.mock_calls[start:start + 6] == [
        call.exists("memes"), call.detail("memes"),
        call.warn("meme directory present", False, str(tmp_path / "memes")),
        call.exists("memes"), call.memes(), call.warn("meme candidates available", False, "count=0"),
    ]
    start = trace.mock_calls.index(call.exists("watch"))
    assert trace.mock_calls[start:start + 4] == [
        call.exists("watch"), call.detail("watch"),
        call.warn("extra quote watch file present", True, str(tmp_path / "watch")), call.exists("watch"),
    ]
    trace.watch.assert_not_called()


@pytest.mark.parametrize("boundary", ["lines", "local", "state", "watch"])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_local_read_catches_remain_exception_only(tmp_path, monkeypatch, boundary, error_type):
    trace = _self_test_case(tmp_path, monkeypatch)
    error = error_type("synthetic read failure")
    label = {"lines": "lines file readable", "local": "local config validates",
             "state": "state file parses", "watch": "extra quote watch file readable"}[boundary]
    if boundary == "lines":
        trace.open = MagicMock()
        trace.open.return_value.__enter__.return_value = [SimpleNamespace(strip=Mock(side_effect=error))]
        monkeypatch.setattr(owner, "open", trace.open, raising=False)
    elif boundary == "local":
        trace.local.side_effect = error
    elif boundary == "state":
        bot.STATE_FILE.write_text("{}")
        monkeypatch.setattr(bot, "json", SimpleNamespace(load=Mock(side_effect=error)))
    else:
        bot.EXTRA_QUOTE_WATCH_FILE.write_text("[]")
        trace.watch.side_effect = error
    if error_type is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.run_self_test()
        assert caught.value is error
        trace.log.error.assert_not_called()
        trace.validate.assert_not_called()
    else:
        assert bot.run_self_test() == 1
        trace.require.assert_any_call(label, False, "synthetic read failure")
        trace.log.error.assert_called_once_with("Self-test finished with %d failure(s)", 1)
        if boundary == "local":
            start = trace.mock_calls.index(call.local())
            assert trace.mock_calls[start:start + 3] == [
                call.local(), call.warn("local config file present", True, str(bot.LOCAL_CONFIG_FILE)),
                call.require(label, False, "synthetic read failure"),
            ]
    if boundary == "lines":
        args = trace.open.return_value.__exit__.call_args.args
        assert args[0] is error_type and args[1] is error


@pytest.mark.parametrize("boundary", ["bootstrap", "control", "memes", "validator"])
def test_uncaught_callbacks_keep_native_error_and_progress(tmp_path, monkeypatch, boundary):
    trace = _self_test_case(tmp_path, monkeypatch)
    error = ValueError("synthetic uncaught callback")
    if boundary == "memes":
        monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
        bot.MEME_DIR.mkdir()
    target = {"bootstrap": trace.bootstrap, "control": trace.control,
              "memes": trace.memes, "validator": trace.validate}[boundary]
    target.side_effect = error
    with pytest.raises(ValueError) as caught:
        bot.run_self_test()
    assert caught.value is error
    expected = call.validate({"MAX_AUTO_REPLIES_PER_DAY": 1}) if boundary == "validator" else getattr(call, boundary)()
    assert trace.mock_calls[-1] == expected
    if boundary == "bootstrap":
        assert trace.mock_calls == [call.bootstrap()]
    if boundary == "control":
        assert trace.mock_calls[-2] == call.warn("runtime control file absent", True, str(bot.CONTROL_FILE))
    trace.log.error.assert_not_called()


@pytest.mark.parametrize("auto,enabled", [(False, True), (True, 1), (True, True)])
def test_credential_truth_order_and_lazy_literal_true_gate(tmp_path, monkeypatch, auto, enabled):
    trace = _self_test_case(tmp_path, monkeypatch)
    events = []

    class Credential:
        def __init__(self, name, present):
            self.name, self.present = name, present

        def __bool__(self):
            events.append(self.name)
            return self.present

    for name in ("CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "MY_USER_ID",
                 "OPENAI_API_KEY", "X_BEARER_TOKEN"):
        monkeypatch.setattr(bot, name, Credential(name, name not in {"OPENAI_API_KEY", "X_BEARER_TOKEN"}))
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", Credential("auto", auto))
    monkeypatch.setattr(bot, "single_call_reply", SimpleNamespace(get=lambda key: events.append(key) or enabled))
    required = auto and enabled is True
    assert bot.run_self_test() == int(required)
    assert events == ["CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "MY_USER_ID", "auto",
                      *(["enabled"] if auto else []), *(["OPENAI_API_KEY"] if required else []), "X_BEARER_TOKEN"]
    assert (call("OPENAI_API_KEY set when single-call replies enabled", False, "") in trace.require.call_args_list) is required


@pytest.mark.parametrize("boundary", [None, "int", "str"])
def test_numeric_conversions_are_ordered_and_native_errors_escape(tmp_path, monkeypatch, boundary):
    trace = _self_test_case(tmp_path, monkeypatch)
    events, error = [], ValueError("synthetic numeric failure")

    class Number:
        def __init__(self, name):
            self.name = name

        def observe(self, kind):
            events.append((self.name, kind))
            if self.name == NUMERIC_KEYS[1] and kind == boundary:
                raise error

        def __int__(self):
            self.observe("int")
            return 1

        def __str__(self):
            self.observe("str")
            return "one"

    for name in NUMERIC_KEYS:
        monkeypatch.setattr(bot, name, Number(name))
    expected = [(name, kind) for name in NUMERIC_KEYS for kind in ("int", "str")]
    if boundary:
        with pytest.raises(ValueError) as caught:
            bot.run_self_test()
        assert caught.value is error
        expected = expected[:expected.index((NUMERIC_KEYS[1], boundary)) + 1]
        trace.validate.assert_not_called()
        assert trace.require.call_args.args == (f"{NUMERIC_KEYS[0]} positive", True, "one")
    else:
        assert bot.run_self_test() == 0
        for name in NUMERIC_KEYS:
            trace.require.assert_any_call(f"{name} positive", True, "one")
    assert events == expected


def test_namespace_is_current_for_each_containment_and_subscription(tmp_path, monkeypatch):
    trace = _self_test_case(tmp_path, monkeypatch)
    assert bot._runtime_config_namespace() is vars(bot)
    first, second = object(), object()
    monkeypatch.setattr(bot, "LOCAL_CONFIG_ALLOWED_KEYS", ("MAX_AUTO_REPLIES_PER_DAY", "absent", "second"))

    class Namespace:
        def __contains__(self, key):
            trace.contains(key)
            return key != "absent"

    trace.namespace.side_effect = [Namespace(), {"MAX_AUTO_REPLIES_PER_DAY": first}, Namespace(), Namespace(), {"second": second}]
    monkeypatch.setattr(bot, "_runtime_config_namespace", trace.namespace)
    assert bot.run_self_test() == 0
    start = trace.mock_calls.index(call.namespace())
    values = trace.validate.call_args.args[0]
    assert list(values) == ["MAX_AUTO_REPLIES_PER_DAY", "second"]
    assert values["MAX_AUTO_REPLIES_PER_DAY"] is first and values["second"] is second
    assert trace.mock_calls[start:] == [
        call.namespace(), call.contains("MAX_AUTO_REPLIES_PER_DAY"), call.namespace(),
        call.namespace(), call.contains("absent"), call.namespace(), call.contains("second"), call.namespace(),
        call.validate(values), call.require("runtime config validates", True, ""), call.log.info("Self-test finished successfully"),
    ]


def test_namespace_removal_between_contains_and_get_keeps_native_key_error(tmp_path, monkeypatch):
    trace = _self_test_case(tmp_path, monkeypatch)
    trace.namespace.side_effect = [{"MAX_AUTO_REPLIES_PER_DAY": object()}, {}]
    monkeypatch.setattr(bot, "_runtime_config_namespace", trace.namespace)
    with pytest.raises(KeyError) as caught:
        bot.run_self_test()
    assert caught.value.args == ("MAX_AUTO_REPLIES_PER_DAY",)
    assert trace.namespace.call_count == 2
    trace.validate.assert_not_called()
    trace.log.error.assert_not_called()

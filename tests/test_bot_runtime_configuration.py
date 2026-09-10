"""Focused contracts for current runtime configuration and synthetic credentials."""
from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import mrs_bot_runtime_configuration as owner
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401

DEPENDENCIES = {
    "validate_runtime_config_values": [
        "_runtime_config_namespace", "math", "validate_single_call_reply_config",
    ],
    "apply_local_config": [
        "LOCAL_CONFIG_FILE", "_runtime_config_namespace",
        "load_validated_local_config_overrides", "log", "log_json_debug",
    ],
    "validate_production_credentials": [
        "ACCESS_SECRET", "ACCESS_TOKEN", "CONSUMER_KEY", "CONSUMER_SECRET",
        "ENABLE_AUTO_REPLIES", "MY_USER_ID", "OPENAI_API_KEY", "single_call_reply",
    ],
}
POSITIVE_KEYS = (
    "AUTHOR_NO_REPLY_QUARANTINE_SECONDS", "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
    "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS", "COOLDOWN_AFTER_429_SECONDS",
    "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS", "ERROR_WINDOW_SECONDS",
    "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK", "MAX_AUTO_REPLIES_PER_DAY",
    "MAX_HOT_POST_REPLIES_PER_CHECK", "MAX_OPENAI_ERRORS_PER_WINDOW", "MAX_QUOTE_REPLIES_PER_DAY",
    "MAX_REPLIES_PER_AUTHOR_PER_DAY", "MAX_X_ERRORS_PER_WINDOW",
    "MENTIONS_MAX_PAGES_PER_CHECK", "MIN_SECONDS_BETWEEN_REPLIES",
    "POST_SLEEP_MAX", "POST_SLEEP_MIN", "QUOTE_CHECK_EVERY_SECONDS",
    "QUOTE_LOOKUP_MAX_PAGES_PER_POST", "QUOTE_POST_LOOKBACK_MAIN_POSTS",
    "RECENT_OWN_POST_IDS_MAX", "REPLY_CHECK_EVERY_SECONDS",
    "TWEET_CACHE_MAX_AGE_SECONDS", "TWEET_CACHE_MAX_ITEMS",
)
SHADOW_KEYS = (
    "ORIGINAL_EDITORIAL_SHADOW_WEIGHT", "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
    "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Runtime-configuration import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'engagement_question_experiment', 'transaction_mutation_authority', 'remote_write_transport_journal'} or name.startswith('mrs_bot_') and name != 'mrs_bot_runtime_configuration':
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
import mrs_bot_runtime_configuration
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert mrs_bot_runtime_configuration.validate_runtime_config_values.__annotations__['values'] == 'dict[str, object]'
assert mrs_bot_runtime_configuration.apply_local_config.__annotations__['return'] == 'None'
assert mrs_bot_runtime_configuration.validate_production_credentials.__annotations__['return'] == 'None'
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", list(DEPENDENCIES))
def test_public_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    public, extracted = getattr(bot, name), getattr(owner, name)
    assert bot._runtime_configuration is owner
    expected = "(values: 'dict[str, object]') -> 'list[str]'" if name == "validate_runtime_config_values" else "() -> 'None'"
    assert str(inspect.signature(public)) == expected
    assert public.__doc__ == extracted.__doc__
    signature = inspect.signature(extracted)
    assert signature.replace(parameters=[
        p for key, p in signature.parameters.items() if key not in DEPENDENCIES[name]
    ]) == inspect.signature(public)
    for dep in DEPENDENCIES[name]:
        assert signature.parameters[dep].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters[dep].default is inspect.Parameter.empty
    args = (object(),) if name == "validate_runtime_config_values" else ()
    for _round in range(2):
        dependencies = {dep: object() for dep in DEPENDENCIES[name]}
        for dep, value in dependencies.items():
            monkeypatch.setattr(bot, dep, value)
        result = object()
        callback = Mock(return_value=result)
        monkeypatch.setattr(owner, name, callback)
        assert public(*args) is result
        assert callback.call_args.args == args
        assert all(a is b for a, b in zip(callback.call_args.args, args))
        assert callback.call_args.kwargs == dependencies
        assert all(callback.call_args.kwargs[key] is value for key, value in dependencies.items())
    error = ValueError("synthetic adapter failure")
    callback.side_effect = error
    with pytest.raises(ValueError) as caught:
        public(*args)
    assert caught.value is error


def _validation_values(monkeypatch):
    values = {key: 1 for key in POSITIVE_KEYS}
    values.update({
        "engagement_question_experiment_enabled": False,
        "engagement_question_experiment_plan_path": "synthetic-plan.json",
        "engagement_question_notification_output_path": "",
        "historical_context_reply": {
            "enabled": False, "include_meaning": True, "include_source": True,
            "include_verification": False, "maximum_length": 120,
        },
        "single_call_reply": object(),
        "MAX_MENTIONS_PER_CHECK": 5, "QUOTE_LOOKUP_API_MAX_RESULTS": 10,
        "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 10,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 0,
        "MEME_TRIGGER_AFTER_HOUR": 0, "MEME_FALLBACK_HOUR": 23,
        "MEME_FALLBACK_MINUTE": 59,
        "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": 0,
        "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS": 0,
        **{key: 0.0 for key in SHADOW_KEYS},
    })
    for key, value in values.items():
        monkeypatch.setattr(bot, key, value)
    monkeypatch.setattr(bot, "validate_single_call_reply_config", Mock(return_value=[]))
    return values


def test_namespace_identity_eager_fallbacks_and_live_order(monkeypatch):
    values = _validation_values(monkeypatch)
    namespace = bot._runtime_config_namespace
    assert namespace() is vars(bot)
    assert str(inspect.signature(namespace)) == "() -> 'dict[str, object]'"
    marker = object()
    monkeypatch.setattr(bot, "_stage64_namespace_probe", marker, raising=False)
    assert namespace()["_stage64_namespace_probe"] is marker
    trace = []
    defaults = []

    def observed_namespace():
        trace.append("namespace")
        assert namespace() is vars(bot)
        return namespace()

    class Overrides(dict):
        def get(self, key, default=None):
            trace.append(key)
            defaults.append((key, default))
            if key == "engagement_question_experiment_enabled":
                monkeypatch.setattr(bot, "engagement_question_experiment_plan_path", "new-synthetic-plan.json")
            if key == "MEME_FALLBACK_MINUTE":
                monkeypatch.setattr(bot, "POST_SLEEP_MIN", 7)
            return super().get(key, default)

    monkeypatch.setattr(bot, "_runtime_config_namespace", observed_namespace)
    overrides = Overrides(values)
    first = bot.validate_runtime_config_values(overrides)
    assert first == []
    keys = [
        "engagement_question_experiment_enabled", "engagement_question_experiment_plan_path",
        "engagement_question_notification_output_path", "historical_context_reply", "single_call_reply",
        *POSITIVE_KEYS, "MAX_MENTIONS_PER_CHECK", "QUOTE_LOOKUP_API_MAX_RESULTS",
        "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS", "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
        *SHADOW_KEYS, "MEME_TRIGGER_AFTER_HOUR", "MEME_FALLBACK_HOUR", "MEME_FALLBACK_MINUTE",
        "POST_SLEEP_MIN", "POST_SLEEP_MAX",
        "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
    ]
    assert trace == [event for key in keys for event in ("namespace", key)]
    assert defaults[1] == ("engagement_question_experiment_plan_path", "new-synthetic-plan.json")
    assert [value for key, value in defaults if key == "POST_SLEEP_MIN"] == [1, 7]
    assert defaults[3][1] is values["historical_context_reply"]
    assert defaults[4][1] is values["single_call_reply"]
    assert bot.validate_single_call_reply_config.call_args.args[0] is values["single_call_reply"]
    second = bot.validate_runtime_config_values(overrides)
    assert second == [] and second is not first


def test_validation_keeps_ordered_errors_context_fields_and_validator_references(monkeypatch):
    _validation_values(monkeypatch)
    fields = []

    class Context(dict):
        def get(self, key, default=None):
            fields.append(key)
            return super().get(key, default)

    reply = object()
    reply_error = object()
    monkeypatch.setattr(bot, "validate_single_call_reply_config", Mock(return_value=[reply_error]))
    errors = bot.validate_runtime_config_values({
        "engagement_question_experiment_enabled": 1,
        "engagement_question_experiment_plan_path": " bad\n",
        "engagement_question_notification_output_path": " bad ",
        "historical_context_reply": Context(
            enabled=1, include_meaning=0, include_source=None,
            include_verification="yes", maximum_length=True,
        ),
        "single_call_reply": reply,
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS": 0,
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD": "bad",
        "MAX_MENTIONS_PER_CHECK": 4, "QUOTE_LOOKUP_API_MAX_RESULTS": "bad",
        "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS": 101,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": True,
        **dict(zip(SHADOW_KEYS, [True, float("nan"), -1.0, "bad"])),
        "MEME_TRIGGER_AFTER_HOUR": 24, "MEME_FALLBACK_HOUR": -1, "MEME_FALLBACK_MINUTE": 60,
        "POST_SLEEP_MIN": 9, "POST_SLEEP_MAX": 2,
        "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS": "bad",
    })
    assert fields == ["enabled", "include_meaning", "include_source", "include_verification", "maximum_length"]
    assert bot.validate_single_call_reply_config.call_count == 1
    assert bot.validate_single_call_reply_config.call_args.args[0] is reply
    assert errors == [
        "engagement_question_experiment_enabled must be boolean",
        "engagement_question_experiment_plan_path must be a non-empty clean path",
        "engagement_question_notification_output_path must be an empty or clean path",
        *[f"historical_context_reply.{key} must be boolean" for key in fields[:-1]],
        "historical_context_reply.maximum_length must be an integer from 120 to 25000",
        reply_error,
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS must be positive",
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD must be an integer",
        "MAX_MENTIONS_PER_CHECK must be between 5 and 100",
        "QUOTE_LOOKUP_API_MAX_RESULTS must be an integer",
        "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS must be between 10 and 100",
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be an integer",
        "ORIGINAL_EDITORIAL_SHADOW_WEIGHT must be a number",
        "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT must be finite",
        "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY must be non-negative",
        "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY must be a number",
        "MEME_TRIGGER_AFTER_HOUR must be between 0 and 23",
        "MEME_FALLBACK_HOUR must be between 0 and 23",
        "MEME_FALLBACK_MINUTE must be between 0 and 59",
        "POST_SLEEP_MIN must be <= POST_SLEEP_MAX",
        "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS/MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS must be integers",
    ]


@pytest.mark.parametrize("context,error", [(None, "must be an object"), ({}, "fields mismatch")])
def test_context_schema_and_strict_path_types_short_circuit(monkeypatch, context, error):
    _validation_values(monkeypatch)

    class PathText(str):
        def strip(self):
            raise AssertionError("Strict type rejection must precede path operations")

    assert bot.validate_runtime_config_values({
        "engagement_question_experiment_plan_path": PathText("synthetic.json"),
        "engagement_question_notification_output_path": PathText(""),
        "historical_context_reply": context,
    }) == [
        "engagement_question_experiment_plan_path must be a non-empty clean path",
        "engagement_question_notification_output_path must be an empty or clean path",
        f"historical_context_reply {error}",
    ]


@pytest.mark.parametrize("boundary", ["namespace", "values", "validator", "float", "finite"])
def test_validation_native_errors_outside_integer_catches(monkeypatch, boundary):
    values = _validation_values(monkeypatch)
    error = ValueError("synthetic validation failure")
    failing = Mock(side_effect=error)
    if boundary == "namespace":
        monkeypatch.setattr(bot, "_runtime_config_namespace", failing)
    elif boundary == "values":
        values = SimpleNamespace(get=failing)
    elif boundary == "validator":
        monkeypatch.setattr(bot, "validate_single_call_reply_config", failing)
    elif boundary == "float":
        class Number(float):
            def __float__(self):
                return failing()
        values[SHADOW_KEYS[0]] = Number(1)
    else:
        monkeypatch.setattr(bot, "math", SimpleNamespace(isfinite=failing))
    with pytest.raises(ValueError) as caught:
        bot.validate_runtime_config_values(values)
    assert caught.value is error
    assert failing.call_count == 1


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_paired_integer_order_current_fallback_and_exception_scope(monkeypatch, error_type):
    _validation_values(monkeypatch)
    trace = []
    error = error_type("synthetic integer failure")
    min_key = "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS"
    max_key = "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS"

    class Minimum:
        def __int__(self):
            trace.append("minimum")
            monkeypatch.setattr(bot, max_key, Maximum())
            return 1

    class Maximum:
        def __int__(self):
            trace.append("maximum")
            raise error

    monkeypatch.setattr(bot, min_key, Minimum())
    if error_type is ValueError:
        assert bot.validate_runtime_config_values({}) == [f"{min_key}/{max_key} must be integers"]
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.validate_runtime_config_values({})
        assert caught.value is error
    assert trace == ["minimum", "maximum"]


def test_missing_numeric_defaults_and_separate_shadow_float_observations(monkeypatch):
    _validation_values(monkeypatch)
    for key in ("MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS", *SHADOW_KEYS):
        monkeypatch.delattr(bot, key)
    monkeypatch.delattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN")
    assert bot.validate_runtime_config_values({}) == []
    monkeypatch.delattr(bot, "AUTHOR_NO_REPLY_QUARANTINE_SECONDS")
    assert bot.validate_runtime_config_values({}) == ["AUTHOR_NO_REPLY_QUARANTINE_SECONDS must be positive"]
    trace = []
    observations = iter([1.0, -1.0])

    class Number(float):
        def __float__(self):
            trace.append("float")
            return next(observations)

    def finite(value):
        trace.append(("finite", value))
        return True

    monkeypatch.setattr(bot, "math", SimpleNamespace(isfinite=finite))
    assert bot.validate_runtime_config_values({
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS": True,
        SHADOW_KEYS[0]: Number(1),
    }) == ["ORIGINAL_EDITORIAL_SHADOW_WEIGHT must be non-negative"]
    assert trace == ["float", ("finite", 1.0), "float", *[("finite", 0.0)] * 3]


@pytest.mark.parametrize("proposed", [None, {}, False], ids=["absent", "empty", "false"])
def test_application_absent_and_falsy_non_none_branches(monkeypatch, tmp_path, proposed):
    path = tmp_path / "synthetic-config.json"
    loader = Mock(return_value=proposed)
    log = Mock()
    debug = Mock()
    namespace = Mock(side_effect=AssertionError("Empty overrides must not access the namespace"))
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    monkeypatch.setattr(bot, "load_validated_local_config_overrides", loader)
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "log_json_debug", debug)
    monkeypatch.setattr(bot, "_runtime_config_namespace", namespace)
    assert bot.apply_local_config() is None
    loader.assert_called_once_with()
    message = (
        "Local config file not present; using script defaults. path=%s"
        if proposed is None else "Local config file present but no valid overrides applied: %s"
    )
    log.info.assert_called_once_with(message, path)
    namespace.assert_not_called()
    debug.assert_not_called()


@pytest.mark.parametrize("boundary", [None, "load", "truth", "items", "iterator", "namespace", "key", "length", "log", "debug"])
def test_application_per_item_live_writes_order_and_native_failures(monkeypatch, tmp_path, boundary):
    trace = []
    error = ValueError("synthetic application failure")
    first_key, second_key = "_stage64_config_first", "_stage64_config_second"
    first, second = object(), object()
    for key in (first_key, second_key):
        monkeypatch.setattr(bot, key, None, raising=False)
    namespace = bot._runtime_config_namespace
    path = tmp_path / "synthetic-config.json"
    logs = []

    def step(point):
        trace.append(point)
        if point == boundary and (point != "namespace" or trace.count(point) == 2):
            raise error

    class BadKey:
        def __hash__(self):
            step("key")
            raise AssertionError("The native key failure should have escaped")

    def pairs():
        yield first_key, first
        assert getattr(bot, first_key) is first
        step("iterator")
        yield (BadKey() if boundary == "key" else second_key), second
        assert getattr(bot, second_key) is second
        step("done")

    class Proposed:
        def __bool__(self):
            step("truth")
            return True

        def items(self):
            step("items")
            return pairs()

        def __len__(self):
            step("length")
            return 2

    proposed = Proposed()

    def load():
        step("load")
        return proposed

    def current_namespace():
        step("namespace")
        assert namespace() is vars(bot)
        return namespace()

    def info(*args):
        logs.append(args)
        step("log")

    def debug(message, value):
        assert message == "Local config overrides applied" and value is proposed
        step("debug")

    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    monkeypatch.setattr(bot, "load_validated_local_config_overrides", load)
    monkeypatch.setattr(bot, "_runtime_config_namespace", current_namespace)
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=info))
    monkeypatch.setattr(bot, "log_json_debug", debug)
    expected = ["load", "truth", "items", "namespace", "iterator", "namespace", "done", "length", "log", "debug"]
    if boundary is None:
        assert bot.apply_local_config() is None
    else:
        with pytest.raises(ValueError) as caught:
            bot.apply_local_config()
        assert caught.value is error
        if boundary == "key":
            expected.insert(6, "key")
        end = 5 if boundary == "namespace" else expected.index(boundary)
        expected = expected[:end + 1]
    assert trace == expected
    assert getattr(bot, first_key) is (first if "iterator" in trace else None)
    assert getattr(bot, second_key) is (second if "done" in trace else None)
    assert logs == ([("Applied %d local config override(s) from %s", 2, path)] if "log" in trace else [])


CREDENTIAL_KEYS = ("CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "MY_USER_ID")
X_ERROR = (
    "Missing X credentials. Set X_CONSUMER_KEY, X_CONSUMER_SECRET, "
    "X_ACCESS_TOKEN, X_ACCESS_SECRET, X_MY_USER_ID"
)
OPENAI_ERROR = "single_call_reply is enabled, but OPENAI_API_KEY is not set"


def _credentials(monkeypatch, *, stop=None, auto=True, enabled=True, api=True, failure=None):
    trace = []
    error = ValueError("synthetic credential failure")

    def observe(label):
        trace.append(label)
        if label == failure:
            raise error

    class Truth:
        def __init__(self, label, value):
            self.label, self.value = label, value

        def __bool__(self):
            observe(self.label)
            return self.value

    class Credential(str):
        def __new__(cls, label, valid):
            value = super().__new__(cls, "12345" if label == "MY_USER_ID" else "synthetic")
            value.label, value.valid = label, valid
            return value

        def strip(self):
            observe(self.label)
            return str(self) if self.valid else ""

    credentials = [Credential(key, index != stop) for index, key in enumerate(CREDENTIAL_KEYS)]
    for key, value in zip(CREDENTIAL_KEYS, credentials):
        monkeypatch.setattr(bot, key, value)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", Truth("auto", auto))
    monkeypatch.setattr(bot, "OPENAI_API_KEY", Credential("openai", api))

    def get(key):
        assert key == "enabled"
        observe("get")
        return enabled

    monkeypatch.setattr(bot, "single_call_reply", SimpleNamespace(get=get))
    return credentials, trace, error


@pytest.mark.parametrize("stop", [None, 0, 3])
def test_credentials_strip_required_strings_in_order(monkeypatch, stop):
    _, trace, _ = _credentials(monkeypatch, stop=stop)
    if stop is None:
        assert bot.validate_production_credentials() is None
        assert trace == [*CREDENTIAL_KEYS, "auto", "get", "openai"]
    else:
        with pytest.raises(RuntimeError, match="Missing X credentials"):
            bot.validate_production_credentials()
        assert trace == list(CREDENTIAL_KEYS[:stop + 1])


@pytest.mark.parametrize("auto,enabled,api,tail,message", [
    (False, True, False, ["auto"], None),
    (True, 1, False, ["auto", "get"], None),
    (True, False, False, ["auto", "get"], None),
    (True, True, True, ["auto", "get", "openai"], None),
    (True, True, False, ["auto", "get", "openai"], OPENAI_ERROR),
])
def test_credential_gate_short_circuits_and_requires_literal_true(monkeypatch, auto, enabled, api, tail, message):
    _, trace, _ = _credentials(monkeypatch, auto=auto, enabled=enabled, api=api)
    if message is None:
        assert bot.validate_production_credentials() is None
    else:
        with pytest.raises(RuntimeError) as caught:
            bot.validate_production_credentials()
        assert str(caught.value) == message
    assert trace == [*CREDENTIAL_KEYS, *tail]


@pytest.mark.parametrize("boundary", ["ACCESS_TOKEN", "auto", "get", "openai"])
def test_credential_native_strip_truth_and_get_errors_keep_exact_progress(monkeypatch, boundary):
    _, trace, error = _credentials(monkeypatch, failure=boundary)
    with pytest.raises(ValueError) as caught:
        bot.validate_production_credentials()
    assert caught.value is error
    order = [*CREDENTIAL_KEYS, "auto", "get", "openai"]
    assert trace == order[:order.index(boundary) + 1]

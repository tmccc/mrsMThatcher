from __future__ import annotations

from decimal import Decimal
import inspect
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_runtime_control as control
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('runtime control import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_runtime_control':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_runtime_control
assert random.getstate() == before
assert not hasattr(mrs_bot_runtime_control, '_CONTROL_CACHE')
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_adapters_forward_current_dependencies_starred_arguments_references_and_errors(monkeypatch):
    for name, count in (
        ("parse_control_time", 4), ("validate_control_document", 5),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(control, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count, name
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        positional = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        has_varargs = any(param.kind == param.VAR_POSITIONAL for param in public.values())
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(control, name, owner)
            for extra_count in (0, 3):
                args = positional + tuple(object() for _ in range(extra_count if has_varargs else 0))
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == (options | current).keys()
                assert all(actual_kwargs[key] is value for key, value in (options | current).items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure



def patch_control_method(monkeypatch, method, callback):
    monkeypatch.setattr(control.RuntimeControls, method, lambda self, *args, **kwargs: callback(*args, **kwargs))


def test_owned_adapters_preserve_public_shapes_references_and_errors(monkeypatch):
    methods = {'control_failure_result': 'failure_result', '_runtime_control_stat_identity': 'stat_identity', '_read_stable_runtime_control': 'read_snapshot', 'load_control': 'load', 'control_bool': 'boolean', 'control_pause_active': 'pause_active', 'lane_paused': 'lane_paused', 'global_remote_writes_paused': 'global_paused'}
    for name, method in methods.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        owned = inspect.signature(getattr(control.RuntimeControls, method)).parameters
        assert tuple(owned)[1:] == tuple(public)
        for key, parameter in public.items():
            assert owned[key].kind == parameter.kind
            assert owned[key].default == parameter.default
        positional = tuple(object() for p in public.values() if p.kind == p.POSITIONAL_OR_KEYWORD)
        if any(p.kind == p.VAR_POSITIONAL for p in public.values()):
            positional += (object(), object())
        options = {key: object() for key, p in public.items() if p.kind == p.KEYWORD_ONLY}
        result = object()
        target = Mock(return_value=result)
        with monkeypatch.context() as patch:
            factory = Mock(return_value=SimpleNamespace(**{method: target}))
            patch.setattr(bot, "_runtime_controls_owner", factory)
            assert adapter(*positional, **options) is result
            assert len(target.call_args.args) == len(positional)
            assert all(a is b for a, b in zip(target.call_args.args, positional))
            assert target.call_args.kwargs.keys() == options.keys()
            assert all(target.call_args.kwargs[key] is value for key, value in options.items())
            factory.assert_called_once_with()
            failure = TypeError("current owner failure")
            target.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*positional, **options)
            assert caught.value is failure


def test_composition_binds_fresh_current_control_authorities_without_access(monkeypatch):
    fields = {'CONTROL_FILE': 'control_file', '_CONTROL_CACHE': 'cache', 'RUNTIME_CONTROL_MAX_BYTES': 'maximum_bytes', '_RuntimeControlAbsent': 'absent_error', 'hashlib': 'hashlib', 'os': 'os', 'stat': 'stat', 'load_strict_runtime_json': 'parse_json', 'log': 'log', 'log_json_debug': 'log_json_debug', 'validate_control_document': 'validate_document', 'now_epoch': 'now_epoch', 'parse_control_time': 'parse_time', 'datetime': 'datetime', 'log_event': 'log_event'}
    previous = None
    for _ in range(2):
        current = {name: object() for name in fields}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        owner = bot._runtime_controls_owner()
        assert owner is not previous
        assert all(getattr(owner, field) is current[name] for name, field in fields.items())
        previous = owner


def test_pause_decision_uses_owned_load_and_boolean(monkeypatch):
    bot.CONTROL_FILE.write_text('{"disable_all":true}')
    def forbidden(*args, **kwargs):
        raise AssertionError("control operation bounced through root")
    for name in ("load_control", "control_bool", "control_pause_active", "_read_stable_runtime_control", "_runtime_control_stat_identity"):
        monkeypatch.setattr(bot, name, forbidden)
    assert bot.lane_paused("disable_replies") is True
    assert bot.global_remote_writes_paused() is True

def test_fixed_key_objects_and_current_sets_parser_preserve_validation_references(monkeypatch):
    for name in ("CONTROL_BOOLEAN_KEYS", "CONTROL_TIME_KEYS", "CONTROL_METADATA_KEYS", "CONTROL_ALLOWED_KEYS"):
        assert getattr(bot, name) is getattr(control, name)
        assert type(getattr(bot, name)) is frozenset
    assert control.CONTROL_TIME_KEYS == frozenset(f"{key}_until" for key in control.CONTROL_BOOLEAN_KEYS)
    assert control.CONTROL_METADATA_KEYS == frozenset({"generation"})
    assert control.CONTROL_ALLOWED_KEYS == control.CONTROL_BOOLEAN_KEYS | control.CONTROL_TIME_KEYS | control.CONTROL_METADATA_KEYS
    monkeypatch.setattr(bot, "CONTROL_ALLOWED_KEYS", frozenset({"window", "date", "flag", "generation"}))
    monkeypatch.setattr(bot, "CONTROL_TIME_KEYS", frozenset({"window", "date"}))
    monkeypatch.setattr(bot, "CONTROL_BOOLEAN_KEYS", frozenset({"flag"}))
    parser = Mock(return_value=77)
    monkeypatch.setattr(bot, "parse_control_time", parser)
    data = {"window": Decimal("2.0"), "date": "2030-01-02T03:04:05", "flag": " YES ", "generation": 3}
    result = bot.validate_control_document(data)
    assert result == {**data, "window": 77}
    assert result is not data and type(data["window"]) is Decimal
    assert result["date"] is data["date"] and result["flag"] is data["flag"]
    assert parser.call_args_list == [call(data["window"]), call(data["date"])]
    with pytest.raises(ValueError, match="flag must be a boolean"):
        bot.validate_control_document({"flag": 1})
    with pytest.raises(ValueError, match="unsupported runtime-control key"):
        bot.validate_control_document({"disable_all": False})


def test_load_and_failure_use_current_mutable_root_cache_without_retaining_old_cache(monkeypatch):
    path = bot.CONTROL_FILE
    first_cache = bot._CONTROL_CACHE
    path.write_bytes(b'{"disable_all":false,"generation":1}')
    assert bot.load_control() == {"disable_all": False, "generation": 1}
    original = dict(first_cache)
    replacement = {"signature": None, "data": {"generation": 9}, "has_valid": True, "failure_signature": None}
    monkeypatch.setattr(bot, "_CONTROL_CACHE", replacement)
    path.write_bytes(b"{")
    failed = bot.load_control()
    assert failed == {"generation": 9, "disable_all": True, "_control_fail_closed": True}
    failed["generation"] = 100
    replacement["data"]["generation"] = 10
    assert bot.load_control()["generation"] == 10
    path.write_bytes(b'{"pause_all":false,"generation":2}')
    repaired = bot.load_control()
    assert repaired == {"pause_all": False, "generation": 2}
    assert repaired is not replacement["data"]
    assert replacement["has_valid"] is True and replacement["failure_signature"] is None
    path.unlink()
    assert bot.load_control() == {}
    assert bot._CONTROL_CACHE is replacement
    assert replacement == {"signature": None, "data": {}, "has_valid": False, "failure_signature": None}
    assert first_cache == original and first_cache["data"] is original["data"]
    assert not hasattr(control, "_CONTROL_CACHE")


def test_failure_logging_precedes_cache_signature_mutation_and_preserves_native_error(monkeypatch):
    cache = bot._CONTROL_CACHE
    original = dict(cache)
    failure = RuntimeError("logger unavailable")
    logger = Mock()
    logger.error.side_effect = failure
    monkeypatch.setattr(bot, "log", logger)
    signature = object()
    with pytest.raises(RuntimeError) as caught:
        bot.control_failure_result("bad snapshot", signature=signature)
    assert caught.value is failure and cache == original
    logger.reset_mock()

    def before_mutation(*args):
        assert args[1:] == (bot.CONTROL_FILE, "bad snapshot")
        assert cache["failure_signature"] is None

    logger.error.side_effect = before_mutation
    first = bot.control_failure_result("bad snapshot", signature=signature)
    second = bot.control_failure_result("bad snapshot", signature=signature)
    assert first == second == {"disable_all": True, "_control_fail_closed": True}
    assert first is not second and cache["failure_signature"] is signature
    logger.error.assert_called_once()


def test_pause_samples_clock_before_boolean_priority_then_ordered_times_and_native_errors(monkeypatch):
    data = {"alpha_until": "bad", "beta_until": 200, "gamma_until": 201}
    trace = Mock()
    trace.clock.return_value = 200
    trace.boolean.side_effect = [False, True]
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    patch_control_method(monkeypatch, "boolean", trace.boolean)
    monkeypatch.setattr(bot, "parse_control_time", trace.parse)
    monkeypatch.setattr(bot, "log", trace.log)
    assert bot.control_pause_active(data, "alpha", "beta", "gamma") == (True, "beta", 0)
    assert trace.mock_calls == [call.clock(), call.boolean(data, "alpha"), call.boolean(data, "beta")]
    assert trace.boolean.call_args.args[0] is data
    trace.reset_mock()
    trace.boolean.side_effect = None
    trace.boolean.return_value = False
    parse_error = ValueError("invalid time")
    trace.parse.side_effect = [parse_error, 200, 201]
    assert bot.control_pause_active(data, "alpha", "beta", "gamma") == (True, "gamma_until", 201)
    assert trace.mock_calls == [
        call.clock(), call.boolean(data, "alpha"), call.boolean(data, "beta"), call.boolean(data, "gamma"),
        call.parse("bad"),
        call.log.error("Invalid pause passed directly to control_pause_active for %s=%r; runtime loader rejects invalid controls: %s", "alpha_until", "bad", parse_error),
        call.parse(200), call.parse(201),
    ]
    trace.reset_mock()
    failure = TypeError("native parser failure")
    trace.parse.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.control_pause_active(data, "alpha")
    assert caught.value is failure
    assert trace.mock_calls == [call.clock(), call.boolean(data, "alpha"), call.parse("bad")]


def test_lane_alias_order_current_data_datetime_and_warning_before_event(monkeypatch):
    data = {"generation": 3}
    lane_keys = ("disable_quote_replies", "pause_all", "disable_replies", "disable_quote_posts",
                 "disable_quote_replies", "disable_meme_posts", "disable_hot_post_replies", "disable_normal_replies")
    expanded = ("disable_all", "pause_all", "disable_quote_replies", "disable_replies", "disable_quote_posts",
                "disable_meme_posts", "disable_hot_post_replies", "disable_normal_replies", "pause_replies",
                "pause_normal_replies", "pause_quote_replies", "pause_hot_post_replies", "pause_quote_posts", "pause_meme_posts")
    trace = Mock()
    trace.load.return_value = data
    trace.pause.return_value = (True, "pause_quote_posts_until", 201)
    trace.date.fromtimestamp.return_value.strftime.return_value = "formatted"
    patch_control_method(monkeypatch, "load", trace.load)
    patch_control_method(monkeypatch, "pause_active", trace.pause)
    monkeypatch.setattr(bot, "datetime", trace.date)
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "log_event", trace.event)
    assert bot.lane_paused(*lane_keys) is True
    assert trace.pause.call_args.args[0] is data
    assert trace.mock_calls == [
        call.load(), call.pause(data, *expanded), call.date.fromtimestamp(201),
        call.date.fromtimestamp().strftime("%Y-%m-%d %H:%M:%S"),
        call.log.warning("Runtime control active: %s pauses %s (%s)", "pause_quote_posts_until", ",".join(lane_keys), "formatted"),
        call.event("runtime_control_pause", key="pause_quote_posts_until", lanes=list(lane_keys), until_epoch=201),
    ]
    trace.reset_mock()
    assert bot.global_remote_writes_paused() is True
    assert trace.mock_calls == [call.load(), call.pause(data, "disable_all", "pause_all")]
    trace.reset_mock()
    failure = RuntimeError("warning failed")
    trace.log.warning.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.lane_paused(*lane_keys)
    assert caught.value is failure
    trace.event.assert_not_called()


@pytest.mark.parametrize("final_path_failure", [False, True])
def test_stable_reader_keeps_real_syscall_order_and_closes_before_hash_or_error(monkeypatch, final_path_failure):
    path = bot.CONTROL_FILE
    document = b'{"disable_all":false}'
    path.write_bytes(document)
    events = []
    descriptors = []
    failure = FileNotFoundError("final path disappeared")
    proxy = SimpleNamespace(**vars(os))

    def tracked(name, callback):
        def invoke(*args, **kwargs):
            events.append(name)
            if name == "lstat" and final_path_failure and events.count(name) == 2:
                raise failure
            result = callback(*args, **kwargs)
            if name == "open":
                descriptors.append(result)
                assert args[1] & os.O_NOFOLLOW and args[1] & os.O_NONBLOCK
            return result
        return invoke

    for name in ("fspath", "lstat", "open", "fstat", "read", "lseek", "close"):
        setattr(proxy, name, tracked(name, getattr(os, name)))
    proxy.path = SimpleNamespace(abspath=tracked("abspath", os.path.abspath))
    monkeypatch.setattr(bot, "os", proxy)
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=tracked("sha256", bot.hashlib.sha256)))
    if final_path_failure:
        with pytest.raises(FileNotFoundError) as caught:
            bot._read_stable_runtime_control()
        assert caught.value is failure
        assert not isinstance(caught.value, bot._RuntimeControlAbsent)
    else:
        actual, signature = bot._read_stable_runtime_control()
        assert actual == document and signature[0] == str(path)
    expected = ["fspath", "abspath", "lstat", "open", "fstat", "read", "read", "fstat",
                "lseek", "read", "read", "fstat", "lstat", "close"]
    assert events == expected + ([] if final_path_failure else ["sha256"])
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])

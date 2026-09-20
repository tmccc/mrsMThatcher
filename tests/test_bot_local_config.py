from __future__ import annotations

import copy
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401

DEPENDENCIES = {'load_strict_runtime_json': []}

SIGNATURES = {'load_strict_runtime_json': "(handle_or_document, *, label: 'str', "
                             "parse_floats_as_decimal: 'bool' = False) -> "
                             "'object'",
 '_coerce_local_config_value': "(key: 'str', value: 'object', current_value: "
                               "'object') -> 'object'",
 '_local_config_stat_identity': "(file_stat: 'os.stat_result') -> 'tuple[int, "
                                "...]'",
 '_read_stable_local_config_bytes': "() -> 'bytes | None'",
 'load_validated_local_config_overrides': "() -> 'dict[str, object] | None'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, decimal, io, json, logging, math, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Local config import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox'} or name.startswith('mrs_bot_') and name != 'mrs_bot_local_config':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_local_config
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


@pytest.mark.parametrize("name", DEPENDENCIES)
def test_adapters_preserve_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    assert str(signature) == SIGNATURES[name]
    positional = [p.name for p in signature.parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in signature.parameters.values()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY]
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

            patch.setattr(bot, "_local_config", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_local_config", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_stat_identity_is_the_exact_alias_with_deferred_standard_annotations():
    identity = bot._local_config_stat_identity
    assert identity is bot._local_config._local_config_stat_identity
    assert str(inspect.signature(identity)) == SIGNATURES[identity.__name__]
    assert get_type_hints(identity) == {"file_stat": os.stat_result, "return": tuple[int, ...]}
    fields = "st_dev st_ino st_mode st_nlink st_uid st_gid st_size st_mtime_ns st_ctime_ns".split()
    values = {name: object() for name in fields}
    seen = []

    class ObservedStat:
        def __getattr__(self, name):
            seen.append(name)
            return values[name]

    result = identity(ObservedStat())
    assert seen == fields
    assert all(value is values[name] for name, value in zip(fields, result))


@pytest.mark.parametrize("decimal_mode", [False, object()], ids=["float", "truthy-decimal"])
def test_parser_preserves_options_callbacks_and_owned_numeric_operations(monkeypatch, decimal_mode):
    result = {"original": []}
    loads = Mock(return_value=result)
    finite = Mock(return_value=True)
    number = SimpleNamespace(is_finite=Mock(return_value=True))
    decimal = Mock(return_value=number)
    monkeypatch.setattr(bot._local_config, "json", SimpleNamespace(loads=loads))
    monkeypatch.setattr(bot._local_config, "math", SimpleNamespace(isfinite=finite))
    monkeypatch.setattr(bot._local_config, "Decimal", decimal)
    reader = SimpleNamespace(read=Mock(return_value=' {"é":1.25} '.encode()))
    assert bot.load_strict_runtime_json(reader, label=" label ", parse_floats_as_decimal=decimal_mode) is result
    reader.read.assert_called_once_with()
    assert loads.call_args.args == (' {"é":1.25} ',)
    options = loads.call_args.kwargs
    assert list(options) == ["object_pairs_hook", "parse_constant", "parse_float"]
    first, second = object(), object()
    pairs = options["object_pairs_hook"]
    parsed = pairs([("b", first), ("a", second)])
    assert list(parsed) == ["b", "a"] and parsed["b"] is first and parsed["a"] is second
    with pytest.raises(ValueError, match="^ label  contains a duplicate object name$"):
        pairs([("b", first), ("b", second)])
    with pytest.raises(ValueError, match="^ label  contains a non-finite JSON constant$"):
        options["parse_constant"]("NaN")
    parse_float = options["parse_float"]
    if decimal_mode:
        assert parse_float("1.25") is number
        decimal.assert_called_once_with("1.25")
        number.is_finite.assert_called_once_with()
        finite.assert_not_called()
        check = number.is_finite
    else:
        assert parse_float("1.25") == 1.25
        finite.assert_called_once_with(1.25)
        decimal.assert_not_called()
        check = finite
    check.return_value = False
    with pytest.raises(ValueError, match="^ label  contains a non-finite JSON number$"):
        parse_float("1.25")
    failure = LookupError("numeric callback")
    check.side_effect = failure
    with pytest.raises(LookupError) as caught:
        parse_float("1.25")
    assert caught.value is failure


def test_parser_keeps_exact_reader_types_utf8_and_native_errors():
    class StringSubclass(str):
        pass

    for value in (StringSubclass("{}"), SimpleNamespace(read=lambda: StringSubclass("{}")),
                  SimpleNamespace(read=lambda: bytearray(b"{}"))):
        with pytest.raises(ValueError, match="reader returned unsupported content"):
            bot.load_strict_runtime_json(value, label="local config")
    with pytest.raises(UnicodeDecodeError):
        bot.load_strict_runtime_json(b"\xff", label="local config")
    with pytest.raises(json.JSONDecodeError):
        bot.load_strict_runtime_json("{", label="local config")
    with pytest.raises(AttributeError):
        bot.load_strict_runtime_json(object(), label="local config")
    failure = OSError("reader failure")
    with pytest.raises(OSError) as caught:
        bot.load_strict_runtime_json(SimpleNamespace(read=Mock(side_effect=failure)), label="local config")
    assert caught.value is failure
    assert bot.load_strict_runtime_json("1e999", label="control", parse_floats_as_decimal=True) == bot.Decimal("1e999")


def test_coercion_keeps_exact_types_references_and_string_exemptions():
    assert bot._coerce_local_config_value is bot._local_config._coerce_local_config_value
    assert str(inspect.signature(bot._coerce_local_config_value)) == SIGNATURES["_coerce_local_config_value"]
    class IntegerSubclass(int):
        pass

    class StringSubclass(str):
        pass

    coerce = bot._coerce_local_config_value
    assert coerce("enabled", True, False) is True
    assert coerce("count", 42, IntegerSubclass(1)) == 42
    for value, current in ((IntegerSubclass(1), 1), (StringSubclass("x"), "x")):
        with pytest.raises(ValueError, match="must be a JSON"):
            coerce("key", value, current)
    value = {"nested": []}
    assert coerce("mapping", value, {}) is value
    text = " \n\t "
    for key in ("MEME_POST_TEXT",):
        assert coerce(key, text, "") is text
        assert coerce(key, "", "") == ""
        with pytest.raises(ValueError, match="unsafe control"):
            coerce(key, "\x00", "")
    assert math.isnan(coerce("unlisted", "nan", 0.0))
    with pytest.raises(ValueError, match="not a boolean"):
        coerce("unlisted", True, 0.0)


def test_coercion_leaves_numeric_bounds_to_complete_config_validation():
    coerce = bot._coerce_local_config_value
    assert coerce("MAX_QUOTE_POSTS_PER_CHECK", 0, 1) == 0
    assert coerce("STATE_BACKUP_COUNT", -1, 1) == -1
    assert coerce("ORIGINAL_EDITORIAL_SHADOW_WEIGHT", "-0.5", 0.0) == -0.5
    assert math.isnan(coerce("ORIGINAL_EDITORIAL_SHADOW_WEIGHT", "nan", 0.0))


@pytest.fixture
def snapshot_reader(monkeypatch):
    trace = Mock()
    snapshots = [SimpleNamespace(st_mode=0o100600, st_size=3, label=name)
                 for name in ("path-before", "fd-before", "fd-middle", "fd-after", "path-after")]
    trace.fspath.return_value = "relative.json"
    trace.abspath.return_value = "/synthetic/local.json"
    trace.lstat.side_effect = [snapshots[0], snapshots[4]]
    trace.is_regular.return_value = True
    trace.open.return_value = 71
    trace.fstat.side_effect = snapshots[1:4]
    trace.read.side_effect = [b"ab", b"c", b""]
    trace.pread.return_value = b"abc"
    trace.identity.return_value = ("same",)
    fake_os = SimpleNamespace(**{name: getattr(trace, name) for name in
                               ("fspath", "lstat", "open", "fstat", "read", "pread", "close")},
                              path=SimpleNamespace(abspath=trace.abspath),
                              O_RDONLY=1, O_CLOEXEC=2, O_NOFOLLOW=4, O_NONBLOCK=8)
    source = object()
    monkeypatch.setattr(bot, "os", fake_os)
    monkeypatch.setattr(bot._local_config, "stat", SimpleNamespace(S_ISREG=trace.is_regular))
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", source)
    monkeypatch.setattr(bot, "LOCAL_CONFIG_MAX_BYTES", 4)
    monkeypatch.setattr(bot._local_config, "_local_config_stat_identity", trace.identity)
    return SimpleNamespace(trace=trace, snapshots=snapshots, source=source, os=fake_os)


def test_snapshot_read_keeps_flags_bounded_reads_reread_and_identity_order(snapshot_reader):
    state = snapshot_reader
    before_path, before_fd, middle_fd, after_fd, after_path = state.snapshots
    assert bot._read_stable_local_config_bytes() == b"abc"
    assert state.trace.mock_calls == [
        call.fspath(state.source), call.abspath("relative.json"), call.lstat("/synthetic/local.json"),
        call.is_regular(before_path.st_mode), call.open("/synthetic/local.json", 15),
        call.fstat(71), call.is_regular(before_fd.st_mode),
        call.identity(before_path), call.identity(before_fd),
        call.read(71, 5), call.read(71, 3), call.read(71, 2), call.fstat(71),
        call.pread(71, 4, 0), call.fstat(71), call.lstat("/synthetic/local.json"), call.close(71),
        call.identity(before_fd), call.identity(middle_fd), call.identity(after_fd), call.identity(after_path),
    ]


@pytest.mark.parametrize("boundary", ["length", "bytes", "identity"])
def test_snapshot_comparisons_keep_precedence_and_short_circuit(snapshot_reader, boundary):
    trace = snapshot_reader.trace
    middle_fd = snapshot_reader.snapshots[2]
    trace.identity.side_effect = lambda value: ("changed",) if value is middle_fd else ("same",)
    if boundary == "length":
        trace.read.side_effect = [b"ab", b""]
    if boundary != "identity":
        trace.pread.return_value = b"xyz"
    with pytest.raises(bot.LocalConfigError, match=boundary + " changed while it was read"):
        bot._read_stable_local_config_bytes()
    trace.close.assert_called_once_with(71)
    if boundary == "identity":
        assert trace.identity.call_args_list[-2:] == [call(snapshot_reader.snapshots[1]), call(middle_fd)]
        assert trace.identity.call_count == 4
    else:
        assert trace.identity.call_count == 2
        assert trace.mock_calls[-1] == call.close(71)


@pytest.mark.parametrize("point, exception_type, wrapped, closes", [
    ("fspath", TypeError, None, False),
    ("lstat", PermissionError, "Failed to inspect", False),
    ("open", FileNotFoundError, "changed before it was opened", False),
    ("pread", ValueError, None, True),
    ("close", OSError, "stable snapshot", True),
])
def test_snapshot_native_errors_and_descriptor_scopes(snapshot_reader, point, exception_type, wrapped, closes):
    trace = snapshot_reader.trace
    failure = exception_type("injected boundary")
    getattr(trace, point).side_effect = failure
    with pytest.raises(bot.LocalConfigError if wrapped else exception_type) as caught:
        bot._read_stable_local_config_bytes()
    if wrapped:
        assert wrapped in str(caught.value) and caught.value.__cause__ is failure
    else:
        assert caught.value is failure
    assert trace.close.call_count == int(closes)
    if closes:
        trace.close.assert_called_once_with(71)


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_NONBLOCK"])
def test_snapshot_requires_current_platform_flags_before_open(snapshot_reader, flag):
    delattr(snapshot_reader.os, flag)
    with pytest.raises(bot.LocalConfigError, match="requires O_NOFOLLOW and O_NONBLOCK"):
        bot._read_stable_local_config_bytes()
    snapshot_reader.trace.open.assert_not_called()


@pytest.fixture
def overrides(monkeypatch):
    trace = Mock()
    defaults = {"first": [], "second": [], "untouched": {"nested": []}, "MAX_OPENAI_ERRORS_PER_WINDOW": 3}
    trace.read.return_value = b"synthetic document"
    trace.parse.return_value = {"first": []}
    trace.coerce.side_effect = lambda _key, value, _default: value
    trace.deepcopy.side_effect = copy.deepcopy
    trace.validate.return_value = []
    for name, value in {
        "LOCAL_CONFIG_FILE": "synthetic.json", "SOURCE_DEFAULT_CONFIG_VALUES": defaults,
        "_read_stable_local_config_bytes": trace.read, "load_strict_runtime_json": trace.parse,
        "_coerce_local_config_value": trace.coerce, "validate_runtime_config_values": trace.validate,
        "copy": SimpleNamespace(deepcopy=trace.deepcopy),
        "log": SimpleNamespace(warning=trace.warning, error=trace.error),
    }.items():
        if name == "_read_stable_local_config_bytes":
            patch_configuration_method(monkeypatch, "read_snapshot", value)
        else:
            target = bot._local_config if name in {"_coerce_local_config_value", "load_strict_runtime_json", "copy"} else bot
            monkeypatch.setattr(target, name, value)
    return SimpleNamespace(trace=trace, defaults=defaults)


def test_loader_migration_order_preserves_input_defaults_and_value_references(overrides):
    trace, defaults = overrides.trace, overrides.defaults
    legacy, current = "MAX_XAI_ERRORS_PER_WINDOW", "MAX_OPENAI_ERRORS_PER_WINDOW"
    first, limit = [], object()
    data = {legacy: limit, "first": first}
    trace.parse.return_value = data
    proposed = bot.load_validated_local_config_overrides()
    assert list(data) == [legacy, "first"] and data[legacy] is limit
    assert list(proposed) == ["first", current]
    assert proposed["first"] is first and proposed[current] is limit
    candidate = trace.validate.call_args.args[0]
    assert candidate is not defaults and candidate["untouched"] is not defaults["untouched"]
    candidate["untouched"]["nested"].append("candidate only")
    assert defaults["untouched"] == {"nested": []} and defaults["first"] == []
    assert candidate["first"] is first and candidate[current] is limit
    assert [c[0] for c in trace.mock_calls] == ["read", "parse", "warning", "coerce", "coerce", "deepcopy", "validate"]
    trace.parse.assert_called_once_with(b"synthetic document", label="local config")
    trace.warning.assert_called_once_with("Migrating retired local config key %s to %s", legacy, current)
    assert trace.coerce.call_args_list == [call("first", first, defaults["first"]), call(current, limit, defaults[current])]
    trace.deepcopy.assert_called_once_with(defaults)


@pytest.mark.parametrize("document, data, error", [
    (None, {"first": []}, None),
    (b"{}", {}, None),
    (b"[]", [], "must contain a JSON object"),
    (b"retired", {"reply_strategy": {}, "MAX_XAI_ERRORS_PER_WINDOW": 1, "MAX_OPENAI_ERRORS_PER_WINDOW": 2}, "retired reply_strategy"),
    (b"conflict", {"MAX_XAI_ERRORS_PER_WINDOW": 1, "MAX_OPENAI_ERRORS_PER_WINDOW": 2}, "both the retired xAI"),
])
def test_loader_missing_empty_and_schema_gates_precede_other_callbacks(overrides, document, data, error):
    trace = overrides.trace
    trace.read.return_value, trace.parse.return_value = document, data
    if error:
        with pytest.raises(bot.LocalConfigError, match=error):
            bot.load_validated_local_config_overrides()
    else:
        assert bot.load_validated_local_config_overrides() == (None if document is None else {})
    assert [c[0] for c in trace.mock_calls] == (["read"] if document is None else ["read", "parse"])


@pytest.mark.parametrize("unknown", [False, True])
def test_loader_ordered_coercion_errors_and_unknown_keys_precede_validation(overrides, unknown):
    trace = overrides.trace
    trace.parse.return_value = {"first": "one", "unknown" if unknown else "second": "two"}
    trace.coerce.side_effect = [ValueError("first failure"), TypeError("second failure")]
    with pytest.raises(bot.LocalConfigError) as caught:
        bot.load_validated_local_config_overrides()
    if unknown:
        assert "Unsupported local config key 'unknown'" in str(caught.value)
        assert trace.coerce.call_count == trace.error.call_count == 1
    else:
        assert str(caught.value) == "Invalid local config synthetic.json: first: first failure; second: second failure"
        assert [c.args[0] for c in trace.coerce.call_args_list] == ["first", "second"]
        assert [c.args[1] for c in trace.error.call_args_list] == ["first", "second"]
    trace.deepcopy.assert_not_called()
    trace.validate.assert_not_called()


@pytest.mark.parametrize("point", ["read", "parse", "warning", "error", "deepcopy", "validate"])
def test_loader_wraps_only_parser_failures_and_keeps_other_native_errors(overrides, point):
    trace = overrides.trace
    if point == "warning":
        trace.parse.return_value = {"MAX_XAI_ERRORS_PER_WINDOW": 1}
    if point == "error":
        trace.coerce.side_effect = ValueError("coercion")
    failure = LookupError("current dependency")
    getattr(trace, point).side_effect = failure
    with pytest.raises(bot.LocalConfigError if point == "parse" else LookupError) as caught:
        bot.load_validated_local_config_overrides()
    if point == "parse":
        assert str(caught.value) == "Failed to read local config file synthetic.json: current dependency"
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure


def test_loader_preserves_current_validator_error_order(overrides):
    overrides.trace.validate.return_value = ["second schema error", "first schema error"]
    with pytest.raises(bot.LocalConfigError) as caught:
        bot.load_validated_local_config_overrides()
    assert str(caught.value) == "Invalid local config synthetic.json: second schema error; first schema error"


def patch_configuration_method(monkeypatch, method, callback):
    monkeypatch.setattr(bot._local_config.LocalConfiguration, method, lambda _owner, *args, **kwargs: callback(*args, **kwargs))


def test_configuration_owner_preserves_current_inputs_without_reading(monkeypatch):
    fields = {'LOCAL_CONFIG_FILE': 'config_file', 'LOCAL_CONFIG_MAX_BYTES': 'maximum_bytes', 'LocalConfigError': 'error_type', 'os': 'os', 'SOURCE_DEFAULT_CONFIG_VALUES': 'source_defaults', 'log': 'log', 'validate_runtime_config_values': 'validate_runtime_values'}
    previous = None
    for _ in range(2):
        current = {name: Mock(side_effect=AssertionError("construction performed work")) for name in fields}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        owner = bot._local_configuration_owner()
        assert owner is not previous and set(vars(owner)) == set(fields.values())
        assert all(getattr(owner, field) is current[name] for name, field in fields.items())
        assert all(not value.called for value in current.values())
        previous = owner


@pytest.mark.parametrize("name, method", [('_read_stable_local_config_bytes', 'read_snapshot'), ('load_validated_local_config_overrides', 'load_overrides')])
def test_configuration_owner_adapters_preserve_public_contract_and_error(monkeypatch, name, method):
    adapter = getattr(bot, name)
    assert str(inspect.signature(adapter)) == SIGNATURES[name]
    assert list(inspect.signature(getattr(bot._local_config.LocalConfiguration, method)).parameters) == ["self"]
    result = {"original": []}
    operation = Mock(return_value=result)
    factory = Mock(return_value=SimpleNamespace(**{method: operation}))
    monkeypatch.setattr(bot, "_local_configuration_owner", factory)
    assert adapter() is result
    factory.assert_called_once_with()
    operation.assert_called_once_with()
    failure = KeyboardInterrupt("current owner failure")
    operation.side_effect = failure
    with pytest.raises(KeyboardInterrupt) as caught:
        adapter()
    assert caught.value is failure


def test_override_loader_uses_owned_reader_and_fixed_parser(monkeypatch, tmp_path):
    config_file = tmp_path / "local.json"
    config_file.write_text('{"MAX_OPENAI_ERRORS_PER_WINDOW": 7}', encoding="utf-8")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", config_file)
    for name in ("_read_stable_local_config_bytes", "load_strict_runtime_json"):
        monkeypatch.setattr(bot, name, Mock(side_effect=AssertionError("fixed operation bounced through root")))
    validator = Mock(return_value=[])
    monkeypatch.setattr(bot, "validate_runtime_config_values", validator)
    assert bot.load_validated_local_config_overrides() == {"MAX_OPENAI_ERRORS_PER_WINDOW": 7}
    assert validator.call_args.args[0]["MAX_OPENAI_ERRORS_PER_WINDOW"] == 7

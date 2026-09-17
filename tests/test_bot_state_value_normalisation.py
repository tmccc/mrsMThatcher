from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_state_value_normalisation as values
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('state value normalisation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_state_value_normalisation':
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
import mrs_bot_state_value_normalisation
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


def test_adapters_forward_current_dependencies_references_and_native_errors(monkeypatch):
    for name, count in (
        ("bounded_tweet_id_value", 1), ("normalise_state_int", 2),
        ("normalise_state_epoch", 3), ("normalise_string_list", 1),
        ("normalise_int_list", 2), ("normalise_epoch_list", 3),
        ("normalise_string_map", 1), ("normalise_int_map", 2),
        ("normalise_record_map", 1), ("normalise_optional_scalar", 1),
        ("normalise_optional_numeric_id", 2),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(values, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        original = object()
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        with monkeypatch.context() as patch:
            for _ in range(2):
                result = object()
                owner = Mock(return_value=result)
                patch.setattr(bot, "_state_value_normalisation", SimpleNamespace(**{name: owner}))
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(original, **options) is result
                assert owner.call_args.args == (original,)
                assert owner.call_args.args[0] is original
                assert owner.call_args.kwargs.keys() == (options | current).keys()
                assert all(owner.call_args.kwargs[key] is value for key, value in (options | current).items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(original, **options)
            assert caught.value is failure


def test_ids_and_optional_scalars_keep_distinct_types_and_equality_shortcuts(tmp_path):
    class Text(str):
        pass

    class EmptyLike:
        def __eq__(self, other):
            return other is None or other == ""

        def __str__(self):
            raise AssertionError("empty equality must precede conversion")

    options = {"key": "id", "path": tmp_path}
    assert bot.bounded_tweet_id_value("") is None
    assert bot.bounded_tweet_id_value("", allow_empty=True) == 0
    assert bot.bounded_tweet_id_value(EmptyLike(), allow_empty=True) == 0
    assert bot.bounded_tweet_id_value(Text("7")) is None
    assert bot.bounded_tweet_id_value(7) is None
    assert bot.bounded_tweet_id_value(" 7") is None
    assert bot.bounded_tweet_id_value("٠١٢") == 12
    assert bot.bounded_tweet_id_value("0" * 29 + "7") == 7
    assert bot.bounded_tweet_id_value("0" * 30 + "7") is None
    assert bot.normalise_optional_numeric_id(EmptyLike(), **options) == ""
    assert bot.normalise_optional_numeric_id(7, **options) == "7"
    assert bot.normalise_optional_numeric_id(Text("007"), **options) == "007"
    assert bot.normalise_optional_numeric_id(True, **options) is None
    assert bot.normalise_optional_scalar(True, **options) == "True"
    assert bot.normalise_optional_scalar(Text("007"), **options) == "007"
    assert bot.normalise_optional_scalar(None, **options) == ""
    assert bot.normalise_optional_scalar(7.0, **options) is None


def test_current_regex_and_optional_id_callback_keep_text_and_error_order(monkeypatch, tmp_path):
    regex, bounded, logger = Mock(return_value=True), Mock(return_value=0), Mock()
    monkeypatch.setattr(bot, "re", SimpleNamespace(fullmatch=regex))
    assert bot.bounded_tweet_id_value("007") == 7
    regex.assert_called_once_with(r"\d{1,30}", "007")
    failure = ValueError("current regex failure")
    regex.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.bounded_tweet_id_value("007")
    assert caught.value is failure
    monkeypatch.setattr(bot, "bounded_tweet_id_value", bounded)
    monkeypatch.setattr(bot, "log", logger)
    assert bot.normalise_optional_numeric_id("007", key="id", path=tmp_path) == "007"
    bounded.assert_called_once_with("007")
    bounded.return_value = None
    assert bot.normalise_optional_numeric_id(7, key="id", path=tmp_path) is None
    logger.error.assert_called_once_with(
        "State candidate %s has invalid %s value %r; ignoring", tmp_path, "id", 7,
    )


def test_state_int_truth_conversion_and_narrow_error_boundary(monkeypatch, tmp_path):
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    options = {"key": "count", "path": tmp_path}
    events = []
    truth, failure = False, None

    class Number:
        def __bool__(self):
            events.append("truth")
            return truth

        def __int__(self):
            events.append("int")
            if failure is not None:
                raise failure
            return 7

    value = Number()
    assert bot.normalise_state_int(value, **options) == 0
    assert events == ["truth"]
    truth = True
    events.clear()
    assert bot.normalise_state_int(value, **options) == 7
    assert events == ["truth", "int"]
    assert bot.normalise_state_int(" 007 ", **options) == 7
    assert bot.normalise_state_int(-1, **options) is None
    logger.error.assert_called_once_with(
        "State candidate %s has negative %s value %r; ignoring", tmp_path, "count", -1,
    )
    for failure in (TypeError("type"), ValueError("value"), OverflowError("overflow")):
        logger.reset_mock()
        assert bot.normalise_state_int(value, **options) is None
        logger.error.assert_called_once_with(
            "State candidate %s has invalid %s value %r; ignoring", tmp_path, "count", value,
        )
        assert logger.error.call_args.args[-1] is value
    failure = RuntimeError("native conversion failure")
    logger.reset_mock()
    with pytest.raises(RuntimeError) as caught:
        bot.normalise_state_int(value, **options)
    assert caught.value is failure
    logger.error.assert_not_called()


def test_state_int_uses_current_math_outside_the_conversion_catch(monkeypatch, tmp_path):
    finite, logger = Mock(return_value=False), Mock()
    monkeypatch.setattr(bot, "math", SimpleNamespace(isfinite=finite))
    monkeypatch.setattr(bot, "log", logger)
    options = {"key": "count", "path": tmp_path}
    assert bot.normalise_state_int(True, **options) is None
    finite.assert_not_called()
    assert bot.normalise_state_int(2.0, **options) is None
    finite.assert_called_once_with(2.0)
    finite.return_value = True
    assert bot.normalise_state_int(2.0, **options) == 2
    failure = ValueError("native math failure")
    finite.side_effect = failure
    logger.reset_mock()
    with pytest.raises(ValueError) as caught:
        bot.normalise_state_int(2.0, **options)
    assert caught.value is failure
    logger.error.assert_not_called()


def test_epochs_use_current_callbacks_inclusive_cap_and_original_list(monkeypatch, tmp_path):
    original, logger = object(), Mock()
    number, epochs = Mock(return_value=10), Mock(return_value=[0, 10])
    monkeypatch.setattr(bot, "normalise_state_int", number)
    monkeypatch.setattr(bot, "normalise_int_list", epochs)
    monkeypatch.setattr(bot, "MAX_REASONABLE_STATE_EPOCH", 10)
    monkeypatch.setattr(bot, "log", logger)
    options = {"key": "epoch", "path": tmp_path}
    assert bot.normalise_state_epoch(original, **options) == 10
    assert bot.normalise_epoch_list(original, **options) is epochs.return_value
    number.assert_called_once_with(original, **options)
    epochs.assert_called_once_with(original, **options)
    logger.error.assert_not_called()
    monkeypatch.setattr(bot, "MAX_REASONABLE_STATE_EPOCH", 9)
    assert bot.normalise_state_epoch(original, **options) is None
    assert bot.normalise_epoch_list(original, **options) is None
    assert logger.error.call_args_list == [
        call("State candidate %s has impossible epoch %s=%r; ignoring", tmp_path, "epoch", original),
        call("State candidate %s has impossible %s epoch item %r; ignoring", tmp_path, "epoch", 10),
    ]
    epochs.return_value = [10, object()]
    assert bot.normalise_epoch_list(original, **options) is None
    number.return_value = epochs.return_value = None
    logger.reset_mock()
    assert bot.normalise_state_epoch(original, **options) is None
    assert bot.normalise_epoch_list(original, **options) is None
    logger.error.assert_not_called()


def test_string_containers_skip_only_none_and_preserve_collision_order(monkeypatch, tmp_path):
    class Rows(list):
        pass

    class Mapping(dict):
        pass

    class Unconvertible:
        def __str__(self):
            raise ValueError("native string failure")

    options = {"key": "items", "path": tmp_path}
    assert bot.normalise_string_list(Rows([None, 0, False, ""]), **options) == ["0", "False", ""]
    source = Mapping({1: False, "after": 0, "1": "", Unconvertible(): None})
    assert list(bot.normalise_string_map(source, **options).items()) == [("1", ""), ("after", "0")]
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    assert bot.normalise_string_list((), **options) is None
    logger.error.assert_called_once_with(
        "State candidate %s has invalid %s type %s; ignoring", tmp_path, "items", "tuple",
    )
    logger.reset_mock()
    with pytest.raises(ValueError, match="native string failure"):
        bot.normalise_string_map({1: Unconvertible()}, **options)
    logger.error.assert_not_called()


def test_int_collections_keep_callback_stop_and_key_format_conversion_order(monkeypatch, tmp_path):
    first, rejected, later = object(), object(), object()
    normalizer = Mock(side_effect=[1, None])
    monkeypatch.setattr(bot, "normalise_state_int", normalizer)
    options = {"key": "counts", "path": tmp_path}
    assert bot.normalise_int_list([first, rejected, later], **options) is None
    assert normalizer.call_args_list == [call(first, **options), call(rejected, **options)]
    events = []

    class Key:
        def __init__(self, label):
            self.label = label

        def __format__(self, spec):
            assert spec == ""
            events.append(("format", self.label))
            return self.label

        def __str__(self):
            events.append(("str", self.label))
            return "same"

    def normalize(value, *, key, path):
        assert path is tmp_path
        events.append(("normalize", key))
        return value

    monkeypatch.setattr(bot, "normalise_state_int", normalize)
    one, two = Key("one"), Key("two")
    source = {one: 1, "after": 2, two: 3}
    assert list(bot.normalise_int_map(source, **options).items()) == [("same", 3), ("after", 2)]
    assert events == [
        ("format", "one"), ("normalize", "counts.one"), ("str", "one"),
        ("normalize", "counts.after"),
        ("format", "two"), ("normalize", "counts.two"), ("str", "two"),
    ]
    events.clear()
    assert bot.normalise_int_map({one: None, two: 2}, **options) is None
    assert events == [("format", "one"), ("normalize", "counts.one")]


def test_record_maps_keep_shallow_copies_collision_order_and_early_failure(monkeypatch, tmp_path):
    class Record(dict):
        pass

    class Unconvertible:
        def __str__(self):
            raise AssertionError("rejected record key must not be converted")

    nested = []
    row = Record(nested=nested)
    source = {1: {"old": True}, "after": row, "1": row}
    options = {"key": "records", "path": tmp_path}
    result = bot.normalise_record_map(source, **options)
    assert list(result) == ["1", "after"]
    assert result["1"] == result["after"] == row
    assert result["1"] is not row and result["after"] is not row
    assert result["1"] is not result["after"]
    assert result["1"]["nested"] is result["after"]["nested"] is nested
    bad_key, logger = Unconvertible(), Mock()
    monkeypatch.setattr(bot, "log", logger)
    assert bot.normalise_record_map({bad_key: None, Unconvertible(): row}, **options) is None
    logger.error.assert_called_once_with(
        "State candidate %s has invalid %s.%s type %s; ignoring",
        tmp_path, "records", bad_key, "NoneType",
    )


def test_collection_subclasses_keep_native_iteration_failures(monkeypatch, tmp_path):
    failure, logger = RuntimeError("native iteration failure"), Mock()

    class Rows(list):
        def __iter__(self):
            raise failure

    class Mapping(dict):
        def items(self):
            raise failure

    monkeypatch.setattr(bot, "log", logger)
    for normalizer, source in (
        (bot.normalise_string_list, Rows()), (bot.normalise_int_list, Rows()),
        (bot.normalise_string_map, Mapping()), (bot.normalise_int_map, Mapping()),
        (bot.normalise_record_map, Mapping()),
    ):
        with pytest.raises(RuntimeError) as caught:
            normalizer(source, key="items", path=tmp_path)
        assert caught.value is failure
    logger.error.assert_not_called()

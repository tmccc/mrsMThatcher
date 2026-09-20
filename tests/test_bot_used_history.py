"""Contracts for durable quote and image used-history extraction."""
from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
import mrs_bot_used_history as owner
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401

METHODS = {'load_used_set': 'load_used_set', 'save_used_set': 'save_used_set', 'quote_used_history_has_legacy_indices': 'quote_used_history_has_legacy_indices', 'quote_source_matches_analysis': 'quote_source_matches_analysis', 'normalise_quote_used_hashes': 'normalise_quote_used_hashes', 'load_quote_used_hashes': 'load_quote_used_hashes', 'save_quote_used_hashes': 'save_quote_used_hashes', 'save_image_used_basenames': 'save_image_used_basenames', 'image_used_history_has_legacy_indices': 'image_used_history_has_legacy_indices', 'image_corpus_verified_for_legacy_migration': 'image_corpus_verified_for_legacy_migration', 'load_image_used_basenames': 'load_image_used_basenames', 'normalise_image_used_basenames': 'normalise_image_used_basenames'}


SIGNATURES = {
    'coerce_used_set': "(value: 'object', *, path: 'Path') -> 'set'",
    'used_set_to_sorted_list': "(value: 'set') -> 'list'",
    'load_used_set': "(path: 'Path', *, legacy_pickle_path: 'Path | None' = None) -> 'set'",
    'save_used_set': "(path: 'Path', value: 'set', *, durable: 'bool' = False) -> 'None'",
    'quote_used_history_has_legacy_indices': "(value: 'set') -> 'bool'",
    'quote_source_matches_analysis': "(quote_analysis: 'dict | None', lines: 'list[str]') -> 'bool'",
    'normalise_quote_used_hashes': "(raw_used: 'set', lines: 'list[str]', quote_analysis: 'dict | None' = None) -> 'tuple[set, bool]'",
    'load_quote_used_hashes': "(lines: 'list[str]') -> 'set[str]'",
    'save_quote_used_hashes': "(path: 'Path', value: 'set[str]', *, durable: 'bool' = False) -> 'None'",
    'save_image_used_basenames': "(path: 'Path', value: 'set[str]', *, durable: 'bool' = False) -> 'None'",
    'image_used_history_has_legacy_indices': "(images_used: 'set') -> 'bool'",
    'image_corpus_verified_for_legacy_migration': "(images: 'list[str]', image_analysis: 'dict | None') -> 'bool'",
    'normalise_image_used_basenames': "(images_used: 'set', images: 'list[str]', image_analysis: 'dict | None' = None) -> 'tuple[set, bool]'",
    'load_image_used_basenames': "(images: 'list[str]') -> 'set'",
}

def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Used-history import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_used_history':
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
import mrs_bot_used_history
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_used_history.coerce_used_set.__annotations__['path'] == 'Path'
assert mrs_bot_used_history.UsedHistory.save_used_set.__annotations__['return'] == 'None'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", SIGNATURES)
def test_adapters_preserve_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    assert str(signature) == SIGNATURES[name]
    positional = [p.name for p in signature.parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in signature.parameters.values()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY]
    if name not in METHODS:
        assert adapter is getattr(owner, name)
        assert adapter.__annotations__ == getattr(owner, name).__annotations__
        return
    method = METHODS[name]
    owned = inspect.signature(getattr(owner.UsedHistory, method)).parameters
    assert tuple(owned)[1:] == tuple(signature.parameters)
    for key, parameter in signature.parameters.items():
        assert owned[key].kind == parameter.kind
        assert owned[key].default == parameter.default
    for _ in range(2):
        with monkeypatch.context() as patch:
            result = {"original": []}
            expected = {}
            def capture(*args, **kwargs):
                assert len(args) == len(positional)
                assert all(value is expected[key] for key, value in zip(positional, args))
                supplied = {key: expected[key] for key in keyword_only}
                assert kwargs.keys() == supplied.keys()
                assert all(kwargs[key] is value for key, value in supplied.items())
                return result
            current = SimpleNamespace(**{method: capture})
            patch.setattr(bot, "_used_history_owner", Mock(return_value=current))
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
            setattr(current, method, Mock(side_effect=failure))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure



def patch_history_dependency(monkeypatch, name, callback):
    if name in METHODS:
        monkeypatch.setattr(owner.UsedHistory, METHODS[name], lambda self, *args, **kwargs: callback(*args, **kwargs))
    elif name in {"coerce_used_set", "used_set_to_sorted_list"}:
        monkeypatch.setattr(owner, name, callback)
    else:
        monkeypatch.setattr(bot, name, callback)


def test_composition_binds_current_history_authorities_without_runtime_access(monkeypatch):
    fields = {'CorruptUsedHistoryError': 'corrupt_error', 'UnsafeDurableStateNamespace': 'unsafe_namespace', 'json': 'json', 'log': 'log', 'read_stable_owned_json_bytes_no_follow': 'read_stable_bytes', 'atomic_write_json': 'write_json', 're': 're', 'hashlib': 'hashlib', 'current_quote_hashes_by_line': 'quote_hashes_by_line', 'LINES_USED_FILE': 'quote_history_file', 'PICKLE_FILE': 'legacy_quote_file', 'load_quote_analysis': 'quote_analysis', 'Path': 'path_type', 'IMAGES_USED_FILE': 'image_history_file', 'IMAGE_PICKLE_FILE': 'legacy_image_file', 'load_image_analysis': 'image_analysis'}
    previous = None
    for _ in range(2):
        current = {name: object() for name in fields}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        current_owner = bot._used_history_owner()
        assert current_owner is not previous
        assert all(getattr(current_owner, field) is current[name] for name, field in fields.items())
        previous = current_owner


def test_owned_loading_keeps_migration_and_save_inside_history(monkeypatch, tmp_path):
    path = tmp_path / "used.json"
    monkeypatch.setattr(bot, "read_stable_owned_json_bytes_no_follow", lambda path: (True, b'["2", "1", "1"]'))
    write = Mock()
    monkeypatch.setattr(bot, "atomic_write_json", write)
    def forbidden(*args, **kwargs):
        raise AssertionError("used history bounced through root")
    for name in ("save_used_set", "coerce_used_set", "used_set_to_sorted_list"):
        monkeypatch.setattr(bot, name, forbidden)
    result = bot.load_used_set(path)
    assert result == {"1", "2"}
    write.assert_called_once_with(path, ["1", "2"], durable=False)

def test_coercion_keeps_set_identity_list_only_conversion_and_native_errors():
    class Used(set):
        pass

    path = Path("synthetic-history.json")
    existing = Used({1})
    assert bot.coerce_used_set(existing, path=path) is existing
    assert bot.coerce_used_set([1, 1, "2"], path=path) == {1, "2"}
    for value in (None, {}, (1,), frozenset({1}), "[]"):
        with pytest.raises(ValueError) as caught:
            bot.coerce_used_set(value, path=path)
        assert str(caught.value) == f"Used-history file {path} must contain a JSON list"
    with pytest.raises(TypeError, match="unhashable"):
        bot.coerce_used_set([[]], path=path)


def test_sorting_keeps_numeric_first_stable_ties_fallback_and_original_items():
    class Fallback:
        def __int__(self):
            raise RuntimeError("use text")

        def __str__(self):
            return "alpha"

    item = Fallback()
    values = ["z", "02", 2, item, -1, "11"]
    before = list(values)
    result = bot.used_set_to_sorted_list(values)
    assert result == [-1, "02", 2, "11", item, "z"]
    assert result[4] is item
    assert values == before


@pytest.mark.parametrize("failure", [KeyboardInterrupt("int"), TypeError("str")])
def test_sorting_preserves_conversion_exception_boundaries(failure):
    class Item:
        def __int__(self):
            if isinstance(failure, KeyboardInterrupt):
                raise failure
            raise ValueError("fallback")

        def __str__(self):
            raise failure

    with pytest.raises(type(failure)) as caught:
        bot.used_set_to_sorted_list([Item()])
    assert caught.value is failure


def _read_trace(monkeypatch):
    trace = Mock()
    path, legacy = Path("synthetic-used.json"), Mock()
    legacy.exists.return_value = False
    data = SimpleNamespace(decode=trace.decode)
    trace.read.return_value = (True, data)
    trace.decode.return_value = object()
    trace.loads.return_value = [2, 1, 1]
    trace.coerce.return_value = {1, 2}
    trace.sort.return_value = [1, 2]
    for name, value in {
        "log": trace.log, "read_stable_owned_json_bytes_no_follow": trace.read,
        "json": SimpleNamespace(loads=trace.loads), "coerce_used_set": trace.coerce,
        "used_set_to_sorted_list": trace.sort, "save_used_set": trace.save,
    }.items():
        patch_history_dependency(monkeypatch, name, value)
    return trace, path, legacy


@pytest.mark.parametrize("value", [[2, 1, 1], [1, 2], {1, 2}])
def test_load_keeps_decode_coercion_order_rewrite_and_result_reference(monkeypatch, value):
    trace, path, legacy = _read_trace(monkeypatch)
    trace.loads.return_value = value
    result = bot.load_used_set(path, legacy_pickle_path=legacy)
    assert result is trace.coerce.return_value
    expected = [
        call.log.debug("Loading used-history set from %s", path), call.read(path),
        call.decode("utf-8"), call.loads(trace.decode.return_value),
        call.coerce(value, path=path),
    ]
    if isinstance(value, list):
        expected.append(call.sort(result))
        if value != [1, 2]:
            expected += [call.save(path, result), call.log.info("Normalized used-history JSON ordering in %s", path)]
    expected.append(call.log.debug("Loaded %d entries from %s", 2, path))
    assert trace.mock_calls == expected
    assert trace.coerce.call_args.args[0] is value
    legacy.exists.assert_not_called()


@pytest.mark.parametrize("present, data", [(False, b"ignored"), (True, None), (False, None)])
def test_load_missing_reader_outcomes_never_decode_and_return_fresh_empty_sets(monkeypatch, present, data):
    trace, path, legacy = _read_trace(monkeypatch)
    trace.read.return_value = present, data
    first = bot.load_used_set(path, legacy_pickle_path=legacy)
    second = bot.load_used_set(path, legacy_pickle_path=legacy)
    assert first == second == set() and first is not second
    assert legacy.exists.call_count == 2
    trace.decode.assert_not_called()
    trace.save.assert_not_called()


@pytest.mark.parametrize("stage, failure, message", [
    ("read", OSError("read"), "unreadable"),
    ("read", bot.UnsafeDurableStateNamespace("namespace"), "unreadable"),
    ("decode", UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"), "corrupt or invalid"),
    ("loads", ValueError("json"), "corrupt or invalid"),
    ("coerce", TypeError("set"), "corrupt or invalid"),
    ("sort", OSError("sort"), "unreadable"),
    ("save", ValueError("save"), "corrupt or invalid"),
    ("info", RuntimeError("normalized log"), "corrupt or invalid"),
    ("loaded", OSError("loaded log"), "unreadable"),
])
def test_load_retains_original_try_scope_and_implicit_exception_context(monkeypatch, stage, failure, message):
    trace, path, legacy = _read_trace(monkeypatch)
    if stage == "loaded":
        trace.log.debug.side_effect = [None, failure]
    elif stage == "info":
        trace.log.info.side_effect = failure
    else:
        getattr(trace, stage).side_effect = failure
    with pytest.raises(bot.CorruptUsedHistoryError) as caught:
        bot.load_used_set(path, legacy_pickle_path=legacy)
    assert str(caught.value) == f"Existing used-history JSON is {message}: {path}"
    assert caught.value.__context__ is failure
    assert caught.value.__cause__ is None and not caught.value.__suppress_context__
    legacy.exists.assert_not_called()
    trace.log.exception.assert_called_once()


@pytest.mark.parametrize("stage", ["read", "sort", "save", "info", "loaded"])
def test_file_not_found_inside_try_keeps_missing_only_legacy_refusal(monkeypatch, stage):
    trace, path, legacy = _read_trace(monkeypatch)
    failure = FileNotFoundError("inside try")
    if stage == "loaded":
        trace.log.debug.side_effect = [None, failure]
    elif stage == "info":
        trace.log.info.side_effect = failure
    else:
        getattr(trace, stage).side_effect = failure
    legacy.exists.return_value = True
    with pytest.raises(bot.CorruptUsedHistoryError) as caught:
        bot.load_used_set(path, legacy_pickle_path=legacy)
    assert str(caught.value) == f"Used-history JSON missing while legacy pickle exists: {path}"
    assert caught.value.__context__ is None
    assert legacy.mock_calls == [call.exists()]
    trace.log.warning.assert_called_once_with("Used-history JSON file does not exist yet: %s", path)
    trace.log.critical.assert_called_once()
    trace.log.exception.assert_not_called()


@pytest.mark.parametrize("stage", ["initial_log", "legacy_exists", "legacy_log"])
def test_load_errors_outside_try_escape_without_wrapping(monkeypatch, stage):
    trace, path, legacy = _read_trace(monkeypatch)
    failure = OSError(stage)
    trace.read.return_value = False, None
    if stage == "initial_log":
        trace.log.debug.side_effect = failure
    elif stage == "legacy_exists":
        legacy.exists.side_effect = failure
    else:
        legacy.exists.return_value = True
        trace.log.critical.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.load_used_set(path, legacy_pickle_path=legacy)
    assert caught.value is failure
    trace.log.exception.assert_not_called()
    if stage == "initial_log":
        trace.read.assert_not_called()


def test_savers_keep_current_callbacks_raw_paths_durable_values_and_distinct_conversion(monkeypatch):
    trace = Mock()
    path, durable = object(), object()
    values = {"2", 2, "11"}
    patch_history_dependency(monkeypatch, "log", trace.log)
    patch_history_dependency(monkeypatch, "atomic_write_json", trace.write)
    patch_history_dependency(monkeypatch, "used_set_to_sorted_list", trace.sort)
    trace.sort.return_value = object()
    assert bot.save_used_set(path, values, durable=durable) is None
    assert trace.mock_calls == [
        call.log.debug("Saving %d entries to used-history JSON %s", 3, path),
        call.sort(values), call.write(path, trace.sort.return_value, durable=durable),
    ]
    assert trace.sort.call_args.args[0] is values
    assert trace.write.call_args.args[0] is path
    assert trace.write.call_args.kwargs["durable"] is durable
    patch_history_dependency(monkeypatch, "save_used_set", trace.save)
    assert bot.save_quote_used_hashes(path, values, durable=durable) is None
    trace.save.assert_called_once_with(path, {"2", "11"}, durable=durable)
    trace.reset_mock()
    assert bot.save_image_used_basenames(path, values, durable=durable) is None
    assert trace.mock_calls == [call.write(path, ["11", "2", "2"], durable=durable)]
    assert values == {"2", 2, "11"}


@pytest.mark.parametrize("name", ["quote_used_history_has_legacy_indices", "image_used_history_has_legacy_indices"])
def test_legacy_predicates_keep_exact_pattern_current_re_and_short_circuit(monkeypatch, name):
    trace = Mock()
    trace.fullmatch.side_effect = [None, object()]
    patch_history_dependency(monkeypatch, "re", trace)

    def entries():
        yield "not-index"
        yield -2
        raise AssertionError("predicate consumed after match")

    assert getattr(bot, name)(entries()) is True
    assert trace.mock_calls == [call.fullmatch(r"-?\d+", "not-index"), call.fullmatch(r"-?\d+", "-2")]


def test_quote_source_proof_hashes_exact_joined_lines_and_keeps_repeated_gets(monkeypatch):
    trace = Mock()

    class Digest:
        def __str__(self):
            trace.digest_text()
            return "current"

    class Analysis(dict):
        def get(self, *args):
            trace.analysis_get(*args)
            return super().get(*args)

    class Source(dict):
        def get(self, *args):
            trace.source_get(*args)
            return super().get(*args)

    source = Source(source_sha256=Digest())
    analysis = Analysis(source=source)
    patch_history_dependency(monkeypatch, "hashlib", SimpleNamespace(sha256=trace.sha256))
    trace.sha256.return_value = SimpleNamespace(hexdigest=trace.hexdigest)
    trace.hexdigest.return_value = "current"
    lines = [" one\r\n", "é\n", "last"]
    assert bot.quote_source_matches_analysis(analysis, lines) is True
    assert trace.mock_calls == [
        call.analysis_get("source"), call.analysis_get("source", {}),
        call.source_get("source_sha256"), call.sha256(" one\r\né\nlast".encode("utf-8")),
        call.hexdigest(), call.digest_text(),
    ]


def test_quote_source_proof_refuses_missing_metadata_before_touching_lines(monkeypatch):
    hashing = Mock(side_effect=AssertionError("unexpected hash"))
    patch_history_dependency(monkeypatch, "hashlib", SimpleNamespace(sha256=hashing))
    for analysis in (None, [], {}, {"source": []}, {"source": {"source_sha256": ""}}):
        assert bot.quote_source_matches_analysis(analysis, object()) is False
    hashing.assert_not_called()
    with pytest.raises(TypeError):
        bot.quote_source_matches_analysis({"source": {"source_sha256": "expected"}}, [None])


def test_quote_normalization_keeps_eager_callbacks_unproved_item_identity_and_changed_rule(monkeypatch):
    trace = Mock()

    class Legacy:
        def __str__(self):
            trace.text()
            return "1"

        def __int__(self):
            trace.integer()
            return 1

    item, lines, analysis = Legacy(), object(), object()
    trace.hashes.return_value = {1: "unused"}
    trace.proof.return_value = False
    patch_history_dependency(monkeypatch, "current_quote_hashes_by_line", trace.hashes)
    patch_history_dependency(monkeypatch, "quote_source_matches_analysis", trace.proof)
    result, changed = bot.normalise_quote_used_hashes({item}, lines, analysis)
    assert next(iter(result)) is item and changed is True
    assert trace.mock_calls == [call.hashes(lines), call.proof(analysis, lines), call.text(), call.integer(), call.text()]
    assert trace.hashes.call_args.args[0] is lines
    assert trace.proof.call_args.args[0] is analysis
    trace.reset_mock()
    failure = TypeError("eager hash lookup")
    trace.hashes.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.normalise_quote_used_hashes(set(), lines, analysis)
    assert caught.value is failure
    assert trace.mock_calls == [call.hashes(lines)]


def test_quote_normalization_retains_hash_rules_mapping_and_drops_invalid_entries(monkeypatch):
    trace = Mock()
    mapped = "c" * 64
    patch_history_dependency(monkeypatch, "current_quote_hashes_by_line", lambda lines: {1: mapped})
    patch_history_dependency(monkeypatch, "quote_source_matches_analysis", lambda analysis, lines: True)
    patch_history_dependency(monkeypatch, "log", trace)
    raw = ["A" * 64, "b" * 64, "1", "invalid", -3]
    result, changed = bot.normalise_quote_used_hashes(raw, [])
    assert result == {"a" * 64, "b" * 64, mapped} and changed is True
    assert trace.mock_calls == [
        call.warning("Dropping unrecognised quote used-history entry: %r", "invalid"),
        call.warning("Dropping out-of-range quote line used-history entry: %r", -3),
    ]
    assert raw == ["A" * 64, "b" * 64, "1", "invalid", -3]


def test_image_corpus_proof_requires_exact_nonempty_names_and_preserves_native_errors(monkeypatch):
    paths = Mock(side_effect=Path)
    patch_history_dependency(monkeypatch, "Path", paths)
    assert bot.image_corpus_verified_for_legacy_migration(object(), None) is False
    paths.assert_not_called()
    for images, analysis, expected in [
        ([], {}, False),
        (["/synthetic/a.jpg"], {}, False),
        (["/synthetic/a.jpg"], {"path_index": {"a.jpg": "hash", "b.jpg": "hash"}}, False),
        (["/synthetic/a.jpg", "/elsewhere/a.jpg"], {"path_index": {"a.jpg": "ignored"}}, True),
        (["/synthetic/1"], {"path_index": {1: "ignored"}}, True),
    ]:
        assert bot.image_corpus_verified_for_legacy_migration(images, analysis) is expected
    with pytest.raises(AttributeError):
        bot.image_corpus_verified_for_legacy_migration([], {"path_index": [1]})


@pytest.mark.parametrize("proved", [False, True])
def test_image_normalization_keeps_eager_names_original_unsafe_items_and_invalid_indices(monkeypatch, proved):
    trace = Mock()

    class InvalidInteger:
        def __str__(self):
            return "0"

        def __int__(self):
            raise ValueError("retain original")

    invalid = InvalidInteger()
    image = "/synthetic/a.jpg"
    images, analysis = [image], object()
    trace.path.side_effect = Path
    trace.proof.return_value = proved
    patch_history_dependency(monkeypatch, "Path", trace.path)
    patch_history_dependency(monkeypatch, "image_corpus_verified_for_legacy_migration", trace.proof)
    raw = {0, -1, 4, invalid, "missing.jpg"}
    result, changed = bot.normalise_image_used_basenames(raw, images, analysis)
    assert trace.mock_calls == [call.path(image), call.proof(images, analysis)]
    assert trace.proof.call_args.args[0] is images and trace.proof.call_args.args[1] is analysis
    assert result == ({"a.jpg", -1, 4, invalid, "missing.jpg"} if proved else raw)
    assert any(item is invalid for item in result)
    assert changed is proved
    assert raw == {0, -1, 4, invalid, "missing.jpg"}


def test_normalizers_retain_distinct_final_comparison_and_short_circuit(monkeypatch):
    class Once(set):
        def __iter__(self):
            self.iterations = getattr(self, "iterations", 0) + 1
            if self.iterations > 1:
                raise RuntimeError("second pass")
            return super().__iter__()

    patch_history_dependency(monkeypatch, "current_quote_hashes_by_line", lambda lines: {0: "a" * 64})
    patch_history_dependency(monkeypatch, "quote_source_matches_analysis", lambda analysis, lines: True)
    patch_history_dependency(monkeypatch, "image_corpus_verified_for_legacy_migration", lambda images, analysis: True)
    raw = Once({0})
    assert bot.normalise_quote_used_hashes(raw, []) == ({"a" * 64}, True)
    assert raw.iterations == 1
    with pytest.raises(RuntimeError, match="second pass"):
        bot.normalise_quote_used_hashes(Once({"a" * 64}), [])
    with pytest.raises(RuntimeError, match="second pass"):
        bot.normalise_image_used_basenames(Once({0}), ["/synthetic/a.jpg"])


def test_image_string_only_conversion_does_not_force_changed_and_paths_precede_proof(monkeypatch):
    class Basename:
        def __str__(self):
            return "missing.jpg"

    trace = Mock()
    patch_history_dependency(monkeypatch, "image_corpus_verified_for_legacy_migration", trace.proof)
    result, changed = bot.normalise_image_used_basenames({Basename()}, [])
    assert result == {"missing.jpg"} and changed is False
    trace.proof.assert_called_once_with([], None)
    trace.reset_mock()
    failure = TypeError("path conversion")
    patch_history_dependency(monkeypatch, "Path", Mock(side_effect=failure))
    with pytest.raises(TypeError) as caught:
        bot.normalise_image_used_basenames(set(), [object()])
    assert caught.value is failure
    trace.proof.assert_not_called()


def _history_trace(monkeypatch, kind, changed, legacy, exists):
    trace = Mock()
    path, old_path = SimpleNamespace(exists=trace.exists), object()
    trace.load.return_value = {"raw"}
    trace.analysis.return_value = object()
    trace.normalise.return_value = ({"normalised"}, changed)
    trace.legacy.return_value = legacy
    trace.exists.return_value = exists
    if kind == "quote":
        names = ("LINES_USED_FILE", "PICKLE_FILE", "load_quote_analysis", "normalise_quote_used_hashes", "quote_used_history_has_legacy_indices", "save_used_set")
    else:
        names = ("IMAGES_USED_FILE", "IMAGE_PICKLE_FILE", "load_image_analysis", "normalise_image_used_basenames", "image_used_history_has_legacy_indices", "save_image_used_basenames")
    for name, value in zip(names, (path, old_path, trace.analysis, trace.normalise, trace.legacy, trace.save)):
        patch_history_dependency(monkeypatch, name, value)
    patch_history_dependency(monkeypatch, "load_used_set", trace.load)
    patch_history_dependency(monkeypatch, "log", trace.log)
    return trace, path, old_path


@pytest.mark.parametrize("legacy, changed, exists", [
    (True, True, True), (False, True, False), (False, False, True), (False, False, False),
])
def test_quote_loader_preserves_legacy_gate_lazy_exists_write_order_and_references(monkeypatch, legacy, changed, exists):
    trace, path, old_path = _history_trace(monkeypatch, "quote", changed, legacy, exists)
    lines = object()
    result = bot.load_quote_used_hashes(lines)
    assert result is trace.normalise.return_value[0]
    assert trace.normalise.call_args.args[0] is trace.load.return_value
    assert trace.normalise.call_args.args[1] is lines
    assert trace.normalise.call_args.args[2] is trace.analysis.return_value
    expected = [call.load(path, legacy_pickle_path=old_path), call.analysis(),
                call.normalise(trace.load.return_value, lines, trace.analysis.return_value), call.legacy(result)]
    if legacy:
        expected.append(call.log.critical("Quote used-history contains legacy integer entries but current quote source does not match analysed source; refusing destructive migration"))
    else:
        if not changed:
            expected.append(call.exists())
        if changed or exists:
            expected += [call.save(path, result), call.log.info("Quote used-history normalised to %d quote hash(es)", 1)]
            assert trace.save.call_args.args[0] is path and trace.save.call_args.args[1] is result
    assert trace.mock_calls == expected


@pytest.mark.parametrize("legacy, changed, nonempty, exists", [
    (True, True, True, True), (True, True, False, True),
    (False, True, True, False), (False, False, True, True),
    (False, False, True, False), (False, False, False, True),
])
def test_image_loader_preserves_empty_scan_legacy_gate_lazy_exists_and_write_order(monkeypatch, legacy, changed, nonempty, exists):
    trace, path, old_path = _history_trace(monkeypatch, "image", changed, legacy, exists)
    images = [object()] if nonempty else []
    result = bot.load_image_used_basenames(images)
    assert result is trace.normalise.return_value[0]
    assert trace.normalise.call_args.args[0] is trace.load.return_value
    assert trace.normalise.call_args.args[1] is images
    assert trace.normalise.call_args.args[2] is trace.analysis.return_value
    expected = [call.load(path, legacy_pickle_path=old_path), call.analysis(),
                call.normalise(trace.load.return_value, images, trace.analysis.return_value), call.legacy(result)]
    if legacy and nonempty:
        expected.append(call.log.critical("Image used-history contains legacy integer entries but current image corpus is not verified complete; refusing destructive migration"))
    elif nonempty:
        if not changed:
            expected.append(call.exists())
        if changed or exists:
            expected += [call.save(path, result), call.log.info("Image used-history normalised to %d basename(s)", 1)]
            assert trace.save.call_args.args[0] is path and trace.save.call_args.args[1] is result
    elif changed:
        expected.append(call.log.warning("Image scan is empty; preserving image used-history without rewriting %s", path))
    assert trace.mock_calls == expected
